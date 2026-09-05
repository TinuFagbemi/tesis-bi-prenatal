from fastapi import FastAPI

from app.api.v1 import router_sesiones
from app.config import settings

app = FastAPI(title=settings.app_name)

app.include_router(router_sesiones)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
