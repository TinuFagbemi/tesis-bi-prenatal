"""Recorridos HTTP reales con la credencial de la API (SCRUM-98).

Se omite salvo que este definida ``SCRUM98_HTTP_TEST_DATABASE_URL``. Nunca cae
por omision sobre otra variable.

**Por que existe esta suite.** Las demas suites HTTP de este repositorio
(SCRUM-62, 63, 70, 97) se conectan con el usuario de inicializacion del
contenedor de PostgreSQL, que es superusuario. Eso las hace comodas -- pueden
fabricar cualquier fila de referencia -- y al mismo tiempo ciegas para lo unico
que SCRUM-98 introduce: un superusuario no esta sujeto a ninguna politica, asi
que una ruta puede pasar sus pruebas enteras y fallar en produccion la primera
vez que la atiende ``fetalalert_api``.

Eso ocurrio. Dos defectos reales sobrevivieron a una regresion completa en verde:

* ``_contexto_para`` resolvia el perfil clinico **antes** de instalar
  ``fetalalert.id_usuario``. Como ``usuario_paciente`` y ``usuario_medico``
  llevan FORCE RLS filtrando por esa variable, bajo un rol restringido la
  consulta devolvia cero filas y toda ruta de PACIENTE o de MEDICO respondia
  403;
* las rutas administrativas de SCRUM-97 comprobaban el rol pero no instalaban
  el contexto, de modo que ``seguridad.es_admin()`` era falso dentro de la
  transaccion: el ``WITH CHECK`` de ``pol_provision`` rechazaba el INSERT del
  puente y el helper de perfiles respondia ``sin_autorizacion``.

Ninguno de los dos es detectable sin conectarse como el rol real. Por eso esta
suite ata la aplicacion -- la de verdad, con su router, sus dependencias y sus
transacciones -- a un engine de ``fetalalert_api``, y comprueba primero que sea
ese el ``current_user`` que ejecuta las sentencias.

**Dos engines, dos papeles que no se mezclan.** El de la aplicacion es el de la
API y es el sujeto de cada asercion de comportamiento. El administrativo solo
observa y limpia: prepara nada, afirma nada sobre el aislamiento. Cuando una
prueba necesita mirar una fila que la API no puede leer -- el puente que acaba
de crear, por ejemplo -- lo hace con el observador y lo dice.

La base debe estar migrada al head y cargada con el dataset simulado, igual que
para ``test_rls_postgresql.py``.

Todas las cuentas y todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import os
import re
import uuid
from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api.dependencias import (
    contexto_actual,
    get_db_auditoria,
    obtener_configuracion_jwt,
)
from app.config import ConfiguracionJWT
from app.db.session import get_db
from app.main import app
from app.models.enums import NombreRol
from app.services.consulta_clinica import (
    RecursoClinicoInexistente,
    exigir_embarazo_visible,
    listar_embarazos,
)
from app.services.contexto import (
    ContextoClinico,
    ContextoNoResoluble,
    resolver_contexto,
)
from app.services.principal import PrincipalAutenticado

VARIABLE_DE_ENTORNO = "SCRUM98_HTTP_TEST_DATABASE_URL"
ROL_ESPERADO = "fetalalert_api"

# ---------------------------------------------------------------------------
# Que bases acepta esta suite, y por que la lista es corta
# ---------------------------------------------------------------------------
#
# Esta suite **escribe y borra**. Crea dos perfiles clinicos, aprovisiona
# cuentas, ingesta sesiones con sus lecturas, y al terminar retira todo eso
# usando marcas de agua: «borra las filas de auditoria con id_log mayor que el
# que habia antes», «borra las sesiones con id_sesion mayor que el que habia
# antes». Esa tecnica es exacta y no alcanza una sola fila ajena, pero descansa
# en dos supuestos que ningun codigo de la suite puede verificar por si solo:
#
# * que la base sea **desechable**, porque aunque la limpieza sea precisa, una
#   prueba que falle a mitad puede dejar filas a medio retirar;
# * que **no haya concurrencia**, porque si otro proceso escribe entre la marca
#   de agua y el teardown, esas filas caen dentro del rango y se borran.
#
# De modo que el nombre de la base se valida antes de que exista un engine, y
# solo se admiten dos cosas: el nombre efimero que CI crea y destruye con el
# servicio de PostgreSQL, y el prefijo reservado a las pruebas de SCRUM-98. Una
# URL hacia cualquier otra base aborta la sesion de pytest entera, antes de que
# ninguna fixture haya creado un perfil, una cuenta, una auditoria, una sesion o
# una reclamacion.
BASE_EFIMERA_DE_CI = "scrum52_validacion_tmp"
PREFIJO_DE_BASE_APROBADO = re.compile(r"^scrum98_[a-z0-9_]{1,40}$")

class BaseNoAutorizada(RuntimeError):
    """La URL apunta a una base que esta suite no puede tocar."""

# La misma que el generador del dataset usa para todas las cuentas simuladas.
# Se repite aqui en vez de importarse del generador para que esta suite no
# dependa de ejecutarlo: la base ya viene cargada cuando llega aqui.
PASSWORD_SIMULADA = "FetalAlert-Dataset-Simulado-2026"

RUTA_TOKEN = "/api/v1/autenticacion/token"
RUTA_YO = "/api/v1/autenticacion/yo"
RUTA_SESIONES = "/api/v1/sesiones-monitoreo"
RUTA_PACIENTES = "/api/v1/cuentas/pacientes/{}"
RUTA_MEDICOS = "/api/v1/cuentas/medicos/{}"
RUTA_ESTADO = "/api/v1/cuentas/{}/estado"
RUTA_EMBARAZOS = "/api/v1/clinico/embarazos"
RUTA_SESIONES_DE = "/api/v1/clinico/embarazos/{}/sesiones"
RUTA_LECTURAS_DE = "/api/v1/clinico/sesiones/{}/lecturas"

EMAIL_ADMIN = "admin01@example.com"
EMAIL_PACIENTE = "paciente01@example.com"
EMAIL_MEDICO = "medico01@example.com"

# Marca de esta suite en todo lo que escribe, para que la limpieza sepa
# exactamente que retirar y no toque una sola fila del dataset.
#
# En las sesiones la marca no puede ir en ``tipo_sesion``: es un enum del dominio
# con dos valores, y meter un tercero seria inventarse el modelo para comodidad
# de la prueba. Va en la hora de inicio, que es un centinela improbable y que la
# fixture comprueba que no exista ya en el dataset.
MARCA = "scrum98http"
HORA_CENTINELA = "08:07:11"

# Los correos que esta suite puede llegar a registrar, enumerados. Un conjunto
# cerrado y no un patron: la limpieza resuelve estos nombres exactos a
# identificadores y borra por identificador, de modo que no hay forma de que
# alcance una cuenta que no sea suya.
EMAIL_CUENTA_PACIENTE = f"paciente.{MARCA}@example.com"
EMAIL_CUENTA_MEDICO = f"medico.{MARCA}@example.com"
EMAIL_CUENTA_CICLO = f"ciclo.{MARCA}@example.com"
EMAIL_CUENTA_DUPLICADA = f"otra.{MARCA}@example.com"
EMAIL_CUENTA_FANTASMA = f"fantasma.{MARCA}@example.com"
EMAIL_CUENTA_USURPADA = f"usurpada.{MARCA}@example.com"
EMAIL_SONDA_A = f"a.{MARCA}@example.com"
EMAIL_SONDA_B = f"b.{MARCA}@example.com"

EMAILS_DE_LA_SUITE = (
    EMAIL_CUENTA_PACIENTE,
    EMAIL_CUENTA_MEDICO,
    EMAIL_CUENTA_CICLO,
    EMAIL_CUENTA_DUPLICADA,
    EMAIL_CUENTA_FANTASMA,
    EMAIL_CUENTA_USURPADA,
    EMAIL_SONDA_A,
    EMAIL_SONDA_B,
)

# Los dos perfiles clinicos ficticios que esta suite crea para poder
# aprovisionarles cuenta. No corresponden a ninguna persona, no estan en el
# dataset, y el correo lleva la marca para que la limpieza los reconozca.
CEDULA_PACIENTE_SIMULADA = "SIM-PAC-SCRUM98"
EMAIL_PERFIL_PACIENTE = f"perfil.paciente.{MARCA}@example.com"
EMAIL_PERFIL_MEDICO = f"perfil.medico.{MARCA}@example.com"
PASSWORD_NUEVA = "Clave-Simulada-SCRUM98-4417"

CONFIGURACION = ConfiguracionJWT(
    secreto="secreto-simulado-de-pruebas-scrum98-http-0001",
    expiracion=timedelta(minutes=30),
)

pytestmark = pytest.mark.skipif(
    not os.environ.get(VARIABLE_DE_ENTORNO),
    reason=(
        f"Define {VARIABLE_DE_ENTORNO} apuntando a una base migrada y cargada, "
        f"con el rol {ROL_ESPERADO}."
    ),
)


MENSAJE_BASE_NO_AUTORIZADA = (
    "{variable} apunta a la base {base!r}, que esta suite no tiene permitido "
    "tocar. Escribe y borra por marcas de agua, asi que solo es segura sobre una "
    "base desechable y sin nadie mas escribiendo a la vez. Se admiten la base "
    "efimera de CI ({ci!r}) y cualquier nombre que empiece por 'scrum98_'. "
    "Apuntala a un contenedor desechable; nunca a una base persistente, "
    "canonica ni compartida."
)


def exigir_base_desechable(url: str, variable: str = VARIABLE_DE_ENTORNO) -> str:
    """Devuelve el nombre de la base si esta autorizada; si no, lo explica.

    Funcion pura y separada de la fixture a proposito: asi la negativa se puede
    probar sin servidor, que es donde tiene que estar probada. Una salvaguarda
    que solo se ejercita cuando hay un PostgreSQL delante no protege del caso
    que importa.
    """
    base = make_url(url).database or ""
    # ``fullmatch`` y no ``match``: el ancla ``$`` de una expresion regular de
    # Python acepta un salto de linea final, de modo que un nombre terminado en
    # un salto -- el que deja un secreto copiado con el suyo -- pasaba ``match``
    # y nombraba una base distinta de la que la comprobacion creyo aprobar.
    # ``fullmatch`` exige que la cadena entera sea el nombre.
    if base == BASE_EFIMERA_DE_CI or PREFIJO_DE_BASE_APROBADO.fullmatch(base):
        return base
    raise BaseNoAutorizada(
        MENSAJE_BASE_NO_AUTORIZADA.format(
            variable=variable, base=base, ci=BASE_EFIMERA_DE_CI
        )
    )
# ---------------------------------------------------------------------------
# Engines: el de la API y el observador
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def url() -> str:
    """La URL, validada dos veces antes de que exista un engine.

    Es la fixture de la que cuelgan todas las demas -- los dos engines, las
    referencias, la limpieza --, asi que validar aqui es validar antes de que
    nada se escriba. Las dos comprobaciones responden a dos riesgos distintos:
    la credencial equivocada haria que la suite no demostrara nada, y la base
    equivocada haria que borrara lo que no es suyo.
    """
    valor = os.environ[VARIABLE_DE_ENTORNO]

    try:
        exigir_base_desechable(valor)
    except BaseNoAutorizada as error:
        pytest.fail(str(error))

    if make_url(valor).username != ROL_ESPERADO:
        pytest.fail(
            f"{VARIABLE_DE_ENTORNO} debe conectarse como {ROL_ESPERADO}. Recorrer "
            "estas rutas con otra credencial es exactamente lo que dejo pasar los "
            "defectos que esta suite existe para detectar."
        )
    return valor


@pytest.fixture(scope="session")
def engine_api(url):
    motor = create_engine(url, poolclass=NullPool)
    try:
        yield motor
    finally:
        motor.dispose()


@pytest.fixture(scope="session")
def engine_observador(url):
    """Solo mira y limpia. Ninguna asercion de aislamiento se hace con el."""
    administrativa = make_url(url).set(
        username=os.environ.get("SCRUM98_HTTP_ADMIN_USER", "fetalalert_ci"),
        password=os.environ.get("SCRUM98_HTTP_ADMIN_PASSWORD", ""),
    )
    motor = create_engine(administrativa, poolclass=NullPool)
    try:
        yield motor
    finally:
        motor.dispose()


def observar(engine_observador, consulta: str, **parametros):
    with engine_observador.connect() as conexion:
        return conexion.execute(text(consulta), parametros).mappings().all()


def escalar(engine_observador, consulta: str, **parametros):
    filas = observar(engine_observador, consulta, **parametros)
    return None if not filas else list(filas[0].values())[0]


# ---------------------------------------------------------------------------
# La aplicacion real, atada al engine de la API
# ---------------------------------------------------------------------------


@contextmanager
def api_sobre(engine):
    """``app`` con sus dos sesiones -- distintas -- sobre la credencial de la API.

    ``get_db`` y ``get_db_auditoria`` se sustituyen por sesiones de este engine,
    que es lo unico que cambia respecto de como corre en produccion: ahi las dos
    salen del engine del proceso, que se construye con la misma URL.
    """

    def sesion_negocio():
        with Session(bind=engine, autoflush=False) as sesion_bd:
            yield sesion_bd

    def sesion_auditoria():
        with Session(bind=engine, autoflush=False) as sesion_bd:
            yield sesion_bd

    app.dependency_overrides[get_db] = sesion_negocio
    app.dependency_overrides[get_db_auditoria] = sesion_auditoria
    app.dependency_overrides[obtener_configuracion_jwt] = lambda: CONFIGURACION
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def cliente(engine_api, limpieza):
    with api_sobre(engine_api) as cliente:
        yield cliente


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def iniciar_sesion(cliente, email: str, password: str = PASSWORD_SIMULADA) -> str:
    respuesta = cliente.post(RUTA_TOKEN, json={"email": email, "password": password})
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()["access_token"]


@pytest.fixture
def token_admin(cliente) -> str:
    return iniciar_sesion(cliente, EMAIL_ADMIN)


@pytest.fixture
def token_paciente(cliente) -> str:
    return iniciar_sesion(cliente, EMAIL_PACIENTE)


@pytest.fixture
def token_medico(cliente) -> str:
    return iniciar_sesion(cliente, EMAIL_MEDICO)


# ---------------------------------------------------------------------------
# Referencias del dataset y limpieza dirigida
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def identidades(engine_observador):
    """Las tres identidades canonicas del dataset, con su perfil clinico.

    Se leen con el observador porque hace falta saber *cual* es el perfil
    correcto para poder afirmar que la dependencia devuelve ese y no otro. El
    observador no participa en ninguna asercion de aislamiento: lo que se juzga
    lo ejecuta siempre el engine de la API.
    """
    with engine_observador.connect() as conexion:
        filas = conexion.execute(
            text(
                """
                SELECT u.email, u.id_usuario, up.id_paciente, um.id_medico
                FROM operacional.usuario u
                LEFT JOIN operacional.usuario_paciente up
                       ON up.id_usuario = u.id_usuario
                LEFT JOIN operacional.usuario_medico um
                       ON um.id_usuario = u.id_usuario
                WHERE u.email = ANY(:emails)
                """
            ),
            {"emails": [EMAIL_ADMIN, EMAIL_PACIENTE, EMAIL_MEDICO]},
        ).mappings().all()

    por_email = {fila["email"]: dict(fila) for fila in filas}
    assert set(por_email) == {EMAIL_ADMIN, EMAIL_PACIENTE, EMAIL_MEDICO}, (
        "El dataset no trae las tres cuentas canonicas que esta suite necesita."
    )
    assert por_email[EMAIL_PACIENTE]["id_paciente"] is not None
    assert por_email[EMAIL_MEDICO]["id_medico"] is not None

    return {
        "admin": por_email[EMAIL_ADMIN],
        "paciente": por_email[EMAIL_PACIENTE],
        "medico": por_email[EMAIL_MEDICO],
    }


@pytest.fixture(scope="session")
def alcance_clinico(engine_observador, identidades):
    """Lo que el dataset canonico ofrece para probar los dos alcances.

    Todo se **lee**; aqui no se escribe nada. Los identificadores salen del
    dataset tal como esta, y las aserciones de mas abajo comparan contra ellos en
    vez de contra numeros escritos a mano: si el generador cambiara, estas
    pruebas hablarian del dataset nuevo en lugar de romperse por un literal.
    """
    with engine_observador.connect() as conexion:
        vigente = "sc.activo AND sc.fecha_asignacion <= CURRENT_DATE AND (sc.fecha_fin IS NULL OR sc.fecha_fin >= CURRENT_DATE)"

        del_medico = list(
            conexion.execute(
                text(
                    f"""
                    SELECT sc.id_embarazo FROM operacional.seguimiento_clinico sc
                    WHERE sc.id_medico = :m AND {vigente}
                    ORDER BY sc.id_embarazo
                    """
                ),
                {"m": identidades["medico"]["id_medico"]},
            ).scalars()
        )
        de_la_paciente = list(
            conexion.execute(
                text(
                    "SELECT id_embarazo FROM operacional.embarazo "
                    "WHERE id_paciente = :p ORDER BY id_embarazo"
                ),
                {"p": identidades["paciente"]["id_paciente"]},
            ).scalars()
        )
        # Un embarazo que el medico *no* sigue pero cuya clinica comparte. Es el
        # caso que demuestra que ``medico_clinica`` no concede acceso clinico.
        por_clinica = conexion.execute(
            text(
                f"""
                SELECT e.id_embarazo
                FROM operacional.medico_clinica mc
                JOIN operacional.embarazo e ON e.id_clinica = mc.id_clinica
                WHERE mc.id_medico = :m
                  AND NOT EXISTS (
                      SELECT 1 FROM operacional.seguimiento_clinico sc
                      WHERE sc.id_medico = mc.id_medico
                        AND sc.id_embarazo = e.id_embarazo AND {vigente})
                ORDER BY e.id_embarazo LIMIT 1
                """
            ),
            {"m": identidades["medico"]["id_medico"]},
        ).scalar_one_or_none()
        # Y uno cuya asignacion existe pero ya no esta vigente.
        asignacion_terminada = conexion.execute(
            text(
                f"""
                SELECT sc.id_embarazo FROM operacional.seguimiento_clinico sc
                WHERE sc.id_medico = :m AND NOT ({vigente})
                ORDER BY sc.id_embarazo LIMIT 1
                """
            ),
            {"m": identidades["medico"]["id_medico"]},
        ).scalar_one_or_none()
        estado = conexion.execute(
            text(
                "SELECT estado_embarazo FROM operacional.embarazo "
                "WHERE id_embarazo = :e"
            ),
            {"e": de_la_paciente[0]},
        ).scalar_one()
        sesiones_del_medico = list(
            conexion.execute(
                text(
                    "SELECT id_sesion FROM operacional.sesion_monitoreo "
                    "WHERE id_embarazo = :e ORDER BY id_sesion"
                ),
                {"e": del_medico[0]},
            ).scalars()
        )
        inexistente = conexion.execute(
            text("SELECT max(id_embarazo) + 5000 FROM operacional.embarazo")
        ).scalar_one()
        sesion_inexistente = conexion.execute(
            text("SELECT max(id_sesion) + 5000 FROM operacional.sesion_monitoreo")
        ).scalar_one()

    assert del_medico, "el dataset no da al medico ninguna asignacion vigente"
    assert de_la_paciente, "el dataset no da a la paciente ningun embarazo"
    assert por_clinica is not None, "el dataset no permite probar medico_clinica"
    assert asignacion_terminada is not None, "el dataset no trae una asignacion no vigente"

    return {
        "del_medico": del_medico,
        "de_la_paciente": de_la_paciente,
        "estado_del_primero_de_la_paciente": estado,
        "solo_por_clinica": por_clinica,
        "asignacion_terminada": asignacion_terminada,
        "sesiones_del_medico": sesiones_del_medico,
        "embarazo_inexistente": inexistente,
        "sesion_inexistente": sesion_inexistente,
    }


@pytest.fixture(scope="session")
def referencias(engine_observador):
    """Identificadores reales del dataset. Se leen; no se escriben."""
    with engine_observador.connect() as conexion:
        propia = conexion.execute(
            text(
                """
                SELECT up.id_usuario, up.id_paciente, e.id_embarazo
                FROM operacional.usuario_paciente up
                JOIN operacional.usuario u ON u.id_usuario = up.id_usuario
                JOIN operacional.embarazo e ON e.id_paciente = up.id_paciente
                WHERE u.email = :email
                ORDER BY e.id_embarazo
                LIMIT 1
                """
            ),
            {"email": EMAIL_PACIENTE},
        ).mappings().one()
        ajeno = conexion.execute(
            text(
                """
                SELECT e.id_embarazo
                FROM operacional.embarazo e
                WHERE e.id_paciente <> :mia
                ORDER BY e.id_embarazo
                LIMIT 1
                """
            ),
            {"mia": propia["id_paciente"]},
        ).scalar_one()
        dispositivo = conexion.execute(
            text(
                """
                SELECT ad.id_dispositivo, ad.fecha_inicio, ad.fecha_fin
                FROM operacional.asignacion_dispositivo ad
                WHERE ad.id_embarazo = :e
                ORDER BY ad.id_asignacion
                LIMIT 1
                """
            ),
            {"e": propia["id_embarazo"]},
        ).mappings().one()
        # Los catalogos se toman de una lectura que el dataset ya escribio para
        # este embarazo: asi el paquete de prueba es coherente con la semana
        # gestacional del embarazo sin tener que recalcularla aqui.
        catalogo = conexion.execute(
            text(
                """
                SELECT lb.id_tiempo_gest, lb.id_semaforo,
                       lb.fecha_hora_captura::date AS dia
                FROM operacional.lectura_biometrica lb
                JOIN operacional.sesion_monitoreo sm ON sm.id_sesion = lb.id_sesion
                WHERE sm.id_embarazo = :e
                  AND lb.hr_valor IS NOT NULL
                  AND lb.mov_valor IS NULL
                ORDER BY lb.id_lectura
                LIMIT 1
                """
            ),
            {"e": propia["id_embarazo"]},
        ).mappings().one()
        perfil_tomado = conexion.execute(
            text(
                "SELECT id_paciente FROM operacional.usuario_paciente "
                "ORDER BY id_paciente LIMIT 1"
            )
        ).scalar_one()
        inexistente = conexion.execute(
            text("SELECT max(id_embarazo) + 1000 FROM operacional.embarazo")
        ).scalar_one()

    assert catalogo["id_tiempo_gest"] is not None, "dataset sin tiempo gestacional"

    with engine_observador.connect() as conexion:
        choques = conexion.execute(
            text(
                "SELECT count(*) FROM operacional.sesion_monitoreo "
                "WHERE fecha_inicio::time = :hora"
            ),
            {"hora": HORA_CENTINELA},
        ).scalar_one()
    assert choques == 0, (
        f"El dataset ya trae sesiones a las {HORA_CENTINELA}; la limpieza de esta "
        "suite las borraria. Cambia HORA_CENTINELA."
    )

    # Dos perfiles clinicos ficticios, sin cuenta, creados por esta suite.
    #
    # El dataset canonico deja sus treinta pacientes y sus cinco medicos ya
    # vinculados, asi que no hay ninguno libre y los recorridos de provision se
    # habrian saltado -- que es justo lo que no puede pasar aqui. Se crean
    # nuevos en vez de desvincular uno existente: desvincular cambiaria el
    # dataset, y crear no toca una sola de sus filas. La limpieza los retira.
    with engine_observador.begin() as conexion:
        id_paciente_libre = conexion.execute(
            text(
                """
                INSERT INTO operacional.paciente
                    (cedula, primer_nombre, apellido_paterno, email_pac, fecha_nac)
                VALUES (:cedula, 'Perfil', 'Simulado', :email, '1998-04-17')
                RETURNING id_paciente
                """
            ),
            {"cedula": CEDULA_PACIENTE_SIMULADA, "email": EMAIL_PERFIL_PACIENTE},
        ).scalar_one()
        id_medico_libre = conexion.execute(
            text(
                """
                INSERT INTO operacional.medico
                    (id_especialidad, primer_nombre, apellido_paterno, email_med)
                VALUES ((SELECT id_especialidad FROM operacional.especialidad
                          ORDER BY id_especialidad LIMIT 1),
                        'Medico', 'Simulado', :email)
                RETURNING id_medico
                """
            ),
            {"email": EMAIL_PERFIL_MEDICO},
        ).scalar_one()

    return {
        "id_usuario_paciente": propia["id_usuario"],
        "id_paciente": propia["id_paciente"],
        "id_embarazo": propia["id_embarazo"],
        "id_embarazo_ajeno": ajeno,
        "id_embarazo_inexistente": inexistente,
        "id_dispositivo": dispositivo["id_dispositivo"],
        "asignacion": dispositivo,
        "id_tiempo_gest": catalogo["id_tiempo_gest"],
        "id_semaforo": catalogo["id_semaforo"],
        "dia": catalogo["dia"],
        "id_perfil_libre": id_paciente_libre,
        "id_medico_libre": id_medico_libre,
        "id_perfil_tomado": perfil_tomado,
    }


@pytest.fixture
def limpieza(engine_observador):
    """Retira exactamente lo que esta suite escribe, por identificador.

    Estas pruebas confirman: una cuenta aprovisionada por HTTP sobrevive a la
    peticion, igual que en produccion. Asi que la limpieza es explicita, y la
    forma en que identifica lo suyo es la parte que importa.

    **Ni un patron, ni un UPDATE general.** Una version anterior cerraba con
    ``UPDATE operacional.usuario SET activo = true WHERE NOT activo``, que habria
    reactivado cualquier cuenta que otra cosa hubiera dejado desactivada a
    proposito -- exactamente el tipo de efecto que una prueba no puede tener. Y
    borraba auditorias con ``email LIKE '%marca%'``, que alcanza lo que se
    parezca a la marca en vez de lo que la suite escribio.

    Ahora se toman tres marcas de agua **antes** de la prueba -- el ultimo
    ``id_log``, el ultimo ``id_sesion`` y el conjunto de cuentas inactivas -- y
    se borra unicamente lo que aparecio despues. Las cuentas se resuelven desde
    ``EMAILS_DE_LA_SUITE``, que es una lista cerrada, y todo lo demas se borra
    por los identificadores resultantes.

    La reactivacion desaparecio en vez de acotarse: las unicas cuentas que las
    pruebas de ciclo desactivan son las que ellas mismas crearon, y esas se
    borran aqui. En su lugar queda una asercion de que el conjunto de cuentas
    inactivas ajenas no cambio, que es la afirmacion que la reactivacion
    pretendia sostener sin comprobarla.
    """
    with engine_observador.connect() as conexion:
        ultimo_log = conexion.execute(
            text("SELECT coalesce(max(id_log), 0) FROM operacional.auditoria_log")
        ).scalar_one()
        ultima_sesion = conexion.execute(
            text("SELECT coalesce(max(id_sesion), 0) FROM operacional.sesion_monitoreo")
        ).scalar_one()
        inactivas_antes = frozenset(
            conexion.execute(
                text("SELECT id_usuario FROM operacional.usuario WHERE NOT activo")
            ).scalars()
        )

    yield

    with engine_observador.begin() as conexion:
        cuentas = list(
            conexion.execute(
                text(
                    "SELECT id_usuario FROM operacional.usuario "
                    "WHERE email = ANY(:emails)"
                ),
                {"emails": list(EMAILS_DE_LA_SUITE)},
            ).scalars()
        )

        # La auditoria va primero: su clave foranea hacia ``usuario`` es RESTRICT
        # y PostgreSQL se niega a borrar una cuenta con historial. Se acota por
        # la marca de agua, de modo que no puede alcanzar una fila anterior ni
        # una de otra cuenta.
        conexion.execute(
            text("DELETE FROM operacional.auditoria_log WHERE id_log > :desde"),
            {"desde": ultimo_log},
        )

        if cuentas:
            for tabla in ("usuario_paciente", "usuario_medico"):
                conexion.execute(
                    text(
                        f"DELETE FROM operacional.{tabla} "
                        "WHERE id_usuario = ANY(:ids)"
                    ),
                    {"ids": cuentas},
                )
            conexion.execute(
                text("DELETE FROM operacional.usuario WHERE id_usuario = ANY(:ids)"),
                {"ids": cuentas},
            )

        # Las reclamaciones, por su clave exacta. Van antes de las sesiones:
        # su clave foranea tambien es RESTRICT.
        if CLAVES_GENERADAS:
            conexion.execute(
                text(
                    "DELETE FROM operacional.idempotencia_solicitud "
                    "WHERE clave = ANY(:claves)"
                ),
                {"claves": sorted(CLAVES_GENERADAS)},
            )
            CLAVES_GENERADAS.clear()

        # Y las sesiones que la ingesta creo, acotadas por la marca de agua y
        # por la hora centinela que la fixture de referencias comprobo que no
        # existia en el dataset.
        conexion.execute(
            text(
                """
                DELETE FROM operacional.lectura_biometrica
                WHERE id_sesion IN (
                    SELECT id_sesion FROM operacional.sesion_monitoreo
                    WHERE id_sesion > :desde AND fecha_inicio::time = :hora)
                """
            ),
            {"desde": ultima_sesion, "hora": HORA_CENTINELA},
        )
        conexion.execute(
            text(
                "DELETE FROM operacional.sesion_monitoreo "
                "WHERE id_sesion > :desde AND fecha_inicio::time = :hora"
            ),
            {"desde": ultima_sesion, "hora": HORA_CENTINELA},
        )

        inactivas_despues = frozenset(
            conexion.execute(
                text("SELECT id_usuario FROM operacional.usuario WHERE NOT activo")
            ).scalars()
        )

    assert inactivas_despues == inactivas_antes, (
        "La prueba dejo desactivada una cuenta que no creo, o reactivo una que "
        f"ya estaba desactivada: antes {sorted(inactivas_antes)}, despues "
        f"{sorted(inactivas_despues)}. Esta suite solo puede cambiar el estado "
        "de las cuentas que ella misma aprovisiona."
    )


@pytest.fixture(scope="session", autouse=True)
def retirar_los_perfiles_simulados(engine_observador, referencias):
    """Los dos perfiles que la suite creo, al terminar toda la sesion.

    Van aparte de ``limpieza`` porque su vida es la de la suite entera: se crean
    una vez y se retiran una vez. Se borran por su correo ficticio, que es lo
    unico que los identifica, y despues de que ``limpieza`` haya retirado las
    cuentas que los apuntaban.
    """
    yield
    with engine_observador.begin() as conexion:
        conexion.execute(
            text("DELETE FROM operacional.paciente WHERE email_pac = :c"),
            {"c": EMAIL_PERFIL_PACIENTE},
        )
        conexion.execute(
            text("DELETE FROM operacional.medico WHERE email_med = :c"),
            {"c": EMAIL_PERFIL_MEDICO},
        )


# Cada clave que la suite genera queda anotada aqui, para que la limpieza borre
# esas y no las que se parezcan a esas.
CLAVES_GENERADAS: set[str] = set()


@pytest.fixture
def episodios_simulados(engine_observador, cliente, token_admin, referencias):
    """Dos embarazos de la gestante ficticia, con una sesion y una lectura cada uno.

    **Por que se fabrican en vez de buscarlos.** El dataset canonico no tiene
    ninguna paciente con mas de un episodio, y la separacion entre episodios es
    una de las garantias que esta subfase debe demostrar. Desdoblar un embarazo
    del dataset lo modificaria; crear dos nuevos para el perfil ficticio que esta
    suite ya posee no toca una sola fila canonica.

    La cuenta se aprovisiona por HTTP, como en produccion. Las filas clinicas las
    escribe el observador, porque ``fetalalert_api`` no tiene -- ni debe tener --
    privilegio para crear embarazos.
    """
    id_paciente = referencias["id_perfil_libre"]

    creada = cliente.post(
        RUTA_PACIENTES.format(id_paciente),
        json={"email": EMAIL_CUENTA_PACIENTE, "password": PASSWORD_NUEVA},
        headers=bearer(token_admin),
    )
    assert creada.status_code == 201, creada.text

    embarazos: list[int] = []
    sesiones: list[int] = []
    with engine_observador.begin() as conexion:
        id_clinica = conexion.execute(
            text("SELECT id_clinica FROM operacional.clinica ORDER BY id_clinica LIMIT 1")
        ).scalar_one()
        id_dispositivo = referencias["id_dispositivo"]
        catalogo = conexion.execute(
            text(
                "SELECT :t AS id_tiempo_gest, :s AS id_semaforo"
            ),
            {"t": referencias["id_tiempo_gest"], "s": referencias["id_semaforo"]},
        ).mappings().one()

        for indice, inicio in enumerate(("2023-02-06", "2024-03-11")):
            id_embarazo = conexion.execute(
                text(
                    """
                    INSERT INTO operacional.embarazo
                        (id_paciente, id_clinica, numero_gestas, numero_partos,
                         fecha_inicio, fecha_probable_parto, estado_embarazo,
                         fecha_cierre)
                    VALUES (:p, :c, :g, :pa, :inicio,
                            (CAST(:inicio AS date) + 280), :estado, :cierre)
                    RETURNING id_embarazo
                    """
                ),
                {
                    "p": id_paciente,
                    "c": id_clinica,
                    "g": indice + 1,
                    "pa": indice,
                    "inicio": inicio,
                    "estado": "FINALIZADO" if indice == 0 else "ACTIVO",
                    "cierre": f"{int(inicio[:4]) + 1}-01-05" if indice == 0 else None,
                },
            ).scalar_one()

            id_sesion = conexion.execute(
                text(
                    """
                    INSERT INTO operacional.sesion_monitoreo
                        (id_embarazo, id_dispositivo, tipo_sesion, fecha_inicio,
                         fecha_fin, estado_sesion, origen_dato)
                    VALUES (:e, :d, 'SIGNOS_MATERNOS',
                            CAST(:inicio || ' ' || :hora AS timestamptz),
                            CAST(:inicio || ' 08:37:11' AS timestamptz),
                            'COMPLETADA', 'DISPOSITIVO')
                    RETURNING id_sesion
                    """
                ),
                {
                    "e": id_embarazo,
                    "d": id_dispositivo,
                    "inicio": inicio,
                    "hora": HORA_CENTINELA,
                },
            ).scalar_one()

            conexion.execute(
                text(
                    """
                    INSERT INTO operacional.lectura_biometrica
                        (id_sesion, id_tiempo_gest, id_semaforo,
                         fecha_hora_captura, fecha_hora_sincronizacion,
                         hr_valor, spo2_valor, mov_valor)
                    VALUES (:s, :t, :sem,
                            CAST(:inicio || ' 08:17:11' AS timestamptz),
                            CAST(:inicio || ' 08:27:11' AS timestamptz),
                            :hr, :spo2, NULL)
                    """
                ),
                {
                    "s": id_sesion,
                    "t": catalogo["id_tiempo_gest"],
                    "sem": catalogo["id_semaforo"],
                    "inicio": inicio,
                    "hr": 80 + indice,
                    "spo2": 97,
                },
            )

            embarazos.append(id_embarazo)
            sesiones.append(id_sesion)

    yield {
        "id_usuario": creada.json()["id_usuario"],
        "id_paciente": id_paciente,
        "embarazos": embarazos,
        "sesiones": sesiones,
    }

    # Orden inverso de llaves foraneas. Las lecturas y las sesiones tambien las
    # alcanzaria ``limpieza`` por la hora centinela; borrarlas aqui es lo que
    # permite retirar los embarazos, que son de esta fixture y de nadie mas.
    with engine_observador.begin() as conexion:
        conexion.execute(
            text(
                "DELETE FROM operacional.lectura_biometrica "
                "WHERE id_sesion = ANY(:ids)"
            ),
            {"ids": sesiones},
        )
        conexion.execute(
            text("DELETE FROM operacional.sesion_monitoreo WHERE id_sesion = ANY(:ids)"),
            {"ids": sesiones},
        )
        conexion.execute(
            text("DELETE FROM operacional.embarazo WHERE id_embarazo = ANY(:ids)"),
            {"ids": embarazos},
        )


@pytest.fixture
def token_de_la_gestante_simulada(cliente, episodios_simulados) -> str:
    return iniciar_sesion(cliente, EMAIL_CUENTA_PACIENTE, PASSWORD_NUEVA)


def clave_unica() -> str:
    clave = f"{MARCA}-{uuid.uuid4().hex[:16]}"
    CLAVES_GENERADAS.add(clave)
    return clave


def paquete(referencias, id_embarazo=None) -> dict:
    """Un paquete de ingesta valido para el embarazo de la paciente conectada.

    Sesion de signos maternos, asi que la lectura trae ``hr_valor`` y
    ``spo2_valor`` con ``mov_valor`` en ``null``: el modelo exige una de las dos
    formas y rechaza la mezcla. El catalogo sale de una lectura que el dataset ya
    escribio para este embarazo, de modo que la semana gestacional encaja.
    """
    # El dia sale de la lectura de la que se tomo el catalogo, no del inicio de
    # la asignacion: la regla del dominio exige que ``id_tiempo_gest`` concuerde
    # con la semana en que cae la captura, y ese par ya concuerda en el dataset.
    # Solo se cambia la hora, que es el centinela de esta suite.
    dia = referencias["dia"].isoformat()
    return {
        "id_embarazo": id_embarazo or referencias["id_embarazo"],
        "id_dispositivo": referencias["id_dispositivo"],
        "tipo_sesion": "SIGNOS_MATERNOS",
        # ``COMPLETADA`` porque el paquete trae ``fecha_fin``: una sesion
        # PENDIENTE no puede traerla, y el esquema lo rechaza con un 422.
        "estado_sesion": "COMPLETADA",
        "fecha_inicio": f"{dia}T{HORA_CENTINELA}+00:00",
        "fecha_fin": f"{dia}T08:37:11+00:00",
        "lecturas": [
            {
                "id_tiempo_gest": referencias["id_tiempo_gest"],
                "id_semaforo": referencias["id_semaforo"],
                "fecha_hora_captura": f"{dia}T08:17:11+00:00",
                "fecha_hora_sincronizacion": f"{dia}T08:27:11+00:00",
                "hr_valor": 78,
                "spo2_valor": 97,
                "mov_valor": None,
            }
        ],
    }


def _sin_identificador(mensaje: str) -> str:
    """El mensaje con los numeros sustituidos, para compararlo como plantilla."""
    return re.sub(r"\d+", "N", mensaje)


def enviar(cliente, token, cuerpo, clave):
    return cliente.post(
        RUTA_SESIONES,
        json=cuerpo,
        headers={**bearer(token), "Idempotency-Key": clave},
    )


# ---------------------------------------------------------------------------
# 0. La credencial que ejecuta es la de la API
# ---------------------------------------------------------------------------


def test_el_engine_de_la_aplicacion_es_el_rol_de_la_api(engine_api):
    """Si esta fallara, ninguna de las demas demostraria nada."""
    with engine_api.connect() as conexion:
        actual = conexion.execute(text("SELECT current_user")).scalar_one()

    assert actual == ROL_ESPERADO


def test_el_rol_de_la_aplicacion_no_omite_las_politicas(engine_api):
    with engine_api.connect() as conexion:
        fila = conexion.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, "
                "rolreplication FROM pg_roles WHERE rolname = current_user"
            )
        ).mappings().one()

    assert not any(fila.values())


def test_una_peticion_real_se_atiende_con_esa_credencial(cliente, token_admin):
    """El ``current_user`` de dentro del proceso, no el de una conexion suelta."""
    respuesta = cliente.get(RUTA_YO, headers=bearer(token_admin))

    assert respuesta.status_code == 200
    # La ruta respondio, luego la transaccion que la atendio leyo ``usuario`` y
    # ``rol`` bajo este rol. Que sea el esperado lo fija la prueba de arriba.
    assert respuesta.json()["rol"] == "ADMIN"


# ---------------------------------------------------------------------------
# 1. Resolucion de contexto: PACIENTE y MEDICO
# ---------------------------------------------------------------------------


def test_una_paciente_resuelve_su_contexto_y_no_recibe_403(
    cliente, token_paciente, referencias
):
    """El defecto original: bajo RLS la resolucion devolvia cero filas.

    Se comprueba con la ruta de ingesta porque es la que exige contexto de
    PACIENTE. Un 403 aqui es exactamente la regresion que hubo.
    """
    respuesta = enviar(
        cliente, token_paciente, paquete(referencias), clave_unica()
    )

    assert respuesta.status_code != 403, respuesta.text
    assert respuesta.status_code == 201, respuesta.text


def test_un_medico_se_autentica_como_medico(cliente, token_medico):
    """Autenticacion, no resolucion clinica -- y conviene no confundirlas.

    Esta prueba se llamaba «resuelve su contexto y no recibe 403», y no lo
    demostraba: ``/autenticacion/yo`` depende de ``usuario_actual`` y no pasa
    nunca por ``contexto_actual``, asi que un 200 aqui prueba que el token vale
    y que el rol es el esperado, nada mas. La resolucion clinica del MEDICO se
    demuestra en ``test_el_contexto_de_un_medico_resuelve_su_id_medico``, contra
    la base y con la dependencia real.
    """
    respuesta = cliente.get(RUTA_YO, headers=bearer(token_medico))

    assert respuesta.status_code == 200
    assert respuesta.json()["rol"] == "MEDICO"


def _peticion_simulada() -> Request:
    """Lo minimo que la dependencia necesita de ``Request``: el cliente."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "client": ("127.0.0.1", 0),
        }
    )


def _resolver_con_la_dependencia(engine, principal):
    """Ejecuta la dependencia real de contexto sobre una transaccion de la API.

    No se inventa una ruta productiva para esto. Se invoca ``contexto_actual``,
    que es la misma funcion que las rutas reciben por ``Depends``, con una sesion
    atada al engine de ``fetalalert_api``: eso ejercita el orden que importa
    -- instalar el GUC y *despues* resolver el perfil -- contra las politicas
    reales de ``usuario_paciente`` y ``usuario_medico``.

    La transaccion se revierte siempre: aqui no se escribe nada.
    """
    with Session(bind=engine, autoflush=False) as sesion_bd, Session(
        bind=engine, autoflush=False
    ) as sesion_auditoria:
        try:
            return contexto_actual(
                _peticion_simulada(), principal, sesion_bd, sesion_auditoria
            )
        finally:
            sesion_bd.rollback()
            sesion_auditoria.rollback()


def test_el_contexto_de_un_medico_resuelve_su_id_medico(engine_api, identidades):
    """La evidencia que faltaba: el MEDICO obtiene su perfil, no un 403.

    Conectada como ``fetalalert_api``, con FORCE RLS activo sobre los dos
    puentes. Si la dependencia resolviera antes de instalar el ``id_usuario``
    -- el defecto que esta ronda corrigio --, ``usuario_medico`` devolveria cero
    filas y esto seria un ``HTTPException`` 403.
    """
    medico = identidades["medico"]

    contexto = _resolver_con_la_dependencia(
        engine_api,
        PrincipalAutenticado(id_usuario=medico["id_usuario"], rol=NombreRol.MEDICO),
    )

    assert contexto.rol is NombreRol.MEDICO
    assert contexto.id_medico == medico["id_medico"]
    assert contexto.id_paciente is None
    assert contexto.id_usuario == medico["id_usuario"]


def test_el_contexto_de_una_paciente_resuelve_su_id_paciente(engine_api, identidades):
    """La misma comprobacion por el otro puente."""
    paciente = identidades["paciente"]

    contexto = _resolver_con_la_dependencia(
        engine_api,
        PrincipalAutenticado(id_usuario=paciente["id_usuario"], rol=NombreRol.PACIENTE),
    )

    assert contexto.rol is NombreRol.PACIENTE
    assert contexto.id_paciente == paciente["id_paciente"]
    assert contexto.id_medico is None


def test_un_admin_resuelve_un_contexto_sin_perfil_clinico(engine_api, identidades):
    """ADMIN es una identidad valida con alcance clinico vacio, no un rechazo."""
    contexto = _resolver_con_la_dependencia(
        engine_api,
        PrincipalAutenticado(
            id_usuario=identidades["admin"]["id_usuario"], rol=NombreRol.ADMIN
        ),
    )

    assert contexto.rol is NombreRol.ADMIN
    assert (contexto.id_paciente, contexto.id_medico) == (None, None)


@pytest.mark.parametrize("rol", ["MEDICO", "PACIENTE"])
def test_sin_el_guc_instalado_el_puente_no_devuelve_nada(engine_api, identidades, rol):
    """El control negativo, y la razon por la que el orden es el orden.

    ``resolver_contexto`` a secas -- sin haber instalado el ``id_usuario`` en la
    transaccion -- es exactamente lo que hacia la dependencia antes de esta
    ronda. Bajo un rol restringido no encuentra el vinculo y se niega. Que esta
    prueba pase es lo que hace que las tres de arriba signifiquen algo: no
    resuelven porque las politicas sean permisivas, sino porque la identidad
    esta instalada.
    """
    clave = "medico" if rol == "MEDICO" else "paciente"
    sujeto = identidades[clave]

    with Session(bind=engine_api, autoflush=False) as sesion_bd:
        with pytest.raises(ContextoNoResoluble):
            resolver_contexto(
                sesion_bd,
                PrincipalAutenticado(
                    id_usuario=sujeto["id_usuario"], rol=NombreRol[rol]
                ),
            )
        sesion_bd.rollback()


def test_el_contexto_de_la_paciente_es_el_suyo_y_no_otro(
    cliente, token_paciente, referencias, engine_observador
):
    """Escribe una sesion y la fila queda bajo *su* embarazo, no bajo otro."""
    respuesta = enviar(cliente, token_paciente, paquete(referencias), clave_unica())
    assert respuesta.status_code == 201, respuesta.text

    id_sesion = respuesta.json()["id_sesion"]
    duena = escalar(
        engine_observador,
        "SELECT e.id_paciente FROM operacional.sesion_monitoreo s "
        "JOIN operacional.embarazo e ON e.id_embarazo = s.id_embarazo "
        "WHERE s.id_sesion = :s",
        s=id_sesion,
    )

    assert duena == referencias["id_paciente"]


# ---------------------------------------------------------------------------
# 2. Ingesta: propia, ajena, inexistente e idempotencia
# ---------------------------------------------------------------------------


def test_la_ingesta_de_su_propio_embarazo_se_acepta(
    cliente, token_paciente, referencias
):
    respuesta = enviar(cliente, token_paciente, paquete(referencias), clave_unica())

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.headers["Idempotency-Replayed"] == "false"
    assert len(respuesta.json()["ids_lectura"]) == 1


def test_un_embarazo_ajeno_responde_404(cliente, token_paciente, referencias):
    respuesta = enviar(
        cliente,
        token_paciente,
        paquete(referencias, referencias["id_embarazo_ajeno"]),
        clave_unica(),
    )

    assert respuesta.status_code == 404, respuesta.text


def test_un_embarazo_inexistente_responde_lo_mismo_que_uno_ajeno(
    cliente, token_paciente, referencias
):
    """Distinguirlos convertiria la ruta en un oraculo de que embarazos existen."""
    ajeno = enviar(
        cliente,
        token_paciente,
        paquete(referencias, referencias["id_embarazo_ajeno"]),
        clave_unica(),
    )
    inexistente = enviar(
        cliente,
        token_paciente,
        paquete(referencias, referencias["id_embarazo_inexistente"]),
        clave_unica(),
    )

    assert ajeno.status_code == inexistente.status_code == 404
    # Se comparan como plantilla: lo unico que difiere es el identificador que
    # la propia peticion envio, que quien llama ya conoce. Lo que no puede
    # diferir es la frase, porque ahi estaria la diferencia entre «no existe» y
    # «existe y no es tuyo».
    assert _sin_identificador(ajeno.json()["detail"]) == _sin_identificador(
        inexistente.json()["detail"]
    )


def test_un_intento_contra_un_embarazo_ajeno_no_consume_la_clave(
    cliente, token_paciente, referencias, engine_observador
):
    """La autorizacion va antes de reclamar: la clave queda libre para el reintento."""
    clave = clave_unica()

    rechazada = enviar(
        cliente,
        token_paciente,
        paquete(referencias, referencias["id_embarazo_ajeno"]),
        clave,
    )
    assert rechazada.status_code == 404

    reclamaciones = escalar(
        engine_observador,
        "SELECT count(*) FROM operacional.idempotencia_solicitud WHERE clave = :c",
        c=clave,
    )
    assert reclamaciones == 0

    corregida = enviar(cliente, token_paciente, paquete(referencias), clave)
    assert corregida.status_code == 201, corregida.text


def test_el_reenvio_de_la_misma_clave_es_un_replay(
    cliente, token_paciente, referencias, engine_observador
):
    clave = clave_unica()
    cuerpo = paquete(referencias)

    primera = enviar(cliente, token_paciente, cuerpo, clave)
    segunda = enviar(cliente, token_paciente, cuerpo, clave)

    assert primera.status_code == segunda.status_code == 201
    assert primera.headers["Idempotency-Replayed"] == "false"
    assert segunda.headers["Idempotency-Replayed"] == "true"
    assert primera.json() == segunda.json()

    sesiones = escalar(
        engine_observador,
        "SELECT count(*) FROM operacional.sesion_monitoreo WHERE id_sesion = :s",
        s=primera.json()["id_sesion"],
    )
    assert sesiones == 1


def test_un_cuerpo_distinto_bajo_la_misma_clave_es_409(
    cliente, token_paciente, referencias
):
    clave = clave_unica()
    cuerpo = paquete(referencias)

    primera = enviar(cliente, token_paciente, cuerpo, clave)
    otro = dict(cuerpo, lecturas=[dict(cuerpo["lecturas"][0], hr_valor=99)])
    segunda = enviar(cliente, token_paciente, otro, clave)

    assert primera.status_code == 201
    assert segunda.status_code == 409


def test_un_medico_no_puede_ingestar(cliente, token_medico, referencias):
    """La ingesta es de la gestante. Un MEDICO lee; no crea sesiones."""
    respuesta = enviar(cliente, token_medico, paquete(referencias), clave_unica())

    assert respuesta.status_code == 403


# ---------------------------------------------------------------------------
# 3. Provision de cuentas: PACIENTE y MEDICO
# ---------------------------------------------------------------------------


def test_provisionar_una_cuenta_paciente(
    cliente, token_admin, referencias, engine_observador
):
    """El segundo defecto: sin contexto instalado, ``pol_provision`` rechazaba."""
    if referencias["id_perfil_libre"] is None:
        pytest.skip("el dataset no deja ningun perfil de paciente sin cuenta")

    email = EMAIL_CUENTA_PACIENTE
    respuesta = cliente.post(
        RUTA_PACIENTES.format(referencias["id_perfil_libre"]),
        json={"email": email, "password": PASSWORD_NUEVA},
        headers=bearer(token_admin),
    )

    assert respuesta.status_code == 201, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["rol"] == "PACIENTE"
    assert cuerpo["activo"] is True

    # El puente quedo escrito: lo mira el observador, porque la API no puede
    # leer un vinculo que no es el de la cuenta conectada.
    vinculo = escalar(
        engine_observador,
        "SELECT id_paciente FROM operacional.usuario_paciente WHERE id_usuario = :u",
        u=cuerpo["id_usuario"],
    )
    assert vinculo == referencias["id_perfil_libre"]


def test_provisionar_una_cuenta_medico(
    cliente, token_admin, referencias, engine_observador
):
    if referencias["id_medico_libre"] is None:
        pytest.skip("el dataset no deja ningun perfil medico sin cuenta")

    email = EMAIL_CUENTA_MEDICO
    respuesta = cliente.post(
        RUTA_MEDICOS.format(referencias["id_medico_libre"]),
        json={"email": email, "password": PASSWORD_NUEVA},
        headers=bearer(token_admin),
    )

    assert respuesta.status_code == 201, respuesta.text
    assert respuesta.json()["rol"] == "MEDICO"

    vinculo = escalar(
        engine_observador,
        "SELECT id_medico FROM operacional.usuario_medico WHERE id_usuario = :u",
        u=respuesta.json()["id_usuario"],
    )
    assert vinculo == referencias["id_medico_libre"]


def test_la_cuenta_provisionada_puede_iniciar_sesion(
    cliente, token_admin, referencias
):
    """El recorrido completo: se crea por HTTP y se autentica por HTTP."""
    if referencias["id_perfil_libre"] is None:
        pytest.skip("el dataset no deja ningun perfil de paciente sin cuenta")

    email = EMAIL_CUENTA_PACIENTE
    creada = cliente.post(
        RUTA_PACIENTES.format(referencias["id_perfil_libre"]),
        json={"email": email, "password": PASSWORD_NUEVA},
        headers=bearer(token_admin),
    )
    assert creada.status_code == 201, creada.text

    token = iniciar_sesion(cliente, email, PASSWORD_NUEVA)
    yo = cliente.get(RUTA_YO, headers=bearer(token))

    assert yo.status_code == 200
    assert yo.json()["rol"] == "PACIENTE"


def test_un_perfil_ya_vinculado_se_rechaza(cliente, token_admin, referencias):
    """El helper responde ``vinculado``, y eso es un 409, no un 404."""
    respuesta = cliente.post(
        RUTA_PACIENTES.format(referencias["id_perfil_tomado"]),
        json={"email": EMAIL_CUENTA_DUPLICADA, "password": PASSWORD_NUEVA},
        headers=bearer(token_admin),
    )

    assert respuesta.status_code == 409, respuesta.text


def test_un_perfil_inexistente_se_rechaza_con_404(cliente, token_admin):
    respuesta = cliente.post(
        RUTA_PACIENTES.format(999_000),
        json={"email": EMAIL_CUENTA_FANTASMA, "password": PASSWORD_NUEVA},
        headers=bearer(token_admin),
    )

    assert respuesta.status_code == 404, respuesta.text


def test_una_paciente_no_puede_provisionar(cliente, token_paciente, referencias):
    respuesta = cliente.post(
        RUTA_PACIENTES.format(referencias["id_perfil_tomado"]),
        json={"email": EMAIL_CUENTA_USURPADA, "password": PASSWORD_NUEVA},
        headers=bearer(token_paciente),
    )

    assert respuesta.status_code == 403


# ---------------------------------------------------------------------------
# 4. Activacion y desactivacion
# ---------------------------------------------------------------------------


@pytest.fixture
def cuenta_recien_creada(cliente, token_admin, referencias):
    """Una cuenta de esta suite, para no tocar el estado del dataset."""
    if referencias["id_perfil_libre"] is None:
        pytest.skip("el dataset no deja ningun perfil de paciente sin cuenta")

    email = EMAIL_CUENTA_CICLO
    respuesta = cliente.post(
        RUTA_PACIENTES.format(referencias["id_perfil_libre"]),
        json={"email": email, "password": PASSWORD_NUEVA},
        headers=bearer(token_admin),
    )
    assert respuesta.status_code == 201, respuesta.text
    return {"id_usuario": respuesta.json()["id_usuario"], "email": email}


def test_desactivar_una_cuenta(
    cliente, token_admin, cuenta_recien_creada, engine_observador
):
    respuesta = cliente.patch(
        RUTA_ESTADO.format(cuenta_recien_creada["id_usuario"]),
        json={"activo": False},
        headers=bearer(token_admin),
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["activo"] is False

    activo = escalar(
        engine_observador,
        "SELECT activo FROM operacional.usuario WHERE id_usuario = :u",
        u=cuenta_recien_creada["id_usuario"],
    )
    assert activo is False


def test_una_cuenta_desactivada_no_obtiene_token(
    cliente, token_admin, cuenta_recien_creada
):
    cliente.patch(
        RUTA_ESTADO.format(cuenta_recien_creada["id_usuario"]),
        json={"activo": False},
        headers=bearer(token_admin),
    )

    respuesta = cliente.post(
        RUTA_TOKEN,
        json={"email": cuenta_recien_creada["email"], "password": PASSWORD_NUEVA},
    )

    assert respuesta.status_code == 401


def test_reactivar_devuelve_el_acceso(cliente, token_admin, cuenta_recien_creada):
    ruta = RUTA_ESTADO.format(cuenta_recien_creada["id_usuario"])
    cliente.patch(ruta, json={"activo": False}, headers=bearer(token_admin))
    reactivada = cliente.patch(ruta, json={"activo": True}, headers=bearer(token_admin))

    assert reactivada.status_code == 200
    assert reactivada.json()["activo"] is True

    token = iniciar_sesion(cliente, cuenta_recien_creada["email"], PASSWORD_NUEVA)
    assert cliente.get(RUTA_YO, headers=bearer(token)).status_code == 200


def test_una_paciente_no_puede_cambiar_estados(
    cliente, token_paciente, cuenta_recien_creada
):
    respuesta = cliente.patch(
        RUTA_ESTADO.format(cuenta_recien_creada["id_usuario"]),
        json={"activo": False},
        headers=bearer(token_paciente),
    )

    assert respuesta.status_code == 403


# ---------------------------------------------------------------------------
# 5. ADMIN no enumera
# ---------------------------------------------------------------------------


def test_el_admin_no_lee_ninguna_fila_clinica(cliente, token_admin, referencias):
    """Alcance administrativo, alcance clinico vacio, por la via HTTP.

    La ingesta es la unica ruta que llega a lo clinico, y a un ADMIN le responde
    403 por rol. Lo que esta prueba fija es que ni siquiera cambiando de ruta
    hay un camino: no existe endpoint que le devuelva embarazos ni lecturas.
    """
    respuesta = enviar(cliente, token_admin, paquete(referencias), clave_unica())

    assert respuesta.status_code == 403


def test_el_admin_no_enumera_los_puentes_por_sql(engine_api, referencias):
    """Con el contexto del ADMIN instalado, los dos puentes estan vacios.

    Es la contraparte SQL de la politica: ``pol_propia`` ya no tiene rama de
    ADMIN, asi que la credencial de la API con identidad administrativa no ve
    un solo vinculo -- ni el suyo, porque un ADMIN no tiene.
    """
    with engine_api.connect() as conexion:
        # El ADMIN puede leer ``usuario`` y ``rol``: no llevan RLS. Lo que no
        # puede es pasar de ahi a los puentes.
        id_admin = conexion.execute(
            text(
                "SELECT u.id_usuario FROM operacional.usuario u "
                "JOIN operacional.rol r ON r.id_rol = u.id_rol "
                "WHERE u.email = :email"
            ),
            {"email": EMAIL_ADMIN},
        ).scalar_one()
        # El SELECT de arriba ya abrio transaccion por autobegin. Se cierra antes
        # de instalar el contexto, porque ``set_config(..., true)`` es local a la
        # transaccion y tiene que vivir en la misma que las consultas de abajo.
        conexion.rollback()

        conexion.execute(
            text("SELECT set_config('fetalalert.id_usuario', :v, true)"),
            {"v": str(id_admin)},
        )
        pacientes = conexion.execute(
            text("SELECT count(*) FROM operacional.usuario_paciente")
        ).scalar_one()
        medicos = conexion.execute(
            text("SELECT count(*) FROM operacional.usuario_medico")
        ).scalar_one()
        conexion.rollback()

    assert (pacientes, medicos) == (0, 0)


def test_el_admin_no_puede_leer_los_perfiles_clinicos(engine_api):
    """La proteccion aqui no es una politica: es la ausencia de privilegio."""
    from sqlalchemy.exc import ProgrammingError

    with engine_api.connect() as conexion:
        with pytest.raises(ProgrammingError, match="permission denied"):
            conexion.execute(text("SELECT count(*) FROM operacional.paciente"))


def test_la_provision_no_revela_si_un_perfil_existe(cliente, token_paciente):
    """Sin contexto administrativo, existente e inexistente responden igual.

    El helper devuelve ``sin_autorizacion`` en los dos casos, pero la guardia de
    rol ni siquiera lo llega a invocar: el 403 es anterior y es el mismo.
    """
    existente = cliente.post(
        RUTA_PACIENTES.format(1),
        json={"email": EMAIL_SONDA_A, "password": PASSWORD_NUEVA},
        headers=bearer(token_paciente),
    )
    inexistente = cliente.post(
        RUTA_PACIENTES.format(999_000),
        json={"email": EMAIL_SONDA_B, "password": PASSWORD_NUEVA},
        headers=bearer(token_paciente),
    )

    assert existente.status_code == inexistente.status_code == 403
    assert existente.json() == inexistente.json()


# ---------------------------------------------------------------------------
# 6. Lectura clinica: los dos alcances aprobados (subfase 4)
# ---------------------------------------------------------------------------
#
# Todo lo que sigue recorre las rutas reales, con token real, conectado como
# ``fetalalert_api``. Ninguna prueba consulta la base para decidir lo que la
# ruta deberia devolver: los valores esperados salen de ``alcance_clinico``, que
# los lee una vez con el observador, y la ruta se juzga contra ellos.


def ids(respuesta, clave="id_embarazo"):
    return sorted(fila[clave] for fila in respuesta.json())


# --- 6.1 PACIENTE ----------------------------------------------------------


def test_una_paciente_lee_sus_embarazos_incluidos_los_finalizados(
    cliente, token_paciente, alcance_clinico
):
    """El alcance aprobado incluye los episodios cerrados.

    Un embarazo terminado sigue siendo el historial clinico de quien lo vivio.
    La cuenta canonica de esta prueba tiene precisamente uno finalizado, asi que
    si la ruta filtrara por «en curso» devolveria una lista vacia.
    """
    respuesta = cliente.get(RUTA_EMBARAZOS, headers=bearer(token_paciente))

    assert respuesta.status_code == 200, respuesta.text
    assert ids(respuesta) == alcance_clinico["de_la_paciente"]
    assert alcance_clinico["estado_del_primero_de_la_paciente"] != "ACTIVO"
    assert any(fila["estado_embarazo"] != "ACTIVO" for fila in respuesta.json())


def test_el_resumen_del_embarazo_no_lleva_identificadores_de_persona(
    cliente, token_paciente
):
    """Lo que el esquema omite es tan contrato como lo que incluye."""
    respuesta = cliente.get(RUTA_EMBARAZOS, headers=bearer(token_paciente))

    [fila] = respuesta.json()[:1]
    assert set(fila) == {
        "id_embarazo",
        "fecha_inicio",
        "fecha_probable_parto",
        "estado_embarazo",
        "fecha_cierre",
    }
    assert "id_paciente" not in fila and "id_clinica" not in fila
    assert "numero_gestas" not in fila and "numero_partos" not in fila


def test_una_paciente_lee_las_sesiones_de_su_embarazo(
    cliente, token_paciente, alcance_clinico, engine_observador
):
    id_embarazo = alcance_clinico["de_la_paciente"][0]

    respuesta = cliente.get(
        RUTA_SESIONES_DE.format(id_embarazo), headers=bearer(token_paciente)
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json(), "el dataset no dio sesiones a este embarazo"
    # El resumen no devuelve ``id_embarazo``: quien pregunta lo puso en la ruta.
    assert "id_embarazo" not in respuesta.json()[0]
    # Que sean las de *este* episodio se comprueba contra el catalogo, no contra
    # un campo que la propia respuesta podria repetir mal.
    esperadas = observar(
        engine_observador,
        "SELECT id_sesion FROM operacional.sesion_monitoreo WHERE id_embarazo = :e",
        e=id_embarazo,
    )
    assert ids(respuesta, "id_sesion") == sorted(f["id_sesion"] for f in esperadas)


def test_una_paciente_lee_las_lecturas_de_su_sesion(
    cliente, token_paciente, alcance_clinico, engine_observador
):
    id_embarazo = alcance_clinico["de_la_paciente"][0]
    sesiones = cliente.get(
        RUTA_SESIONES_DE.format(id_embarazo), headers=bearer(token_paciente)
    ).json()

    respuesta = cliente.get(
        RUTA_LECTURAS_DE.format(sesiones[0]["id_sesion"]), headers=bearer(token_paciente)
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()

    [lectura] = respuesta.json()[:1]
    assert set(lectura) == {
        "id_lectura",
        "fecha_hora_captura",
        "codigo_semaforo",
        "semana_gestacion",
        "hr_valor",
        "spo2_valor",
        "mov_valor",
    }
    # Valores de catalogo, no claves tecnicas: se comparan con el catalogo real.
    codigos = {
        fila["codigo_nivel"]
        for fila in observar(
            engine_observador, "SELECT codigo_nivel FROM operacional.semaforo"
        )
    }
    assert lectura["codigo_semaforo"] in codigos
    assert 1 <= lectura["semana_gestacion"] <= 42


def test_una_paciente_no_lee_el_embarazo_de_otra(
    cliente, token_paciente, alcance_clinico
):
    ajeno = alcance_clinico["del_medico"][0]
    assert ajeno not in alcance_clinico["de_la_paciente"]

    respuesta = cliente.get(
        RUTA_SESIONES_DE.format(ajeno), headers=bearer(token_paciente)
    )

    assert respuesta.status_code == 404, respuesta.text


# --- 6.2 Separacion entre episodios ----------------------------------------


def test_dos_episodios_de_la_misma_gestante_no_se_mezclan(
    cliente, token_de_la_gestante_simulada, episodios_simulados
):
    """El punto del contrato: «su historial» no es una sola serie.

    La gestante ficticia de esta suite tiene dos embarazos, cada uno con su
    sesion. Los dos aparecen en el listado, y pedir las sesiones de uno nunca
    devuelve las del otro: para ver el segundo hay que nombrarlo.
    """
    listado = cliente.get(RUTA_EMBARAZOS, headers=bearer(token_de_la_gestante_simulada))

    assert listado.status_code == 200, listado.text
    assert ids(listado) == sorted(episodios_simulados["embarazos"])

    # La demostracion no se apoya en que la respuesta repita el ``id_embarazo``
    # -- ya no lo devuelve --, sino en algo mas fuerte: pedir el episodio A
    # devuelve **exactamente** la sesion de A, y la de B no aparece por ningun
    # lado. Si las series se mezclaran, cada listado traeria las dos.
    for indice, id_embarazo in enumerate(episodios_simulados["embarazos"]):
        propia = episodios_simulados["sesiones"][indice]
        ajena = episodios_simulados["sesiones"][1 - indice]

        sesiones = cliente.get(
            RUTA_SESIONES_DE.format(id_embarazo),
            headers=bearer(token_de_la_gestante_simulada),
        )

        assert sesiones.status_code == 200, sesiones.text
        devueltas = [fila["id_sesion"] for fila in sesiones.json()]
        assert devueltas == [propia]
        assert ajena not in devueltas


# --- 6.3 MEDICO ------------------------------------------------------------


def test_un_medico_lee_exactamente_los_embarazos_que_sigue_hoy(
    cliente, token_medico, alcance_clinico
):
    """Ni uno mas. La lista se compara con la del catalogo, no con un numero."""
    respuesta = cliente.get(RUTA_EMBARAZOS, headers=bearer(token_medico))

    assert respuesta.status_code == 200, respuesta.text
    assert ids(respuesta) == alcance_clinico["del_medico"]


def test_compartir_clinica_no_concede_acceso_clinico(
    cliente, token_medico, alcance_clinico
):
    """``medico_clinica`` no aparece ni en la consulta ni en la politica.

    El medico de esta prueba esta afiliado a la clinica del embarazo y no lo
    sigue. La afiliacion es una relacion administrativa; el acceso clinico nace
    de una asignacion directa y de ninguna otra cosa.
    """
    por_clinica = alcance_clinico["solo_por_clinica"]
    assert por_clinica not in alcance_clinico["del_medico"]

    listado = cliente.get(RUTA_EMBARAZOS, headers=bearer(token_medico))
    directo = cliente.get(
        RUTA_SESIONES_DE.format(por_clinica), headers=bearer(token_medico)
    )

    assert por_clinica not in ids(listado)
    assert directo.status_code == 404, directo.text


def test_una_asignacion_terminada_no_concede_acceso(
    cliente, token_medico, alcance_clinico
):
    """La asignacion existe en la tabla, pero no esta vigente hoy."""
    terminada = alcance_clinico["asignacion_terminada"]
    assert terminada not in alcance_clinico["del_medico"]

    listado = cliente.get(RUTA_EMBARAZOS, headers=bearer(token_medico))
    directo = cliente.get(
        RUTA_SESIONES_DE.format(terminada), headers=bearer(token_medico)
    )

    assert terminada not in ids(listado)
    assert directo.status_code == 404, directo.text


def test_dentro_del_embarazo_asignado_el_medico_lee_todo_el_historial(
    cliente, token_medico, alcance_clinico
):
    """Una asignacion es permiso para seguir el episodio, no un tramo de el.

    Las sesiones que la ruta devuelve son **todas** las del embarazo, incluidas
    las anteriores a la fecha de asignacion. Medio historial clinico no es un
    historial clinico.
    """
    id_embarazo = alcance_clinico["del_medico"][0]

    respuesta = cliente.get(
        RUTA_SESIONES_DE.format(id_embarazo), headers=bearer(token_medico)
    )

    assert respuesta.status_code == 200, respuesta.text
    assert ids(respuesta, "id_sesion") == alcance_clinico["sesiones_del_medico"]


def test_un_medico_lee_las_lecturas_de_una_sesion_asignada(
    cliente, token_medico, alcance_clinico
):
    respuesta = cliente.get(
        RUTA_LECTURAS_DE.format(alcance_clinico["sesiones_del_medico"][0]),
        headers=bearer(token_medico),
    )

    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()


def test_un_medico_no_lee_las_lecturas_de_una_sesion_no_asignada(
    cliente, token_medico, token_paciente, alcance_clinico
):
    """El cruce por la ruta de lecturas, que no nombra el embarazo."""
    id_embarazo = alcance_clinico["de_la_paciente"][0]
    ajena = cliente.get(
        RUTA_SESIONES_DE.format(id_embarazo), headers=bearer(token_paciente)
    ).json()[0]["id_sesion"]

    respuesta = cliente.get(RUTA_LECTURAS_DE.format(ajena), headers=bearer(token_medico))

    assert respuesta.status_code == 404, respuesta.text


# --- 6.4 ADMIN -------------------------------------------------------------


@pytest.mark.parametrize(
    "ruta",
    [RUTA_EMBARAZOS, RUTA_SESIONES_DE.format(1), RUTA_LECTURAS_DE.format(1)],
)
def test_el_admin_no_tiene_lectura_clinica_por_http(cliente, token_admin, ruta):
    """Las tres rutas, una por una. ADMIN no esta en la guardia."""
    respuesta = cliente.get(ruta, headers=bearer(token_admin))

    assert respuesta.status_code == 403, respuesta.text


# --- 6.5 Ajeno e inexistente responden lo mismo ----------------------------


def test_un_embarazo_ajeno_y_uno_inexistente_responden_igual(
    cliente, token_medico, alcance_clinico
):
    ajeno = cliente.get(
        RUTA_SESIONES_DE.format(alcance_clinico["solo_por_clinica"]),
        headers=bearer(token_medico),
    )
    inexistente = cliente.get(
        RUTA_SESIONES_DE.format(alcance_clinico["embarazo_inexistente"]),
        headers=bearer(token_medico),
    )

    assert ajeno.status_code == inexistente.status_code == 404
    assert _sin_identificador(ajeno.json()["detail"]) == _sin_identificador(
        inexistente.json()["detail"]
    )


def test_una_sesion_ajena_y_una_inexistente_responden_igual(
    cliente, token_medico, token_paciente, alcance_clinico
):
    """Y el mensaje habla de la sesion, no del embarazo.

    Cambiar de sujeto seria en si mismo la senal de que el embarazo existe.
    """
    id_embarazo = alcance_clinico["de_la_paciente"][0]
    ajena = cliente.get(
        RUTA_SESIONES_DE.format(id_embarazo), headers=bearer(token_paciente)
    ).json()[0]["id_sesion"]

    respuesta_ajena = cliente.get(
        RUTA_LECTURAS_DE.format(ajena), headers=bearer(token_medico)
    )
    respuesta_inexistente = cliente.get(
        RUTA_LECTURAS_DE.format(alcance_clinico["sesion_inexistente"]),
        headers=bearer(token_medico),
    )

    assert respuesta_ajena.status_code == respuesta_inexistente.status_code == 404
    assert _sin_identificador(
        respuesta_ajena.json()["detail"]
    ) == _sin_identificador(respuesta_inexistente.json()["detail"])
    assert "sesion" in respuesta_ajena.json()["detail"].lower()
    assert "embarazo" not in respuesta_ajena.json()["detail"].lower()


# --- 6.6 Cuenta inactiva ---------------------------------------------------


def test_una_cuenta_desactivada_pierde_la_lectura_clinica(
    cliente, token_admin, token_de_la_gestante_simulada, episodios_simulados
):
    """El token sigue siendo criptograficamente valido; la cuenta ya no.

    SCRUM-70 revalida el estado en cada peticion, asi que desactivar corta el
    acceso sin esperar a que el token expire. Aqui se comprueba sobre una ruta
    clinica, que es donde mas importa.
    """
    antes = cliente.get(RUTA_EMBARAZOS, headers=bearer(token_de_la_gestante_simulada))
    assert antes.status_code == 200

    cliente.patch(
        RUTA_ESTADO.format(episodios_simulados["id_usuario"]),
        json={"activo": False},
        headers=bearer(token_admin),
    )

    despues = cliente.get(RUTA_EMBARAZOS, headers=bearer(token_de_la_gestante_simulada))

    assert despues.status_code == 401, despues.text


# --- 6.7 Ausencia y manipulacion del contexto ------------------------------


@pytest.mark.parametrize(
    "instalado",
    [None, "", "   ", "no-soy-un-numero", "0", "-1", "99999999999999999999", "1; DROP"],
)
def test_sin_contexto_valido_la_consulta_clinica_no_devuelve_nada(
    engine_api, instalado
):
    """La capa de PostgreSQL, sin pasar por la aplicacion.

    Es la otra mitad de la defensa: aunque una consulta llegara a la base sin
    identidad o con una manipulada, las politicas devuelven cero filas y ningun
    valor corrupto revienta el cast. Se prueba contra las tres tablas que las
    rutas de esta subfase leen.
    """
    with engine_api.connect() as conexion:
        if instalado is not None:
            conexion.execute(
                text("SELECT set_config('fetalalert.id_usuario', :v, true)"),
                {"v": instalado},
            )
        conteos = [
            conexion.execute(
                text(f"SELECT count(*) FROM operacional.{tabla}")
            ).scalar_one()
            for tabla in ("embarazo", "sesion_monitoreo", "lectura_biometrica")
        ]
        conexion.rollback()

    assert conteos == [0, 0, 0]


def test_un_contexto_sin_perfil_no_alcanza_ningun_embarazo(engine_api, identidades):
    """Y la capa de aplicacion, con el servicio real.

    Un ``ContextoClinico`` de ADMIN -- identidad valida, alcance clinico vacio --
    no produce un predicado permisivo por omision: produce una lista vacia.
    """
    contexto = ContextoClinico(
        id_usuario=identidades["admin"]["id_usuario"], rol=NombreRol.ADMIN
    )

    with Session(bind=engine_api, autoflush=False) as sesion_bd:
        assert listar_embarazos(sesion_bd, contexto) == []
        with pytest.raises(RecursoClinicoInexistente):
            exigir_embarazo_visible(sesion_bd, contexto, 1)
        sesion_bd.rollback()


def test_el_servicio_no_acepta_un_perfil_que_el_contexto_no_trae(
    engine_api, identidades
):
    """Un contexto de MEDICO sin ``id_medico`` no ve nada, en vez de verlo todo.

    El fallo cerrado tiene que estar en la rama, no en que la consulta resulte
    vacia por casualidad: un ``None`` mal manejado habria producido
    ``id_medico IS NULL``, que no filtra nada.
    """
    contexto = ContextoClinico(
        id_usuario=identidades["medico"]["id_usuario"], rol=NombreRol.MEDICO
    )

    with Session(bind=engine_api, autoflush=False) as sesion_bd:
        assert listar_embarazos(sesion_bd, contexto) == []
        sesion_bd.rollback()


# ---------------------------------------------------------------------------
# 7. Cada denegacion clinica deja rastro (subfase 4)
# ---------------------------------------------------------------------------
#
# La respuesta externa no cambia -- ajeno e inexistente siguen siendo el mismo
# 404 con la misma frase --, y lo que cambia esta del otro lado: una entrada por
# denegacion, con quien pregunto, desde donde, por que entidad y con que
# identificador. Nada de lo que la base respondio.


def denegaciones(engine_observador, desde: int) -> list[dict]:
    """Las entradas de ``ACCESO_CLINICO_DENEGADO`` posteriores a una marca."""
    return [
        dict(fila)
        for fila in observar(
            engine_observador,
            """
            SELECT id_log, id_usuario, accion, nombre_entidad_afectada,
                   id_entidad_afectada, ip_origen
            FROM operacional.auditoria_log
            WHERE id_log > :desde AND accion = :accion
            ORDER BY id_log
            """,
            desde=desde,
            accion="ACCESO_CLINICO_DENEGADO",
        )
    ]


@pytest.fixture
def marca_de_auditoria(engine_observador) -> int:
    return escalar(
        engine_observador,
        "SELECT coalesce(max(id_log), 0) FROM operacional.auditoria_log",
    )


def test_un_embarazo_ajeno_deja_una_entrada_de_denegacion(
    cliente, token_paciente, alcance_clinico, engine_observador,
    marca_de_auditoria, identidades
):
    ajeno = alcance_clinico["del_medico"][0]

    respuesta = cliente.get(
        RUTA_SESIONES_DE.format(ajeno), headers=bearer(token_paciente)
    )
    assert respuesta.status_code == 404

    [entrada] = denegaciones(engine_observador, marca_de_auditoria)
    assert entrada["id_usuario"] == identidades["paciente"]["id_usuario"]
    assert entrada["nombre_entidad_afectada"] == "embarazo"
    assert entrada["id_entidad_afectada"] == str(ajeno)
    assert entrada["ip_origen"]


def test_un_embarazo_inexistente_deja_la_misma_clase_de_entrada(
    cliente, token_paciente, alcance_clinico, engine_observador, marca_de_auditoria
):
    """La respuesta externa es indistinguible; la traza registra los dos."""
    inexistente = alcance_clinico["embarazo_inexistente"]

    respuesta = cliente.get(
        RUTA_SESIONES_DE.format(inexistente), headers=bearer(token_paciente)
    )
    assert respuesta.status_code == 404

    [entrada] = denegaciones(engine_observador, marca_de_auditoria)
    assert entrada["nombre_entidad_afectada"] == "embarazo"
    assert entrada["id_entidad_afectada"] == str(inexistente)


def test_una_sesion_ajena_deja_una_entrada_que_nombra_la_sesion(
    cliente, token_medico, token_paciente, alcance_clinico, engine_observador,
    marca_de_auditoria, identidades
):
    """La entidad es la que la ruta nombra, no el embarazo que hay detras."""
    ajena = cliente.get(
        RUTA_SESIONES_DE.format(alcance_clinico["de_la_paciente"][0]),
        headers=bearer(token_paciente),
    ).json()[0]["id_sesion"]

    respuesta = cliente.get(RUTA_LECTURAS_DE.format(ajena), headers=bearer(token_medico))
    assert respuesta.status_code == 404

    [entrada] = denegaciones(engine_observador, marca_de_auditoria)
    assert entrada["id_usuario"] == identidades["medico"]["id_usuario"]
    assert entrada["nombre_entidad_afectada"] == "sesion_monitoreo"
    assert entrada["id_entidad_afectada"] == str(ajena)


def test_una_sesion_inexistente_deja_la_misma_clase_de_entrada(
    cliente, token_medico, alcance_clinico, engine_observador, marca_de_auditoria
):
    inexistente = alcance_clinico["sesion_inexistente"]

    respuesta = cliente.get(
        RUTA_LECTURAS_DE.format(inexistente), headers=bearer(token_medico)
    )
    assert respuesta.status_code == 404

    [entrada] = denegaciones(engine_observador, marca_de_auditoria)
    assert entrada["nombre_entidad_afectada"] == "sesion_monitoreo"
    assert entrada["id_entidad_afectada"] == str(inexistente)


def test_una_denegacion_deja_una_entrada_y_no_dos(
    cliente, token_paciente, alcance_clinico, engine_observador, marca_de_auditoria
):
    """Una peticion, una entrada. Y tres peticiones, tres.

    El camino de las lecturas comprueba el embarazo por dentro; si esa
    comprobacion auditara ademas de la de la ruta, cada denegacion de sesion
    dejaria dos filas y la traza contaria mal los intentos.
    """
    ajeno = alcance_clinico["del_medico"][0]

    for _ in range(3):
        assert (
            cliente.get(
                RUTA_SESIONES_DE.format(ajeno), headers=bearer(token_paciente)
            ).status_code
            == 404
        )

    assert len(denegaciones(engine_observador, marca_de_auditoria)) == 3


def test_una_lectura_exitosa_no_deja_entrada(
    cliente, token_paciente, alcance_clinico, engine_observador, marca_de_auditoria
):
    """Una traza que creciera con cada consulta legitima sepultaria las negativas."""
    id_embarazo = alcance_clinico["de_la_paciente"][0]

    assert cliente.get(RUTA_EMBARAZOS, headers=bearer(token_paciente)).status_code == 200
    sesiones = cliente.get(
        RUTA_SESIONES_DE.format(id_embarazo), headers=bearer(token_paciente)
    )
    assert sesiones.status_code == 200
    assert (
        cliente.get(
            RUTA_LECTURAS_DE.format(sesiones.json()[0]["id_sesion"]),
            headers=bearer(token_paciente),
        ).status_code
        == 200
    )

    assert denegaciones(engine_observador, marca_de_auditoria) == []


def test_la_entrada_no_lleva_biometria_ni_credenciales(
    cliente, token_paciente, alcance_clinico, engine_observador, marca_de_auditoria
):
    """Lo que la fila contiene es lo que la peticion trajo, y nada mas."""
    cliente.get(
        RUTA_SESIONES_DE.format(alcance_clinico["del_medico"][0]),
        headers=bearer(token_paciente),
    )

    [entrada] = denegaciones(engine_observador, marca_de_auditoria)
    texto = " ".join(str(valor) for valor in entrada.values())

    assert token_paciente not in texto
    assert PASSWORD_SIMULADA not in texto
    assert "Bearer" not in texto
    for prohibido in ("hr_valor", "spo2_valor", "mov_valor", "@"):
        assert prohibido not in texto


def test_si_la_auditoria_falla_la_respuesta_sigue_siendo_404(
    cliente, token_paciente, alcance_clinico, monkeypatch
):
    """El acceso ya esta denegado; degradarlo a 500 seria el oraculo.

    Una tabla de auditoria rota que convirtiera el 404 en un 500 distinguiria un
    recurso ajeno de uno inexistente, que es exactamente lo que este 404 existe
    para impedir. Se fuerza el fallo en el escritor, que es donde ocurriria.
    """
    from app.api.v1 import clinico as router_clinico
    from app.services.auditoria import FalloDeAuditoria

    def revienta(*_, **__):
        # Sin argumentos: ``FalloDeAuditoria`` no lleva mensaje a proposito --
        # el diagnostico ya quedo en la traza saneada del servicio, y adjuntarlo
        # a la excepcion lo pondria al alcance de quien la capture.
        raise FalloDeAuditoria()

    monkeypatch.setattr(
        router_clinico.auditoria, "registrar_con_commit", revienta
    )

    ajeno = cliente.get(
        RUTA_SESIONES_DE.format(alcance_clinico["del_medico"][0]),
        headers=bearer(token_paciente),
    )
    inexistente = cliente.get(
        RUTA_SESIONES_DE.format(alcance_clinico["embarazo_inexistente"]),
        headers=bearer(token_paciente),
    )

    assert ajeno.status_code == inexistente.status_code == 404
    assert _sin_identificador(ajeno.json()["detail"]) == _sin_identificador(
        inexistente.json()["detail"]
    )


# ---------------------------------------------------------------------------
# 8. Cada lectura clinica autorizada deja rastro (RF-10 / RNF-07)
# ---------------------------------------------------------------------------
#
# La seccion anterior cubre las denegaciones. Esta cubre la otra mitad, que es
# la que los requerimientos piden de verdad: quien accedio a la informacion y
# cuando. Un registro que solo guarde rechazos dice quien fue rechazado y nunca
# quien leyo la serie de una paciente.
#
# El contrato es **una entrada por peticion**. No una por embarazo devuelto, ni
# una por sesion, ni una por lectura.


def permitidos(engine_observador, desde: int) -> list[dict]:
    """Las entradas de ``ACCESO_CLINICO_PERMITIDO`` posteriores a una marca."""
    return [
        dict(fila)
        for fila in observar(
            engine_observador,
            """
            SELECT id_log, id_usuario, accion, nombre_entidad_afectada,
                   id_entidad_afectada, ip_origen
            FROM operacional.auditoria_log
            WHERE id_log > :desde AND accion = :accion
            ORDER BY id_log
            """,
            desde=desde,
            accion="ACCESO_CLINICO_PERMITIDO",
        )
    ]


def test_listar_embarazos_deja_una_entrada_sin_identificador(
    cliente, token_paciente, engine_observador, marca_de_auditoria, identidades
):
    """La ruta de coleccion no nombra ningun episodio, asi que el id es NULL.

    Serializar la lista de embarazos devueltos convertiria la traza en un
    segundo almacen de datos clinicos.
    """
    respuesta = cliente.get(RUTA_EMBARAZOS, headers=bearer(token_paciente))
    assert respuesta.status_code == 200

    [entrada] = permitidos(engine_observador, marca_de_auditoria)
    assert entrada["id_usuario"] == identidades["paciente"]["id_usuario"]
    assert entrada["nombre_entidad_afectada"] == "embarazo"
    assert entrada["id_entidad_afectada"] is None
    assert entrada["ip_origen"]


def test_listar_sesiones_deja_una_entrada_con_el_embarazo_pedido(
    cliente, token_paciente, alcance_clinico, engine_observador, marca_de_auditoria
):
    id_embarazo = alcance_clinico["de_la_paciente"][0]

    respuesta = cliente.get(
        RUTA_SESIONES_DE.format(id_embarazo), headers=bearer(token_paciente)
    )
    assert respuesta.status_code == 200
    assert len(respuesta.json()) > 1, "hace falta mas de una sesion para que valga"

    [entrada] = permitidos(engine_observador, marca_de_auditoria)
    assert entrada["nombre_entidad_afectada"] == "embarazo"
    assert entrada["id_entidad_afectada"] == str(id_embarazo)


def test_listar_lecturas_deja_una_entrada_con_la_sesion_pedida(
    cliente, token_paciente, alcance_clinico, engine_observador, marca_de_auditoria
):
    """Una serie de muchas lecturas es **un** acceso, no uno por fila."""
    id_embarazo = alcance_clinico["de_la_paciente"][0]
    id_sesion = cliente.get(
        RUTA_SESIONES_DE.format(id_embarazo), headers=bearer(token_paciente)
    ).json()[0]["id_sesion"]

    marca = escalar(
        engine_observador,
        "SELECT coalesce(max(id_log), 0) FROM operacional.auditoria_log",
    )
    respuesta = cliente.get(
        RUTA_LECTURAS_DE.format(id_sesion), headers=bearer(token_paciente)
    )

    assert respuesta.status_code == 200
    assert len(respuesta.json()) >= 1

    [entrada] = permitidos(engine_observador, marca)
    assert entrada["nombre_entidad_afectada"] == "sesion_monitoreo"
    assert entrada["id_entidad_afectada"] == str(id_sesion)


def test_una_serie_larga_sigue_dejando_una_sola_entrada(
    cliente, token_paciente, alcance_clinico, engine_observador
):
    """El contrato que importa: por peticion, no por fila devuelta."""
    id_embarazo = alcance_clinico["de_la_paciente"][0]
    sesiones = cliente.get(
        RUTA_SESIONES_DE.format(id_embarazo), headers=bearer(token_paciente)
    ).json()

    # La sesion con mas lecturas del episodio.
    mejor, cuantas = None, 0
    for sesion in sesiones:
        total = len(
            cliente.get(
                RUTA_LECTURAS_DE.format(sesion["id_sesion"]),
                headers=bearer(token_paciente),
            ).json()
        )
        if total > cuantas:
            mejor, cuantas = sesion["id_sesion"], total

    assert cuantas >= 2, "el dataset no da ninguna sesion con varias lecturas"

    marca = escalar(
        engine_observador,
        "SELECT coalesce(max(id_log), 0) FROM operacional.auditoria_log",
    )
    respuesta = cliente.get(
        RUTA_LECTURAS_DE.format(mejor), headers=bearer(token_paciente)
    )

    assert len(respuesta.json()) == cuantas
    assert len(permitidos(engine_observador, marca)) == 1


def test_una_lista_vacia_autorizada_tambien_deja_entrada(
    cliente, token_admin, referencias, engine_observador
):
    """Preguntar tambien es acceder.

    Una cuenta recien aprovisionada sin embarazos recibe una lista vacia, y ese
    acceso se registra igual: la traza tiene que poder decir quien consulto,
    aunque no se llevara nada.
    """
    if referencias["id_perfil_libre"] is None:
        pytest.skip("el dataset no deja ningun perfil de paciente sin cuenta")

    creada = cliente.post(
        RUTA_PACIENTES.format(referencias["id_perfil_libre"]),
        json={"email": EMAIL_CUENTA_PACIENTE, "password": PASSWORD_NUEVA},
        headers=bearer(token_admin),
    )
    assert creada.status_code == 201, creada.text
    token = iniciar_sesion(cliente, EMAIL_CUENTA_PACIENTE, PASSWORD_NUEVA)

    marca = escalar(
        engine_observador,
        "SELECT coalesce(max(id_log), 0) FROM operacional.auditoria_log",
    )
    respuesta = cliente.get(RUTA_EMBARAZOS, headers=bearer(token))

    assert respuesta.status_code == 200
    assert respuesta.json() == []

    [entrada] = permitidos(engine_observador, marca)
    assert entrada["id_usuario"] == creada.json()["id_usuario"]
    assert entrada["id_entidad_afectada"] is None


def test_un_404_sigue_registrando_denegado_y_no_permitido(
    cliente, token_paciente, alcance_clinico, engine_observador, marca_de_auditoria
):
    """Ajeno e inexistente no pueden pasar por accesos concedidos."""
    for id_embarazo in (
        alcance_clinico["del_medico"][0],
        alcance_clinico["embarazo_inexistente"],
    ):
        assert (
            cliente.get(
                RUTA_SESIONES_DE.format(id_embarazo), headers=bearer(token_paciente)
            ).status_code
            == 404
        )

    assert permitidos(engine_observador, marca_de_auditoria) == []
    assert len(denegaciones(engine_observador, marca_de_auditoria)) == 2


def test_un_403_por_rol_no_registra_acceso_permitido(
    cliente, token_admin, engine_observador, marca_de_auditoria
):
    """ADMIN no llega al handler: su rechazo es el de la guardia de rol."""
    assert cliente.get(RUTA_EMBARAZOS, headers=bearer(token_admin)).status_code == 403

    assert permitidos(engine_observador, marca_de_auditoria) == []
    assert (
        escalar(
            engine_observador,
            "SELECT count(*) FROM operacional.auditoria_log "
            "WHERE id_log > :d AND accion = 'ACCESO_DENEGADO_ROL'",
            d=marca_de_auditoria,
        )
        >= 1
    )


def test_la_entrada_no_lleva_biometria_token_ni_pii(
    cliente, token_paciente, alcance_clinico, engine_observador, marca_de_auditoria
):
    """La fila contiene lo que la peticion trajo, y nada de lo que devolvio."""
    id_embarazo = alcance_clinico["de_la_paciente"][0]
    cliente.get(RUTA_SESIONES_DE.format(id_embarazo), headers=bearer(token_paciente))

    [entrada] = permitidos(engine_observador, marca_de_auditoria)
    texto = " ".join(str(valor) for valor in entrada.values())

    assert token_paciente not in texto
    assert PASSWORD_SIMULADA not in texto
    assert "Bearer" not in texto and "@" not in texto
    for prohibido in ("hr_valor", "spo2_valor", "mov_valor", "SELECT", "codigo_semaforo"):
        assert prohibido not in texto


def test_si_no_puede_registrarse_el_acceso_no_se_entregan_los_datos(
    cliente, token_paciente, alcance_clinico, monkeypatch
):
    """Fail-closed, y es lo contrario de lo que hace una denegacion.

    Entregar la serie de una paciente sin poder registrar quien se la llevo es
    exactamente lo que RF-10 prohibe. Asi que si la auditoria no puede escribir,
    la respuesta falla por el canal saneado y los datos no salen.
    """
    from app.api.v1 import clinico as router_clinico
    from app.services.auditoria import FalloDeAuditoria

    def revienta(*_, **__):
        raise FalloDeAuditoria()

    monkeypatch.setattr(router_clinico.auditoria, "registrar_con_commit", revienta)

    respuesta = cliente.get(RUTA_EMBARAZOS, headers=bearer(token_paciente))

    assert respuesta.status_code >= 400
    assert respuesta.status_code != 200
    cuerpo = respuesta.text.lower()
    assert "select" not in cuerpo and "operacional." not in cuerpo
    assert "traceback" not in cuerpo


def test_una_denegacion_sigue_respondiendo_404_aunque_falle_la_auditoria(
    cliente, token_paciente, alcance_clinico, monkeypatch
):
    """La asimetria, comprobada: la denegacion no se degrada.

    Si tambien fallara en cerrado, una tabla de auditoria rota distinguiria un
    recurso ajeno de uno inexistente por el codigo de respuesta.
    """
    from app.api.v1 import clinico as router_clinico
    from app.services.auditoria import FalloDeAuditoria

    def revienta(*_, **__):
        raise FalloDeAuditoria()

    monkeypatch.setattr(router_clinico.auditoria, "registrar_con_commit", revienta)

    ajeno = cliente.get(
        RUTA_SESIONES_DE.format(alcance_clinico["del_medico"][0]),
        headers=bearer(token_paciente),
    )
    inexistente = cliente.get(
        RUTA_SESIONES_DE.format(alcance_clinico["embarazo_inexistente"]),
        headers=bearer(token_paciente),
    )

    assert ajeno.status_code == inexistente.status_code == 404
