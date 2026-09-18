"""The FastAPI application, and the one check that decides whether it may run.

``exigir_configuracion_jwt()`` is called here, while the module is being
imported, which is exactly what uvicorn does before serving anything. A missing
or unusable ``JWT_SECRET_KEY`` therefore stops the process with a sentence
instead of letting it start and refuse every request afterwards -- the API fails
**closed**.

The check lives here and not in ``app.config`` on purpose. ``app.config`` is
imported by ``alembic/env.py``, by the ETL entry point and by the dataset
loader, none of which issue or verify a token; making the secret mandatory at
that level would break migrations and the ETL over a credential they never use.
This module is imported by the API and by nothing else.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.v1 import (
    router_autenticacion,
    router_clinico,
    router_cuentas,
    router_sesiones,
)
from app.api.v1.autenticacion import sanear_errores_de_validacion
from app.config import exigir_configuracion_jwt, settings
from app.db.privilegios import verificar_al_arrancar
from app.db.session import engine

# Raises ``ConfiguracionJWTInvalida`` -- with a sanitised message that never
# contains the value -- before ``app`` exists.
exigir_configuracion_jwt()


@asynccontextmanager
async def ciclo_de_vida(aplicacion: FastAPI):
    """Lo que se verifica una vez, cuando el proceso empieza a servir.

    Va aquí y no al importar el módulo, y la diferencia importa: ``app.main`` lo
    importan la suite offline y herramientas que no tienen PostgreSQL a mano, y
    abrir una conexión al importar convertiría un servidor en requisito para
    leer el módulo.

    ``verificar_al_arrancar`` decide qué hacer con cada fallo según el ambiente:
    en uno desplegado, cualquiera de ellos -- credencial no restringida, base
    inalcanzable, consulta agotada, catálogo incompleto -- levanta y detiene el
    arranque. Aquí no se captura nada: dejar pasar una excepción es exactamente
    lo que hace que la API falle cerrado.
    """
    verificar_al_arrancar(engine, settings.app_env)
    yield


app = FastAPI(title=settings.app_name, lifespan=ciclo_de_vida)

app.include_router(router_autenticacion)
app.include_router(router_cuentas)
app.include_router(router_sesiones)
app.include_router(router_clinico)

# Un 422 de Pydantic devuelve el valor que no paso la validacion. Para el
# cuerpo del login -- y el de la provision de cuentas (SCRUM-97) -- eso seria la
# contrasena en claro, asi que ese eco se retira en las rutas con credenciales.
# El resto de la API conserva su 422 intacto.
app.add_exception_handler(RequestValidationError, sanear_errores_de_validacion)


@app.get("/health")
def health() -> dict:
    """Liveness probe. Public on purpose: Docker Compose and CI depend on it.

    It reports nothing about the system beyond the fact that the process
    answers, so there is nothing here to protect.
    """
    return {"status": "ok"}
