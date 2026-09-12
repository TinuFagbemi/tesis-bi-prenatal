"""Sincronizacion resiliente del nodo edge contra la API y PostgreSQL 16 (SCRUM-65).

Se omite salvo que este definida ``SCRUM65_TEST_DATABASE_URL``. Nunca cae por
omision sobre ``DATABASE_URL`` ni sobre la variable de otra suite: una prueba que
escribe no puede terminar sobre la base de desarrollo de alguien por haberse
olvidado de exportar una variable. El workflow de CI convierte la omision en un
job en rojo.

**Que prueba esto que no prueban las suites offline.** Alli el servidor es un
``MockTransport`` y las respuestas se guionan. Aqui la peticion recorre el camino
completo -- SQLite temporal del edge, cliente HTTP, router de FastAPI, servicio de
idempotencia, PostgreSQL -- y lo que se cuenta al final son filas reales. En
particular: que un evento que fallo, espero, se reintento y se recupero deja
**una** sesion y no dos, y que la misma clave permite recorrer el evento desde su
captura local hasta ``operacional.idempotencia_solicitud`` y sus lecturas.

**El tiempo no se gasta.** El reloj y el sleeper se inyectan igual que en la
suite offline, asi que una espera de ocho segundos no cuesta ocho segundos. Lo
unico real aqui es la base de datos.

**Aislamiento.** Se reutilizan las fixtures de SCRUM-62 sin cambiarlas: cada
prueba abre una transaccion exterior que revierte pase lo que pase, y la
``Session`` que atiende la peticion se une a ella con
``join_transaction_mode="create_savepoint"``, de modo que el ``commit`` del
endpoint es real y observable pero no sobrevive. No se ejecuta DDL, ni
``TRUNCATE``, ni ``create_all``. El archivo SQLite del nodo vive en el
``tmp_path`` de cada prueba y no se comparte con ninguna otra.

Ejecucion desde ``backend/``::

    $env:SCRUM65_TEST_DATABASE_URL = "postgresql+psycopg://<usuario>:<clave>@127.0.0.1:<puerto>/<base>"
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_edge_sincronizacion_postgresql.py -v

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.db.session import get_db
from app.edge import almacenamiento as alm
from app.edge import captura as cap
from app.edge import outbox
from app.edge import sincronizacion as sincro
from app.edge.cliente import CABECERA_REPLAY, ClienteEdge
from app.edge.estados import EstadoEntrega, MotivoRevision
from app.edge.politica import PoliticaDeReintentos
from app.main import app
from app.models.idempotencia import IdempotenciaSolicitud
from tests.test_edge_sincronizacion import RelojFalso, Sleeper

# Fixtures y ayudantes de SCRUM-62, reutilizados tal cual. ``engine_de_pruebas``
# se redefine mas abajo sobre la variable de esta suite, y por eso
# ``conexion_revertida`` -- que lo pide por nombre -- acaba usando el de aqui.
from tests.test_ingestion_api_postgresql import (  # noqa: F401
    conexion_revertida,
    contar_lecturas,
    contar_sesiones,
    lectura_de_signos,
    lecturas_del_embarazo,
    paquete_de_signos,
    referencias,
    sesion_de_pruebas,
    sesiones_del_embarazo,
)

VARIABLE_DE_ENTORNO = "SCRUM65_TEST_DATABASE_URL"
MOTOR_REQUERIDO = "postgresql"

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a una base PostgreSQL ya migrada "
        "a head para ejecutar la sincronizacion resiliente del nodo edge."
    ),
)


# ---------------------------------------------------------------------------
# Conexion: se exige el entorno, no se prepara
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def url_de_pruebas() -> str:
    url = os.environ[VARIABLE_DE_ENTORNO]
    try:
        motor = make_url(url).get_backend_name()
    except ArgumentError:
        pytest.fail(f"{VARIABLE_DE_ENTORNO} no es una URL de conexion valida.")
    if motor != MOTOR_REQUERIDO:
        pytest.fail(
            f"{VARIABLE_DE_ENTORNO} apunta a un motor '{motor}'. Estas pruebas "
            "solo se ejecutan contra PostgreSQL."
        )
    return url


@pytest.fixture(scope="session")
def engine_de_pruebas(url_de_pruebas):
    engine = create_engine(url_de_pruebas)
    if not inspect(engine).has_table(
        IdempotenciaSolicitud.__tablename__,
        schema=IdempotenciaSolicitud.__table__.schema,
    ):
        engine.dispose()
        # Sin URL en el mensaje: lleva credenciales.
        pytest.fail(
            f"La base indicada por {VARIABLE_DE_ENTORNO} no tiene la tabla de "
            "idempotencia. Ejecuta 'alembic upgrade head' desde backend/ antes "
            "de correr estas pruebas."
        )
    yield engine
    engine.dispose()


def test_las_fixtures_heredadas_cuelgan_del_engine_de_esta_suite(
    conexion_revertida, engine_de_pruebas, sesion_de_pruebas
):
    """La atadura que hace que reutilizar fixtures de SCRUM-62 sea seguro.

    ``conexion_revertida`` y ``sesion_de_pruebas`` se definen en el modulo de
    SCRUM-62 pero piden ``engine_de_pruebas`` **por nombre**, y pytest lo
    resuelve desde el modulo que solicita la fixture -- este. Si alguien retirara
    ``engine_de_pruebas`` de aqui, las fixtures heredadas caerian en silencio
    sobre el engine de SCRUM-62 y la suite seguiria en verde mientras ambas
    variables estuvieran definidas. Aqui no.
    """
    assert conexion_revertida.engine is engine_de_pruebas
    assert sesion_de_pruebas.get_bind() is conexion_revertida
    assert VARIABLE_DE_ENTORNO == "SCRUM65_TEST_DATABASE_URL"


# ---------------------------------------------------------------------------
# El nodo, el reloj y los transportes
# ---------------------------------------------------------------------------


@pytest.fixture
def ruta_nodo(tmp_path):
    return tmp_path / "nodo_edge.sqlite3"


@pytest.fixture
def nodo(ruta_nodo):
    with alm.conectar(ruta_nodo) as conexion:
        alm.inicializar(conexion)
        yield conexion


@pytest.fixture
def reloj():
    return RelojFalso()


@pytest.fixture
def dormir(reloj):
    return Sleeper(reloj)


class ApiInestable(TestClient):
    """La aplicacion real, que se cae las primeras ``fallos`` peticiones.

    El fallo se lanza **antes** de entregar la peticion, asi que el servidor no
    llega a verla: es una desconexion, no una respuesta perdida. Las dos ventanas
    son distintas y esta suite las prueba por separado.
    """

    def __init__(self, *argumentos, fallos: int = 1, **claves) -> None:
        super().__init__(*argumentos, **claves)
        self.restantes = fallos
        self.entregadas = 0

    def send(self, request, **claves):
        if self.restantes > 0:
            self.restantes -= 1
            raise httpx.ConnectError("simulado: API inaccesible", request=request)
        self.entregadas += 1
        return super().send(request, **claves)


class ApiQuePierdeLaRespuesta(TestClient):
    """Entrega la peticion de verdad y despues pierde la respuesta.

    Es la ventana critica del ticket: el endpoint procesa, PostgreSQL confirma, y
    el edge no llega a enterarse. ``vistas`` conserva el codigo que el servidor si
    devolvio, para que una prueba pueda afirmar que hubo un 201 real antes del
    timeout en lugar de suponerlo.
    """

    def __init__(self, *argumentos, perdidas: int = 1, **claves) -> None:
        super().__init__(*argumentos, **claves)
        self.restantes = perdidas
        self.vistas: list[tuple[int, str | None]] = []

    def send(self, request, **claves):
        respuesta = super().send(request, **claves)
        respuesta.read()
        self.vistas.append(
            (respuesta.status_code, respuesta.headers.get(CABECERA_REPLAY))
        )
        if self.restantes > 0:
            self.restantes -= 1
            raise httpx.ReadTimeout("respuesta perdida en el camino", request=request)
        return respuesta


class ApiQueRechaza(TestClient):
    """Contesta un 4xx controlado sin tocar la base."""

    def __init__(self, *argumentos, codigo: int = 422, **claves) -> None:
        super().__init__(*argumentos, **claves)
        self.codigo = codigo
        self.peticiones = 0

    def send(self, request, **claves):
        self.peticiones += 1
        return httpx.Response(
            self.codigo, json={"detail": "rechazo simulado"}, request=request
        )


@pytest.fixture
def preparar_api(sesion_de_pruebas):
    """Construye un ``TestClient`` de la clase pedida sobre la sesion revertida."""

    def constructor(clase=TestClient, **claves):
        app.dependency_overrides[get_db] = lambda: sesion_de_pruebas
        return clase(app, **claves)

    yield constructor
    app.dependency_overrides.clear()


def paquete(referencias, *, lecturas=1):
    """Paquete valido para el endpoint y para la captura del edge.

    ``fecha_hora_sincronizacion`` va en ``None``: es lo que describe una captura
    offline, y congelarla es lo que impide que un reenvio se convierta en 409.
    """
    return paquete_de_signos(
        referencias,
        lecturas=[
            lectura_de_signos(referencias, hr_valor=88 + indice)
            for indice in range(lecturas)
        ],
    )


def politica(**cambios) -> PoliticaDeReintentos:
    valores = dict(
        max_attempts=3, base_delay_seconds=1.0, max_delay_seconds=8.0,
        batch_limit=50, http_timeout=10.0,
    )
    valores.update(cambios)
    return PoliticaDeReintentos(**valores)


def fila_de_idempotencia(conexion, clave: str):
    return conexion.execute(
        select(
            IdempotenciaSolicitud.clave,
            IdempotenciaSolicitud.recurso,
            IdempotenciaSolicitud.id_sesion,
            IdempotenciaSolicitud.ids_lectura,
            IdempotenciaSolicitud.fecha_hora,
        ).where(IdempotenciaSolicitud.clave == clave)
    ).all()


# ---------------------------------------------------------------------------
# Escenario 1 -- Desconexion y recuperacion
# ---------------------------------------------------------------------------


def test_escenario_1_una_desconexion_temporal_no_pierde_datos(
    nodo, referencias, conexion_revertida, preparar_api, reloj, dormir
):
    """Falla, espera, se recupera y entrega. Una sesion, sus lecturas, sin duplicar."""
    p = politica(max_attempts=3, base_delay_seconds=2.0)
    registro = cap.capturar(nodo, paquete(referencias, lecturas=3), reloj=reloj)

    api = preparar_api(ApiInestable, fallos=1)
    informe = sincro.sincronizar(
        nodo, ClienteEdge(api), politica=p, reloj=reloj, dormir=dormir
    )

    assert informe.entregados == 1
    assert dormir.esperas == [2.0], "una sola espera, la de delay(1)"
    assert api.entregadas == 1

    fila = outbox.leer_evento(nodo, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.ENVIADO.value
    assert fila["intentos"] == 2

    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 3
    assert len(fila_de_idempotencia(conexion_revertida, registro.clave)) == 1


def test_escenario_1_la_clave_y_el_cuerpo_son_los_mismos_en_los_dos_intentos(
    nodo, referencias, conexion_revertida, preparar_api, reloj, dormir
):
    registro = cap.capturar(nodo, paquete(referencias, lecturas=2), reloj=reloj)
    original = outbox.leer_payload(nodo, registro.id_captura)

    api = preparar_api(ApiInestable, fallos=1)
    sincro.sincronizar(
        nodo, ClienteEdge(api), politica=politica(), reloj=reloj, dormir=dormir
    )

    assert outbox.leer_payload(nodo, registro.id_captura) == original
    assert outbox.leer_evento(nodo, registro.id_outbox)["clave_idempotencia"] == (
        registro.clave
    )


# ---------------------------------------------------------------------------
# Escenario 2 -- Confirmacion perdida
# ---------------------------------------------------------------------------


def test_escenario_2_una_confirmacion_perdida_se_recupera_por_replay(
    nodo, referencias, conexion_revertida, preparar_api, reloj, dormir
):
    """El servidor confirmo, el edge no se entero, y el reenvio no duplica nada."""
    p = politica(max_attempts=3, base_delay_seconds=1.0)
    registro = cap.capturar(nodo, paquete(referencias, lecturas=2), reloj=reloj)

    api = preparar_api(ApiQuePierdeLaRespuesta, perdidas=1)
    informe = sincro.sincronizar(
        nodo, ClienteEdge(api), politica=p, reloj=reloj, dormir=dormir
    )

    # El primer 201 existio de verdad; el segundo fue un replay del mismo.
    assert [codigo for codigo, _ in api.vistas] == [201, 201]
    assert [replay for _, replay in api.vistas] == ["false", "true"]

    assert informe.entregados == 1
    fila = outbox.leer_evento(nodo, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.ENVIADO.value
    assert fila["intentos"] == 2

    # Una sola sesion, sus dos lecturas y una sola reclamacion.
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 2
    assert len(fila_de_idempotencia(conexion_revertida, registro.clave)) == 1


def test_escenario_2_el_replay_devuelve_los_mismos_identificadores(
    nodo, referencias, conexion_revertida, preparar_api, reloj, dormir
):
    registro = cap.capturar(nodo, paquete(referencias, lecturas=2), reloj=reloj)
    api = preparar_api(ApiQuePierdeLaRespuesta, perdidas=1)
    sincro.sincronizar(
        nodo, ClienteEdge(api), politica=politica(), reloj=reloj, dormir=dormir
    )

    fila = outbox.leer_evento(nodo, registro.id_outbox)
    (registrada,) = fila_de_idempotencia(conexion_revertida, registro.clave)
    assert fila["id_sesion_remota"] == registrada.id_sesion
    assert json.loads(fila["ids_lectura_remotos"]) == list(registrada.ids_lectura)


def test_escenario_2_una_respuesta_que_llega_tras_vencer_el_lease_no_duplica(
    nodo, referencias, conexion_revertida, preparar_api, reloj, dormir
):
    """El lease vence, se reconcilia, se reintenta, y sigue habiendo una sesion.

    El vencimiento del lease **autoriza** la recuperacion; no demuestra que la
    peticion original haya terminado. Lo que impide el duplicado no es la ventana
    sino la misma ``Idempotency-Key``.
    """
    p = politica(max_attempts=4, base_delay_seconds=1.0, http_timeout=1.0)
    registro = cap.capturar(nodo, paquete(referencias, lecturas=1), reloj=reloj)

    # Un intento reclamado que nadie cierra: el proceso "murio".
    with alm.transaccion(nodo):
        outbox.reclamar_intento(
            nodo, registro.id_outbox, max_attempts=p.max_attempts,
            duracion_lease=p.duracion_del_lease, momento=reloj(),
        )

    api = preparar_api(TestClient)
    informe = sincro.sincronizar(
        nodo, ClienteEdge(api), politica=p, reloj=reloj, dormir=dormir
    )

    assert informe.intentos_reconciliados == 1
    assert informe.entregados == 1
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert len(fila_de_idempotencia(conexion_revertida, registro.clave)) == 1

    # El intento abandonado sigue sin desenlace inventado.
    intentos = nodo.execute(
        "SELECT * FROM intento_sincronizacion WHERE id_outbox = ? ORDER BY numero",
        (registro.id_outbox,),
    ).fetchall()
    assert intentos[0]["resultado"] is None
    assert intentos[0]["reconciliado_en"] is not None
    assert intentos[1]["resultado"] == "ENTREGADO"
    assert intentos[1]["confirmo_transicion"] == 1


# ---------------------------------------------------------------------------
# Escenario 3 -- Agotamiento
# ---------------------------------------------------------------------------


def test_escenario_3_el_agotamiento_es_exacto_y_no_deja_nada_remoto(
    nodo, referencias, conexion_revertida, preparar_api, reloj, dormir
):
    p = politica(max_attempts=3, base_delay_seconds=2.0, max_delay_seconds=8.0)
    registro = cap.capturar(nodo, paquete(referencias, lecturas=2), reloj=reloj)

    api = preparar_api(ApiInestable, fallos=99)
    informe = sincro.sincronizar(
        nodo, ClienteEdge(api), politica=p, reloj=reloj, dormir=dormir
    )

    assert informe.agotados == 1
    assert informe.codigo_de_salida == sincro.CODIGO_REVISION
    assert dormir.esperas == [2.0, 4.0], "dos esperas para tres intentos"

    fila = outbox.leer_evento(nodo, registro.id_outbox)
    assert fila["intentos"] == 3
    assert fila["reintentable"] == 0
    assert fila["motivo_revision"] == MotivoRevision.AGOTAMIENTO.value
    assert fila["proximo_intento_en"] is None

    # Ninguna peticion llego: cero datos remotos.
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0
    assert fila_de_idempotencia(conexion_revertida, registro.clave) == []

    # Una ejecucion ordinaria posterior no crea un cuarto intento.
    informe = sincro.sincronizar(
        nodo, ClienteEdge(api), politica=p, reloj=reloj, dormir=dormir
    )
    assert outbox.leer_evento(nodo, registro.id_outbox)["intentos"] == 3
    assert informe.rondas == 0


# ---------------------------------------------------------------------------
# Escenario 4 -- Error permanente
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("codigo", [400, 409, 422])
def test_escenario_4_un_error_permanente_gasta_un_intento_y_ninguna_espera(
    nodo, referencias, conexion_revertida, preparar_api, reloj, dormir, codigo
):
    p = politica(max_attempts=5)
    registro = cap.capturar(nodo, paquete(referencias, lecturas=1), reloj=reloj)

    api = preparar_api(ApiQueRechaza, codigo=codigo)
    informe = sincro.sincronizar(
        nodo, ClienteEdge(api), politica=p, reloj=reloj, dormir=dormir
    )

    assert api.peticiones == 1
    assert dormir.esperas == []
    assert informe.rechazados == 1
    assert informe.codigo_de_salida == sincro.CODIGO_REVISION

    fila = outbox.leer_evento(nodo, registro.id_outbox)
    assert fila["intentos"] == 1
    assert fila["motivo_revision"] == MotivoRevision.RECHAZO_PERMANENTE.value
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


# ---------------------------------------------------------------------------
# Escenario 5 -- Trazabilidad extremo a extremo
# ---------------------------------------------------------------------------


def test_escenario_5_la_misma_clave_recorre_sqlite_http_y_postgresql(
    nodo, referencias, conexion_revertida, preparar_api, reloj, dormir
):
    """El recorrido completo, con un fallo recuperable en medio.

    ``clave local -> fila de idempotencia -> id_sesion -> lecturas``, y los cuatro
    momentos temporales con la semantica documentada. No se crea un segundo
    identificador: la ``Idempotency-Key`` cumple los dos papeles.
    """
    p = politica(max_attempts=4, base_delay_seconds=1.0)
    registro = cap.capturar(nodo, paquete(referencias, lecturas=3), reloj=reloj)
    capturado_en = reloj()

    api = preparar_api(ApiInestable, fallos=1)
    sincro.sincronizar(
        nodo, ClienteEdge(api), politica=p, reloj=reloj, dormir=dormir
    )

    traza = outbox.leer_traza(nodo, clave=registro.clave)

    # 1. La correlacion es la clave, y es la misma en los tres sitios.
    assert traza.correlation_id == registro.clave
    (remota,) = fila_de_idempotencia(conexion_revertida, registro.clave)
    assert remota.clave == traza.correlation_id
    assert remota.recurso == "POST /api/v1/sesiones-monitoreo"

    # 2. Los identificadores remotos coinciden con los que guardo el edge.
    assert traza.id_sesion_remota == remota.id_sesion
    assert json.loads(traza.ids_lectura_remotos) == list(remota.ids_lectura)
    assert len(remota.ids_lectura) == 3

    # 3. Una sesion y tres lecturas, y ninguna duplicada.
    sesiones = sesiones_del_embarazo(conexion_revertida, referencias.id_embarazo)
    assert len(sesiones) == 1
    assert sesiones[0].id_sesion == remota.id_sesion
    assert len(lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)) == 3

    # 4. Los cuatro momentos, cada uno de su fuente.
    assert traza.capturado_en == capturado_en.isoformat()
    assert [i.numero for i in traza.intentos_registrados] == [1, 2]
    assert traza.intentos_registrados[0].resultado == "REINTENTABLE"
    assert traza.intentos_registrados[0].demora_programada_s == 1.0
    confirmador = traza.intentos_registrados[1]
    assert confirmador.confirmo_transicion == 1
    assert traza.confirmado_en == confirmador.finalizado_en
    assert traza.sincronizado_en == traza.enviado_en
    assert traza.confirmado_en <= traza.sincronizado_en

    # 5. PostgreSQL conserva su propia evidencia temporal, de su propio reloj.
    #    No se finge que sea el mismo instante que el del edge.
    assert remota.fecha_hora is not None


def test_escenario_5_la_traza_no_expone_el_paquete_ni_secretos(
    nodo, referencias, conexion_revertida, preparar_api, reloj, dormir
):
    registro = cap.capturar(nodo, paquete(referencias, lecturas=2), reloj=reloj)
    api = preparar_api(ApiInestable, fallos=1)
    sincro.sincronizar(
        nodo, ClienteEdge(api), politica=politica(), reloj=reloj, dormir=dormir
    )

    traza = outbox.leer_traza(nodo, clave=registro.clave)
    plano = repr(traza) + " ".join(repr(i) for i in traza.intentos_registrados)
    assert outbox.leer_payload(nodo, registro.id_captura) not in plano
    for prohibido in ("hr_valor", "spo2_valor", "postgresql", "http://", "SELECT"):
        assert prohibido not in plano


# ---------------------------------------------------------------------------
# Escenario 6 -- Reinicio entre intentos
# ---------------------------------------------------------------------------


def test_escenario_6_un_reinicio_entre_intentos_no_reinicia_nada(
    ruta_nodo, referencias, conexion_revertida, preparar_api, reloj, dormir
):
    """Se cierra el proceso durante el backoff y se abre otro sobre el mismo archivo."""
    p = politica(max_attempts=4, base_delay_seconds=30.0, max_delay_seconds=30.0)

    with alm.conectar(ruta_nodo) as nodo:
        alm.inicializar(nodo)
        registro = cap.capturar(nodo, paquete(referencias, lecturas=2), reloj=reloj)
        api = preparar_api(ApiInestable, fallos=99)
        # Una sola ronda: falla, programa el reintento y se corta la ejecucion.
        from app.edge.emisor import ejecutar_pasada

        ejecutar_pasada(
            nodo, ClienteEdge(api), politica=p, reloj=reloj,
            respetar_programacion=True,
        )
        antes = dict(outbox.leer_evento(nodo, registro.id_outbox))

    assert antes["intentos"] == 1
    assert antes["proximo_intento_en"] is not None

    with alm.conectar(ruta_nodo) as nodo:
        alm.inicializar(nodo)
        despues = dict(outbox.leer_evento(nodo, registro.id_outbox))
        assert despues["intentos"] == 1, "el contador no se reinicia"
        assert despues["proximo_intento_en"] == antes["proximo_intento_en"]
        assert despues["clave_idempotencia"] == registro.clave
        assert despues["max_intentos_aplicado"] == 4

        # La API vuelve y la sincronizacion completa el trabajo sin duplicar.
        api = preparar_api(TestClient)
        informe = sincro.sincronizar(
            nodo, ClienteEdge(api), politica=p, reloj=reloj, dormir=dormir
        )
        assert informe.entregados == 1
        assert outbox.leer_evento(nodo, registro.id_outbox)["intentos"] == 2

    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 2
    assert len(fila_de_idempotencia(conexion_revertida, registro.clave)) == 1


# ---------------------------------------------------------------------------
# Escenario 7 -- Varios eventos
# ---------------------------------------------------------------------------


def test_escenario_7_un_evento_permanente_no_bloquea_a_los_que_se_recuperan(
    nodo, referencias, conexion_revertida, preparar_api, reloj, dormir
):
    p = politica(max_attempts=3, base_delay_seconds=1.0)
    rechazado = cap.capturar(nodo, paquete(referencias, lecturas=1), reloj=reloj)
    bueno_uno = cap.capturar(nodo, paquete(referencias, lecturas=2), reloj=reloj)
    bueno_dos = cap.capturar(nodo, paquete(referencias, lecturas=1), reloj=reloj)

    class ApiSelectiva(TestClient):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.peticiones: list[str] = []

        def send(self, request, **claves):
            clave = request.headers["Idempotency-Key"]
            self.peticiones.append(clave)
            if clave == rechazado.clave:
                return httpx.Response(
                    409, json={"detail": "colision simulada"}, request=request
                )
            return super().send(request, **claves)

    api = preparar_api(ApiSelectiva)
    informe = sincro.sincronizar(
        nodo, ClienteEdge(api), politica=p, reloj=reloj, dormir=dormir
    )

    assert informe.entregados == 2
    assert informe.rechazados == 1
    assert informe.codigo_de_salida == sincro.CODIGO_REVISION

    # El rechazado se intento una sola vez y no arrastro a los demas.
    assert api.peticiones.count(rechazado.clave) == 1
    for registro in (bueno_uno, bueno_dos):
        assert outbox.leer_evento(nodo, registro.id_outbox)["estado"] == (
            EstadoEntrega.ENVIADO.value
        )
        assert len(fila_de_idempotencia(conexion_revertida, registro.clave)) == 1
    assert fila_de_idempotencia(conexion_revertida, rechazado.clave) == []

    # Dos sesiones, tres lecturas: el paquete rechazado no dejo nada.
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 2
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 3

    # Y sus trazas son independientes.
    assert outbox.leer_traza(nodo, clave=rechazado.clave).requiere_revision is True
    assert outbox.leer_traza(nodo, clave=bueno_uno.clave).requiere_revision is False


def test_escenario_7_una_desconexion_pausa_la_cola_y_no_la_castiga(
    nodo, referencias, conexion_revertida, preparar_api, reloj, dormir
):
    """Con la API caida se hace **una** peticion, no una por evento."""
    p = politica(max_attempts=2, base_delay_seconds=1.0)
    registros = [
        cap.capturar(nodo, paquete(referencias, lecturas=1), reloj=reloj)
        for _ in range(4)
    ]

    api = preparar_api(ApiInestable, fallos=1)
    informe = sincro.sincronizar(
        nodo, ClienteEdge(api), politica=p, reloj=reloj, dormir=dormir
    )

    # Una sola desconexion consumio un intento de un solo evento.
    consumidos = [
        outbox.leer_evento(nodo, registro.id_outbox)["intentos"] for registro in registros
    ]
    assert consumidos.count(2) == 1, "solo el que fallo gasto dos intentos"
    assert consumidos.count(1) == 3
    assert informe.entregados == 4
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 4


# ---------------------------------------------------------------------------
# La base queda como estaba
# ---------------------------------------------------------------------------


def test_la_base_no_conserva_nada_de_esta_suite(engine_de_pruebas, referencias):
    """Toda escritura vivio dentro de una transaccion que siempre revierte."""
    with engine_de_pruebas.connect() as conexion:
        sesiones = conexion.execute(
            select(func.count()).select_from(
                __import__("app.models.monitoreo", fromlist=["SesionMonitoreo"]).SesionMonitoreo
            )
        ).scalar_one()
        idempotencia = conexion.execute(
            select(func.count()).select_from(IdempotenciaSolicitud)
        ).scalar_one()
    assert sesiones == 0
    assert idempotencia == 0
