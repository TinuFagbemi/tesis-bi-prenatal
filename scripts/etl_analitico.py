"""ETL analítico de FetalAlert: esquema operacional -> esquema analitico.

Uso desde la raíz del repositorio::

    python scripts/etl_analitico.py ejecutar     # carga incremental + conciliación
    python scripts/etl_analitico.py conciliar    # solo conciliación, sin escribir

``ejecutar`` es el proceso *batch* del Capítulo III: extrae de ``operacional``,
clasifica cada métrica con los umbrales SIM-1.0, deriva el semáforo global, lo
compara con el registrado en origen, carga dimensiones, bridge y hechos nuevos
y concilia todo antes de confirmar. Es una única transacción: o se confirma
completa o no queda nada. No hay scheduler ni demonio; una ejecución nocturna
futura invocaría este mismo comando.

``conciliar`` repite la conciliación completa en una transacción de solo
lectura y termina.

La conexión sale de la configuración del proyecto (``DATABASE_URL``). Nunca se
recibe por argumento, nunca se imprime y nunca aparece en un mensaje de error.
Tampoco se imprime nunca un nombre, una cédula, un teléfono, un correo ni un
valor biométrico: solo identificadores técnicos, conteos y códigos.

Códigos de salida:

    0  éxito (en ``conciliar``: conciliación sin diferencias)
    1  error de configuración, de precondición o de base de datos
    2  el origen contiene algo que las reglas no permiten cargar sin inventar
       (valor sin regla, derivación ambigua, semáforo distinto del de origen)
    3  la conciliación encontró diferencias
    4  otra ejecución tiene el candado

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

from app.config import settings  # noqa: E402
from app.etl import (  # noqa: E402
    RESULTADO_FALLO,
    CandadoOcupado,
    ConciliacionFallida,
    ErrorDeDatos,
    ErrorDeEtl,
    conciliar_sin_escribir,
    describir_error_de_base,
    ejecutar_etl,
    formatear_conciliacion,
    formatear_resumen,
)
from app.loader import (  # noqa: E402
    ErrorDeCarga,
    MotorNoSoportado,
    sanear_mensaje,
    verificar_url,
)

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1
CODIGO_ORIGEN_RECHAZADO = 2
CODIGO_CONCILIACION_FALLIDA = 3
CODIGO_CANDADO_OCUPADO = 4


def construir_parser() -> argparse.ArgumentParser:
    """Argumentos del comando. No hay opción para pasar la URL de la base."""
    parser = argparse.ArgumentParser(
        prog="etl_analitico.py",
        description=(
            "ETL de FetalAlert: carga el esquema analitico desde el esquema "
            "operacional de PostgreSQL, de forma incremental e idempotente, en "
            "una única transacción que se confirma solo si la conciliación es "
            "completa."
        ),
    )
    ordenes = parser.add_subparsers(dest="orden", required=True, metavar="orden")
    ordenes.add_parser(
        "ejecutar",
        help="carga dimensiones, bridge y hechos nuevos, y concilia antes de confirmar",
    )
    ordenes.add_parser(
        "conciliar",
        help="solo conciliación, en una transacción de solo lectura",
    )
    return parser


def _fallo(mensaje: str, codigo: int) -> int:
    print(f"resultado={RESULTADO_FALLO}", file=sys.stderr)
    print(f"Error: {mensaje}", file=sys.stderr)
    return codigo


def main(argv: list[str] | None = None) -> int:
    argumentos = construir_parser().parse_args(argv)

    try:
        # Sobre la URL, sin conectarse: un destino que no es PostgreSQL se
        # rechaza antes de que exista un engine que pueda crear algo.
        verificar_url(settings.database_url)
    except MotorNoSoportado:
        return _fallo(
            "DATABASE_URL no apunta a una base PostgreSQL válida. El ETL solo "
            "opera contra PostgreSQL; el valor configurado no se muestra porque "
            "puede contener credenciales.",
            CODIGO_DE_ERROR,
        )

    try:
        engine = create_engine(settings.database_url)
        try:
            if argumentos.orden == "ejecutar":
                resultado = ejecutar_etl(engine)
                print(formatear_resumen(resultado))
                return CODIGO_DE_EXITO

            informe = conciliar_sin_escribir(engine)
            print(formatear_conciliacion(informe))
            return (
                CODIGO_DE_EXITO if informe.correcta else CODIGO_CONCILIACION_FALLIDA
            )
        finally:
            engine.dispose()

    except CandadoOcupado as error:
        return _fallo(error.detalle, CODIGO_CANDADO_OCUPADO)
    except ConciliacionFallida as error:
        print(formatear_conciliacion(error.informe), file=sys.stderr)
        return _fallo(error.detalle, CODIGO_CONCILIACION_FALLIDA)
    except ErrorDeDatos as error:
        return _fallo(
            f"{error.detalle} No se conservó ningún cambio.", CODIGO_ORIGEN_RECHAZADO
        )
    except ErrorDeEtl as error:
        return _fallo(error.detalle, CODIGO_CONCILIACION_FALLIDA)
    except ErrorDeCarga as error:
        return _fallo(sanear_mensaje(str(error)), CODIGO_DE_ERROR)
    except SQLAlchemyError as error:
        return _fallo(
            f"{describir_error_de_base(error)}. No se conservó ningún cambio: la "
            "transacción completa fue revertida.",
            CODIGO_DE_ERROR,
        )


if __name__ == "__main__":
    raise SystemExit(main())
