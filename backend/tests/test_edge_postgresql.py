"""Ciclo real del nodo edge contra la API y un servidor PostgreSQL 16 (SCRUM-64).

Se omite salvo que este definida ``SCRUM64_TEST_DATABASE_URL``. Nunca cae por
omision sobre ``DATABASE_URL`` ni sobre la variable de otra suite: una prueba que
escribe no puede terminar sobre la base de desarrollo de alguien por haberse
olvidado de exportar una variable. El workflow de CI convierte la omision en un
job en rojo.

**Que prueba esto que no prueban las suites offline.** Alli el servidor es un
``MockTransport`` y las respuestas se guionan. Aqui la peticion recorre el camino
completo -- SQLite temporal del edge, cliente HTTP, router de FastAPI, servicio
de idempotencia, PostgreSQL -- y lo que se cuenta al final son filas reales. En
particular, que un reenvio tras una respuesta perdida deja **una** sesion, no
dos.

**Aislamiento.** Se reutilizan las fixtures de SCRUM-62 sin cambiarlas: cada
prueba abre una transaccion exterior que revierte pase lo que pase, y la
``Session`` que atiende la peticion se une a ella con
``join_transaction_mode="create_savepoint"``, de modo que el ``commit`` del
endpoint es real y observable pero no sobrevive. No se ejecuta DDL, ni
``TRUNCATE``, ni ``create_all``. El archivo SQLite del nodo vive en el
``tmp_path`` de cada prueba y no se comparte con ninguna otra.

**El transporte.** El cliente del edge recibe un ``TestClient``, que *es* un
``httpx.Client``, asi que no existe una sola linea de codigo de produccion
escrita para las pruebas. Para simular una confirmacion perdida se usa una
subclase que deja pasar la peticion --el endpoint procesa y PostgreSQL confirma--
y despues lanza ``ReadTimeout`` en lugar de devolver la respuesta.

Ejecucion desde ``backend/``::

    $env:SCRUM64_TEST_DATABASE_URL = "postgresql+psycopg://<usuario>:<clave>@127.0.0.1:<puerto>/<base>"
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_edge_postgresql.py -v

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
from app.edge.cliente import CABECERA_IDEMPOTENCIA, CABECERA_REPLAY, ClienteEdge
from app.edge.emisor import ejecutar_pasada
from app.edge.estados import EstadoEntrega
from app.main import app
from app.models.idempotencia import IdempotenciaSolicitud
from app.models.monitoreo import LecturaBiometrica, SesionMonitoreo

# Fixtures y ayudantes de SCRUM-62, reutilizados tal cual. ``engine_de_pruebas``
# se redefine mas abajo sobre la variable de esta suite, y por eso
# ``conexion_revertida`` -- que lo pide por nombre -- acaba usando el de aqui.
from tests.test_ingestion_api_postgresql import (  # noqa: F401
    RUTA,
    VENTANA,
    Referencias,
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

VARIABLE_DE_ENTORNO = "SCRUM64_TEST_DATABASE_URL"
MOTOR_REQUERIDO = "postgresql"

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a una base PostgreSQL ya migrada "
        "a head para ejecutar el ciclo real del nodo edge."
    ),
)


# ---------------------------------------------------------------------------
# Conexion: se exige el entorno, no se prepara
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def url_de_pruebas() -> str:
    """URL de la base de pruebas, comprobada sin abrir ninguna conexion."""
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
    """Engine sobre una base que ya debe estar migrada. No migra por su cuenta."""
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
    assert VARIABLE_DE_ENTORNO == "SCRUM64_TEST_DATABASE_URL"


# ---------------------------------------------------------------------------
# El nodo edge y su cliente
# ---------------------------------------------------------------------------


@pytest.fixture
def nodo(tmp_path):
    """Un nodo edge recien inicializado, en un archivo propio de esta prueba."""
    with alm.conectar(tmp_path / "nodo_edge.sqlite3") as conexion:
        alm.inicializar(conexion)
        yield conexion


@pytest.fixture
def ruta_nodo(tmp_path):
    return tmp_path / "nodo_edge.sqlite3"


@pytest.fixture
def api(sesion_de_pruebas):
    """La aplicacion real, atendiendo con la Session de la transaccion revertida."""
    app.dependency_overrides[get_db] = lambda: sesion_de_pruebas
    try:
        with TestClient(app) as cliente:
            yield cliente
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def cliente_edge(api):
    return ClienteEdge(api)


class ClienteQuePierdeLaRespuesta(TestClient):
    """Entrega la peticion de verdad y despues pierde la respuesta.

    Es la ventana critica del ticket: el endpoint procesa, PostgreSQL confirma,
    y el edge no llega a enterarse. ``vistas`` conserva el codigo que el servidor
    si devolvio, para que una prueba pueda afirmar que hubo un 201 real antes del
    timeout en lugar de suponerlo.
    """

    def __init__(self, *argumentos, **claves) -> None:
        super().__init__(*argumentos, **claves)
        self.vistas: list[int] = []

    def send(self, request, **claves):
        respuesta = super().send(request, **claves)
        respuesta.read()
        self.vistas.append(respuesta.status_code)
        raise httpx.ReadTimeout("respuesta perdida en el camino", request=request)


@pytest.fixture
def api_que_pierde_la_respuesta(sesion_de_pruebas):
    app.dependency_overrides[get_db] = lambda: sesion_de_pruebas
    try:
        yield ClienteQuePierdeLaRespuesta(app)
    finally:
        app.dependency_overrides.clear()


class ClienteQueRegistraElReplay(TestClient):
    """Entrega con normalidad y anota si el servidor marco cada 201 como replay.

    El edge no guarda esa cabecera --le basta con que sea valida para dar la
    entrega por buena--, asi que sin esto una prueba solo podria deducir el
    replay de que los identificadores coincidan. Anotarla permite afirmarlo.
    """

    def __init__(self, *argumentos, **claves) -> None:
        super().__init__(*argumentos, **claves)
        self.replays: list[tuple[int, str | None]] = []

    def send(self, request, **claves):
        respuesta = super().send(request, **claves)
        respuesta.read()
        self.replays.append(
            (respuesta.status_code, respuesta.headers.get(CABECERA_REPLAY))
        )
        return respuesta


def paquete_para_el_edge(referencias, *, lecturas=1, sincronizacion=None):
    """Paquete valido para el endpoint y para la captura del edge.

    ``fecha_hora_sincronizacion`` va en ``None`` por omision porque es lo que
    describe una captura offline: nada se ha sincronizado todavia. El parametro
    existe para las pruebas que comprueban el otro caso -- un archivo que ya trae
    ese instante --, que el edge acepta y conserva sin alterarlo.
    """
    return paquete_de_signos(
        referencias,
        lecturas=[
            lectura_de_signos(
                referencias,
                fecha_hora_sincronizacion=(
                    None if sincronizacion is None else sincronizacion.isoformat()
                ),
                hr_valor=88 + indice,
            )
            for indice in range(lecturas)
        ],
    )


def contar_idempotencia(conexion, clave: str) -> int:
    return conexion.execute(
        select(func.count())
        .select_from(IdempotenciaSolicitud)
        .where(IdempotenciaSolicitud.clave == clave)
    ).scalar_one()


# ---------------------------------------------------------------------------
# Escenario 1 -- Captura completamente offline
# ---------------------------------------------------------------------------


def test_escenario_1_la_captura_offline_no_toca_postgresql(
    nodo, referencias, conexion_revertida
):
    """Se captura sin que exista transporte alguno; la base remota no se entera."""
    paquete = paquete_para_el_edge(referencias, lecturas=3)

    registro = cap.capturar(nodo, paquete)

    assert outbox.resumen(nodo).pendientes == 1
    fila = outbox.leer_evento(nodo, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.PENDIENTE.value
    assert fila["enviado_en"] is None

    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 0


# ---------------------------------------------------------------------------
# Escenario 2 -- Reinicio del nodo
# ---------------------------------------------------------------------------


def test_escenario_2_el_evento_sobrevive_al_reinicio_del_nodo(
    ruta_nodo, referencias, conexion_revertida
):
    """Cerrar todas las conexiones y volver a abrir el archivo es "reiniciar"."""
    paquete = paquete_para_el_edge(referencias, lecturas=2)

    with alm.conectar(ruta_nodo) as conexion:
        alm.inicializar(conexion)
        registro = cap.capturar(conexion, paquete)
        payload_original = outbox.leer_payload(conexion, registro.id_captura)

    with alm.conectar(ruta_nodo) as conexion:
        alm.inicializar(conexion)
        elegibles = outbox.seleccionar_elegibles(conexion, limite=10)

        assert len(elegibles) == 1
        assert elegibles[0].clave == registro.clave
        assert elegibles[0].payload_json == payload_original
        assert elegibles[0].estado is EstadoEntrega.PENDIENTE

    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0


# ---------------------------------------------------------------------------
# Escenario 3 -- Recuperacion de conectividad
# ---------------------------------------------------------------------------


def test_escenario_3_una_pasada_entrega_el_paquete_completo(
    nodo, cliente_edge, referencias, conexion_revertida
):
    paquete = paquete_para_el_edge(referencias, lecturas=3)
    registro = cap.capturar(nodo, paquete)

    resumen = ejecutar_pasada(nodo, cliente_edge)

    assert resumen.entregados == 1
    fila = outbox.leer_evento(nodo, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.ENVIADO.value
    assert fila["ultimo_http"] == 201
    assert fila["id_sesion_remota"] is not None

    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 3


def test_escenario_3_los_identificadores_guardados_son_los_de_postgresql(
    nodo, cliente_edge, referencias, conexion_revertida
):
    registro = cap.capturar(nodo, paquete_para_el_edge(referencias, lecturas=2))
    ejecutar_pasada(nodo, cliente_edge)

    fila = outbox.leer_evento(nodo, registro.id_outbox)
    persistidas = lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)

    assert json.loads(fila["ids_lectura_remotos"]) == [
        lectura["id_lectura"] for lectura in persistidas
    ]
    sesiones = sesiones_del_embarazo(conexion_revertida, referencias.id_embarazo)
    assert fila["id_sesion_remota"] == sesiones[0]["id_sesion"]


def test_escenario_3_el_paquete_llega_con_la_clave_persistida(
    nodo, cliente_edge, referencias, conexion_revertida
):
    registro = cap.capturar(nodo, paquete_para_el_edge(referencias))
    ejecutar_pasada(nodo, cliente_edge)

    assert contar_idempotencia(conexion_revertida, registro.clave) == 1


def test_escenario_3_una_captura_offline_persiste_sin_sincronizacion(
    nodo, cliente_edge, referencias, conexion_revertida
):
    """El edge no inventa el instante de sincronizacion, porque no lo conoce.

    Un paquete capturado sin conexion no se ha sincronizado, asi que el campo
    llega en ``null`` y PostgreSQL lo guarda asi. El instante en que la entrega
    se confirmo queda en ``outbox.enviado_en``, que es donde ese hecho ocurre.
    """
    registro = cap.capturar(nodo, paquete_para_el_edge(referencias, lecturas=2))
    ejecutar_pasada(nodo, cliente_edge)

    persistidas = lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)
    assert all(
        lectura["fecha_hora_sincronizacion"] is None for lectura in persistidas
    )
    assert outbox.leer_evento(nodo, registro.id_outbox)["enviado_en"] is not None


def test_escenario_3_una_sincronizacion_informada_se_persiste_sin_alterarla(
    nodo, cliente_edge, referencias, conexion_revertida
):
    """El edge no impone una regla mas estricta que el contrato de la API.

    Si el archivo de entrada ya trae un instante que el contrato acepta, se
    guarda tal cual y llega a PostgreSQL tal cual. Rechazarlo habria significado
    que el edge negara paquetes que el endpoint si acepta.
    """
    cap.capturar(
        nodo,
        paquete_para_el_edge(
            referencias, lecturas=2, sincronizacion=VENTANA.sincronizacion
        ),
    )
    resumen = ejecutar_pasada(nodo, cliente_edge)

    assert resumen.entregados == 1
    persistidas = lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)
    assert len(persistidas) == 2
    for lectura in persistidas:
        assert lectura["fecha_hora_sincronizacion"] == VENTANA.sincronizacion


def test_escenario_3_una_sincronizacion_informada_sigue_dando_replay(
    ruta_nodo, api_que_pierde_la_respuesta, sesion_de_pruebas, referencias, conexion_revertida
):
    """La prueba de que congelar el valor basta, y prohibirlo era innecesario.

    Se recorre la ventana completa con el campo informado: el servidor confirma,
    la respuesta se pierde, el nodo se reinicia y la siguiente pasada reenvia el
    **mismo** instante. Si el edge lo hubiera refrescado, esto seria un 409.
    """
    paquete = paquete_para_el_edge(
        referencias, lecturas=2, sincronizacion=VENTANA.sincronizacion
    )

    with alm.conectar(ruta_nodo) as conexion:
        alm.inicializar(conexion)
        registro = cap.capturar(conexion, paquete)
        ejecutar_pasada(conexion, ClienteEdge(api_que_pierde_la_respuesta))

    assert api_que_pierde_la_respuesta.vistas == [201]
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    ids_originales = [
        lectura["id_lectura"]
        for lectura in lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)
    ]

    with alm.conectar(ruta_nodo) as conexion:
        alm.inicializar(conexion)
        app.dependency_overrides[get_db] = lambda: sesion_de_pruebas
        try:
            with TestClient(app) as bueno:
                resumen = ejecutar_pasada(conexion, ClienteEdge(bueno))
        finally:
            app.dependency_overrides.clear()

        assert resumen.entregados == 1
        fila = outbox.leer_evento(conexion, registro.id_outbox)
        assert fila["estado"] == EstadoEntrega.ENVIADO.value
        assert json.loads(fila["ids_lectura_remotos"]) == ids_originales

    # Ni una sesion de mas, y el instante sigue siendo el que se capturo.
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 2
    for lectura in lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo):
        assert lectura["fecha_hora_sincronizacion"] == VENTANA.sincronizacion


# ---------------------------------------------------------------------------
# Escenario 4 -- Respuesta perdida despues del commit remoto
# ---------------------------------------------------------------------------


def test_escenario_4_una_respuesta_perdida_no_marca_enviado(
    nodo, api_que_pierde_la_respuesta, referencias, conexion_revertida
):
    """El servidor confirmo de verdad, y el edge no lo sabe.

    La prueba comprueba las dos mitades: que PostgreSQL tiene la sesion, y que la
    respuesta que se perdio era un 201 real -- no un fallo disfrazado.
    """
    registro = cap.capturar(nodo, paquete_para_el_edge(referencias, lecturas=2))
    cliente = ClienteEdge(api_que_pierde_la_respuesta)

    resumen = ejecutar_pasada(nodo, cliente)

    # El endpoint si respondio 201 antes de que el transporte perdiera la respuesta.
    assert api_que_pierde_la_respuesta.vistas == [201]

    assert resumen.entregados == 0
    assert resumen.reintentables == 1
    fila = outbox.leer_evento(nodo, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 1
    assert fila["id_sesion_remota"] is None

    # Y sin embargo el paquete esta guardado.
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 2


def test_escenario_4_la_siguiente_pasada_es_un_replay_sin_duplicar(
    nodo, api_que_pierde_la_respuesta, sesion_de_pruebas, referencias, conexion_revertida
):
    """La ventana completa: se confirma, se pierde la respuesta, se reintenta."""
    registro = cap.capturar(nodo, paquete_para_el_edge(referencias, lecturas=2))

    # Primer intento: PostgreSQL confirma, el edge no se entera.
    ejecutar_pasada(nodo, ClienteEdge(api_que_pierde_la_respuesta))
    assert api_que_pierde_la_respuesta.vistas == [201]
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1

    sesiones = sesiones_del_embarazo(conexion_revertida, referencias.id_embarazo)
    id_sesion_original = sesiones[0]["id_sesion"]
    ids_originales = [
        lectura["id_lectura"]
        for lectura in lecturas_del_embarazo(conexion_revertida, referencias.id_embarazo)
    ]

    # Segunda pasada, con un transporte que si entrega la respuesta.
    app.dependency_overrides[get_db] = lambda: sesion_de_pruebas
    try:
        bueno = ClienteQueRegistraElReplay(app)
        resumen = ejecutar_pasada(nodo, ClienteEdge(bueno))
    finally:
        app.dependency_overrides.clear()

    assert resumen.entregados == 1

    # El servidor reconocio el reenvio: 201 marcado explicitamente como replay.
    assert bueno.replays == [(201, "true")]

    # Los mismos identificadores, y una sola sesion.
    fila = outbox.leer_evento(nodo, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.ENVIADO.value
    assert fila["id_sesion_remota"] == id_sesion_original
    assert json.loads(fila["ids_lectura_remotos"]) == ids_originales

    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 2
    assert contar_idempotencia(conexion_revertida, registro.clave) == 1


# ---------------------------------------------------------------------------
# Escenario 5 -- Reinicio entre el fallo y la recuperacion
# ---------------------------------------------------------------------------


def test_escenario_5_el_nodo_se_reinicia_entre_el_fallo_y_la_entrega(
    ruta_nodo, api_que_pierde_la_respuesta, sesion_de_pruebas, referencias, conexion_revertida
):
    paquete = paquete_para_el_edge(referencias, lecturas=2)

    with alm.conectar(ruta_nodo) as conexion:
        alm.inicializar(conexion)
        registro = cap.capturar(conexion, paquete)
        ejecutar_pasada(conexion, ClienteEdge(api_que_pierde_la_respuesta))

    assert api_que_pierde_la_respuesta.vistas == [201]
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1

    # El nodo se apaga y vuelve. La clave y el payload siguen ahi.
    with alm.conectar(ruta_nodo) as conexion:
        alm.inicializar(conexion)
        elegibles = outbox.seleccionar_elegibles(conexion, limite=10)
        assert len(elegibles) == 1
        assert elegibles[0].clave == registro.clave

        app.dependency_overrides[get_db] = lambda: sesion_de_pruebas
        try:
            with TestClient(app) as bueno:
                resumen = ejecutar_pasada(conexion, ClienteEdge(bueno))
        finally:
            app.dependency_overrides.clear()

        assert resumen.entregados == 1
        assert (
            outbox.leer_evento(conexion, registro.id_outbox)["estado"]
            == EstadoEntrega.ENVIADO.value
        )

    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 2


# ---------------------------------------------------------------------------
# Escenario 6 -- Varios eventos
# ---------------------------------------------------------------------------


def test_escenario_6_varios_paquetes_se_entregan_en_orden_y_sin_duplicar(
    ruta_nodo, sesion_de_pruebas, referencias, conexion_revertida
):
    claves = []
    with alm.conectar(ruta_nodo) as conexion:
        alm.inicializar(conexion)
        for _ in range(3):
            claves.append(cap.capturar(conexion, paquete_para_el_edge(referencias, lecturas=2)).clave)

    # Reinicio antes de enviar.
    with alm.conectar(ruta_nodo) as conexion:
        alm.inicializar(conexion)
        assert [evento.clave for evento in outbox.seleccionar_elegibles(conexion, limite=10)] == claves

        app.dependency_overrides[get_db] = lambda: sesion_de_pruebas
        try:
            with TestClient(app) as api:
                resumen = ejecutar_pasada(conexion, ClienteEdge(api))
        finally:
            app.dependency_overrides.clear()

        assert resumen.entregados == 3
        assert outbox.resumen(conexion).enviados == 3

    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 3
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 6
    for clave in claves:
        assert contar_idempotencia(conexion_revertida, clave) == 1


def test_escenario_6_un_paquete_rechazado_no_arrastra_a_los_demas(
    nodo, cliente_edge, referencias, conexion_revertida
):
    """Un paquete con una referencia inexistente se queda solo en su fallo."""
    bueno_1 = cap.capturar(nodo, paquete_para_el_edge(referencias))
    malo = cap.capturar(
        nodo,
        paquete_de_signos(
            referencias,
            id_embarazo=2_000_000_000,
            lecturas=[lectura_de_signos(referencias, fecha_hora_sincronizacion=None)],
        ),
    )
    bueno_2 = cap.capturar(nodo, paquete_para_el_edge(referencias))

    resumen = ejecutar_pasada(nodo, cliente_edge)

    assert resumen.entregados == 2
    assert resumen.rechazados == 1
    assert outbox.leer_evento(nodo, malo.id_outbox)["estado"] == EstadoEntrega.FALLIDO.value
    assert outbox.leer_evento(nodo, malo.id_outbox)["ultimo_http"] == 404
    for registro in (bueno_1, bueno_2):
        assert (
            outbox.leer_evento(nodo, registro.id_outbox)["estado"]
            == EstadoEntrega.ENVIADO.value
        )

    # El paquete rechazado no dejo nada en PostgreSQL.
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 2


# ---------------------------------------------------------------------------
# Escenario 7 -- Colision idempotente
# ---------------------------------------------------------------------------


def test_escenario_7_una_clave_ya_usada_con_otro_contenido_da_409(
    nodo, api, cliente_edge, referencias, conexion_revertida
):
    """La clave del evento ya identifica otro paquete en el servidor.

    Se reproduce de forma controlada: se fija la clave que generara la captura y
    se reclama antes, desde fuera del edge, con un paquete distinto.
    """
    clave = "scrum64-colision-controlada-0001"
    registro = cap.capturar(
        nodo,
        paquete_para_el_edge(referencias, lecturas=2),
        generador_de_clave=lambda: clave,
    )

    # Otro contenido bajo la misma clave, enviado directamente a la API.
    primera = api.post(
        RUTA,
        json=paquete_para_el_edge(referencias, lecturas=1),
        headers={CABECERA_IDEMPOTENCIA: clave},
    )
    assert primera.status_code == 201

    resumen = ejecutar_pasada(nodo, cliente_edge)

    assert resumen.rechazados == 1
    fila = outbox.leer_evento(nodo, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.FALLIDO.value
    assert fila["reintentable"] == 0
    assert fila["ultimo_http"] == 409
    assert fila["id_sesion_remota"] is None
    assert fila["clave_idempotencia"] == clave

    # No se creo una segunda sesion: solo la que reclamo la clave primero.
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1
    assert contar_lecturas(conexion_revertida, referencias.id_embarazo) == 1
    assert contar_idempotencia(conexion_revertida, clave) == 1


def test_escenario_7_una_colision_no_se_reintenta_sola(
    nodo, api, cliente_edge, referencias, conexion_revertida
):
    clave = "scrum64-colision-controlada-0002"
    cap.capturar(
        nodo,
        paquete_para_el_edge(referencias, lecturas=2),
        generador_de_clave=lambda: clave,
    )
    api.post(
        RUTA,
        json=paquete_para_el_edge(referencias, lecturas=1),
        headers={CABECERA_IDEMPOTENCIA: clave},
    )
    ejecutar_pasada(nodo, cliente_edge)

    segunda = ejecutar_pasada(nodo, cliente_edge)

    assert segunda.seleccionados == 0
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 1


# ---------------------------------------------------------------------------
# Escenario 8 -- Rollback local
# ---------------------------------------------------------------------------


def test_escenario_8_un_fallo_de_captura_no_deja_filas_ni_toca_postgresql(
    nodo, referencias, conexion_revertida, monkeypatch
):
    import sqlite3

    original = outbox.registrar

    def registrar_que_falla(conexion_bd, **argumentos):
        original(conexion_bd, **argumentos)
        raise sqlite3.OperationalError("fallo simulado tras escribir")

    monkeypatch.setattr(cap.outbox, "registrar", registrar_que_falla)
    with pytest.raises(sqlite3.OperationalError):
        cap.capturar(nodo, paquete_para_el_edge(referencias))

    assert nodo.execute("SELECT count(*) FROM captura_local").fetchone()[0] == 0
    assert nodo.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
    assert contar_sesiones(conexion_revertida, referencias.id_embarazo) == 0

    # Y despues se puede capturar bien.
    monkeypatch.setattr(cap.outbox, "registrar", original)
    assert cap.capturar(nodo, paquete_para_el_edge(referencias)).id_outbox > 0


# ---------------------------------------------------------------------------
# Escenario 9 -- Aislamiento
# ---------------------------------------------------------------------------


def test_escenario_9_el_archivo_sqlite_es_propio_de_cada_prueba(nodo, tmp_path):
    archivos = sorted(tmp_path.glob("*.sqlite3"))
    assert [ruta.name for ruta in archivos] == ["nodo_edge.sqlite3"]
    assert archivos[0].parent == tmp_path


def test_escenario_9_el_edge_no_abre_conexiones_a_postgresql():
    """Todo lo que llega al servidor pasa por la API, nunca por el motor."""
    import ast
    import inspect

    from app.edge import cliente, emisor

    for modulo in (alm, cap, outbox, cliente, emisor):
        arbol = ast.parse(inspect.getsource(modulo))
        importados = set()
        for nodo_ast in ast.walk(arbol):
            if isinstance(nodo_ast, ast.Import):
                importados.update(alias.name.split(".")[0] for alias in nodo_ast.names)
            elif isinstance(nodo_ast, ast.ImportFrom) and nodo_ast.module:
                importados.add(nodo_ast.module.split(".")[0])
        assert "sqlalchemy" not in importados, modulo.__name__
        assert "psycopg" not in importados, modulo.__name__


def test_la_base_no_conserva_nada_de_esta_suite(engine_de_pruebas, referencias):
    """Cada prueba revierte su transaccion exterior: no queda una sola fila.

    Se mira desde una conexion nueva, fuera de cualquier transaccion de prueba.
    ``referencias`` se pide a proposito: obliga a que la fixture que crea filas
    se haya ejecutado tambien en esta prueba, de modo que lo que se comprueba es
    que se revirtieron y no que nunca existieron.
    """
    with engine_de_pruebas.connect() as verificacion:
        for modelo in (IdempotenciaSolicitud, LecturaBiometrica, SesionMonitoreo):
            assert (
                verificacion.execute(
                    select(func.count()).select_from(modelo)
                ).scalar_one()
                == 0
            ), modelo.__name__
