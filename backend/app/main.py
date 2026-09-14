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

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.api.v1 import router_autenticacion, router_sesiones
from app.api.v1.autenticacion import sanear_errores_de_validacion
from app.config import exigir_configuracion_jwt, settings

# Raises ``ConfiguracionJWTInvalida`` -- with a sanitised message that never
# contains the value -- before ``app`` exists.
exigir_configuracion_jwt()

app = FastAPI(title=settings.app_name)

app.include_router(router_autenticacion)
app.include_router(router_sesiones)

# Un 422 de Pydantic devuelve el valor que no paso la validacion. Para el
# cuerpo del login eso seria la contrasena en claro, asi que ese eco se retira
# en las rutas con credenciales. El resto de la API conserva su 422 intacto.
app.add_exception_handler(RequestValidationError, sanear_errores_de_validacion)


@app.get("/health")
def health() -> dict:
    """Liveness probe. Public on purpose: Docker Compose and CI depend on it.

    It reports nothing about the system beyond the fact that the process
    answers, so there is nothing here to protect.
    """
    return {"status": "ok"}
