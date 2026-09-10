"""Sincronizacion diferida: politica, lease, reconciliacion y resultados tardios.

Todo lo de este archivo ocurre **sin dormir un solo segundo real**. El reloj y el
sleeper se inyectan; el sleeper anota lo que recibe y adelanta el reloj falso, de
modo que una espera de setenta segundos cuesta lo mismo que una de uno y la
secuencia exacta de demoras queda observable.

Lo que se ejerce aqui son las intercalaciones, que es donde estaba el riesgo:
dos respuestas exitosas legitimas para el mismo evento, un reconciliador que
llega tarde, un resultado que llega despues de que la reconciliacion cerrara el
intento, un intento abierto sobre un evento ya entregado, y la pausa global que
impide golpear cincuenta veces una API caida.

El ciclo real contra la aplicacion FastAPI y PostgreSQL 16 vive en
``test_edge_sincronizacion_postgresql.py``.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.edge import almacenamiento as alm
from app.edge import captura as cap
from app.edge import outbox
from app.edge import sincronizacion as sincro
from app.edge.cliente import CABECERA_REPLAY, ClienteEdge
from app.edge.emisor import ejecutar_pasada
from app.edge.estados import EstadoEntrega, MotivoRevision, ResultadoEntrega
from app.edge.politica import PoliticaDeReintentos
from tests.test_edge_captura import PAQUETE_DE_UNA_LECTURA

BASE = "http://nodo-edge.invalid"
INICIO = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Instrumentos
# ---------------------------------------------------------------------------


class RelojFalso:
    """Un reloj que solo avanza cuando alguien lo mueve."""

    def __init__(self, inicio: datetime = INICIO) -> None:
        self.ahora = inicio

    def __call__(self) -> datetime:
        return self.ahora

    def avanzar(self, segundos: float) -> None:
        self.ahora = self.ahora + timedelta(seconds=segundos)


class Sleeper:
    """Anota cada espera y adelanta el reloj. Nunca duerme de verdad."""

    def __init__(self, reloj: RelojFalso) -> None:
        self.reloj = reloj
        self.esperas: list[float] = []

    def __call__(self, segundos: float) -> None:
        assert segundos > 0, "no se debe llamar al sleeper con un valor no positivo"
        self.esperas.append(segundos)
        self.reloj.avanzar(segundos)


class Transporte:
    """Un guion de respuestas, con cuenta de peticiones realmente enviadas."""

    def __init__(self, guion) -> None:
        self._guion = guion
        self.peticiones: list[str] = []

    def __call__(self, peticion: httpx.Request):
        self.peticiones.append(peticion.headers.get("Idempotency-Key", ""))
        return self._guion(peticion, len(self.peticiones))

    @property
    def enviadas(self) -> int:
        return len(self.peticiones)


def creada(*, reproducido: bool, lecturas: int = 1, id_sesion: int = 832):
    return httpx.Response(
        201,
        json={
            "id_sesion": id_sesion,
            "lecturas_creadas": lecturas,
            "ids_lectura": [1280 + i for i in range(lecturas)],
        },
        headers={CABECERA_REPLAY: "true" if reproducido else "false"},
    )


def cliente(transporte: Transporte) -> ClienteEdge:
    return ClienteEdge(
        httpx.Client(transport=httpx.MockTransport(transporte), base_url=BASE)
    )


def politica(**cambios) -> PoliticaDeReintentos:
    valores = dict(
        max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=60.0,
        batch_limit=50, http_timeout=10.0,
    )
    valores.update(cambios)
    return PoliticaDeReintentos(**valores)


@pytest.fixture
def conexion(tmp_path):
    with alm.conectar(tmp_path / "nodo_edge.sqlite3") as conexion:
        alm.inicializar(conexion)
        yield conexion


def capturar(conexion, cuantos: int = 1, reloj=None):
    return [
        cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA, reloj=reloj or outbox.ahora_utc)
        for _ in range(cuantos)
    ]


def historial(conexion, id_outbox):
    return conexion.execute(
        "SELECT * FROM intento_sincronizacion WHERE id_outbox = ? ORDER BY numero",
        (id_outbox,),
    ).fetchall()


def reclamar(conexion, id_outbox, *, p, momento):
    with alm.transaccion(conexion):
        return outbox.reclamar_intento(
            conexion,
            id_outbox,
            max_attempts=p.max_attempts,
            duracion_lease=p.duracion_del_lease,
            momento=momento,
        )


# ---------------------------------------------------------------------------
# 1. La guarda de progreso no castiga una espera normal (E1)
# ---------------------------------------------------------------------------


def test_un_lease_largo_con_tope_corto_no_termina_en_codigo_tres(conexion):
    """Setenta segundos de lease, un segundo de tope: 70 esperas, no un codigo 3.

    Es el caso exacto que rompia el diseno anterior: la guarda se incrementaba
    tras cada iteracion sin modificacion durable, y cuatro esperas correctas de
    un segundo la agotaban mucho antes de que el lease venciera.
    """
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_delay_seconds=1.0, http_timeout=10.0)
    assert p.duracion_del_lease == 70.0

    registro = capturar(conexion)[0]
    reclamar(conexion, registro.id_outbox, p=p, momento=reloj())

    transporte = Transporte(lambda peticion, n: creada(reproducido=True))
    informe = sincro.sincronizar(
        conexion, cliente(transporte), politica=p, reloj=reloj, dormir=dormir
    )

    assert informe.codigo_de_salida == sincro.CODIGO_EXITO
    assert len(dormir.esperas) >= 60
    assert set(dormir.esperas) == {1.0}
    assert informe.intentos_reconciliados == 1
    assert outbox.leer_evento(conexion, registro.id_outbox)["estado"] == (
        EstadoEntrega.ENVIADO.value
    )


def test_una_espera_positiva_nunca_incrementa_la_guarda(conexion):
    """Diez esperas seguidas y la ejecucion sigue viva."""
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_delay_seconds=1.0, http_timeout=1.0)

    registro = capturar(conexion)[0]
    reclamar(conexion, registro.id_outbox, p=p, momento=reloj())

    transporte = Transporte(lambda peticion, n: creada(reproducido=True))
    informe = sincro.sincronizar(
        conexion, cliente(transporte), politica=p, reloj=reloj, dormir=dormir
    )
    assert len(dormir.esperas) > sincro.COTA_INERTE
    assert informe.codigo_de_salida != sincro.CODIGO_ANOMALIA


def test_trabajo_futuro_programado_nunca_produce_codigo_tres(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=3, base_delay_seconds=4.0, max_delay_seconds=4.0)

    capturar(conexion)
    intentos = {"n": 0}

    def guion(peticion, n):
        intentos["n"] += 1
        if intentos["n"] < 3:
            return httpx.Response(503, json={"detail": "no disponible"})
        return creada(reproducido=False)

    informe = sincro.sincronizar(
        conexion, cliente(Transporte(guion)), politica=p, reloj=reloj, dormir=dormir
    )
    assert informe.codigo_de_salida == sincro.CODIGO_EXITO
    assert dormir.esperas == [4.0, 4.0]


def test_una_cola_vacia_termina_de_inmediato(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    informe = sincro.sincronizar(
        conexion,
        cliente(Transporte(lambda p, n: creada(reproducido=False))),
        politica=politica(),
        reloj=reloj,
        dormir=dormir,
    )
    assert informe.codigo_de_salida == sincro.CODIGO_EXITO
    assert dormir.esperas == []
    assert informe.rondas == 0


# ---------------------------------------------------------------------------
# 2. La pausa global por transporte (E2)
# ---------------------------------------------------------------------------


def test_una_api_caida_produce_una_sola_peticion_antes_de_esperar(conexion):
    """Cinco eventos pendientes, la API caida: **una** llamada, no cinco.

    Es la regresion del defecto: la ronda rompia, pero el bucle censaba, veia los
    otros cuatro elegibles y abria otra ronda de inmediato. La pausa global es lo
    que hace que el ``break`` proteja la cola y no solo la ronda.
    """
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=2, base_delay_seconds=5.0)
    capturar(conexion, 5)

    caida = {"activa": True}

    def guion(peticion, n):
        if caida["activa"]:
            raise httpx.ConnectError("simulado", request=peticion)
        return creada(reproducido=False)

    transporte = Transporte(guion)

    # Se corta la ejecucion en cuanto empieza a esperar, para contar peticiones.
    class Corta(Exception):
        pass

    def dormir_y_cortar(segundos):
        dormir(segundos)
        raise Corta

    with pytest.raises(Corta):
        sincro.sincronizar(
            conexion, cliente(transporte), politica=p, reloj=reloj,
            dormir=dormir_y_cortar,
        )

    assert transporte.enviadas == 1
    assert dormir.esperas == [5.0]

    # La API vuelve: los cuatro restantes y el reintento del primero se entregan.
    caida["activa"] = False
    informe = sincro.sincronizar(
        conexion, cliente(transporte), politica=p, reloj=reloj, dormir=dormir
    )
    assert informe.codigo_de_salida == sincro.CODIGO_EXITO
    assert informe.censo.enviados == 5


def test_la_pausa_no_bloquea_la_reconciliacion(conexion):
    """Un lease puede vencer y sellarse durante una pausa: es SQLite puro.

    Si la pausa global tambien detuviera la reparacion, un intento abandonado
    quedaria abierto mientras la API siguiera caida, y el evento no volveria a la
    cola nunca. La pausa solo cierra la puerta de la red.
    """
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=3, base_delay_seconds=1.0, http_timeout=1.0)

    abandonado, otro = capturar(conexion, 2)
    reclamar(conexion, abandonado.id_outbox, p=p, momento=reloj())

    def guion(peticion, n):
        raise httpx.ConnectError("simulado", request=peticion)

    transporte = Transporte(guion)
    informe = sincro.sincronizar(
        conexion, cliente(transporte), politica=p, reloj=reloj, dormir=dormir
    )

    # Hubo pausas por transporte, y aun asi el intento abandonado quedo sellado.
    assert informe.pausas_por_transporte > 0
    assert informe.intentos_reconciliados >= 1
    fila = historial(conexion, abandonado.id_outbox)[0]
    assert fila["reconciliado_en"] is not None
    assert fila["resultado"] is None, "no se inventa el desenlace"


def test_la_pausa_se_extiende_y_nunca_se_acorta(conexion):
    reloj = RelojFalso()
    p = politica(max_attempts=5, base_delay_seconds=1.0)
    capturar(conexion, 1)

    def guion(peticion, n):
        raise httpx.ConnectError("simulado", request=peticion)

    ronda_uno = ejecutar_pasada(
        conexion, cliente(Transporte(guion)), politica=p, reloj=reloj,
        respetar_programacion=True,
    )
    assert ronda_uno.pausa_hasta == reloj() + timedelta(seconds=1.0)

    reloj.avanzar(1.0)
    ronda_dos = ejecutar_pasada(
        conexion, cliente(Transporte(guion)), politica=p, reloj=reloj,
        respetar_programacion=True,
    )
    # Segundo fallo -> demora(2) = 2 s, mas tarde que la primera pausa.
    assert ronda_dos.pausa_hasta == reloj() + timedelta(seconds=2.0)
    assert ronda_dos.pausa_hasta > ronda_uno.pausa_hasta


def test_un_transporte_en_el_ultimo_intento_agota_y_aun_asi_pausa(conexion):
    """El evento se cierra, pero la pausa sigue hablando de la API."""
    reloj = RelojFalso()
    p = politica(max_attempts=1, base_delay_seconds=3.0)
    registro = capturar(conexion)[0]

    def guion(peticion, n):
        raise httpx.ConnectError("simulado", request=peticion)

    ronda = ejecutar_pasada(
        conexion, cliente(Transporte(guion)), politica=p, reloj=reloj,
        respetar_programacion=True,
    )

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["reintentable"] == 0
    assert fila["motivo_revision"] == MotivoRevision.AGOTAMIENTO.value
    assert fila["proximo_intento_en"] is None
    assert ronda.pausa_hasta == reloj() + timedelta(seconds=p.demora(1))


def test_la_pausa_no_se_persiste(conexion):
    """Otra ejecucion sobre el mismo archivo no arranca en pausa."""
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=2, base_delay_seconds=1.0)
    capturar(conexion, 1)

    caida = {"activa": True}

    def guion(peticion, n):
        if caida["activa"]:
            raise httpx.ConnectError("simulado", request=peticion)
        return creada(reproducido=False)

    transporte = Transporte(guion)

    class Corta(Exception):
        pass

    with pytest.raises(Corta):
        sincro.sincronizar(
            conexion, cliente(transporte), politica=p, reloj=reloj,
            dormir=lambda s: (_ for _ in ()).throw(Corta()),
        )

    caida["activa"] = False
    reloj.avanzar(10)
    informe = sincro.sincronizar(
        conexion, cliente(transporte), politica=p, reloj=reloj, dormir=dormir
    )
    assert informe.entregados == 1
    assert informe.pausas_por_transporte == 0


def test_con_pausa_pero_sin_elegibles_la_ejecucion_termina(conexion):
    """Esperar una pausa sin nada que enviar mantendria viva una ejecucion inutil."""
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=1, base_delay_seconds=30.0)
    capturar(conexion, 1)

    def guion(peticion, n):
        raise httpx.ConnectError("simulado", request=peticion)

    informe = sincro.sincronizar(
        conexion, cliente(Transporte(guion)), politica=p, reloj=reloj, dormir=dormir
    )
    # Un solo intento, agotado; ya no hay elegibles, asi que no se espera la pausa.
    assert dormir.esperas == []
    assert informe.codigo_de_salida == sincro.CODIGO_REVISION


# ---------------------------------------------------------------------------
# 3. Varias respuestas ENTREGADO legitimas (D1)
# ---------------------------------------------------------------------------


def test_dos_respuestas_exitosas_conviven_y_solo_una_aplica_la_transicion(conexion):
    """Intento 1 reconciliado, intento 2 entregado, y luego llega el 201 del 1.

    Sin el indice retirado esto habria reventado con ``IntegrityError`` antes de
    llegar a la guarda. Con ``confirmo_transicion`` los dos resultados reales se
    conservan y solo uno movio la fila.
    """
    reloj = RelojFalso()
    p = politica(max_attempts=3)
    registro = capturar(conexion)[0]

    # Intento 1: reclamado y abandonado.
    primera = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    reloj.avanzar(p.duracion_del_lease + 1)
    sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj())

    # Intento 2: reclamado y entregado.
    reloj.avanzar(p.demora(1) + 1)
    segunda = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    with alm.transaccion(conexion):
        tardio = outbox.finalizar_intento(
            conexion, segunda.id_intento, resultado=ResultadoEntrega.ENTREGADO,
            codigo_http=201, reproducido=True, error="", demora=None, momento=reloj(),
        )
        assert tardio is False
        assert outbox.marcar_enviado(
            conexion, registro.id_outbox, id_sesion=832, ids_lectura=(1280,),
            codigo_http=201, momento=reloj(),
        )
        outbox.confirmar_transicion(conexion, segunda.id_intento)

    # Y ahora llega, tardisimo, el 201 del intento 1.
    with alm.transaccion(conexion):
        tardio = outbox.finalizar_intento(
            conexion, primera.id_intento, resultado=ResultadoEntrega.ENTREGADO,
            codigo_http=201, reproducido=False, error="", demora=None, momento=reloj(),
        )
        assert tardio is True
        # Prevalece la entrega ya aplicada: la guarda rechaza la segunda.
        assert outbox.marcar_enviado(
            conexion, registro.id_outbox, id_sesion=999, ids_lectura=(1,),
            codigo_http=201, momento=reloj(),
        ) is False

    filas = historial(conexion, registro.id_outbox)
    entregados = [f for f in filas if f["resultado"] == "ENTREGADO"]
    assert len(entregados) == 2
    assert sum(f["confirmo_transicion"] for f in filas) == 1
    assert filas[1]["confirmo_transicion"] == 1
    assert outbox.leer_evento(conexion, registro.id_outbox)["id_sesion_remota"] == 832


def test_no_puede_haber_dos_intentos_que_apliquen_la_transicion(conexion):
    """El indice parcial lo impide, no una convencion del codigo."""
    import sqlite3

    reloj = RelojFalso()
    p = politica()
    registro = capturar(conexion)[0]
    primera = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    with alm.transaccion(conexion):
        outbox.finalizar_intento(
            conexion, primera.id_intento, resultado=ResultadoEntrega.ENTREGADO,
            codigo_http=201, reproducido=False, error="", demora=None, momento=reloj(),
        )
        outbox.confirmar_transicion(conexion, primera.id_intento)

    reloj.avanzar(p.duracion_del_lease + 1)
    with alm.transaccion(conexion):
        conexion.execute(
            "INSERT INTO intento_sincronizacion (id_outbox, numero, iniciado_en,"
            " reconciliable_en, finalizado_en, resultado, confirmo_transicion)"
            " VALUES (?, 99, 't', 't', 't', 'ENTREGADO', 0)",
            (registro.id_outbox,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            conexion.execute(
                "UPDATE intento_sincronizacion SET confirmo_transicion = 1"
                " WHERE id_outbox = ? AND numero = 99",
                (registro.id_outbox,),
            )


def test_el_lease_impide_dos_intentos_abiertos(conexion):
    reloj = RelojFalso()
    p = politica()
    registro = capturar(conexion)[0]
    assert reclamar(conexion, registro.id_outbox, p=p, momento=reloj()) is not None
    # El segundo no encuentra nada que reclamar: hay un intento abierto.
    assert reclamar(conexion, registro.id_outbox, p=p, momento=reloj()) is None
    assert len(historial(conexion, registro.id_outbox)) == 1


def test_un_evento_con_intento_abierto_no_es_elegible(conexion):
    reloj = RelojFalso()
    p = politica()
    registro = capturar(conexion)[0]
    reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    assert outbox.seleccionar_elegibles(conexion, limite=10) == ()
    # Ni siquiera el envio manual, que si ignora la programacion.
    assert (
        outbox.seleccionar_elegibles(
            conexion, limite=10, respetar_programacion=False
        )
        == ()
    )


# ---------------------------------------------------------------------------
# 4. La carrera del reconciliador (D2)
# ---------------------------------------------------------------------------


def test_el_cas_de_la_reconciliacion_pierde_ante_un_resultado_real(conexion):
    """Preseleccion, resultado real, CAS a cero: no se toca nada.

    1. el reconciliador preselecciona un intento vencido;
    2. el emisor original registra su resultado real;
    3. el CAS del reconciliador devuelve cero;
    4. el resultado real y la outbox permanecen intactos.
    """
    reloj = RelojFalso()
    p = politica(max_attempts=3)
    registro = capturar(conexion)[0]
    reclamacion = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())

    reloj.avanzar(p.duracion_del_lease + 1)
    preseleccion = outbox.intentos_abandonados(conexion, momento=reloj())
    assert preseleccion == (reclamacion.id_intento,)

    # El emisor original despierta y registra su 503.
    with alm.transaccion(conexion):
        outbox.finalizar_intento(
            conexion, reclamacion.id_intento, resultado=ResultadoEntrega.REINTENTABLE,
            codigo_http=503, reproducido=None, error="error del servidor 503",
            demora=1.0, momento=reloj(),
        )
        outbox.marcar_fallido(
            conexion, registro.id_outbox, reintentable=True, codigo_http=503,
            error="error del servidor 503", momento=reloj(),
            proximo_intento_en=reloj() + timedelta(seconds=1.0),
        )

    antes = dict(outbox.leer_evento(conexion, registro.id_outbox))

    with alm.transaccion(conexion):
        assert outbox.reclamar_reconciliacion(
            conexion, reclamacion.id_intento, momento=reloj()
        ) is None

    fila = historial(conexion, registro.id_outbox)[0]
    assert fila["resultado"] == "REINTENTABLE"
    assert fila["codigo_http"] == 503
    assert fila["reconciliado_en"] is None
    assert dict(outbox.leer_evento(conexion, registro.id_outbox)) == antes


def test_la_reconciliacion_relee_el_estado_y_no_pisa_un_intento_posterior(conexion):
    """Si el contador avanzo, el intento viejo se sella y la outbox no se toca.

    Es la razon de que la actualizacion lleve ``intentos = :numero``: decidir con
    los valores que vio la preseleccion habria reescrito el evento desde un
    intento que ya es historia.
    """
    reloj = RelojFalso()
    p = politica(max_attempts=5)
    registro = capturar(conexion)[0]
    primera = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    assert primera.numero == 1

    # El contador avanza --otro sincronizador reclamo el intento 2-- mientras el
    # primero sigue abierto.
    with alm.transaccion(conexion):
        conexion.execute(
            "UPDATE outbox SET intentos = 2 WHERE id_outbox = ?",
            (registro.id_outbox,),
        )
    antes = dict(outbox.leer_evento(conexion, registro.id_outbox))

    reloj.avanzar(p.duracion_del_lease + 1)
    assert sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj()) == 1

    # El intento quedo sellado...
    fila = historial(conexion, registro.id_outbox)[0]
    assert fila["reconciliado_en"] is not None
    assert fila["resultado"] is None
    # ...y la fila del evento no se movio ni un campo.
    assert dict(outbox.leer_evento(conexion, registro.id_outbox)) == antes


def test_la_reconciliacion_es_idempotente(conexion):
    """Ejecutarla dos veces no vuelve a mover la fecha del proximo intento."""
    reloj = RelojFalso()
    p = politica(max_attempts=5, base_delay_seconds=10.0)
    registro = capturar(conexion)[0]
    reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    reloj.avanzar(p.duracion_del_lease + 1)

    assert sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj()) == 1
    primera = outbox.leer_evento(conexion, registro.id_outbox)["proximo_intento_en"]

    reloj.avanzar(3600)
    assert sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj()) == 0
    assert outbox.leer_evento(conexion, registro.id_outbox)["proximo_intento_en"] == (
        primera
    )


def test_un_intento_dentro_de_su_ventana_no_se_reconcilia(conexion):
    reloj = RelojFalso()
    p = politica()
    registro = capturar(conexion)[0]
    reclamar(conexion, registro.id_outbox, p=p, momento=reloj())

    reloj.avanzar(p.duracion_del_lease - 1)
    assert outbox.intentos_abandonados(conexion, momento=reloj()) == ()
    assert sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj()) == 0


def test_el_vencimiento_del_lease_no_se_recalcula_al_cambiar_la_configuracion(conexion):
    """``reconciliable_en`` se congela al reclamar."""
    reloj = RelojFalso()
    registro = capturar(conexion)[0]
    reclamacion = reclamar(
        conexion, registro.id_outbox, p=politica(http_timeout=10.0), momento=reloj()
    )
    fila = historial(conexion, registro.id_outbox)[0]
    esperado = reloj() + timedelta(seconds=politica(http_timeout=10.0).duracion_del_lease)
    assert datetime.fromisoformat(fila["reconciliable_en"]) == esperado

    # Otra ejecucion con un timeout mucho menor no adelanta el vencimiento.
    otra = politica(http_timeout=1.0)
    reloj.avanzar(otra.duracion_del_lease + 1)
    assert sincro.reconciliar_abandonados(conexion, politica=otra, momento=reloj()) == 0


# ---------------------------------------------------------------------------
# 5. Intentos abiertos sobre eventos terminales (D3)
# ---------------------------------------------------------------------------


def test_un_intento_abierto_sobre_un_enviado_se_sella_sin_degradar(conexion):
    """Intento 1 reconciliado, intento 2 abierto, exito tardio del 1, muere el 2."""
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=5)
    registro = capturar(conexion)[0]

    primera = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    reloj.avanzar(p.duracion_del_lease + 1)
    sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj())
    reloj.avanzar(p.demora(1) + 1)
    segunda = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())

    # El exito tardio del intento 1 entrega el paquete.
    with alm.transaccion(conexion):
        outbox.finalizar_intento(
            conexion, primera.id_intento, resultado=ResultadoEntrega.ENTREGADO,
            codigo_http=201, reproducido=False, error="", demora=None, momento=reloj(),
        )
        assert outbox.marcar_enviado(
            conexion, registro.id_outbox, id_sesion=832, ids_lectura=(1280,),
            codigo_http=201, momento=reloj(),
        )
        outbox.confirmar_transicion(conexion, primera.id_intento)

    # El proceso del intento 2 murio: queda abierto sobre un evento ENVIADO.
    assert historial(conexion, registro.id_outbox)[1]["finalizado_en"] is None

    censo = outbox.censar(conexion, max_attempts=p.max_attempts, momento=reloj())
    assert censo.con_intento_abierto == 0, "un terminal no cuenta como trabajo"

    reloj.avanzar(p.duracion_del_lease + 1)
    informe = sincro.sincronizar(
        conexion,
        cliente(Transporte(lambda peticion, n: creada(reproducido=True))),
        politica=p, reloj=reloj, dormir=dormir,
    )

    assert informe.codigo_de_salida == sincro.CODIGO_EXITO
    fila = historial(conexion, registro.id_outbox)[1]
    assert fila["reconciliado_en"] is not None
    assert fila["resultado"] is None
    evento = outbox.leer_evento(conexion, registro.id_outbox)
    assert evento["estado"] == EstadoEntrega.ENVIADO.value
    assert evento["id_sesion_remota"] == 832
    assert segunda.id_intento is not None


def test_una_outbox_enteramente_enviada_termina_en_codigo_cero(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica()
    capturar(conexion, 3)
    informe = sincro.sincronizar(
        conexion,
        cliente(Transporte(lambda peticion, n: creada(reproducido=False))),
        politica=p, reloj=reloj, dormir=dormir,
    )
    assert informe.censo.enviados == 3
    assert informe.codigo_de_salida == sincro.CODIGO_EXITO


# ---------------------------------------------------------------------------
# 6. Resultados tardios (D4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "resultado, codigo",
    [(ResultadoEntrega.REINTENTABLE, 503), (ResultadoEntrega.RECHAZADO, 409)],
)
def test_un_resultado_tardio_se_guarda_y_no_toca_la_outbox(conexion, resultado, codigo):
    reloj = RelojFalso()
    p = politica(max_attempts=1)
    registro = capturar(conexion)[0]
    reclamacion = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())

    reloj.avanzar(p.duracion_del_lease + 1)
    sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj())
    antes = dict(outbox.leer_evento(conexion, registro.id_outbox))
    assert antes["motivo_revision"] == MotivoRevision.AGOTAMIENTO.value

    with alm.transaccion(conexion):
        tardio = outbox.finalizar_intento(
            conexion, reclamacion.id_intento, resultado=resultado, codigo_http=codigo,
            reproducido=None, error="respuesta tardia", demora=None, momento=reloj(),
        )
    assert tardio is True

    fila = historial(conexion, registro.id_outbox)[0]
    assert fila["resultado"] == resultado.value
    assert fila["codigo_http"] == codigo
    assert fila["reconciliado_en"] is not None
    assert dict(outbox.leer_evento(conexion, registro.id_outbox)) == antes


def test_un_fallo_tardio_no_revierte_un_agotamiento(conexion):
    """La guarda SQL lo impide aunque el codigo se equivocara."""
    reloj = RelojFalso()
    p = politica(max_attempts=1)
    registro = capturar(conexion)[0]
    reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    reloj.avanzar(p.duracion_del_lease + 1)
    sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj())

    with alm.transaccion(conexion):
        aplicado = outbox.marcar_fallido(
            conexion, registro.id_outbox, reintentable=True, codigo_http=503,
            error="tardio", momento=reloj(),
            proximo_intento_en=reloj() + timedelta(seconds=1),
        )
    assert aplicado is False
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["reintentable"] == 0
    assert fila["motivo_revision"] == MotivoRevision.AGOTAMIENTO.value
    assert fila["proximo_intento_en"] is None


def test_un_exito_tardio_si_prevalece_sobre_un_agotamiento(conexion):
    reloj = RelojFalso()
    p = politica(max_attempts=1)
    registro = capturar(conexion)[0]
    reclamacion = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    reloj.avanzar(p.duracion_del_lease + 1)
    sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj())

    with alm.transaccion(conexion):
        assert outbox.finalizar_intento(
            conexion, reclamacion.id_intento, resultado=ResultadoEntrega.ENTREGADO,
            codigo_http=201, reproducido=True, error="", demora=None, momento=reloj(),
        ) is True
        assert outbox.marcar_enviado(
            conexion, registro.id_outbox, id_sesion=832, ids_lectura=(1280,),
            codigo_http=201, momento=reloj(),
        )
        outbox.confirmar_transicion(conexion, reclamacion.id_intento)

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.ENVIADO.value
    assert fila["motivo_revision"] is None
    assert fila["reintentable"] is None


def test_la_demora_aplicada_por_la_reconciliacion_no_se_borra(conexion):
    reloj = RelojFalso()
    p = politica(max_attempts=5, base_delay_seconds=7.0)
    registro = capturar(conexion)[0]
    reclamacion = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    reloj.avanzar(p.duracion_del_lease + 1)
    sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj())
    assert historial(conexion, registro.id_outbox)[0]["demora_programada_s"] == 7.0

    with alm.transaccion(conexion):
        outbox.finalizar_intento(
            conexion, reclamacion.id_intento, resultado=ResultadoEntrega.REINTENTABLE,
            codigo_http=503, reproducido=None, error="x", demora=999.0, momento=reloj(),
        )
    assert historial(conexion, registro.id_outbox)[0]["demora_programada_s"] == 7.0


def test_finalizar_un_intento_ya_cerrado_no_escribe_nada(conexion):
    reloj = RelojFalso()
    p = politica()
    registro = capturar(conexion)[0]
    reclamacion = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    with alm.transaccion(conexion):
        assert outbox.finalizar_intento(
            conexion, reclamacion.id_intento, resultado=ResultadoEntrega.RECHAZADO,
            codigo_http=409, reproducido=None, error="conflicto", demora=None,
            momento=reloj(),
        ) is False
    with alm.transaccion(conexion):
        assert outbox.finalizar_intento(
            conexion, reclamacion.id_intento, resultado=ResultadoEntrega.ENTREGADO,
            codigo_http=201, reproducido=False, error="", demora=None, momento=reloj(),
        ) is None
    fila = historial(conexion, registro.id_outbox)[0]
    assert fila["resultado"] == "RECHAZADO"
    assert fila["codigo_http"] == 409


# ---------------------------------------------------------------------------
# 7. Agotamiento, reinicio y ordinales
# ---------------------------------------------------------------------------


def test_el_agotamiento_ocurre_en_el_intento_exacto_y_no_hay_uno_mas(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=3, base_delay_seconds=2.0)
    registro = capturar(conexion)[0]

    transporte = Transporte(lambda peticion, n: httpx.Response(503, json={"detail": "x"}))
    informe = sincro.sincronizar(
        conexion, cliente(transporte), politica=p, reloj=reloj, dormir=dormir
    )

    assert transporte.enviadas == 3
    assert dormir.esperas == [2.0, 4.0]
    assert informe.agotados == 1
    assert informe.codigo_de_salida == sincro.CODIGO_REVISION

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["intentos"] == 3
    assert fila["motivo_revision"] == MotivoRevision.AGOTAMIENTO.value
    assert len(historial(conexion, registro.id_outbox)) == 3

    # Otra ejecucion ordinaria no crea un cuarto intento.
    informe = sincro.sincronizar(
        conexion, cliente(transporte), politica=p, reloj=reloj, dormir=dormir
    )
    assert transporte.enviadas == 3
    assert outbox.leer_evento(conexion, registro.id_outbox)["intentos"] == 3


def test_un_error_permanente_gasta_un_solo_intento_y_ninguna_espera(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=5)
    registro = capturar(conexion)[0]
    transporte = Transporte(lambda peticion, n: httpx.Response(409, json={"detail": "x"}))

    informe = sincro.sincronizar(
        conexion, cliente(transporte), politica=p, reloj=reloj, dormir=dormir
    )
    assert transporte.enviadas == 1
    assert dormir.esperas == []
    assert informe.rechazados == 1
    assert informe.codigo_de_salida == sincro.CODIGO_REVISION
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["intentos"] == 1
    assert fila["motivo_revision"] == MotivoRevision.RECHAZO_PERMANENTE.value


def test_un_reinicio_durante_el_backoff_conserva_contador_y_fecha(tmp_path):
    ruta = tmp_path / "nodo_edge.sqlite3"
    reloj = RelojFalso()
    p = politica(max_attempts=5, base_delay_seconds=30.0)

    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
        ejecutar_pasada(
            conexion,
            cliente(Transporte(lambda peticion, n: httpx.Response(503, json={"detail": "x"}))),
            politica=p, reloj=reloj, respetar_programacion=True,
        )
        antes = dict(outbox.leer_evento(conexion, registro.id_outbox))

    # Se cierra el proceso y se abre otro sobre el mismo archivo.
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        despues = dict(outbox.leer_evento(conexion, registro.id_outbox))
        assert despues["intentos"] == antes["intentos"] == 1
        assert despues["proximo_intento_en"] == antes["proximo_intento_en"]
        assert despues["clave_idempotencia"] == antes["clave_idempotencia"]
        assert despues["max_intentos_aplicado"] == 5
        # No es elegible todavia, y la fecha no se ha adelantado.
        assert outbox.seleccionar_elegibles(conexion, limite=10, ahora=reloj()) == ()


def test_el_ordinal_viene_del_returning_y_no_de_la_seleccion(conexion):
    """Aunque la instantanea este vieja, el ordinal es el vigente."""
    reloj = RelojFalso()
    p = politica(max_attempts=5)
    registro = capturar(conexion)[0]
    viejos = outbox.seleccionar_elegibles(conexion, limite=10)
    assert viejos[0].intentos == 0

    primera = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    with alm.transaccion(conexion):
        outbox.finalizar_intento(
            conexion, primera.id_intento, resultado=ResultadoEntrega.REINTENTABLE,
            codigo_http=503, reproducido=None, error="x", demora=1.0, momento=reloj(),
        )
        outbox.marcar_fallido(
            conexion, registro.id_outbox, reintentable=True, codigo_http=503,
            error="x", momento=reloj(), proximo_intento_en=reloj(),
        )
    segunda = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    assert segunda.numero == 2


def test_la_politica_adoptada_sobrevive_a_un_cambio_de_configuracion(conexion):
    reloj = RelojFalso()
    registro = capturar(conexion)[0]
    reclamar(conexion, registro.id_outbox, p=politica(max_attempts=5), momento=reloj())
    assert outbox.leer_evento(conexion, registro.id_outbox)["max_intentos_aplicado"] == 5

    with alm.transaccion(conexion):
        outbox.finalizar_intento(
            conexion, historial(conexion, registro.id_outbox)[0]["id_intento"],
            resultado=ResultadoEntrega.REINTENTABLE, codigo_http=503,
            reproducido=None, error="x", demora=1.0, momento=reloj(),
        )
        outbox.marcar_fallido(
            conexion, registro.id_outbox, reintentable=True, codigo_http=503,
            error="x", momento=reloj(), proximo_intento_en=reloj(),
        )

    # Bajar el entorno a 2 no reescribe el limite adoptado.
    reclamacion = reclamar(
        conexion, registro.id_outbox, p=politica(max_attempts=2), momento=reloj()
    )
    assert reclamacion is not None
    assert reclamacion.max_intentos_aplicado == 5


def test_la_herencia_incompatible_se_resuelve_sin_crear_un_intento(conexion):
    """Un migrado con mas intentos v1 que el limite se cierra explicitamente."""
    reloj = RelojFalso()
    registro = capturar(conexion)[0]
    with alm.transaccion(conexion):
        conexion.execute(
            "UPDATE outbox SET intentos = 4, intentos_heredados = 4,"
            " estado = 'FALLIDO', reintentable = 1 WHERE id_outbox = ?",
            (registro.id_outbox,),
        )

    p = politica(max_attempts=3)
    assert sincro.resolver_herencia(conexion, politica=p, momento=reloj()) == 1

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["motivo_revision"] == MotivoRevision.AGOTAMIENTO_HEREDADO.value
    assert fila["max_intentos_aplicado"] == 3
    assert fila["intentos"] == 4
    assert historial(conexion, registro.id_outbox) == []


def test_la_herencia_incompatible_no_toca_el_ultimo_diagnostico(conexion):
    """``ultimo_http`` y ``ultimo_error`` del intento v1 son evidencia real."""
    reloj = RelojFalso()
    registro = capturar(conexion)[0]
    with alm.transaccion(conexion):
        conexion.execute(
            "UPDATE outbox SET intentos = 4, intentos_heredados = 4,"
            " estado = 'FALLIDO', reintentable = 1, ultimo_http = 503,"
            " ultimo_error = 'error del servidor 503' WHERE id_outbox = ?",
            (registro.id_outbox,),
        )
    sincro.resolver_herencia(conexion, politica=politica(max_attempts=3), momento=reloj())
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["ultimo_http"] == 503
    assert fila["ultimo_error"] == "error del servidor 503"


# ---------------------------------------------------------------------------
# 7 bis. La correlacion de la subconsulta del lease
#
# Regresion de un defecto real: la subconsulta comparaba ``i.id_outbox`` contra
# un ``id_outbox`` **sin calificar**, y como la tabla de intentos tiene una
# columna con ese mismo nombre, SQLite lo resolvia en el ambito interno. La
# condicion se convertia en ``i.id_outbox = i.id_outbox`` --siempre cierta-- y
# el ``NOT EXISTS`` pasaba a preguntar «hay algun intento abierto en cualquier
# parte» en lugar de «lo tiene este evento». El efecto era conservador --dejaba
# de resolver-- pero incorrecto, y ninguna prueba lo veia porque ninguna tenia
# un intento abierto de otro evento.
# ---------------------------------------------------------------------------


def _intento_abierto(conexion, id_outbox, *, numero=1, momento=INICIO):
    """Un intento reclamado que nadie cerro, insertado directamente.

    Se escribe con SQL en lugar de reclamarlo, porque un evento con la herencia
    incompatible ya no es elegible y ``reclamar_intento`` --con razon-- se
    negaria a darle uno.
    """
    with alm.transaccion(conexion):
        conexion.execute(
            "INSERT INTO intento_sincronizacion"
            " (id_outbox, numero, iniciado_en, reconciliable_en)"
            " VALUES (?, ?, ?, ?)",
            (
                id_outbox,
                numero,
                momento.isoformat(),
                (momento + timedelta(seconds=70)).isoformat(),
            ),
        )


def _con_herencia_incompatible(conexion, id_outbox, *, intentos=4):
    """Deja el evento como lo dejaria una base v1 migrada con demasiados intentos."""
    with alm.transaccion(conexion):
        conexion.execute(
            "UPDATE outbox SET intentos = ?, intentos_heredados = ?,"
            " estado = 'FALLIDO', reintentable = 1, max_intentos_aplicado = NULL"
            " WHERE id_outbox = ?",
            (intentos, intentos, id_outbox),
        )


def test_la_subconsulta_del_lease_califica_la_columna_exterior():
    """La forma de la consulta, afirmada directamente.

    Si alguien volviera a pasar un prefijo vacio, la correlacion se romperia en
    silencio y las pruebas de comportamiento de abajo tardarian en explicar por
    que. Esta lo dice en una linea.
    """
    correlacion = outbox._SIN_INTENTO_ABIERTO.format(p=outbox.PREFIJO_OUTBOX)
    assert "i.id_outbox = outbox.id_outbox" in correlacion
    assert "i.id_outbox = id_outbox" not in correlacion


def test_un_intento_abierto_de_otro_evento_no_impide_resolver_la_herencia(conexion):
    """El caso que el defecto rompia: B tiene un intento abierto, A no.

    Con la correlacion rota, el ``NOT EXISTS`` veia el intento abierto de B y se
    negaba a resolver A. Con la columna calificada, cada evento responde por si
    mismo.
    """
    reloj = RelojFalso()
    a, b = capturar(conexion, 2)

    _con_herencia_incompatible(conexion, a.id_outbox)
    _intento_abierto(conexion, b.id_outbox, momento=reloj())

    resueltos = sincro.resolver_herencia(
        conexion, politica=politica(max_attempts=3), momento=reloj()
    )

    assert resueltos == 1
    fila_a = outbox.leer_evento(conexion, a.id_outbox)
    assert fila_a["motivo_revision"] == MotivoRevision.AGOTAMIENTO_HEREDADO.value
    assert fila_a["max_intentos_aplicado"] == 3

    # B no se toco: ni su estado, ni su politica, ni su intento abierto.
    fila_b = outbox.leer_evento(conexion, b.id_outbox)
    assert fila_b["estado"] == EstadoEntrega.PENDIENTE.value
    assert fila_b["motivo_revision"] is None
    assert fila_b["max_intentos_aplicado"] is None
    assert historial(conexion, b.id_outbox)[0]["finalizado_en"] is None


def test_un_intento_abierto_del_propio_evento_si_impide_resolverlo(conexion):
    """La otra mitad: el lease de A sigue protegiendo a A.

    Resolver un evento mientras uno de sus propios intentos esta en vuelo seria
    cerrarlo por debajo de quien lo esta enviando.
    """
    reloj = RelojFalso()
    (a,) = capturar(conexion, 1)

    _con_herencia_incompatible(conexion, a.id_outbox)
    _intento_abierto(conexion, a.id_outbox, numero=4, momento=reloj())
    antes = dict(outbox.leer_evento(conexion, a.id_outbox))

    resueltos = sincro.resolver_herencia(
        conexion, politica=politica(max_attempts=3), momento=reloj()
    )

    assert resueltos == 0
    assert dict(outbox.leer_evento(conexion, a.id_outbox)) == antes


def test_la_resolucion_actualiza_solo_las_filas_correctas_y_cuenta_bien(conexion):
    """Conteo exacto y filas exactas, con los cuatro casos mezclados."""
    reloj = RelojFalso()
    a, b, c, d = capturar(conexion, 4)

    _con_herencia_incompatible(conexion, a.id_outbox)          # se resuelve
    _con_herencia_incompatible(conexion, c.id_outbox)          # se resuelve
    _con_herencia_incompatible(conexion, d.id_outbox)          # NO: lease propio
    _intento_abierto(conexion, d.id_outbox, numero=4, momento=reloj())
    _intento_abierto(conexion, b.id_outbox, momento=reloj())   # ruido de otro evento

    resueltos = sincro.resolver_herencia(
        conexion, politica=politica(max_attempts=3), momento=reloj()
    )

    assert resueltos == 2, "solo A y C"

    for registro in (a, c):
        fila = outbox.leer_evento(conexion, registro.id_outbox)
        assert fila["motivo_revision"] == MotivoRevision.AGOTAMIENTO_HEREDADO.value
        assert fila["reintentable"] == 0
        assert fila["proximo_intento_en"] is None

    for registro in (b, d):
        fila = outbox.leer_evento(conexion, registro.id_outbox)
        assert fila["motivo_revision"] is None
        assert fila["max_intentos_aplicado"] is None

    # Y una segunda pasada no vuelve a contar lo ya resuelto.
    assert sincro.resolver_herencia(
        conexion, politica=politica(max_attempts=3), momento=reloj()
    ) == 0


def test_un_evento_con_herencia_compatible_no_se_resuelve(conexion):
    """El limite es ``>=``: tres heredados con limite tres se cierran; dos no."""
    reloj = RelojFalso()
    justo, holgado = capturar(conexion, 2)
    _con_herencia_incompatible(conexion, justo.id_outbox, intentos=3)
    _con_herencia_incompatible(conexion, holgado.id_outbox, intentos=2)

    assert sincro.resolver_herencia(
        conexion, politica=politica(max_attempts=3), momento=reloj()
    ) == 1
    assert outbox.leer_evento(conexion, justo.id_outbox)["motivo_revision"] == (
        MotivoRevision.AGOTAMIENTO_HEREDADO.value
    )
    assert outbox.leer_evento(conexion, holgado.id_outbox)["motivo_revision"] is None


# ---------------------------------------------------------------------------
# 7 ter. El censo es una sola instantanea
#
# Regresion de un defecto real. El censo se tomaba en **dos** sentencias -- una
# sobre la outbox y otra sobre el historial-- y el docstring afirmaba que un
# cambio entre ambas costaba, como mucho, una iteracion de mas. No era cierto:
#
#   1. la primera consulta ve un evento cuyo unico intento esta abierto, asi que
#      no lo cuenta ni como elegible ni como programado;
#   2. otro proceso cierra ese intento y deja el evento FALLIDO/reintentable con
#      proximo_intento_en en el futuro;
#   3. la segunda consulta ya no encuentra ningun intento abierto;
#   4. el censo combinado devuelve cero de todo, _proximo_despertar responde
#      None y el sincronizador termina dejando un reintento programado atras.
# ---------------------------------------------------------------------------


class ConexionEspia:
    """Proxy que cuenta consultas y puede intercalar una escritura entre ellas.

    ``antes_de`` es el numero de consulta antes de la cual se ejecuta la accion,
    desde **otra conexion**. Con la implementacion de dos consultas, ``antes_de=2``
    reproduce la intercalacion; con la de una sola, esa segunda consulta no
    existe y la accion no llega a dispararse -- que es exactamente la propiedad
    que se quiere afirmar.
    """

    def __init__(self, real, *, antes_de=None, accion=None):
        self._real = real
        self._antes_de = antes_de
        self._accion = accion
        self.llamadas = 0

    def __getattr__(self, nombre):
        return getattr(self._real, nombre)

    def execute(self, *argumentos, **claves):
        self.llamadas += 1
        if self._accion is not None and self.llamadas == self._antes_de:
            self._accion()
        return self._real.execute(*argumentos, **claves)


def _evento_con_intento_abierto(conexion, reloj, p):
    """Un evento cuyo unico intento sigue abierto."""
    (registro,) = capturar(conexion, 1, reloj=reloj)
    reclamacion = reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    return registro, reclamacion


def _cerrar_y_programar(ruta, registro, reclamacion, momento, demora=30.0):
    """Lo que hace el otro proceso: cierra el intento y programa el reintento."""

    def accion():
        with alm.conectar(ruta) as otra:
            with alm.transaccion(otra):
                outbox.finalizar_intento(
                    otra, reclamacion.id_intento,
                    resultado=ResultadoEntrega.REINTENTABLE, codigo_http=503,
                    reproducido=None, error="error del servidor 503",
                    demora=demora, momento=momento,
                )
                outbox.marcar_fallido(
                    otra, registro.id_outbox, reintentable=True, codigo_http=503,
                    error="error del servidor 503", momento=momento,
                    proximo_intento_en=momento + timedelta(seconds=demora),
                )

    return accion


def _es_coherente(censo) -> bool:
    """El censo describe trabajo, o describe una cola sin trabajo. Nunca ambas."""
    return censo.elegibles_ahora > 0 or censo.hay_trabajo_futuro


def test_censar_usa_una_sola_consulta(tmp_path):
    """Una sentencia, una instantanea. Es la forma la que da la garantia."""
    ruta = tmp_path / "nodo_edge.sqlite3"
    reloj = RelojFalso()
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        _evento_con_intento_abierto(conexion, reloj, politica())

        espia = ConexionEspia(conexion)
        outbox.censar(espia, max_attempts=3, momento=reloj())
        assert espia.llamadas == 1


def test_censar_no_deja_una_transaccion_abierta(tmp_path):
    """Es una lectura: sin BEGIN IMMEDIATE y sin nada abierto al volver."""
    ruta = tmp_path / "nodo_edge.sqlite3"
    reloj = RelojFalso()
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        capturar(conexion, 2, reloj=reloj)
        assert conexion.in_transaction is False
        outbox.censar(conexion, max_attempts=3, momento=reloj())
        assert conexion.in_transaction is False


def test_una_escritura_intercalada_no_produce_un_censo_incoherente(tmp_path):
    """La intercalacion exacta del defecto, con dos conexiones SQLite.

    La escritura se dispara justo antes de la segunda consulta del censo. Con
    una sola sentencia esa consulta no existe, la escritura no llega a
    intercalarse y el censo describe integramente el estado anterior: un intento
    abierto con su ``proximo_reconciliable``. Lo que no puede ocurrir --y es lo
    que se afirma-- es que informe de una cola sin trabajo mientras el evento
    real esta programado.
    """
    ruta = tmp_path / "nodo_edge.sqlite3"
    reloj = RelojFalso()
    p = politica()

    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        registro, reclamacion = _evento_con_intento_abierto(conexion, reloj, p)

        espia = ConexionEspia(
            conexion,
            antes_de=2,
            accion=_cerrar_y_programar(ruta, registro, reclamacion, reloj()),
        )
        censo = outbox.censar(espia, max_attempts=p.max_attempts, momento=reloj())

        assert espia.llamadas == 1, "una sola consulta: no hay hueco donde colar nada"
        assert _es_coherente(censo)
        # Estado anterior, integro: el intento sigue abierto y con su vencimiento.
        assert censo.con_intento_abierto == 1
        assert censo.proximo_reconciliable is not None
        assert censo.programados == 0


def test_el_censo_posterior_ve_el_evento_programado(tmp_path):
    """El otro lado de la moneda: censado despues, el estado posterior tambien
    es coherente. Ambos son estados reales; el que no existe es la mezcla."""
    ruta = tmp_path / "nodo_edge.sqlite3"
    reloj = RelojFalso()
    p = politica()

    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        registro, reclamacion = _evento_con_intento_abierto(conexion, reloj, p)
        _cerrar_y_programar(ruta, registro, reclamacion, reloj())()

        censo = outbox.censar(conexion, max_attempts=p.max_attempts, momento=reloj())

        assert _es_coherente(censo)
        assert censo.con_intento_abierto == 0
        assert censo.programados == 1
        assert censo.proximo_reconciliable is None
        assert censo.proximo_programado is not None


def test_el_sincronizador_no_termina_dejando_un_reintento_programado(tmp_path):
    """La consecuencia que el defecto tenia sobre el bucle.

    Con el censo incoherente, ``_proximo_despertar`` respondia ``None`` y la
    ejecucion terminaba con trabajo pendiente. Aqui se comprueba el resultado
    observable: el evento acaba entregado, no abandonado.
    """
    ruta = tmp_path / "nodo_edge.sqlite3"
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=4, base_delay_seconds=30.0, max_delay_seconds=30.0)

    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        registro, reclamacion = _evento_con_intento_abierto(conexion, reloj, p)
        _cerrar_y_programar(ruta, registro, reclamacion, reloj())()

        informe = sincro.sincronizar(
            conexion,
            cliente(Transporte(lambda peticion, n: creada(reproducido=True))),
            politica=p, reloj=reloj, dormir=dormir,
        )

        assert dormir.esperas == [30.0], "espero el reintento programado"
        assert informe.entregados == 1
        assert informe.codigo_de_salida == sincro.CODIGO_EXITO
        assert outbox.leer_evento(conexion, registro.id_outbox)["estado"] == (
            EstadoEntrega.ENVIADO.value
        )


def test_los_dos_proximos_instantes_salen_de_la_misma_instantanea(tmp_path):
    """Programado y reconciliable conviven, y ambos vienen de la misma lectura."""
    ruta = tmp_path / "nodo_edge.sqlite3"
    reloj = RelojFalso()
    p = politica()

    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        # Uno con intento abierto; otro programado al futuro.
        abierto, _ = _evento_con_intento_abierto(conexion, reloj, p)
        (programado,) = capturar(conexion, 1, reloj=reloj)
        with alm.transaccion(conexion):
            outbox.reclamar_intento(
                conexion, programado.id_outbox, max_attempts=p.max_attempts,
                duracion_lease=p.duracion_del_lease, momento=reloj(),
            )
        historial_programado = historial(conexion, programado.id_outbox)[0]
        with alm.transaccion(conexion):
            outbox.finalizar_intento(
                conexion, historial_programado["id_intento"],
                resultado=ResultadoEntrega.REINTENTABLE, codigo_http=503,
                reproducido=None, error="x", demora=45.0, momento=reloj(),
            )
            outbox.marcar_fallido(
                conexion, programado.id_outbox, reintentable=True, codigo_http=503,
                error="x", momento=reloj(),
                proximo_intento_en=reloj() + timedelta(seconds=45),
            )

        espia = ConexionEspia(conexion)
        censo = outbox.censar(espia, max_attempts=p.max_attempts, momento=reloj())

        assert espia.llamadas == 1
        assert censo.con_intento_abierto == 1
        assert censo.programados == 1
        assert censo.proximo_reconciliable is not None
        assert censo.proximo_programado is not None
        assert abierto.id_outbox != programado.id_outbox


# ---------------------------------------------------------------------------
# 8. Coherencia del par ultimo_http / ultimo_error (E3)
# ---------------------------------------------------------------------------


def test_la_reconciliacion_limpia_el_codigo_del_intento_anterior(conexion):
    """Un 503 viejo junto a "proceso interrumpido" seria un par que no ocurrio."""
    reloj = RelojFalso()
    p = politica(max_attempts=5, base_delay_seconds=1.0)
    registro = capturar(conexion)[0]

    # Un primer intento que si respondio 503.
    ejecutar_pasada(
        conexion,
        cliente(Transporte(lambda peticion, n: httpx.Response(503, json={"detail": "x"}))),
        politica=p, reloj=reloj, respetar_programacion=True,
    )
    assert outbox.leer_evento(conexion, registro.id_outbox)["ultimo_http"] == 503

    # Un segundo intento que queda sin resultado y se reconcilia.
    reloj.avanzar(10)
    reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    reloj.avanzar(p.duracion_del_lease + 1)
    sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj())

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["ultimo_http"] is None
    assert fila["ultimo_error"] == outbox.ERROR_SIN_RESULTADO


def test_la_reconciliacion_que_agota_tambien_limpia_el_codigo(conexion):
    reloj = RelojFalso()
    p = politica(max_attempts=2, base_delay_seconds=1.0)
    registro = capturar(conexion)[0]
    ejecutar_pasada(
        conexion,
        cliente(Transporte(lambda peticion, n: httpx.Response(503, json={"detail": "x"}))),
        politica=p, reloj=reloj, respetar_programacion=True,
    )
    reloj.avanzar(10)
    reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    reloj.avanzar(p.duracion_del_lease + 1)
    sincro.reconciliar_abandonados(conexion, politica=p, momento=reloj())

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["ultimo_http"] is None
    assert fila["ultimo_error"] == outbox.ERROR_AGOTADO_SIN_RESULTADO
    assert fila["motivo_revision"] == MotivoRevision.AGOTAMIENTO.value


# ---------------------------------------------------------------------------
# 9. Varios eventos, justicia y marca de agua (E2/D6)
# ---------------------------------------------------------------------------


def test_un_evento_problematico_no_bloquea_a_los_demas(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=3, base_delay_seconds=1.0)
    eventos = capturar(conexion, 3)

    def guion(peticion, n):
        if peticion.headers["Idempotency-Key"] == eventos[0].clave:
            return httpx.Response(409, json={"detail": "colision"})
        return creada(reproducido=False)

    informe = sincro.sincronizar(
        conexion, cliente(Transporte(guion)), politica=p, reloj=reloj, dormir=dormir
    )
    assert informe.entregados == 2
    assert informe.rechazados == 1
    assert informe.codigo_de_salida == sincro.CODIGO_REVISION
    assert outbox.leer_evento(conexion, eventos[1].id_outbox)["estado"] == (
        EstadoEntrega.ENVIADO.value
    )


def test_una_ronda_da_un_intento_a_cada_evento_antes_del_segundo(conexion):
    """Justicia: nadie recibe su segundo intento antes que otro el primero."""
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=2, base_delay_seconds=1.0)
    capturar(conexion, 3)

    orden: list[str] = []

    def guion(peticion, n):
        orden.append(peticion.headers["Idempotency-Key"])
        return httpx.Response(503, json={"detail": "x"})

    sincro.sincronizar(
        conexion, cliente(Transporte(guion)), politica=p, reloj=reloj, dormir=dormir
    )
    assert len(orden) == 6
    assert len(set(orden[:3])) == 3, "la primera ronda toca los tres una vez"
    assert set(orden[:3]) == set(orden[3:])


def test_un_evento_capturado_durante_la_ejecucion_no_la_prolonga(conexion):
    """La marca de agua acota la ejecucion a lo que existia al empezar."""
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=1)
    capturar(conexion, 1)

    nuevos: list = []

    def guion(peticion, n):
        if not nuevos:
            nuevos.extend(capturar(conexion, 1))
        return creada(reproducido=False)

    informe = sincro.sincronizar(
        conexion, cliente(Transporte(guion)), politica=p, reloj=reloj, dormir=dormir
    )
    assert informe.id_maximo == 1
    assert informe.entregados == 1
    assert outbox.leer_evento(conexion, nuevos[0].id_outbox)["estado"] == (
        EstadoEntrega.PENDIENTE.value
    )


def test_el_lote_acota_la_ronda_pero_no_la_ejecucion(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=1, batch_limit=2)
    capturar(conexion, 5)
    informe = sincro.sincronizar(
        conexion,
        cliente(Transporte(lambda peticion, n: creada(reproducido=False))),
        politica=p, reloj=reloj, dormir=dormir,
    )
    assert informe.entregados == 5
    assert informe.rondas >= 3


# ---------------------------------------------------------------------------
# 10. Codigos de salida (C8)
# ---------------------------------------------------------------------------


def test_todo_enviado_da_codigo_cero(conexion):
    reloj = RelojFalso()
    capturar(conexion, 2)
    informe = sincro.sincronizar(
        conexion,
        cliente(Transporte(lambda peticion, n: creada(reproducido=False))),
        politica=politica(), reloj=reloj, dormir=Sleeper(reloj),
    )
    assert informe.codigo_de_salida == sincro.CODIGO_EXITO


def test_un_agotado_da_codigo_dos(conexion):
    reloj = RelojFalso()
    capturar(conexion, 1)
    informe = sincro.sincronizar(
        conexion,
        cliente(Transporte(lambda peticion, n: httpx.Response(500, json={"detail": "x"}))),
        politica=politica(max_attempts=2, base_delay_seconds=1.0),
        reloj=reloj, dormir=Sleeper(reloj),
    )
    assert informe.codigo_de_salida == sincro.CODIGO_REVISION


def test_los_terminales_no_se_confunden_entre_si(conexion):
    """``ENVIADO`` y "requiere revision" son ambos terminales y no son lo mismo."""
    reloj = RelojFalso()
    eventos = capturar(conexion, 2)

    def guion(peticion, n):
        if peticion.headers["Idempotency-Key"] == eventos[0].clave:
            return httpx.Response(422, json={"detail": "invalido"})
        return creada(reproducido=False)

    informe = sincro.sincronizar(
        conexion, cliente(Transporte(guion)), politica=politica(),
        reloj=reloj, dormir=Sleeper(reloj),
    )
    assert informe.censo.enviados == 1
    assert informe.censo.requieren_revision == 1
    assert informe.codigo_de_salida == sincro.CODIGO_REVISION


# ---------------------------------------------------------------------------
# 11. Invariancia de clave y payload
# ---------------------------------------------------------------------------


def test_la_clave_y_el_cuerpo_no_cambian_en_ningun_intento(conexion):
    reloj = RelojFalso()
    dormir = Sleeper(reloj)
    p = politica(max_attempts=4, base_delay_seconds=1.0)
    registro = capturar(conexion)[0]
    original = outbox.leer_payload(conexion, registro.id_captura)

    cuerpos: list[bytes] = []

    def guion(peticion, n):
        cuerpos.append(peticion.content)
        if n < 3:
            return httpx.Response(503, json={"detail": "x"})
        return creada(reproducido=True)

    transporte = Transporte(guion)
    sincro.sincronizar(
        conexion, cliente(transporte), politica=p, reloj=reloj, dormir=dormir
    )

    assert len(set(transporte.peticiones)) == 1
    assert transporte.peticiones[0] == registro.clave
    assert len(set(cuerpos)) == 1
    assert cuerpos[0] == original.encode("utf-8")


def test_los_timestamps_se_guardan_en_utc(conexion):
    reloj = RelojFalso()
    p = politica()
    registro = capturar(conexion)[0]
    reclamar(conexion, registro.id_outbox, p=p, momento=reloj())
    fila = historial(conexion, registro.id_outbox)[0]
    for columna in ("iniciado_en", "reconciliable_en"):
        assert fila[columna].endswith("+00:00")
        assert datetime.fromisoformat(fila[columna]).tzinfo == timezone.utc
