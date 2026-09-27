"""El productor del dispositivo y la cadena completa hasta ``ENVIADO`` (SCRUM-72).

``scripts/dispositivo_gestante.py`` representa, en este MVP sin hardware, el
evento de captura que originaria el dispositivo fisico. Estas pruebas ejecutan
su ``main`` real y siguen ESA captura por la misma cola que entrega el envio
automatico del portal:

    dispositivo_gestante.py capturar
      -> movimientos.registrar_sesion_simulada -> app.edge.capturar
      -> cuenta-<id_usuario>.sqlite3 (PENDIENTE)
      -> envio_automatico.ciclo -> app.edge.ejecutar_pasada
      -> POST /api/v1/sesiones-monitoreo (doble) -> ENVIADO

La API central es un ``httpx.MockTransport`` que puede caerse, volver y
rechazar un token vencido. No hace falta PostgreSQL ni red.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.edge import PoliticaDeReintentos, conectar, outbox, transaccion
from app.edge.cliente import RUTA_IDENTIDAD
from app.gestante import envio_automatico, movimientos
from app.gestante.central import EstadoRespuesta, RespuestaToken
from app.gestante.rutas import AlmacenDeTokens
from app.gestante.simulacion import MOV_SIMULADO
from tests.test_gestante_movimientos import (
    AHORA,
    ENTREGA_REPRODUCIDA,
    ID_DISPOSITIVO,
    ID_EMBARAZO,
    ApiFalsa,
    central_que_reconoce_el_embarazo,
    escribir_provision,
)
from tests.test_gestante_rutas import (
    ID_USUARIO_DE_PRUEBA,
    construir_cliente,
    construir_settings,
    iniciar_sesion,
)
from tests.test_gestante_ultimos_registros import reautenticar

RUTA_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dispositivo_gestante.py"


@pytest.fixture(scope="module")
def dispositivo():
    """El script real, cargado como modulo."""
    spec = importlib.util.spec_from_file_location("dispositivo_gestante", RUTA_SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _settings(tmp_path):
    escribir_provision(tmp_path)
    return construir_settings(tmp_path)


def _capturar(dispositivo, settings, *argumentos):
    return dispositivo.main(["capturar", *argumentos], settings=settings, reloj=lambda: AHORA)


def _outbox(settings, id_usuario=ID_USUARIO_DE_PRUEBA):
    ruta = movimientos.ruta_para_la_cuenta(settings, id_usuario)
    with closing(sqlite3.connect(ruta)) as conexion:
        conexion.row_factory = sqlite3.Row
        return [dict(fila) for fila in conexion.execute(
            "SELECT o.id_outbox, o.clave_idempotencia, o.estado, o.intentos, c.payload_json"
            " FROM outbox o JOIN captura_local c ON c.id_captura = o.id_captura"
        )]


def _portal(tmp_path, api):
    """El portal sobre los mismos archivos, con la paciente ya autenticada."""
    cliente, central, reloj, _ = construir_cliente(
        tmp_path,
        central=central_que_reconoce_el_embarazo(),
        provision_path=tmp_path / "provision.json",
        constructor_cliente_edge=api.constructor,
    )
    iniciar_sesion(cliente)
    return cliente, central


def _ciclo(cliente):
    return envio_automatico.ciclo(cliente.app.state.contexto)


def _sin_red(monkeypatch):
    """Cualquier intento de abrir un cliente HTTP hace fallar la prueba."""
    def prohibido(*args, **kwargs):
        raise AssertionError("capturar no debe usar la red")

    monkeypatch.setattr(httpx, "Client", prohibido)


# ---------------------------------------------------------------------------
# A. Captura sin Internet ni credencial
# ---------------------------------------------------------------------------


def test_capturar_deja_una_sola_captura_pendiente_en_la_cola_de_la_cuenta(
    tmp_path, dispositivo, monkeypatch, capsys
):
    settings = _settings(tmp_path)
    _sin_red(monkeypatch)

    assert _capturar(dispositivo, settings) == dispositivo.CODIGO_DE_EXITO

    esperada = settings.movimientos_dir / f"cuenta-{ID_USUARIO_DE_PRUEBA}.sqlite3"
    assert movimientos.ruta_para_la_cuenta(settings, ID_USUARIO_DE_PRUEBA) == esperada
    assert list(settings.movimientos_dir.iterdir()) == [esperada]
    filas = _outbox(settings)
    assert len(filas) == 1
    assert filas[0]["estado"] == "PENDIENTE"
    assert filas[0]["intentos"] == 0
    # El paquete es el contrato real: movimientos con MOV_SIMULADO, FC y SpO2
    # nulos, y las referencias del aprovisionamiento.
    paquete = json.loads(filas[0]["payload_json"])
    assert MOV_SIMULADO == 12
    assert paquete["id_embarazo"] == ID_EMBARAZO
    assert paquete["id_dispositivo"] == ID_DISPOSITIVO
    assert paquete["tipo_sesion"] == "MOVIMIENTOS_FETALES"
    [lectura] = paquete["lecturas"]
    assert lectura["mov_valor"] == MOV_SIMULADO
    assert lectura["hr_valor"] is None and lectura["spo2_valor"] is None
    # Nada del paquete sale por pantalla.
    salida = capsys.readouterr().out
    assert "PENDIENTE" in salida and "mov_valor" not in salida


def test_capturar_no_necesita_sesion_ni_token(tmp_path, dispositivo):
    """Ni siquiera existe el almacen de sesiones: capturar no lo consulta."""
    settings = _settings(tmp_path)

    assert _capturar(dispositivo, settings) == dispositivo.CODIGO_DE_EXITO
    assert not settings.sqlite_path.exists()
    assert movimientos.leer_estado_de_la_cuenta(settings, ID_USUARIO_DE_PRUEBA).pendientes == 1


def test_cada_ejecucion_es_una_captura_distinta(tmp_path, dispositivo):
    settings = _settings(tmp_path)

    _capturar(dispositivo, settings)
    _capturar(dispositivo, settings)

    claves = {fila["clave_idempotencia"] for fila in _outbox(settings)}
    assert len(claves) == 2


def test_sin_aprovisionamiento_no_se_captura_nada(tmp_path, dispositivo, capsys):
    settings = construir_settings(tmp_path)

    assert _capturar(dispositivo, settings) == dispositivo.CODIGO_DE_ERROR
    assert not settings.movimientos_dir.exists()
    assert "Error" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# E. La cuenta sale del aprovisionamiento, no del operador
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argumento", ["--id-usuario", "--id-embarazo", "--id-dispositivo", "--cuenta", "--token"]
)
def test_el_productor_no_acepta_identificadores_ni_credenciales(tmp_path, dispositivo, argumento):
    settings = _settings(tmp_path)

    with pytest.raises(SystemExit) as salida:
        _capturar(dispositivo, settings, argumento, "999")

    assert salida.value.code == 2
    assert not settings.movimientos_dir.exists()


def test_la_cola_es_la_de_la_cuenta_aprovisionada(tmp_path, dispositivo):
    otra = ID_USUARIO_DE_PRUEBA + 7
    escribir_provision(tmp_path, id_usuario=otra)
    settings = construir_settings(tmp_path)

    _capturar(dispositivo, settings)

    assert [p.name for p in settings.movimientos_dir.iterdir()] == [f"cuenta-{otra}.sqlite3"]


def test_una_cuenta_no_envia_la_cola_de_otra(tmp_path, dispositivo):
    settings = _settings(tmp_path)
    _capturar(dispositivo, settings)
    api = ApiFalsa()
    cliente, central, _, _ = construir_cliente(
        tmp_path,
        central=central_que_reconoce_el_embarazo(),
        provision_path=tmp_path / "provision.json",
        constructor_cliente_edge=api.constructor,
    )
    central.id_usuario = ID_USUARIO_DE_PRUEBA + 1
    iniciar_sesion(cliente)

    hechos = _ciclo(cliente)

    assert ID_USUARIO_DE_PRUEBA not in hechos
    assert api.envios == [] and api.consultas_identidad == 0
    assert _outbox(settings)[0]["estado"] == "PENDIENTE"


# ---------------------------------------------------------------------------
# B. Caida corta: vuelve la conexion con el JWT vigente
# ---------------------------------------------------------------------------


def test_la_misma_captura_sale_sola_al_volver_la_conexion(tmp_path, dispositivo):
    settings = _settings(tmp_path)
    _capturar(dispositivo, settings)
    [pendiente] = _outbox(settings)

    api = ApiFalsa()
    api.caida = True
    cliente, _ = _portal(tmp_path, api)
    for _ in range(5):
        _ciclo(cliente)
    assert _outbox(settings)[0]["estado"] == "PENDIENTE"
    assert _outbox(settings)[0]["intentos"] == 0, "sin red no se gastan intentos"

    api.caida = False
    envio = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]

    assert envio.pasada.entregados == 1
    [enviado] = _outbox(settings)
    assert enviado["estado"] == "ENVIADO"
    assert enviado["id_outbox"] == pendiente["id_outbox"], "es la misma captura"
    # La misma clave y exactamente los mismos bytes guardados al capturar.
    assert api.envios == [(pendiente["clave_idempotencia"], pendiente["payload_json"].encode())]
    # Y no se vuelve a enviar.
    _ciclo(cliente)
    assert len(api.envios) == 1


# ---------------------------------------------------------------------------
# C. Caida larga: el JWT vence mientras no hay red
# ---------------------------------------------------------------------------


def test_con_el_jwt_vencido_la_captura_espera_intacta_a_la_reautenticacion(tmp_path, dispositivo):
    settings = _settings(tmp_path)
    _capturar(dispositivo, settings)
    [pendiente] = _outbox(settings)

    api = ApiFalsa()
    api.caida = True
    cliente, _ = _portal(tmp_path, api)
    _ciclo(cliente)

    # Vuelve Internet, pero el token ya vencio: /yo responde 401.
    api.caida = False
    api.identidad = (401, {"detail": "token vencido"})
    envio = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]

    assert envio.credencial_rechazada is True
    assert _outbox(settings) == [pendiente], "ni se pierde, ni cambia, ni gasta intentos"
    assert api.envios == []
    # Sin token en memoria, los ciclos siguientes no hacen nada.
    assert _ciclo(cliente) == {}

    # La paciente vuelve a iniciar sesion y el envio continua solo.
    api.identidad = (200, {"id_usuario": ID_USUARIO_DE_PRUEBA, "rol": "PACIENTE"})
    assert reautenticar(cliente).status_code == 200
    envio = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]

    assert envio.pasada.entregados == 1
    assert _outbox(settings)[0]["estado"] == "ENVIADO"
    assert api.envios == [(pendiente["clave_idempotencia"], pendiente["payload_json"].encode())]


# ---------------------------------------------------------------------------
# D. Reinicio del portal
# ---------------------------------------------------------------------------


def test_tras_reiniciar_el_portal_el_pendiente_sigue_y_sale_al_volver_a_entrar(tmp_path, dispositivo):
    settings = _settings(tmp_path)
    _capturar(dispositivo, settings)
    [pendiente] = _outbox(settings)
    api = ApiFalsa()

    # Un portal nuevo sobre los mismos archivos: ningun token en memoria.
    cliente, _, _, _ = construir_cliente(
        tmp_path,
        central=central_que_reconoce_el_embarazo(),
        provision_path=tmp_path / "provision.json",
        constructor_cliente_edge=api.constructor,
    )
    assert cliente.app.state.contexto.tokens.activos() == []
    assert _ciclo(cliente) == {}
    assert _outbox(settings) == [pendiente]

    # El token no quedo en ningun archivo.
    iniciar_sesion(cliente)
    token = cliente.app.state.contexto.tokens.activos()[0][1]
    for archivo in tmp_path.rglob("*"):
        if archivo.is_file():
            assert token.encode() not in archivo.read_bytes(), archivo

    envio = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]

    assert envio.pasada.entregados == 1
    assert _outbox(settings)[0]["estado"] == "ENVIADO"
    assert api.envios[0][0] == pendiente["clave_idempotencia"]


# ---------------------------------------------------------------------------
# Caida en mitad de un envio: intento reclamado y nunca cerrado
# ---------------------------------------------------------------------------


def _intentos_registrados(settings):
    ruta = movimientos.ruta_para_la_cuenta(settings, ID_USUARIO_DE_PRUEBA)
    with closing(sqlite3.connect(ruta)) as conexion:
        conexion.row_factory = sqlite3.Row
        return [dict(fila) for fila in conexion.execute(
            "SELECT numero, resultado, finalizado_en, reconciliado_en"
            " FROM intento_sincronizacion ORDER BY numero"
        )]


def test_un_intento_abandonado_por_un_reinicio_se_reconcilia_y_se_envia(tmp_path, dispositivo):
    """El portal reclamo el intento (contador +1, intento abierto con lease) y
    se apago antes de registrar el resultado. Tras el reinicio, el envio
    automatico repara el intento con la reconciliacion de SCRUM-65 cuando vence
    el lease, y el reintento sale con la misma clave y los mismos bytes."""
    settings = _settings(tmp_path)
    _capturar(dispositivo, settings)
    [pendiente] = _outbox(settings)

    # Lo que deja ejecutar_pasada si el proceso muere tras reclamar y antes de
    # registrar el resultado: exactamente su paso 1, con la politica del portal.
    politica = PoliticaDeReintentos(http_timeout=settings.http_timeout)
    ruta = movimientos.ruta_para_la_cuenta(settings, ID_USUARIO_DE_PRUEBA)
    with conectar(ruta) as conexion, transaccion(conexion):
        reclamado = outbox.reclamar_intento(
            conexion,
            pendiente["id_outbox"],
            max_attempts=politica.max_attempts,
            duracion_lease=politica.duracion_del_lease,
            momento=AHORA,
        )
    assert reclamado is not None and reclamado.numero == 1

    # Reinicio: un portal nuevo sobre los mismos archivos; la paciente entra.
    # El servidor si habia recibido aquel primer envio: el reintento es un replay.
    api = ApiFalsa(ENTREGA_REPRODUCIDA)
    cliente, _ = _portal(tmp_path, api)
    reloj = cliente.app.state.contexto.reloj

    # Dentro del lease el intento sigue «en curso»: nada que enviar, nada tocado.
    envio = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]
    assert envio.sin_pendientes is True and envio.intentos_reconciliados == 0
    assert api.envios == [] and api.consultas_identidad == 0

    # Vence el lease: el ciclo lo reconcilia, sin red ni credencial, y el
    # evento vuelve al reintento con la espera ordinaria de SCRUM-65.
    reloj.avanzar(seconds=politica.duracion_del_lease + 1)
    envio = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]
    assert envio.intentos_reconciliados == 1
    assert api.envios == [] and api.consultas_identidad == 0
    [abandonado] = _intentos_registrados(settings)
    assert abandonado["reconciliado_en"] is not None
    assert abandonado["resultado"] is None, "no se inventa lo que hizo el servidor"
    fila = _outbox(settings)[0]
    assert fila["estado"] == "FALLIDO" and fila["intentos"] == 1

    # Cumplida la espera, el siguiente ciclo reintenta y termina ENVIADO.
    reloj.avanzar(seconds=politica.demora(1) + 1)
    envio = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]

    assert envio.pasada.entregados == 1
    [final] = _outbox(settings)
    assert final["estado"] == "ENVIADO"
    assert final["intentos"] == 2
    assert final["clave_idempotencia"] == pendiente["clave_idempotencia"]
    assert final["payload_json"] == pendiente["payload_json"]
    # Un solo envio desde el portal reiniciado, con la clave y los bytes
    # originales; el servidor lo reconocio: una sola sesion remota.
    assert api.envios == [(pendiente["clave_idempotencia"], pendiente["payload_json"].encode())]
    ruta_final = movimientos.ruta_para_la_cuenta(settings, ID_USUARIO_DE_PRUEBA)
    with closing(sqlite3.connect(ruta_final)) as conexion:
        assert conexion.execute("SELECT id_sesion_remota FROM outbox").fetchall() == [(555,)]
    assert [i["resultado"] for i in _intentos_registrados(settings)] == [None, "ENTREGADO"]
    # Y ya no queda nada bloqueado ni pendiente.
    assert _ciclo(cliente)[ID_USUARIO_DE_PRUEBA].sin_pendientes is True
    assert len(api.envios) == 1


# ---------------------------------------------------------------------------
# Tokens compartidos entre el hilo del envio y las peticiones del portal
# ---------------------------------------------------------------------------

TOKEN_B = "token-b-de-la-reautenticacion"


def test_el_rechazo_tardio_del_token_a_no_borra_el_token_b(tmp_path, dispositivo):
    """El envio automatico trabaja con A; mientras la API lo verifica, la
    paciente se reautentica y queda B; despues llega el 401 de A. La limpieza
    de A no puede llevarse B. El intercalado se fuerza con dos Event, sin
    esperas temporizadas."""
    settings = _settings(tmp_path)
    _capturar(dispositivo, settings)
    verificando_a = threading.Event()
    reautenticada = threading.Event()
    envios = []

    def manejador(peticion: httpx.Request) -> httpx.Response:
        token = peticion.headers["authorization"].removeprefix("Bearer ")
        if peticion.url.path == RUTA_IDENTIDAD:
            if token != TOKEN_B:
                verificando_a.set()
                assert reautenticada.wait(timeout=10), "la prueba no avanzo"
                return httpx.Response(401, json={"detail": "token vencido"})
            return httpx.Response(200, json={"id_usuario": ID_USUARIO_DE_PRUEBA, "rol": "PACIENTE"})
        envios.append(token)
        return httpx.Response(201, json={"id_sesion": 555, "lecturas_creadas": 1, "ids_lectura": [999]},
                              headers={"Idempotency-Replayed": "false"})

    def constructor(settings, token):
        return httpx.Client(
            base_url=settings.api_base_url,
            transport=httpx.MockTransport(manejador),
            headers={"Authorization": f"Bearer {token}"},
        )

    cliente, central, _, _ = construir_cliente(
        tmp_path,
        central=central_que_reconoce_el_embarazo(),
        provision_path=tmp_path / "provision.json",
        constructor_cliente_edge=constructor,
    )
    iniciar_sesion(cliente)
    tokens = cliente.app.state.contexto.tokens
    [(hash_sesion, token_a)] = tokens.activos()
    assert token_a != TOKEN_B

    resultado = {}
    hilo = threading.Thread(target=lambda: resultado.update(_ciclo(cliente)))
    hilo.start()
    assert verificando_a.wait(timeout=10), "el envio no llego a verificar A"

    # La reautenticacion guarda B mientras el hilo sigue esperando el 401 de A.
    central.autenticar = lambda **_: RespuestaToken(EstadoRespuesta.OK, token=TOKEN_B)
    assert reautenticar(cliente).status_code == 200
    assert tokens.activos() == [(hash_sesion, TOKEN_B)]

    reautenticada.set()
    hilo.join(timeout=10)
    assert not hilo.is_alive()

    assert resultado[ID_USUARIO_DE_PRUEBA].credencial_rechazada is True
    assert tokens.activos() == [(hash_sesion, TOKEN_B)], "el rechazo de A no se lleva B"
    assert _outbox(settings)[0]["intentos"] == 0
    # Y con B el envio sigue solo.
    assert _ciclo(cliente)[ID_USUARIO_DE_PRUEBA].pasada.entregados == 1
    assert envios == [TOKEN_B]


def test_olvidar_por_digest_solo_borra_el_token_exacto():
    tokens = AlmacenDeTokens()
    tokens.guardar("sesion", "A")
    [(hash_sesion, _)] = tokens.activos()

    tokens.guardar("sesion", "B")
    assert tokens.olvidar_por_digest(hash_sesion, "A") is False
    assert tokens.activos() == [(hash_sesion, "B")]
    assert tokens.olvidar_por_digest(hash_sesion, "B") is True
    assert tokens.activos() == []


def test_comparar_y_borrar_es_atomico_aunque_b_llegue_entre_ambos(tmp_path):
    """La ventana exacta de la carrera: ``olvidar_por_digest`` ya comparo y vio
    A, y justo entonces otra peticion guarda B. El diccionario interno se
    sustituye por uno que, al leerse, lanza esa escritura en otro hilo. Con el
    cerrojo, la escritura no puede entrar hasta que termina la limpieza de A,
    y B sobrevive; sin cerrojo, el borrado posterior se llevaria B."""
    tokens = AlmacenDeTokens()
    tokens.guardar("sesion", "A")
    [(hash_sesion, _)] = tokens.activos()
    otros = []

    class IntercalaB(dict):
        intercalar = True

        def get(self, clave, defecto=None):
            valor = super().get(clave, defecto)
            if self.intercalar:
                self.intercalar = False
                hilo = threading.Thread(target=tokens.guardar, args=("sesion", "B"))
                hilo.start()
                # Con el cerrojo tomado, guardar(B) no puede completarse aqui;
                # la espera acotada solo deja tiempo a que lo intente.
                hilo.join(timeout=0.5)
                otros.append(hilo)
            return valor

    tokens._por_sesion = IntercalaB(tokens._por_sesion)

    tokens.olvidar_por_digest(hash_sesion, "A")
    otros[0].join(timeout=10)

    assert not otros[0].is_alive()
    assert tokens.activos() == [(hash_sesion, "B")]


def test_el_estado_de_conexion_publica_el_ultimo_envio_confirmado_de_la_cuenta(tmp_path, dispositivo):
    """La señal ligera que usa la interfaz: solo cambia cuando el servidor
    confirma una entrega de ESTA cuenta, y se lee aunque no haya red."""
    settings = _settings(tmp_path)
    api = ApiFalsa()
    api.caida = True
    cliente, central = _portal(tmp_path, api)
    estado = lambda: cliente.get("/adaptador/estado-conexion").json()["ultimo_envio_confirmado"]

    assert estado() is None
    _capturar(dispositivo, settings)
    _ciclo(cliente)
    assert estado() is None, "pendiente no es enviado"

    api.caida = False
    _ciclo(cliente)
    enviado = estado()
    assert enviado is not None
    central.disponible_ = False
    assert estado() == enviado, "sin red se sigue leyendo del SQLite local"
    _ciclo(cliente)
    assert estado() == enviado, "sin entregas nuevas no cambia"


def test_la_captura_del_dispositivo_llega_a_la_misma_cola_que_ve_el_portal(tmp_path, dispositivo):
    """Productor y emisor comparten archivo: lo que escribe uno lo lee el otro."""
    settings = _settings(tmp_path)
    cliente, _ = _portal(tmp_path, ApiFalsa())

    _capturar(dispositivo, settings)

    cola = cliente.get("/adaptador/movimientos/estado").json()
    assert cola["pendientes"] == 1
    assert TestClient(cliente.app).get("/adaptador/movimientos/estado").status_code == 401
