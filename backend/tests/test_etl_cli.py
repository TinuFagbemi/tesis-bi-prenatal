"""CLI of the analytic ETL (SCRUM-69), offline: arguments, exit codes, safe output.

The script is loaded as a module and its collaborators are replaced where a
database would be needed, so every exit code is observed without a server. The
real run against PostgreSQL lives in ``test_etl_postgresql.py``.

All data is fictitious and simulated.
"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from app.config import settings
from app.etl import (
    CandadoOcupado,
    CargaInconsistente,
    ConciliacionFallida,
    DerivacionAmbigua,
    InformeDeConciliacion,
    ReglaNoDefinida,
    Verificacion,
    describir_error_de_base,
)

RAIZ = Path(__file__).resolve().parents[2]
RUTA_CLI = RAIZ / "scripts" / "etl_analitico.py"

CLAVE_FICTICIA = "clave-que-no-debe-verse"
URL_FICTICIA = (
    f"postgresql+psycopg://usuario_ficticio:{CLAVE_FICTICIA}@127.0.0.1:1/base_ficticia"
)
CEDULA_FICTICIA = "0-000-0001"
NOMBRE_FICTICIO = "Nombre Ficticio Simulado"


@pytest.fixture(scope="module")
def cli():
    """El script cargado como módulo, sin lanzar un proceso aparte."""
    spec = importlib.util.spec_from_file_location("etl_analitico", RUTA_CLI)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _informe(correcta: bool) -> InformeDeConciliacion:
    return InformeDeConciliacion(
        verificaciones=(
            Verificacion(
                "contenido_divergente", "Contenido distinto.", 0 if correcta else 1, True
            ),
        )
    )


def test_la_ayuda_describe_las_dos_ordenes(cli, capsys):
    with pytest.raises(SystemExit) as salida:
        cli.main(["--help"])

    assert salida.value.code == 0
    texto = capsys.readouterr().out
    assert "ejecutar" in texto
    assert "conciliar" in texto


def test_sin_orden_argparse_conserva_su_codigo(cli):
    with pytest.raises(SystemExit) as salida:
        cli.main([])

    assert salida.value.code == 2


def test_un_destino_que_no_es_postgresql_se_rechaza_sin_crear_nada(
    cli, capsys, monkeypatch, tmp_path
):
    archivo = tmp_path / "no_debe_existir.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{archivo}")

    assert cli.main(["ejecutar"]) == cli.CODIGO_DE_ERROR
    salida = capsys.readouterr()
    assert "resultado=FAILED" in salida.err
    assert str(archivo) not in salida.out + salida.err
    assert not archivo.exists()


def test_una_url_mal_formada_no_se_repite(cli, capsys, monkeypatch):
    monkeypatch.setattr(
        settings, "database_url", f"postgresql+psycopg//usuario:{CLAVE_FICTICIA}@host/db"
    )

    assert cli.main(["conciliar"]) == cli.CODIGO_DE_ERROR
    salida = capsys.readouterr()
    assert CLAVE_FICTICIA not in salida.out + salida.err


@pytest.mark.parametrize(
    ("error", "codigo"),
    [
        (CandadoOcupado("Otra ejecución tiene el candado."), "CODIGO_CANDADO_OCUPADO"),
        (ConciliacionFallida(_informe(correcta=False)), "CODIGO_CONCILIACION_FALLIDA"),
        (CargaInconsistente("El hecho no aceptó todas las filas."), "CODIGO_CONCILIACION_FALLIDA"),
        (ReglaNoDefinida("lectura id_lectura=1: sin regla."), "CODIGO_ORIGEN_RECHAZADO"),
        (DerivacionAmbigua("lectura id_lectura=1: ambigua."), "CODIGO_ORIGEN_RECHAZADO"),
        (
            OperationalError(None, None, Exception(f"connection failed: {URL_FICTICIA}")),
            "CODIGO_DE_ERROR",
        ),
    ],
    ids=["candado", "conciliacion", "carga", "regla", "ambiguedad", "base-de-datos"],
)
def test_cada_fallo_tiene_su_codigo_y_no_revela_credenciales(
    cli, capsys, monkeypatch, error, codigo
):
    monkeypatch.setattr(settings, "database_url", URL_FICTICIA)

    def falla(engine, **_):
        raise error

    monkeypatch.setattr(cli, "ejecutar_etl", falla)

    assert cli.main(["ejecutar"]) == getattr(cli, codigo)
    salida = capsys.readouterr()
    assert "resultado=FAILED" in salida.err
    # The driver may be named; the user, the password and the host never are.
    for fragmento in (CLAVE_FICTICIA, "usuario_ficticio", "127.0.0.1:1"):
        assert fragmento not in salida.out + salida.err


@pytest.mark.parametrize(("correcta", "codigo"), [(True, 0), (False, 3)])
def test_conciliar_devuelve_0_o_3(cli, capsys, monkeypatch, correcta, codigo):
    monkeypatch.setattr(settings, "database_url", URL_FICTICIA)
    monkeypatch.setattr(cli, "conciliar_sin_escribir", lambda engine: _informe(correcta))

    assert cli.main(["conciliar"]) == codigo
    assert ("DISCREPANCIA contenido_divergente=1" in capsys.readouterr().out) is not correcta


def test_un_error_de_base_no_muestra_la_sentencia_ni_sus_parametros():
    error = IntegrityError(
        "INSERT INTO analitico.dim_paciente (cedula, nombre_completo) "
        "VALUES (%(cedula)s, %(nombre)s)",
        {"cedula": CEDULA_FICTICIA, "nombre": NOMBRE_FICTICIO},
        Exception(
            "duplicate key value violates unique constraint "
            f"DETAIL: Key (cedula)=({CEDULA_FICTICIA}) already exists."
        ),
    )

    descripcion = describir_error_de_base(error)

    assert descripcion.startswith("Error de base de datos (IntegrityError")
    for fragmento in (CEDULA_FICTICIA, NOMBRE_FICTICIO, "INSERT", "VALUES"):
        assert fragmento not in descripcion


def test_un_error_de_conexion_se_describe_sin_credenciales():
    error = OperationalError(
        None, None, Exception(f"connection to {URL_FICTICIA} failed: refused")
    )

    descripcion = describir_error_de_base(error)

    assert "OperationalError" in descripcion
    assert CLAVE_FICTICIA not in descripcion
