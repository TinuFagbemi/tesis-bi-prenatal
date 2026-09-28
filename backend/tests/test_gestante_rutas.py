"""Las rutas del adaptador de la interfaz de la gestante (SCRUM-72).

**Estas pruebas no necesitan PostgreSQL ni la API central.** El cliente del
servidor se sustituye por un doble que responde lo que cada caso necesita, y el
almacenamiento de sesiones es un archivo dentro del ``tmp_path`` que pytest da a
cada prueba. Eso las hace deterministas y las deja en la capa sin servidor del
CI, donde corren en milisegundos.

Este modulo define ademas los dobles y la fabrica que reutilizan las otras
suites de la interfaz, siguiendo la convencion del repositorio: un modulo de
prueba importa de otro con ``from tests.test_... import ...``.

**Las rutas se comprueban llamandolas, no inspeccionando ``app.routes``.** La
version instalada de FastAPI envuelve los routers incluidos en un objeto
``_IncludedRouter`` en lugar de aplanar sus rutas, de modo que un inventario por
introspeccion describiria la version de la biblioteca y no el comportamiento del
adaptador. Una peticion real no puede equivocarse en eso.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.gestante.aplicacion import crear_aplicacion
from app.gestante.central import EstadoRespuesta, RespuestaIdentidad, RespuestaToken
from app.gestante.config import NOMBRE_DE_COOKIE, GestanteSettings
from app.models.enums import NombreRol

DIRECTORIO_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "gestante"

# Credenciales ficticias y exclusivas de la suite. No corresponden a ninguna
# cuenta: el doble del servidor central decide el resultado, no el valor.
EMAIL_DE_PRUEBA = "paciente-de-prueba@example.com"
PASSWORD_DE_PRUEBA = "contrasena-ficticia-de-la-suite"
TOKEN_DE_PRUEBA = "token-ficticio-de-la-suite-1234567890"
ID_USUARIO_DE_PRUEBA = 4242


class RelojFalso:
    """Un reloj que solo avanza cuando la prueba se lo pide.

    Es lo que permite recorrer una ventana de setenta y dos horas sin
    esperarlas, igual que ``app.edge.sincronizacion`` inyecta el suyo para no
    dormir de verdad en el CI.
    """

    def __init__(self, inicio: datetime | None = None) -> None:
        self.ahora = inicio or datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.ahora

    def avanzar(self, **delta) -> None:
        self.ahora += timedelta(**delta)


class ClienteCentralDoble:
    """Doble del servidor central, con el resultado fijado por la prueba.

    Registra cuantas veces se le pregunto cada cosa, para poder comprobar
    afirmaciones sobre el comportamiento --por ejemplo, que sin token en memoria
    el adaptador no toca la red--.
    """

    def __init__(
        self,
        *,
        estado_token: EstadoRespuesta = EstadoRespuesta.OK,
        estado_identidad: EstadoRespuesta = EstadoRespuesta.OK,
        rol: NombreRol = NombreRol.PACIENTE,
        id_usuario: int = ID_USUARIO_DE_PRUEBA,
        disponible_: bool = True,
    ) -> None:
        self.estado_token = estado_token
        self.estado_identidad = estado_identidad
        self.rol = rol
        self.id_usuario = id_usuario
        self.disponible_ = disponible_

        self.llamadas_autenticar = 0
        self.llamadas_identidad = 0
        self.ultimo_email: str | None = None
        self.ultimo_password: str | None = None

    def autenticar(self, *, email: str, password: str) -> RespuestaToken:
        self.llamadas_autenticar += 1
        self.ultimo_email = email
        self.ultimo_password = password
        if self.estado_token is EstadoRespuesta.OK:
            return RespuestaToken(EstadoRespuesta.OK, token=TOKEN_DE_PRUEBA)
        return RespuestaToken(self.estado_token)

    def identidad(self, token: str) -> RespuestaIdentidad:
        self.llamadas_identidad += 1
        if self.estado_identidad is EstadoRespuesta.OK:
            return RespuestaIdentidad(
                EstadoRespuesta.OK, id_usuario=self.id_usuario, rol=self.rol
            )
        return RespuestaIdentidad(self.estado_identidad)

    def disponible(self) -> bool:
        return self.disponible_


def construir_settings(tmp_path: Path, **ajustes) -> GestanteSettings:
    """Configuracion aislada: base en ``tmp_path`` y la interfaz real.

    Los campos que las pruebas miran se pasan explicitamente para que el
    resultado no dependa de un ``.env`` presente en la maquina de quien ejecuta.
    """
    valores = {
        "sqlite_path": tmp_path / "sesion_local.sqlite3",
        "movimientos_dir": tmp_path / "movimientos",
        # Dentro de ``tmp_path`` **siempre**, incluso cuando la prueba quiere
        # que no exista. Si se dejara el valor por omision, una maquina con el
        # dispositivo aprovisionado de verdad haria pasar --o fallar-- pruebas
        # segun el estado de ``data/gestante/``, que no es lo que miden.
        "provision_path": tmp_path / "provision.json",
        "frontend_dir": DIRECTORIO_FRONTEND,
        "api_base_url": "http://127.0.0.1:8000",
        "cookie_secure": False,
    }
    valores.update(ajustes)
    return GestanteSettings(**valores)


def construir_cliente(
    tmp_path: Path,
    *,
    central: ClienteCentralDoble | None = None,
    reloj: RelojFalso | None = None,
    constructor_cliente_edge=None,
    **ajustes,
) -> tuple[TestClient, ClienteCentralDoble, RelojFalso, GestanteSettings]:
    """Un cliente HTTP contra el adaptador, con sus dobles."""
    central = central or ClienteCentralDoble()
    reloj = reloj or RelojFalso()
    settings = construir_settings(tmp_path, **ajustes)
    app = crear_aplicacion(
        settings=settings,
        cliente_central=central,
        reloj=reloj,
        constructor_cliente_edge=constructor_cliente_edge,
    )
    return TestClient(app), central, reloj, settings


def iniciar_sesion(cliente: TestClient, **cuerpo):
    """Un inicio de sesion con las credenciales ficticias de la suite."""
    datos = {"email": EMAIL_DE_PRUEBA, "password": PASSWORD_DE_PRUEBA}
    datos.update(cuerpo)
    return cliente.post("/adaptador/iniciar-sesion", json=datos)


# ---------------------------------------------------------------------------
# Archivos de la interfaz
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ruta, fragmento, tipo",
    [
        ("/", "FetalAlert", "text/html"),
        ("/styles.css", "--clr-purple", "text/css"),
        ("/app.js", "adaptador", "text/javascript"),
        ("/graficas.js", "FetalAlertGraficas", "text/javascript"),
    ],
)
def test_sirve_los_archivos_de_la_interfaz(tmp_path, ruta, fragmento, tipo):
    cliente, _, _, _ = construir_cliente(tmp_path)
    respuesta = cliente.get(ruta)

    assert respuesta.status_code == 200
    assert tipo in respuesta.headers["content-type"]
    assert fragmento in respuesta.text
    # Revalidar siempre: un app.js en caché anterior a una corrección seguiría
    # ejecutándose sin que nadie lo note.
    assert respuesta.headers["cache-control"] == "no-cache"


def test_la_interfaz_se_sirve_aunque_la_api_central_no_responda(tmp_path):
    """La razon de que exista un adaptador local: la pagina no depende del servidor."""
    central = ClienteCentralDoble(disponible_=False)
    cliente, _, _, _ = construir_cliente(tmp_path, central=central)

    assert cliente.get("/").status_code == 200
    assert cliente.get("/styles.css").status_code == 200
    assert cliente.get("/app.js").status_code == 200
    assert (
        cliente.get("/adaptador/conectividad").json()["api_central"] == "no_disponible"
    )


# ---------------------------------------------------------------------------
# Inicio de sesion
# ---------------------------------------------------------------------------


def test_login_de_paciente_abre_sesion(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)

    respuesta = iniciar_sesion(cliente)

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["autenticada"] is True
    assert cuerpo["rol"] == NombreRol.PACIENTE.value
    assert central.llamadas_autenticar == 1
    # La identidad se comprueba siempre: un token valido no basta, hace falta
    # saber de quien es y con que rol.
    assert central.llamadas_identidad >= 1


def test_credenciales_invalidas_devuelven_401_generico(tmp_path):
    central = ClienteCentralDoble(estado_token=EstadoRespuesta.RECHAZADO)
    cliente, _, _, _ = construir_cliente(tmp_path, central=central)

    respuesta = iniciar_sesion(cliente)

    assert respuesta.status_code == 401
    # El mensaje no dice cual de las tres situaciones ocurrio, igual que
    # SCRUM-70 no lo dice.
    detalle = respuesta.json()["detail"].lower()
    assert "incorrect" in detalle
    assert EMAIL_DE_PRUEBA not in respuesta.text
    assert NOMBRE_DE_COOKIE not in respuesta.headers.get("set-cookie", "")


@pytest.mark.parametrize("rol", [NombreRol.MEDICO, NombreRol.ADMIN])
def test_un_rol_que_no_es_paciente_recibe_403_y_no_deja_sesion(tmp_path, rol):
    central = ClienteCentralDoble(rol=rol)
    cliente, _, _, settings = construir_cliente(tmp_path, central=central)

    respuesta = iniciar_sesion(cliente)

    assert respuesta.status_code == 403
    assert "paciente" in respuesta.json()["detail"].lower()
    assert respuesta.headers.get("set-cookie") is None
    # Y no se abrio nada: la consulta de sesion sigue diciendo que no hay.
    assert cliente.get("/adaptador/sesion").json() == {"autenticada": False}


def test_sin_conexion_no_se_confunde_con_credenciales_malas(tmp_path):
    """Un 503 y no un 401: una caida de red no es una contrasena equivocada."""
    central = ClienteCentralDoble(estado_token=EstadoRespuesta.SIN_CONEXION)
    cliente, _, _, _ = construir_cliente(tmp_path, central=central)

    respuesta = iniciar_sesion(cliente)

    assert respuesta.status_code == 503
    assert "conexión" in respuesta.json()["detail"]


def test_respuesta_remota_incoherente_es_502(tmp_path):
    central = ClienteCentralDoble(estado_token=EstadoRespuesta.ERROR_REMOTO)
    cliente, _, _, _ = construir_cliente(tmp_path, central=central)

    assert iniciar_sesion(cliente).status_code == 502


def test_el_correo_llega_canonizado_al_servidor_central(tmp_path):
    """Se reutiliza el contrato de SCRUM-70, asi que se aplica su misma regla."""
    cliente, central, _, _ = construir_cliente(tmp_path)

    iniciar_sesion(cliente, email="  PACIENTE31@Example.COM  ")

    assert central.ultimo_email == "paciente31@example.com"


# ---------------------------------------------------------------------------
# Consulta de sesion, cierre y conectividad
# ---------------------------------------------------------------------------


def test_sin_cookie_la_sesion_no_esta_autenticada(tmp_path):
    cliente, _, _, _ = construir_cliente(tmp_path)

    respuesta = cliente.get("/adaptador/sesion")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"autenticada": False}


def test_cerrar_sesion_responde_200_aunque_no_hubiera_sesion(tmp_path):
    """No distingue: averiguar si un identificador existia no aporta nada util."""
    cliente, _, _, _ = construir_cliente(tmp_path)

    assert cliente.post("/adaptador/cerrar-sesion").status_code == 200


def test_conectividad_refleja_el_estado_del_servidor(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)

    assert cliente.get("/adaptador/conectividad").json()["api_central"] == "disponible"

    central.disponible_ = False
    assert (
        cliente.get("/adaptador/conectividad").json()["api_central"] == "no_disponible"
    )


# ---------------------------------------------------------------------------
# Estado local del nodo edge
# ---------------------------------------------------------------------------


def test_el_estado_local_exige_sesion(tmp_path):
    cliente, _, _, _ = construir_cliente(tmp_path)

    respuesta = cliente.get("/adaptador/estado-local")

    assert respuesta.status_code == 401


def test_el_estado_local_informa_cuando_el_nodo_no_esta_inicializado(tmp_path, monkeypatch):
    """No se inventa un cero: se dice que no hay almacenamiento y como crearlo."""
    from app.gestante import rutas

    monkeypatch.setattr(
        rutas, "_ruta_del_nodo", lambda _settings: tmp_path / "no-existe.sqlite3"
    )
    cliente, _, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)

    cuerpo = cliente.get("/adaptador/estado-local").json()

    assert cuerpo["inicializado"] is False
    # Ausencia, no cero. La diferencia es justo lo que la interfaz debe respetar.
    assert cuerpo["pendientes"] is None
    assert cuerpo["enviados"] is None
    assert "edge_node.py init" in cuerpo["detalle"]


def test_el_estado_local_cuenta_los_estados_reales_del_nodo(tmp_path, monkeypatch):
    """Se leen los conteos de app.edge, sin duplicar su vocabulario ni sus tablas."""
    from app.edge.almacenamiento import conectar as conectar_edge, inicializar as inicializar_edge
    from app.edge.captura import capturar
    from app.gestante import rutas
    from tests.test_ingestion_schemas import paquete

    ruta_nodo = tmp_path / "edge" / "nodo.sqlite3"
    ruta_nodo.parent.mkdir(parents=True, exist_ok=True)
    with conectar_edge(ruta_nodo) as conexion:
        inicializar_edge(conexion)
        capturar(conexion, paquete())
        capturar(conexion, paquete())

    monkeypatch.setattr(rutas, "_ruta_del_nodo", lambda _settings: ruta_nodo)
    cliente, _, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)

    cuerpo = cliente.get("/adaptador/estado-local").json()

    assert cuerpo["inicializado"] is True
    assert cuerpo["pendientes"] == 2
    # Un cero real se muestra como cero: no hay nada enviado todavia.
    assert cuerpo["enviados"] == 0
    assert cuerpo["fallidos_reintentables"] == 0
    assert cuerpo["fallidos_en_revision"] == 0
    assert cuerpo["total"] == 2


def test_el_estado_local_no_devuelve_paquetes_ni_claves(tmp_path, monkeypatch):
    """Conteos y una frase. Nada clinico puede salir por esta ruta."""
    from app.edge.almacenamiento import conectar as conectar_edge, inicializar as inicializar_edge
    from app.edge.captura import capturar
    from app.gestante import rutas
    from tests.test_ingestion_schemas import paquete

    ruta_nodo = tmp_path / "edge" / "nodo.sqlite3"
    ruta_nodo.parent.mkdir(parents=True, exist_ok=True)
    contenido = paquete()
    with conectar_edge(ruta_nodo) as conexion:
        inicializar_edge(conexion)
        registro = capturar(conexion, contenido)

    monkeypatch.setattr(rutas, "_ruta_del_nodo", lambda _settings: ruta_nodo)
    cliente, _, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)

    texto = cliente.get("/adaptador/estado-local").text

    assert registro.clave not in texto
    assert "payload" not in texto
    assert str(contenido["id_embarazo"]) not in texto
    assert "hr_valor" not in texto
    assert "id_semaforo" not in texto


def test_el_adaptador_no_inicializa_el_almacenamiento_del_nodo(tmp_path, monkeypatch):
    """Crear la base del nodo es decision de quien lo opera, no del portal.

    Importa porque ``sqlite3.connect`` crea el archivo al abrirlo: sin la
    comprobacion de existencia previa, consultar el estado lo habria creado en
    silencio.
    """
    from app.gestante import rutas

    ruta_nodo = tmp_path / "edge" / "nodo.sqlite3"
    monkeypatch.setattr(rutas, "_ruta_del_nodo", lambda _settings: ruta_nodo)
    cliente, _, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)

    cliente.get("/adaptador/estado-local")

    assert not ruta_nodo.exists()
