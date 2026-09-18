"""Autorizacion por rol: la matriz real y el componente que la aplica (SCRUM-70).

Dos planos, y conviene no confundirlos porque dicen cosas distintas:

* **El plano funcional.** La API tiene **una sola** operacion de negocio, y sobre
  ella hay dos desenlaces: PACIENTE obtiene 201, mientras ADMIN y MEDICO obtienen
  **ambos** 403. No se inventa un CRUD para que cada perfil tenga el suyo.
* **El plano del componente.** ``exigir_roles`` si distingue los tres perfiles, y
  se prueba construyendo listas blancas independientes ADMIN-only, MEDICO-only y
  PACIENTE-only. Que la superficie funcional actual no tenga una ruta para cada
  una es una limitacion de la API, no del mecanismo.

Lo que **no** se prueba aqui, porque no existe todavia: que la paciente
autenticada sea la duena del ``id_embarazo`` que envia. RBAC limita operaciones
por rol; el aislamiento por fila es SCRUM-71.

Todas las cuentas son ficticias y completamente simuladas.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.dependencias import (
    MENSAJE_ROL_NO_AUTORIZADO,
    exigir_roles,
    get_db_auditoria,
    usuario_actual,
)
from app.api.v1.sesiones import EXIGIR_PACIENTE
from app.main import app
from app.models.enums import NombreRol
from app.services.auditoria import AccionAuditada, ENTIDAD_SESION_MONITOREO
from app.services.principal import PrincipalAutenticado
from tests.conftest import principal_de_prueba
from tests.test_autenticacion_api import SesionDeCuentas
from tests.test_ingestion_schemas import paquete

RUTA_INGESTA = "/api/v1/sesiones-monitoreo"
CLAVE = "clave-rbac-0001"


@pytest.fixture
def auditoria() -> SesionDeCuentas:
    return SesionDeCuentas()


# ---------------------------------------------------------------------------
# 1. El componente: tres listas blancas independientes
# ---------------------------------------------------------------------------


def app_con(*roles: NombreRol) -> FastAPI:
    """Una aplicacion minima cuya unica ruta esta guardada por esos roles."""
    aplicacion = FastAPI()
    guardia = exigir_roles(*roles, entidad="recurso_de_prueba")

    @aplicacion.get("/recurso")
    def recurso(principal: PrincipalAutenticado = Depends(guardia)) -> dict:
        return {"rol": principal.rol.value}

    return aplicacion


def cliente_como(aplicacion: FastAPI, rol: NombreRol, auditoria) -> TestClient:
    aplicacion.dependency_overrides[usuario_actual] = lambda: principal_de_prueba(rol)
    aplicacion.dependency_overrides[get_db_auditoria] = lambda: auditoria
    return TestClient(aplicacion)


@pytest.mark.parametrize("permitido", list(NombreRol))
def test_una_lista_blanca_de_un_solo_rol_admite_ese_rol(permitido, auditoria):
    """ADMIN-only, MEDICO-only y PACIENTE-only, cada una por separado."""
    aplicacion = app_con(permitido)

    respuesta = cliente_como(aplicacion, permitido, auditoria).get("/recurso")

    assert respuesta.status_code == HTTPStatus.OK
    assert respuesta.json() == {"rol": permitido.value}


@pytest.mark.parametrize("permitido", list(NombreRol))
def test_una_lista_blanca_de_un_solo_rol_rechaza_los_otros_dos(permitido, auditoria):
    aplicacion = app_con(permitido)

    for rol in NombreRol:
        if rol is permitido:
            continue
        respuesta = cliente_como(aplicacion, rol, auditoria).get("/recurso")
        assert respuesta.status_code == HTTPStatus.FORBIDDEN
        assert respuesta.json()["detail"] == MENSAJE_ROL_NO_AUTORIZADO


def test_una_lista_blanca_de_dos_roles_admite_los_dos(auditoria):
    aplicacion = app_con(NombreRol.ADMIN, NombreRol.MEDICO)

    for rol in (NombreRol.ADMIN, NombreRol.MEDICO):
        assert cliente_como(aplicacion, rol, auditoria).get("/recurso").status_code == 200

    assert (
        cliente_como(aplicacion, NombreRol.PACIENTE, auditoria).get("/recurso").status_code
        == 403
    )


def test_una_lista_blanca_vacia_es_un_error_de_programacion():
    """Una guardia que no admite a nadie es una equivocacion, no una politica."""
    with pytest.raises(ValueError):
        exigir_roles(entidad="recurso_de_prueba")


def test_un_rol_desconocido_no_pasa(auditoria):
    """Por omision se deniega: la pertenencia se comprueba contra un conjunto."""
    aplicacion = app_con(NombreRol.PACIENTE)
    desconocido = PrincipalAutenticado(id_usuario=1, rol="SUPERADMIN")
    aplicacion.dependency_overrides[usuario_actual] = lambda: desconocido
    aplicacion.dependency_overrides[get_db_auditoria] = lambda: auditoria

    assert TestClient(aplicacion).get("/recurso").status_code == 403


def test_el_403_no_lleva_desafio_bearer(auditoria):
    """La credencial es valida; reintentarla no arreglaria nada."""
    aplicacion = app_con(NombreRol.ADMIN)

    respuesta = cliente_como(aplicacion, NombreRol.PACIENTE, auditoria).get("/recurso")

    assert "www-authenticate" not in {k.lower() for k in respuesta.headers}


def test_la_denegacion_se_audita(auditoria):
    aplicacion = app_con(NombreRol.ADMIN)

    cliente_como(aplicacion, NombreRol.PACIENTE, auditoria).get("/recurso")

    assert len(auditoria.agregados) == 1
    entrada = auditoria.agregados[0]
    assert entrada.accion == AccionAuditada.ACCESO_DENEGADO_ROL.value
    assert entrada.nombre_entidad_afectada == "recurso_de_prueba"
    assert entrada.id_entidad_afectada is None
    assert auditoria.commits == 1


def test_el_acceso_permitido_no_genera_denegacion(auditoria):
    aplicacion = app_con(NombreRol.PACIENTE)

    cliente_como(aplicacion, NombreRol.PACIENTE, auditoria).get("/recurso")

    assert auditoria.agregados == []


def test_si_la_auditoria_de_la_denegacion_falla_sigue_siendo_403(auditoria):
    """Nada se concedio, asi que no hay nada que cerrar mas."""
    from sqlalchemy.exc import SQLAlchemyError

    def estallar():
        raise SQLAlchemyError("fallo simulado")

    auditoria.commit = estallar
    aplicacion = app_con(NombreRol.ADMIN)

    respuesta = cliente_como(aplicacion, NombreRol.PACIENTE, auditoria).get("/recurso")

    assert respuesta.status_code == 403


# ---------------------------------------------------------------------------
# 2. La matriz real: la unica operacion de negocio
# ---------------------------------------------------------------------------


@pytest.fixture
def ingesta(auditoria):
    """Cliente de la ingesta cuyo rol la prueba elige."""
    from app.db.session import get_db
    from tests.test_ingestion_api import SesionFalsa
    from app.services import idempotencia as modulo_idempotencia
    from app.services.ingesta import ResultadoIngesta

    sesion = SesionFalsa()
    app.dependency_overrides[get_db] = lambda: sesion
    app.dependency_overrides[get_db_auditoria] = lambda: auditoria

    parche = pytest.MonkeyPatch()
    parche.setattr(
        modulo_idempotencia,
        "registrar_sesion",
        lambda s, e: ResultadoIngesta(id_sesion=900, ids_lectura=(1301,)),
    )

    def como(rol: NombreRol) -> TestClient:
        app.dependency_overrides[usuario_actual] = lambda: principal_de_prueba(rol)
        return TestClient(app, headers={"Idempotency-Key": CLAVE})

    try:
        yield como, sesion
    finally:
        parche.undo()
        app.dependency_overrides.clear()


def test_paciente_puede_registrar_una_sesion(ingesta):
    como, _ = ingesta

    respuesta = como(NombreRol.PACIENTE).post(RUTA_INGESTA, json=paquete())

    assert respuesta.status_code == HTTPStatus.CREATED


@pytest.mark.parametrize("rol", [NombreRol.ADMIN, NombreRol.MEDICO])
def test_admin_y_medico_reciben_403(ingesta, rol):
    """Minimo privilegio: el medico consulta y el administrador no es un bypass."""
    como, _ = ingesta

    respuesta = como(rol).post(RUTA_INGESTA, json=paquete())

    assert respuesta.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("rol", [NombreRol.ADMIN, NombreRol.MEDICO])
def test_un_rol_denegado_no_escribe_nada(ingesta, rol):
    """El 403 ocurre antes de abrir la transaccion del paquete."""
    como, sesion = ingesta

    como(rol).post(RUTA_INGESTA, json=paquete())

    assert sesion.pasos == []
    assert sesion.commits == 0


def test_la_denegacion_de_la_ingesta_nombra_la_entidad_correcta(ingesta, auditoria):
    """Un recurso tecnico estable, no una ruta HTTP."""
    como, _ = ingesta

    como(NombreRol.ADMIN).post(RUTA_INGESTA, json=paquete())

    entrada = auditoria.agregados[0]
    assert entrada.nombre_entidad_afectada == ENTIDAD_SESION_MONITOREO
    assert entrada.id_entidad_afectada is None
    assert "/" not in entrada.nombre_entidad_afectada


def test_la_autorizacion_va_antes_que_la_clave_de_idempotencia(ingesta):
    """Quien no puede entrar no aprende el contrato de la cabecera."""
    como, _ = ingesta
    cliente = como(NombreRol.ADMIN)

    respuesta = cliente.post(RUTA_INGESTA, json=paquete(), headers={"Idempotency-Key": ""})

    assert respuesta.status_code == HTTPStatus.FORBIDDEN


def test_la_ingesta_ya_correlaciona_al_paciente_con_su_embarazo(ingesta):
    """El hueco que SCRUM-70 declaraba, cerrado por SCRUM-98.

    Hasta esta subfase una cuenta PACIENTE cualquiera podia enviar un paquete de
    cualquier ``id_embarazo`` y recibir 201. Ahora la ruta comprueba la
    propiedad antes de reclamar la clave, y un embarazo ajeno responde el mismo
    404 que uno inexistente.
    """
    como, sesion = ingesta
    sesion.id_paciente = 9_000_101
    ajeno = paquete()
    ajeno["id_embarazo"] = 999_001
    # El doble deja de reconocer el embarazo como propio.
    sesion.embarazo_propio = False

    respuesta = como(NombreRol.PACIENTE).post(RUTA_INGESTA, json=ajeno)

    assert respuesta.status_code == HTTPStatus.NOT_FOUND
    assert sesion.inserts == 0, "no debe reclamarse la clave de un embarazo ajeno"


# ---------------------------------------------------------------------------
# 3. Inventario estructural: ninguna ruta sensible queda publica por descuido
# ---------------------------------------------------------------------------

# Rutas que pueden responder sin credencial, y el motivo de cada una.
PUBLICAS = {
    "/health": "sonda de salud que Docker Compose y el CI necesitan",
    "/openapi.json": "documentacion del entorno academico local",
    "/docs": "Swagger UI del entorno academico local",
    "/docs/oauth2-redirect": "auxiliar que genera FastAPI",
    "/redoc": "ReDoc del entorno academico local",
    "/api/v1/autenticacion/token": "sin el, nadie podria autenticarse nunca",
}


def aplanar(rutas):
    """Todas las rutas reales, incluidas las que viven dentro de un router.

    ``app.routes`` no las devuelve planas: cada ``include_router`` deja un
    ``_IncludedRouter`` que envuelve su router original. Recorrer solo el primer
    nivel encuentra ``/health`` y la documentacion, y **ninguna** de las tres
    rutas de ``/api/v1`` -- con lo que el guardian de abajo pasaria sin mirar
    nada, que es la peor forma de fallar que puede tener una prueba como esta.
    """
    for ruta in rutas:
        camino = getattr(ruta, "path", None)
        if camino:
            yield ruta, camino

        original = getattr(ruta, "original_router", None)
        if original is not None:
            yield from aplanar(original.routes)
        elif getattr(ruta, "routes", None):
            yield from aplanar(ruta.routes)


def dependencias_de(ruta) -> set[str]:
    """Nombres de las dependencias declaradas por la ruta, y de sus anidadas."""
    dependant = getattr(ruta, "dependant", None)
    if dependant is None:
        return set()

    directas = {
        getattr(d.call, "__name__", "") for d in dependant.dependencies if d.call
    }
    anidadas = {
        getattr(sub.call, "__name__", "")
        for d in dependant.dependencies
        for sub in getattr(d, "dependencies", [])
        if sub.call
    }
    return directas | anidadas


def test_el_inventario_encuentra_las_rutas_de_los_routers():
    """La prueba que protege al guardian de pasar en vacio."""
    caminos = {camino for _, camino in aplanar(app.routes)}

    assert RUTA_INGESTA in caminos
    assert "/api/v1/autenticacion/yo" in caminos
    assert "/api/v1/autenticacion/token" in caminos


def test_toda_ruta_no_publica_declara_una_dependencia_de_autorizacion():
    """El guardian barato contra el descuido.

    Si alguien anade una ruta y olvida protegerla, esta prueba lo dice. Para
    dejarla publica hay que escribirlo en :data:`PUBLICAS`, con su motivo, que es
    justo la decision que no debe tomarse por omision.
    """
    desprotegidas = [
        camino
        for ruta, camino in aplanar(app.routes)
        if camino not in PUBLICAS
        and not ({"verificar_rol", "usuario_actual"} & dependencias_de(ruta))
    ]

    assert desprotegidas == [], (
        f"Rutas sin autorizacion y sin justificacion publica: {desprotegidas}"
    )


def test_la_ingesta_declara_la_guardia_de_rol():
    """No basta con estar autenticada: esta operacion limita por rol."""
    for ruta, camino in aplanar(app.routes):
        if camino == RUTA_INGESTA:
            assert "verificar_rol" in dependencias_de(ruta)
            break
    else:  # pragma: no cover -- lo cubre el inventario de arriba
        pytest.fail("No se encontro la ruta de ingesta")


def test_la_lista_de_publicas_no_tiene_sobrantes():
    """Una ruta que ya no existe no debe seguir exenta."""
    caminos = {camino for _, camino in aplanar(app.routes)}

    assert set(PUBLICAS) <= caminos


def test_la_ingesta_no_esta_en_la_lista_de_publicas():
    assert RUTA_INGESTA not in PUBLICAS


def test_la_guardia_de_la_ingesta_admite_solo_paciente():
    """Se lee del objeto que produccion usa, no de una copia de la matriz."""
    aplicacion = FastAPI()

    @aplicacion.get("/sonda")
    def sonda(contexto=Depends(EXIGIR_PACIENTE)) -> dict:
        return {"rol": contexto.rol.value}

    auditoria = SesionDeCuentas()
    aplicacion.dependency_overrides[get_db_auditoria] = lambda: auditoria
    # Desde SCRUM-98 la guardia resuelve ademas el contexto clinico, asi que
    # necesita una sesion. Se da un doble: lo que esta prueba mide es que roles
    # pasan, no de donde sale el perfil.
    from app.db.session import get_db
    from tests.test_ingestion_api import SesionFalsa

    aplicacion.dependency_overrides[get_db] = lambda: SesionFalsa()

    permitidos = set()
    for rol in NombreRol:
        aplicacion.dependency_overrides[usuario_actual] = lambda rol=rol: principal_de_prueba(rol)
        if TestClient(aplicacion).get("/sonda").status_code == 200:
            permitidos.add(rol)

    assert permitidos == {NombreRol.PACIENTE}
