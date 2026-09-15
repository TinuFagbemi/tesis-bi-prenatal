"""Configuracion compartida de la suite.

**La clave de firma ficticia se define aqui, y tiene que ser aqui.**
``app.config`` construye ``settings`` en el momento en que se importa, y
``app.main`` valida la configuracion JWT al construir la aplicacion, de modo que
la variable tiene que existir **antes** de que se importe cualquier modulo de
``app``. ``conftest.py`` se carga antes que los modulos de prueba, y dentro de
este archivo la asignacion va antes que los imports de ``app``: ese orden no es
estetico, es lo que hace que funcione.

El valor es ficticio, efimero y exclusivo de la suite: se sortea en cada
ejecucion, no se escribe en ningun archivo, no corresponde a ningun entorno y no
puede reutilizarse fuera de aqui. Se usa ``setdefault`` y no una asignacion
directa para que, si alguien ya exporto la variable --el job de CI, por
ejemplo--, se respete la suya.

``JWT_EXPIRATION_MINUTES`` se deja sin definir a proposito, para que las pruebas
ejerciten el valor por omision que trae la aplicacion.

Aqui viven tambien dos cosas que varios modulos necesitan: el ``Config`` de
Alembic de las pruebas de migracion, resuelto desde la estructura del
repositorio en lugar del directorio de trabajo, y los ayudantes de identidad
simulada que las pruebas HTTP heredadas usan desde SCRUM-70.
"""

import os
import secrets
from contextlib import contextmanager
from pathlib import Path

from alembic.config import Config

# Antes de cualquier import de ``app``: ver el docstring.
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(32))

from app.api.dependencias import (  # noqa: E402  -- despues de fijar el entorno
    get_db_auditoria,
    usuario_actual,
)
from app.models.enums import NombreRol  # noqa: E402
from app.services.principal import PrincipalAutenticado  # noqa: E402

DIRECTORIO_BACKEND = Path(__file__).resolve().parents[1]
DIRECTORIO_ALEMBIC = DIRECTORIO_BACKEND / "alembic"


def construir_config_alembic() -> Config:
    """Alembic ``Config`` that does not depend on the working directory.

    ``sqlalchemy.url`` is left unset on purpose: the offline tests never open a
    connection, and ``alembic/env.py`` always overwrites it with
    ``settings.database_url`` anyway.
    """
    config = Config()
    config.set_main_option("script_location", str(DIRECTORIO_ALEMBIC))
    return config


# ---------------------------------------------------------------------------
# Identidad simulada para las pruebas HTTP (SCRUM-70)
# ---------------------------------------------------------------------------

# Identificador que ninguna fila del dataset simulado puede tener: sus cuentas
# empiezan en 100 y son 37. Un numero imposible evita que una prueba parezca
# funcionar por haber coincidido con un usuario real.
ID_USUARIO_DE_PRUEBA = 9_000_001


def principal_de_prueba(
    rol: NombreRol = NombreRol.PACIENTE,
    id_usuario: int = ID_USUARIO_DE_PRUEBA,
) -> PrincipalAutenticado:
    """Un principal ya resuelto, del rol que la prueba quiera ejercer."""
    return PrincipalAutenticado(id_usuario=id_usuario, rol=rol)


@contextmanager
def identidad_simulada(app, principal=None, sesion_auditoria=None):
    """Instala una identidad ya autenticada en la aplicacion, durante el bloque.

    **Que sustituye y que no.** Sustituye ``usuario_actual``, que es el paso que
    lee la cabecera, verifica la firma del JWT y consulta PostgreSQL. No
    sustituye ``exigir_roles``: la comprobacion del rol sigue ejecutandose de
    verdad contra el principal que se entregue, asi que una prueba que pase un
    ADMIN obtiene un 403 autentico, producido por el mismo codigo que corre en
    produccion.

    Esa es la costura correcta para las pruebas heredadas de ingesta e
    idempotencia: lo que ejercen es el flujo del paquete, no la criptografia del
    token, que tiene sus propias pruebas. Y para las de RBAC es justo lo que hace
    falta, porque permite variar el rol sin fabricar un token por cada caso.

    ``sesion_auditoria`` sustituye ademas la sesion de la auditoria con
    transaccion propia --la de los logins y las denegaciones--, que una prueba
    con dobles necesita controlar.
    """
    principal = principal if principal is not None else principal_de_prueba()

    app.dependency_overrides[usuario_actual] = lambda: principal
    if sesion_auditoria is not None:
        app.dependency_overrides[get_db_auditoria] = lambda: sesion_auditoria

    try:
        yield principal
    finally:
        app.dependency_overrides.pop(usuario_actual, None)
        if sesion_auditoria is not None:
            app.dependency_overrides.pop(get_db_auditoria, None)
