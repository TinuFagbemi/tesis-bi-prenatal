"""Aprovisionamiento de los roles NOLOGIN de FetalAlert (SCRUM-98).

Uso desde la raíz del repositorio::

    python scripts/bootstrap_roles.py aplicar     # crea lo que falte y verifica
    python scripts/bootstrap_roles.py verificar   # solo verifica, no escribe

Ejecuta ``scripts/bootstrap_roles.sql`` -- el mismo archivo que ``psql -1 -f``
aplicaría -- dentro de una única transacción, y después comprueba el resultado
contra el catálogo.

**Orden obligatorio en un clúster nuevo.** Los cuatro pasos son independientes y
los ejecutan responsables distintos; este script es el segundo:

    1. despliegue/CI crea los roles con LOGIN desde secretos externos
       (fetalalert_api, fetalalert_etl, fetalalert_powerbi y, en CI, el rol de
       pruebas restringido). Nunca desde este repositorio.
    2. **este bootstrap** crea los tres roles NOLOGIN y la membresía del
       migrador en fetalalert_mantenimiento.
    3. Alembic aplica schemas, tablas, funciones, índices, policies, RLS y todos
       los GRANT/REVOKE. Alembic no crea ni elimina roles.
    4. el cargador del dataset simulado y el ETL ya pueden ejecutarse.

El paso 1 puede adelantarse al 2, pero el 3 no puede correr antes del 2: las
migraciones conceden privilegios a roles que deben existir, y abortan si no.

La conexión sale de ``ALEMBIC_DATABASE_URL``, que es la credencial
administrativa y la única que necesita CREATEROLE. Nunca se recibe por
argumento, nunca se imprime y nunca aparece en un mensaje de error.

Códigos de salida:

    0  éxito
    1  error de configuración, de precondición o de base de datos
    2  el clúster no cumple lo verificado tras aplicar el bootstrap
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_DEL_REPOSITORIO / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402  -- tras ajustar sys.path

from app.config import (  # noqa: E402
    VARIABLE_URL_ALEMBIC,
    UrlDeEntornoInvalida,
    exigir_url_de_entorno,
)
from app.db.roles import (  # noqa: E402
    ROLES_NOLOGIN,
    ROL_MANTENIMIENTO,
    InformeDeRoles,
    evaluar_roles,
)
from app.loader import sanear_mensaje  # noqa: E402

ARCHIVO_SQL = Path(__file__).resolve().with_suffix(".sql")

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1
CODIGO_NO_CONFORME = 2


def construir_parser() -> argparse.ArgumentParser:
    """Argumentos del comando. No hay opción para pasar la URL de la base."""
    parser = argparse.ArgumentParser(
        prog="bootstrap_roles.py",
        description=(
            "Crea los roles NOLOGIN de FetalAlert y la membresía del migrador en "
            "fetalalert_mantenimiento. Es idempotente y no contiene contraseñas: "
            "los roles con LOGIN son responsabilidad del despliegue o del CI."
        ),
    )
    ordenes = parser.add_subparsers(dest="orden", required=True, metavar="orden")
    ordenes.add_parser("aplicar", help="ejecuta el SQL del bootstrap y verifica")
    ordenes.add_parser("verificar", help="solo verifica el estado, sin escribir")
    return parser


# Prefijo de los mensajes que este bootstrap escribe a propósito. Un
# ``RAISE EXCEPTION`` del archivo SQL empieza así, y es texto que el repositorio
# redactó: puede mostrarse entero. Cualquier otro error viene del servidor o del
# driver y solo viaja su clase.
PREFIJO_DEL_BOOTSTRAP = "bootstrap abortado:"


def _describir(error: BaseException) -> str:
    """Un mensaje publicable para cualquier fallo, del origen que sea.

    **Por qué no basta con capturar ``SQLAlchemyError``.** El SQL del bootstrap
    se ejecuta contra el cursor del driver, no a través del dialecto, así que un
    ``RAISE EXCEPTION`` de PL/pgSQL llega como ``psycopg.errors.RaiseException``
    y nunca pasa por la envoltura de SQLAlchemy. Capturar solo la de SQLAlchemy
    dejaría escapar la excepción y el proceso terminaría con un traceback, que
    es exactamente lo que no debe ocurrir: el traceback de un error de driver
    lleva la sentencia, sus parámetros y la URL de conexión.

    Se conserva el texto que el propio bootstrap redactó -- el que explica qué
    rol no es conforme y por qué --, porque es información del repositorio y es
    la que hace accionable el fallo. De cualquier otro error solo viaja el
    nombre de la clase, saneado además por si acaso.
    """
    texto = sanear_mensaje(str(error))
    inicio = texto.find(PREFIJO_DEL_BOOTSTRAP)
    if inicio != -1:
        # Hasta el final de la frase del bootstrap, sin el contexto que el
        # driver añade alrededor (sentencia, posición, parámetros).
        propio = texto[inicio:].split(chr(10) + "CONTEXT:")[0]
        propio = propio.split(chr(10) + "[SQL")[0]
        return propio.strip()
    return (
        f"El bootstrap no pudo completarse ({type(error).__name__}). No se "
        "conservó ningún cambio: la transacción completa fue revertida. El "
        "detalle del servidor no se muestra porque puede contener la sentencia "
        "y la credencial de conexión."
    )


def _ejecutar_sql_literal(conexion, sql: str) -> None:
    """Ejecuta el archivo tal cual, sin que nadie interprete sus ``%``.

    El bootstrap usa ``format('CREATE ROLE %I ...')`` de PL/pgSQL, y ``%I`` es
    un especificador de PostgreSQL, no un marcador de parámetro. psycopg 3
    inspecciona la sentencia en busca de marcadores siempre que reciba
    ``params`` -- y ``exec_driver_sql`` le pasa una tupla vacía, no ``None`` --,
    así que rechaza el archivo con «only '%s', '%b', '%t' are allowed as
    placeholders». Con ``params=None`` no hay inspección, y eso es exactamente
    lo que hace un cursor del driver invocado con un solo argumento.

    Bajar al cursor crudo es lo que permite que el archivo siga siendo SQL puro
    y que ``psql -1 -f`` y este runner ejecuten el mismo texto, carácter por
    carácter. Escapar los ``%`` a ``%%`` habría roto el camino de psql.

    La sentencia es un archivo versionado del repositorio: no hay ningún valor
    de entrada que interpolar, y por tanto nada que parametrizar.
    """
    cursor_crudo = conexion.connection.dbapi_connection.cursor()
    try:
        cursor_crudo.execute(sql)
    finally:
        cursor_crudo.close()


def _fallo(mensaje: str, codigo: int) -> int:
    print(f"Error: {mensaje}", file=sys.stderr)
    return codigo


def _imprimir(informe: InformeDeRoles) -> None:
    """Estado de los roles. Nunca imprime la URL ni nada que venga de ella."""
    for nombre in sorted(ROLES_NOLOGIN):
        estado = informe.roles.get(nombre)
        if estado is None:
            print(f"  {nombre:<28} AUSENTE")
        else:
            capacidades = ", ".join(estado.capacidades_indebidas) or "ninguna"
            print(f"  {nombre:<28} presente  capacidades indebidas: {capacidades}")
    miembros = ", ".join(sorted(informe.miembros_de_mantenimiento)) or "(ninguno)"
    print(f"  miembros de {ROL_MANTENIMIENTO}: {miembros}")
    print(f"conforme={str(informe.conforme).lower()}")


def main(argv: list[str] | None = None) -> int:
    argumentos = construir_parser().parse_args(argv)

    try:
        url = exigir_url_de_entorno(VARIABLE_URL_ALEMBIC)
    except UrlDeEntornoInvalida as error:
        return _fallo(str(error), CODIGO_DE_ERROR)

    if not ARCHIVO_SQL.is_file():
        return _fallo(
            f"No se encontró el SQL del bootstrap en {ARCHIVO_SQL.name}.",
            CODIGO_DE_ERROR,
        )

    engine = create_engine(url)
    try:
        with engine.connect() as conexion:
            if argumentos.orden == "aplicar":
                # ``with conexion.begin()`` revierte la transacción completa si
                # algo dentro lanza, así que un rol no conforme no deja el
                # clúster a medio aprovisionar.
                with conexion.begin():
                    _ejecutar_sql_literal(
                        conexion, ARCHIVO_SQL.read_text(encoding="utf-8")
                    )

            # Se verifica siempre, también después de aplicar: lo que importa
            # es el estado final del clúster, no que el SQL no lanzara error.
            with conexion.begin():
                conexion.execute(text("SET TRANSACTION READ ONLY"))
                # Por omision exige al usuario conectado: 'verificar' no puede decir
                # conforme=true en un cluster donde el migrador no podria migrar.
                informe = evaluar_roles(conexion)
    except Exception as error:  # noqa: BLE001 -- ver el docstring de _describir
        return _fallo(_describir(error), CODIGO_DE_ERROR)
    finally:
        engine.dispose()

    _imprimir(informe)
    if not informe.conforme:
        return _fallo(informe.motivo, CODIGO_NO_CONFORME)
    return CODIGO_DE_EXITO


if __name__ == "__main__":
    raise SystemExit(main())
