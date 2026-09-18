"""Carga idempotente del dataset simulado de FetalAlert en PostgreSQL.

Uso desde la raíz del repositorio::

    python scripts/load_mock_data.py                 # data/generated/dataset_fetalalert.json
    python scripts/load_mock_data.py otra/ruta.json  # archivo alternativo de prueba

La conexión sale de ``ALEMBIC_DATABASE_URL``, no de ``DATABASE_URL`` (SCRUM-98).
Cargar el dataset simulado es una operación de mantenimiento: escribe en las
tablas clínicas, y desde que esas tablas llevan seguridad por filas la
credencial restringida de la API no puede -- ni debe poder -- hacerlo. Es la
misma credencial que ejecuta las migraciones, y el mismo rol que la policy de
mantenimiento admite. Se ejecuta a mano, fuera del proceso web, que nunca recibe
esa variable.

No hay respaldo a ``DATABASE_URL``: si la variable falta, el cargador se
detiene. Nunca se recibe por argumento, nunca se imprime y nunca aparece en un
mensaje de error.

Requisitos previos: PostgreSQL en marcha, la base en ``alembic upgrade head`` y
el dataset ya generado con ``python scripts/generate_mock_data.py``. Este
comando no crea el esquema, no borra nada y no sobrescribe nada.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_DEL_REPOSITORIO / "backend"))

from sqlalchemy import create_engine  # noqa: E402  -- tras ajustar sys.path
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.config import (  # noqa: E402
    VARIABLE_URL_ALEMBIC,
    UrlDeEntornoInvalida,
    exigir_url_de_entorno,
    settings,
)
from app.loader import (  # noqa: E402
    RUTA_POR_DEFECTO,
    ErrorDeCarga,
    cargar_dataset,
    formatear_resumen,
    leer_dataset,
    preflight,
    sanear_mensaje,
    validar_dataset,
    verificar_ambiente,
)

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1


def construir_parser() -> argparse.ArgumentParser:
    """Argumentos del comando.

    Solo se acepta una ruta de archivo. No hay opción para pasar la URL de la
    base ni ninguna credencial: esa información vive únicamente en la
    configuración del entorno.
    """
    parser = argparse.ArgumentParser(
        prog="load_mock_data.py",
        description=(
            "Carga el dataset simulado de FetalAlert en el esquema operacional "
            "de PostgreSQL, de forma idempotente y en una única transacción."
        ),
    )
    parser.add_argument(
        "ruta",
        nargs="?",
        type=Path,
        default=RUTA_POR_DEFECTO,
        help=(
            "Archivo JSON del dataset. Por omisión, "
            "data/generated/dataset_fetalalert.json"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    argumentos = construir_parser().parse_args(argv)

    try:
        # Las dos guardias corren antes de construir el engine: una URL errónea
        # no debe llegar a crear nada antes de ser rechazada.
        verificar_ambiente(settings.app_env)
        url = exigir_url_de_entorno(VARIABLE_URL_ALEMBIC)

        # Validar antes de abrir la transacción: un archivo inservible no llega
        # nunca a tocar la base.
        dataset = validar_dataset(leer_dataset(argumentos.ruta))

        engine = create_engine(url)
        try:
            # Orden aprobado: validación -> preflight -> transacción -> carga.
            # El preflight de Alembic ocurre en su propia conexión, fuera de la
            # transacción de escritura: comprobar el esquema no es parte de la
            # carga y no debe abrir una transacción que luego haya que revertir.
            with engine.connect() as conexion:
                revision = preflight(conexion)

            # engine.begin() es la transacción única: hace commit al salir sin
            # excepción y rollback ante cualquier error.
            with engine.begin() as conexion:
                resultado = cargar_dataset(
                    conexion,
                    dataset,
                    revision=revision,
                    ruta=argumentos.ruta,
                )
        finally:
            engine.dispose()

    except UrlDeEntornoInvalida as error:
        # El mensaje ya viene saneado y nunca repite el valor rechazado.
        print(f"Error: {error}", file=sys.stderr)
        return CODIGO_DE_ERROR
    except ErrorDeCarga as error:
        print(f"Error: {sanear_mensaje(str(error))}", file=sys.stderr)
        return CODIGO_DE_ERROR
    except SQLAlchemyError as error:
        print(
            f"Error de base de datos ({type(error).__name__}): "
            f"{sanear_mensaje(str(error))}",
            file=sys.stderr,
        )
        print(
            "No se conservó ningún cambio: la transacción completa fue revertida.",
            file=sys.stderr,
        )
        return CODIGO_DE_ERROR

    print(formatear_resumen(resultado, confirmada=True))
    return CODIGO_DE_EXITO


if __name__ == "__main__":
    raise SystemExit(main())
