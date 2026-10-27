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

from app.etl import (
    AVISO_DE_CANDIDATA,
    CandadoOcupado,
    CandidataDeCarga,
    CargaInconsistente,
    ConciliacionFallida,
    DerivacionAmbigua,
    DiscrepanciaDeSemaforo,
    InformeDeConciliacion,
    Previsualizacion,
    ReglaNoDefinida,
    Verificacion,
    describir_error_de_base,
)
from app.etl.ejecucion import OrigenPodriaFiltrarse

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
    monkeypatch.setenv("ETL_DATABASE_URL", f"sqlite:///{archivo}")

    assert cli.main(["ejecutar"]) == cli.CODIGO_DE_ERROR
    salida = capsys.readouterr()
    assert "resultado=FAILED" in salida.err
    assert str(archivo) not in salida.out + salida.err
    assert not archivo.exists()


def test_una_url_mal_formada_no_se_repite(cli, capsys, monkeypatch):
    monkeypatch.setenv("ETL_DATABASE_URL", f"postgresql+psycopg//usuario:{CLAVE_FICTICIA}@host/db")

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
    monkeypatch.setenv("ETL_DATABASE_URL", URL_FICTICIA)

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
    monkeypatch.setenv("ETL_DATABASE_URL", URL_FICTICIA)
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


# ---------------------------------------------------------------------------
# previsualizar (SCRUM-104)
# ---------------------------------------------------------------------------

def _previsualizacion(candidatas: tuple = ()) -> Previsualizacion:
    return Previsualizacion(
        revision="9c1d7f2ab4e8",
        version_umbrales="SIM-1.0",
        instantanea="2026-01-01T00:00:00+00:00",
        candado_ocupado=False,
        hechos_existentes=1180,
        filas_de_dimension=(("dim_clinica", 3), ("dim_embarazo", 30)),
        filas_de_bridge=23,
        candidatas=candidatas,
    )


def _candidata(id_lectura: int = 1281) -> CandidataDeCarga:
    return CandidataDeCarga(
        id_lectura=id_lectura,
        id_sesion=833,
        id_embarazo=130,
        id_clinica=100,
        id_medico=100,
        semana_gestacion=28,
        trimestre=3,
        fecha_clinica="2026-01-01",
        estado_hr="OK",
        estado_spo2="WARNING",
        estado_mov=None,
        semaforo_derivado="WARNING",
        semaforo_origen="WARNING",
        embarazo_en_dimension=True,
    )


def test_la_ayuda_tambien_describe_previsualizar(cli, capsys):
    with pytest.raises(SystemExit) as salida:
        cli.main(["--help"])

    assert salida.value.code == 0
    assert "previsualizar" in capsys.readouterr().out


def test_previsualizar_no_invoca_la_ejecucion_real(cli, capsys, monkeypatch):
    monkeypatch.setenv("ETL_DATABASE_URL", URL_FICTICIA)

    def prohibido(*_, **__):
        raise AssertionError("previsualizar no debe ejecutar ni conciliar")

    monkeypatch.setattr(cli, "ejecutar_etl", prohibido)
    monkeypatch.setattr(cli, "conciliar_sin_escribir", prohibido)
    monkeypatch.setattr(
        cli, "previsualizar_sin_escribir", lambda engine: _previsualizacion((_candidata(),))
    )

    assert cli.main(["previsualizar"]) == cli.CODIGO_DE_EXITO
    salida = capsys.readouterr().out
    assert "modo=SOLO_LECTURA" in salida
    assert "resultado=PREVIEW" in salida
    assert "candidatas=1" in salida
    assert "escrituras=0" in salida
    assert f"aviso={AVISO_DE_CANDIDATA}" in salida
    assert (
        "candidata id_lectura=1281 id_sesion=833 id_embarazo=130 id_clinica=100 "
        "id_medico=100 semana=28 trimestre=3 fecha_clinica=2026-01-01 "
        "estado_hr=OK estado_spo2=WARNING estado_mov=NA "
        "semaforo_derivado=WARNING semaforo_origen=WARNING validacion=COINCIDE "
        "en_dim_embarazo=SI destino=fact_lectura_biometrica"
    ) in salida
    # Nunca "SUCCESS": una previsualizacion no es una ejecucion confirmada.
    assert "resultado=SUCCESS" not in salida


def test_previsualizar_sin_candidatas_tambien_es_exito(cli, capsys, monkeypatch):
    monkeypatch.setenv("ETL_DATABASE_URL", URL_FICTICIA)
    monkeypatch.setattr(cli, "previsualizar_sin_escribir", lambda engine: _previsualizacion())

    assert cli.main(["previsualizar"]) == cli.CODIGO_DE_EXITO
    salida = capsys.readouterr().out
    assert "candidatas=0" in salida
    assert "\ncandidata " not in salida


def test_la_previsualizacion_no_tiene_donde_llevar_un_valor_biometrico():
    """El contrato de salida no declara ningun campo de valor ni dato personal."""
    campos = set(CandidataDeCarga.__dataclass_fields__)

    assert not campos & {"hr_valor", "spo2_valor", "mov_valor", "id_paciente"}
    assert not any("nombre" in campo or "cedula" in campo for campo in campos)


@pytest.mark.parametrize(
    ("error", "codigo"),
    [
        (
            DiscrepanciaDeSemaforo("1 lectura(s) tienen un semáforo distinto."),
            "CODIGO_ORIGEN_RECHAZADO",
        ),
        (ReglaNoDefinida("lectura id_lectura=1: sin regla."), "CODIGO_ORIGEN_RECHAZADO"),
        (OrigenPodriaFiltrarse("El ETL podria leer un subconjunto."), "CODIGO_CONCILIACION_FALLIDA"),
        (
            OperationalError(None, None, Exception(f"connection failed: {URL_FICTICIA}")),
            "CODIGO_DE_ERROR",
        ),
    ],
    ids=["semaforo", "regla", "origen-filtrado", "base-de-datos"],
)
def test_previsualizar_conserva_los_codigos_y_no_revela_credenciales(
    cli, capsys, monkeypatch, error, codigo
):
    monkeypatch.setenv("ETL_DATABASE_URL", URL_FICTICIA)

    def falla(engine, **_):
        raise error

    monkeypatch.setattr(cli, "previsualizar_sin_escribir", falla)

    assert cli.main(["previsualizar"]) == getattr(cli, codigo)
    salida = capsys.readouterr()
    assert "resultado=FAILED" in salida.err
    for fragmento in (CLAVE_FICTICIA, "usuario_ficticio", "127.0.0.1:1"):
        assert fragmento not in salida.out + salida.err


def test_un_origen_rechazado_en_previsualizar_no_dice_que_se_revirtio_una_carga(
    cli, capsys, monkeypatch
):
    monkeypatch.setenv("ETL_DATABASE_URL", URL_FICTICIA)

    def falla(engine, **_):
        raise ReglaNoDefinida("lectura id_lectura=1: sin regla.")

    monkeypatch.setattr(cli, "previsualizar_sin_escribir", falla)

    assert cli.main(["previsualizar"]) == cli.CODIGO_ORIGEN_RECHAZADO
    error = capsys.readouterr().err
    assert "La ejecución real rechazaría este origen" in error
    assert "la previsualización no escribió nada" in error
