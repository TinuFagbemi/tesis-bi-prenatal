"""Pruebas del cargador que no necesitan un servidor PostgreSQL.

Cubren la lectura y validación del dataset, la conversión de tipos, las guardias
de ambiente y de motor, y el comportamiento del comando. El ciclo real contra
PostgreSQL 16 vive en ``test_load_mock_data_postgresql.py``.

Las pruebas de preflight que sí necesitan una conexión usan SQLite en memoria:
alcanza para comprobar que un motor distinto de PostgreSQL se rechaza y que una
base sin ``alembic_version`` se reconoce como no migrada, sin levantar nada.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import inspect
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.config import settings
from app.loader import (
    AMBIENTES_PERMITIDOS,
    MAPA_SECCIONES,
    METADATA_APROBADA,
    ORDEN_DE_CARGA,
    RUTA_POR_DEFECTO,
    SECCIONES_INFORMATIVAS,
    AmbienteNoPermitido,
    DatasetInvalido,
    EsquemaDesactualizado,
    MotorNoSoportado,
    ResultadoCarga,
    ResultadoTabla,
    cargar_dataset,
    formatear_resumen,
    leer_dataset,
    leer_revision_desplegada,
    normalizar_valor,
    obtener_head,
    preflight,
    proximo_valor,
    sanear_mensaje,
    tabla_operacional,
    validar_dataset,
    verificar_ambiente,
    verificar_dialecto,
    verificar_revision,
    verificar_url,
)
from app.models.enums import NombreRol
from tests.test_generate_mock_data import cargar_generador

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI_PATH = REPO_ROOT / "scripts" / "load_mock_data.py"
FUENTES_DEL_CARGADOR = (
    REPO_ROOT / "backend" / "app" / "loader" / "dataset.py",
    REPO_ROOT / "backend" / "app" / "loader" / "postgres.py",
    REPO_ROOT / "backend" / "app" / "loader" / "__init__.py",
    CLI_PATH,
)


def codigo_efectivo(ruta: Path) -> str:
    """Fuente sin comentarios ni docstrings, conservando el resto de literales.

    Un ``in`` sobre el archivo crudo no distingue el código de la prosa que
    explica el código: estos módulos *documentan* que no usan TRUNCATE ni
    ``create_all``, y esa explicación no debe hacer fallar la prueba. Lo que se
    revisa es el árbol sintáctico reimpreso, donde los comentarios ya no están
    y los docstrings se retiran, pero el SQL literal sigue presente.
    """
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))

    for nodo in ast.walk(arbol):
        if not isinstance(
            nodo, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        primero = nodo.body[0] if nodo.body else None
        if (
            isinstance(primero, ast.Expr)
            and isinstance(primero.value, ast.Constant)
            and isinstance(primero.value.value, str)
        ):
            nodo.body.pop(0)

    return ast.unparse(arbol)


def cargar_cli():
    """Importa scripts/load_mock_data.py, que no es parte de un paquete."""
    spec = importlib.util.spec_from_file_location("load_mock_data", CLI_PATH)

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


cli = cargar_cli()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def dataset_crudo(tmp_path_factory) -> dict:
    """Dataset aprobado, generado una sola vez en un directorio temporal.

    Se usa el generador oficial con su semilla fija en vez de un archivo a mano,
    para que estas pruebas fallen si el dataset real deja de ser cargable.
    """
    generador = cargar_generador()
    carpeta = tmp_path_factory.mktemp("dataset_simulado")

    with pytest.MonkeyPatch.context() as parche:
        parche.setattr(generador, "CARPETA_SALIDA", carpeta)
        generador.main()

    return json.loads(
        (carpeta / "dataset_fetalalert.json").read_text(encoding="utf-8")
    )


@pytest.fixture
def dataset(dataset_crudo) -> dict:
    """Copia mutable para que cada prueba estropee lo suyo sin afectar al resto."""
    return copy.deepcopy(dataset_crudo)


@pytest.fixture
def archivo_de_dataset(tmp_path, dataset) -> Path:
    ruta = tmp_path / "dataset_fetalalert.json"
    ruta.write_text(json.dumps(dataset), encoding="utf-8")
    return ruta


# ---------------------------------------------------------------------------
# Lectura del archivo
# ---------------------------------------------------------------------------


def test_archivo_inexistente_indica_ejecutar_el_generador(tmp_path):
    with pytest.raises(DatasetInvalido) as error:
        leer_dataset(tmp_path / "no_existe.json")

    assert "generate_mock_data.py" in str(error.value)


def test_json_invalido_falla_sin_tocar_la_base(tmp_path):
    ruta = tmp_path / "roto.json"
    ruta.write_text('{"metadata": ', encoding="utf-8")

    with pytest.raises(DatasetInvalido) as error:
        leer_dataset(ruta)

    assert "no es JSON válido" in str(error.value)


def test_un_json_que_no_es_objeto_falla(tmp_path):
    ruta = tmp_path / "lista.json"
    ruta.write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(DatasetInvalido):
        leer_dataset(ruta)


def test_el_dataset_aprobado_se_valida_completo(dataset):
    normalizado = validar_dataset(dataset)

    assert set(normalizado) == set(ORDEN_DE_CARGA)
    assert sum(len(filas) for filas in normalizado.values()) == 2270


# ---------------------------------------------------------------------------
# Metadata aprobada
# ---------------------------------------------------------------------------


def test_metadata_ausente_falla(dataset):
    del dataset["metadata"]

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "metadata" in str(error.value)


def test_metadata_que_no_es_objeto_falla(dataset):
    dataset["metadata"] = "Dataset simulado FetalAlert"

    with pytest.raises(DatasetInvalido):
        validar_dataset(dataset)


@pytest.mark.parametrize("campo", sorted(METADATA_APROBADA))
def test_metadata_exige_cada_campo_aprobado(dataset, campo):
    del dataset["metadata"][campo]

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert campo in str(error.value)


def test_metadata_rechaza_un_nombre_distinto_del_aprobado(dataset):
    dataset["metadata"]["nombre"] = "Otro dataset"

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "nombre" in str(error.value)


def test_metadata_rechaza_una_semilla_distinta(dataset):
    dataset["metadata"]["semilla"] = 12345

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "semilla" in str(error.value)


def test_metadata_rechaza_un_total_de_sesiones_distinto(dataset):
    dataset["metadata"]["total_sesiones"] = 700

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "total_sesiones" in str(error.value)


def test_metadata_rechaza_un_total_de_lecturas_distinto(dataset):
    dataset["metadata"]["total_registros_biometricos"] = 1200

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "total_registros_biometricos" in str(error.value)


def test_metadata_rechaza_un_uso_distinto_del_aprobado(dataset):
    dataset["metadata"]["uso"] = "Producción"

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "uso" in str(error.value)


def test_metadata_debe_coincidir_con_el_tamano_real_de_las_colecciones(dataset):
    """Metadata correcta pero colecciones truncadas: sigue sin ser cargable."""
    dataset["sesiones_monitoreo"].pop()

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "731" in str(error.value)


# ---------------------------------------------------------------------------
# Estructura de las colecciones
# ---------------------------------------------------------------------------


def test_seccion_requerida_ausente_falla(dataset):
    del dataset["roles"]

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "roles" in str(error.value)


def test_seccion_que_no_es_lista_falla(dataset):
    dataset["roles"] = {"id_rol": 100}

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "lista" in str(error.value)


def test_una_fila_que_no_es_objeto_falla(dataset):
    dataset["roles"][0] = ["id_rol", 100]

    with pytest.raises(DatasetInvalido):
        validar_dataset(dataset)


def test_pk_duplicada_en_el_archivo_falla(dataset):
    dataset["clinicas"].append(copy.deepcopy(dataset["clinicas"][0]))

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "llave" in str(error.value)


def test_campo_obligatorio_ausente_falla(dataset):
    del dataset["pacientes"][0]["cedula"]

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "cedula" in str(error.value)


def test_campo_desconocido_en_una_fila_falla(dataset):
    dataset["pacientes"][0]["columna_inventada"] = "x"

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "columna_inventada" in str(error.value)


# ---------------------------------------------------------------------------
# Conversión de tipos
# ---------------------------------------------------------------------------


def test_fecha_invalida_falla(dataset):
    dataset["pacientes"][0]["fecha_nac"] = "31 de febrero"

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "fecha_nac" in str(error.value)


def test_datetime_sin_zona_horaria_falla(dataset):
    dataset["lecturas_biometricas"][0]["fecha_hora_captura"] = "2025-02-24T11:20:00"

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "zona horaria" in str(error.value)


def test_normaliza_enteros_y_decimales_como_equivalentes():
    columna = tabla_operacional("lectura_biometrica").c.hr_valor

    assert normalizar_valor(columna, 97) == Decimal("97.00")
    assert normalizar_valor(columna, Decimal("97.00")) == normalizar_valor(columna, 97)


def test_normaliza_los_enums_por_su_valor():
    columna = tabla_operacional("rol").c.nombre_rol

    assert normalizar_valor(columna, NombreRol.ADMIN) == "ADMIN"
    assert normalizar_valor(columna, "ADMIN") == "ADMIN"


def test_normaliza_datetimes_a_utc_conservando_el_instante():
    columna = tabla_operacional("lectura_biometrica").c.fecha_hora_captura

    normalizado = normalizar_valor(columna, "2025-02-24T06:20:00-05:00")

    assert normalizado == datetime(2025, 2, 24, 11, 20, tzinfo=timezone.utc)
    assert normalizado.tzinfo is not None


def test_no_convierte_null_en_cero_ni_en_cadena_vacia():
    lectura = tabla_operacional("lectura_biometrica")

    assert normalizar_valor(lectura.c.mov_valor, None) is None
    assert normalizar_valor(lectura.c.hr_valor, None) is None
    assert normalizar_valor(lectura.c.fecha_hora_sincronizacion, None) is None


def test_conserva_los_booleanos_como_booleanos():
    columna = tabla_operacional("medico_clinica").c.activo

    assert normalizar_valor(columna, True) is True
    assert normalizar_valor(columna, False) is False

    with pytest.raises(DatasetInvalido):
        normalizar_valor(columna, "true")


def test_un_entero_donde_se_espera_texto_falla():
    columna = tabla_operacional("clinica").c.nombre_clinica

    with pytest.raises(DatasetInvalido):
        normalizar_valor(columna, 42)


# ---------------------------------------------------------------------------
# Reglas clínicas del dataset
# ---------------------------------------------------------------------------


def _primera_lectura(dataset: dict, con_movimiento: bool) -> dict:
    for fila in dataset["lecturas_biometricas"]:
        if (fila["mov_valor"] is not None) == con_movimiento:
            return fila
    raise AssertionError("el dataset no trae lecturas de ese tipo")


def test_forma_hr_con_movimiento_falla(dataset):
    _primera_lectura(dataset, con_movimiento=False)["mov_valor"] = 12

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "forma" in str(error.value)


def test_lectura_sin_ninguna_metrica_falla(dataset):
    lectura = _primera_lectura(dataset, con_movimiento=False)
    lectura["hr_valor"] = None
    lectura["spo2_valor"] = None

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "forma" in str(error.value)


def test_sincronizacion_anterior_a_captura_falla(dataset):
    lectura = dataset["lecturas_biometricas"][0]
    lectura["fecha_hora_sincronizacion"] = "2024-01-01T00:00:00+00:00"

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "sincroniza antes" in str(error.value)


def test_movimiento_fetal_antes_de_la_semana_20_falla(dataset):
    temprano = min(
        fila["id_tiempo_gest"]
        for fila in dataset["tiempo_gestacional"]
        if fila["semana_gestacion"] < 20
    )
    _primera_lectura(dataset, con_movimiento=True)["id_tiempo_gest"] = temprano

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "semana" in str(error.value)


def test_referencia_interna_inexistente_falla(dataset):
    dataset["lecturas_biometricas"][0]["id_sesion"] = 999999

    with pytest.raises(DatasetInvalido) as error:
        validar_dataset(dataset)

    assert "sesion_monitoreo" in str(error.value)


# ---------------------------------------------------------------------------
# Mapeo y orden de carga
# ---------------------------------------------------------------------------


def test_el_mapeo_cubre_las_21_tablas_y_excluye_auditoria_log():
    from app.db.base import Base

    fisicas = {tabla.name for tabla in Base.metadata.tables.values()}
    cargadas = set(MAPA_SECCIONES.values())

    assert len(cargadas) == 21
    assert cargadas == set(ORDEN_DE_CARGA)
    assert fisicas - cargadas == {"auditoria_log"}


def test_usuarios_administradores_no_es_una_seccion_cargable(dataset):
    """Los dos administradores ya viven en 'usuarios'; cargarlos aparte los duplicaría."""
    assert "usuarios_administradores" in SECCIONES_INFORMATIVAS
    assert "usuarios_administradores" not in MAPA_SECCIONES

    normalizado = validar_dataset(dataset)
    ids_admin = [fila["id_usuario"] for fila in dataset["usuarios_administradores"]]
    ids_usuario = [fila["id_usuario"] for fila in normalizado["usuario"]]

    assert len(normalizado["usuario"]) == 37
    for identificador in ids_admin:
        assert ids_usuario.count(identificador) == 1


def test_el_orden_de_carga_respeta_todas_las_llaves_foraneas():
    posicion = {nombre: indice for indice, nombre in enumerate(ORDEN_DE_CARGA)}

    for nombre in ORDEN_DE_CARGA:
        for llave in tabla_operacional(nombre).foreign_keys:
            destino = llave.column.table.name
            if destino == nombre or destino not in posicion:
                continue
            assert posicion[destino] < posicion[nombre], (
                f"{nombre}.{llave.parent.name} depende de {destino}, "
                "que se carga después"
            )


def test_auditoria_log_no_aparece_en_el_orden_de_carga():
    assert "auditoria_log" not in ORDEN_DE_CARGA


# ---------------------------------------------------------------------------
# Guardia de ambiente
# ---------------------------------------------------------------------------


def test_app_env_de_produccion_es_rechazado():
    with pytest.raises(AmbienteNoPermitido) as error:
        verificar_ambiente("production")

    assert "producción" in str(error.value)


@pytest.mark.parametrize("ambiente", sorted(AMBIENTES_PERMITIDOS))
def test_los_ambientes_seguros_son_aceptados(ambiente):
    verificar_ambiente(ambiente)


def test_app_env_de_produccion_no_construye_ningun_engine(monkeypatch, capsys):
    """La guardia corre antes del engine: nada llega a conectarse."""

    def explotar(*args, **kwargs):
        raise AssertionError("no debió construirse ningún engine")

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(cli, "create_engine", explotar)

    assert cli.main([]) == 1
    assert "APP_ENV" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Guardia de motor y preflight
# ---------------------------------------------------------------------------


def test_una_url_que_no_es_postgresql_se_rechaza_sin_crear_el_archivo(tmp_path):
    destino = tmp_path / "por_error.db"

    with pytest.raises(MotorNoSoportado) as error:
        verificar_url(f"sqlite:///{destino}")

    assert "PostgreSQL" in str(error.value)
    assert not destino.exists()


def test_una_url_malformada_se_rechaza():
    with pytest.raises(MotorNoSoportado):
        verificar_url("esto no es una url")


def test_la_url_de_postgresql_es_aceptada():
    verificar_url("postgresql+psycopg://usuario:clave@localhost:5432/base")


def test_el_preflight_rechaza_un_motor_que_no_es_postgresql():
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conexion:
            with pytest.raises(MotorNoSoportado):
                verificar_dialecto(conexion)
            with pytest.raises(MotorNoSoportado):
                preflight(conexion)
    finally:
        engine.dispose()


def test_la_revision_desplegada_es_none_sin_alembic_version():
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as conexion:
            assert leer_revision_desplegada(conexion) is None
    finally:
        engine.dispose()


def test_verificar_revision_rechaza_una_base_sin_migrar():
    with pytest.raises(EsquemaDesactualizado) as error:
        verificar_revision(None, obtener_head())

    assert "alembic upgrade head" in str(error.value)


def test_verificar_revision_rechaza_una_revision_distinta_de_head():
    with pytest.raises(EsquemaDesactualizado) as error:
        verificar_revision("revision_vieja", obtener_head())

    assert "revision_vieja" in str(error.value)


def test_verificar_revision_acepta_exactamente_head():
    verificar_revision(obtener_head(), obtener_head())


def test_el_head_se_obtiene_de_alembic_y_no_esta_escrito_en_el_codigo():
    head = obtener_head()

    assert head

    for fuente in FUENTES_DEL_CARGADOR:
        assert head not in fuente.read_text(encoding="utf-8"), (
            f"{fuente.name} tiene la revisión '{head}' escrita a mano; "
            "debe resolverse dinámicamente con Alembic"
        )


# ---------------------------------------------------------------------------
# Orden real: validación -> preflight -> transacción -> carga
# ---------------------------------------------------------------------------


class _ContextoFalso:
    """Sustituto de ``engine.connect()`` / ``engine.begin()`` que deja rastro."""

    def __init__(self, eventos: list[str], etiqueta: str):
        self.eventos = eventos
        self.etiqueta = etiqueta

    def __enter__(self):
        self.eventos.append(self.etiqueta)
        return object()

    def __exit__(self, *_):
        self.eventos.append(f"{self.etiqueta}:salida")
        return False


class _MotorFalso:
    """Engine que solo registra en qué orden lo usan."""

    def __init__(self, eventos: list[str]):
        self.eventos = eventos

    def connect(self):
        return _ContextoFalso(self.eventos, "connect")

    def begin(self):
        return _ContextoFalso(self.eventos, "begin")

    def dispose(self):
        self.eventos.append("dispose")


def test_el_cli_ejecuta_el_preflight_antes_de_abrir_la_transaccion(monkeypatch):
    """El orden observable es validación, preflight, transacción y carga.

    El preflight de Alembic corre en su propia conexión y termina *antes* de
    que ``engine.begin()`` abra la transacción de escritura, de modo que
    comprobar el esquema nunca forma parte de la transacción que se confirma.
    """
    eventos: list[str] = []
    revisiones: dict[str, str] = {}

    def falso_leer(ruta):
        return {}

    def falsa_validacion(dataset):
        eventos.append("validar")
        return {}

    def falso_preflight(conexion):
        eventos.append("preflight")
        return "revision-de-prueba"

    def falsa_carga(conexion, dataset, *, revision, ruta=None):
        eventos.append("cargar_dataset")
        revisiones["recibida"] = revision
        return _resultado(0, 0)

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(
        settings, "database_url", "postgresql+psycopg://u:c@localhost:5432/base"
    )
    monkeypatch.setattr(cli, "leer_dataset", falso_leer)
    monkeypatch.setattr(cli, "validar_dataset", falsa_validacion)
    monkeypatch.setattr(cli, "create_engine", lambda url: _MotorFalso(eventos))
    monkeypatch.setattr(cli, "preflight", falso_preflight)
    monkeypatch.setattr(cli, "cargar_dataset", falsa_carga)

    assert cli.main([]) == 0

    assert eventos == [
        "validar",
        "connect",
        "preflight",
        "connect:salida",
        "begin",
        "cargar_dataset",
        "begin:salida",
        "dispose",
    ]
    # La carga trabaja con la revisión que el preflight ya confirmó.
    assert revisiones["recibida"] == "revision-de-prueba"


def test_cargar_dataset_exige_la_revision_ya_comprobada():
    """La revisión llega desde fuera; la transacción no vuelve a consultarla."""
    parametros = inspect.signature(cargar_dataset).parameters

    assert parametros["revision"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parametros["revision"].default is inspect.Parameter.empty

    cuerpo = inspect.getsource(cargar_dataset)
    for prohibido in ("preflight(", "obtener_head(", "leer_revision_desplegada("):
        assert prohibido not in cuerpo, (
            f"cargar_dataset ejecuta {prohibido} dentro de la transacción"
        )


# ---------------------------------------------------------------------------
# Lo que el cargador tiene prohibido hacer
# ---------------------------------------------------------------------------


def test_el_cargador_no_crea_el_esquema():
    """El esquema lo despliega Alembic; el cargador solo inserta filas."""
    for fuente in FUENTES_DEL_CARGADOR:
        assert "create_all" not in codigo_efectivo(fuente), (
            f"{fuente.name} invoca create_all"
        )


def test_el_cargador_no_maneja_la_transaccion_por_su_cuenta():
    """La transacción es de quien llama: el paquete no hace commit ni rollback.

    El comando la abre con ``engine.begin()`` y las pruebas la revierten
    explícitamente; si el cargador confirmara por su cuenta, un error dejaría
    una carga parcial confirmada.
    """
    paquete = (
        REPO_ROOT / "backend" / "app" / "loader" / "dataset.py",
        REPO_ROOT / "backend" / "app" / "loader" / "postgres.py",
    )
    for fuente in paquete:
        codigo = codigo_efectivo(fuente)
        assert ".commit()" not in codigo, f"{fuente.name} confirma la transacción"
        assert ".rollback()" not in codigo, f"{fuente.name} revierte la transacción"


@pytest.mark.parametrize("operacion", ["TRUNCATE", "DROP ", "DELETE ", "ON CONFLICT"])
def test_el_cargador_no_contiene_operaciones_destructivas(operacion):
    """Sin borrados, sin DDL destructiva y sin ON CONFLICT que oculte conflictos.

    ``ON CONFLICT DO NOTHING`` se tragaría en silencio una violación UNIQUE
    distinta de la llave primaria, que es justo lo que este ticket exige
    detectar.
    """
    for fuente in FUENTES_DEL_CARGADOR:
        assert operacion not in codigo_efectivo(fuente).upper(), (
            f"{fuente.name} ejecuta {operacion}"
        )


def test_ningun_mensaje_de_error_revela_la_url_ni_la_contrasena():
    mensaje = (
        "connection to postgresql+psycopg://fetalalert:clave_secreta@host:5432/base "
        "failed"
    )

    saneado = sanear_mensaje(mensaje)

    assert "clave_secreta" not in saneado
    assert "fetalalert:" not in saneado
    assert "<credenciales>" in saneado


# Contraseñas que rompen cualquier regex ingenua de "usuario:contraseña": los
# propios delimitadores aparecen dentro del secreto, codificados y sin codificar.
CONTRASENAS_HOSTILES = [
    pytest.param("zorro_lince", ("zorro_lince",), id="sin-caracteres-especiales"),
    pytest.param("zorro%40lince", ("zorro%40lince", "lince"), id="arroba-codificada"),
    pytest.param("zorro%2Flince", ("zorro%2Flince", "lince"), id="barra-codificada"),
    pytest.param("zorro@lince", ("zorro@lince", "lince"), id="arroba-sin-codificar"),
    pytest.param("zorro/lince", ("zorro/lince", "lince"), id="barra-sin-codificar"),
    pytest.param("zorro lince", ("zorro", "lince"), id="espacio-sin-codificar"),
]


@pytest.mark.parametrize(("contrasena", "fragmentos"), CONTRASENAS_HOSTILES)
def test_sanear_mensaje_oculta_usuario_y_contrasena(contrasena, fragmentos):
    """Ni la contraseña ni un pedazo suyo pueden sobrevivir al saneo."""
    mensaje = (
        f"connection to postgresql+psycopg://ardilla:{contrasena}@host:5432/base "
        "failed: timeout"
    )

    saneado = sanear_mensaje(mensaje)

    assert "ardilla" not in saneado
    for fragmento in fragmentos:
        assert fragmento not in saneado
    assert "<credenciales>" in saneado
    # El contexto útil sobrevive: qué driver era y qué falló.
    assert saneado.startswith("connection to postgresql+psycopg://")
    assert saneado.endswith("failed: timeout")


def test_sanear_mensaje_oculta_una_url_truncada_sin_host():
    """Sin host después del último `@`, lo que sigue es cola de contraseña."""
    saneado = sanear_mensaje("no se pudo conectar a postgresql://ardilla:zorro@lince")

    assert saneado == "no se pudo conectar a postgresql://<credenciales>"


def test_sanear_mensaje_oculta_el_usuario_aunque_no_haya_contrasena():
    saneado = sanear_mensaje("connection to postgresql://ardilla@host/base failed")

    assert "ardilla" not in saneado
    assert "<credenciales>" in saneado


def test_sanear_mensaje_respeta_las_urls_sin_credenciales():
    """Una URL sin userinfo no esconde nada y sigue siendo legible."""
    mensaje = "detalle en https://sqlalche.me/e/20/e3q8"

    assert sanear_mensaje(mensaje) == mensaje


def test_sanear_mensaje_trata_cada_url_del_mensaje_por_separado():
    mensaje = (
        "fallo postgresql://ardilla:zorro@host/base y ver https://sqlalche.me/e/20/e3q8"
    )

    saneado = sanear_mensaje(mensaje)

    assert "ardilla" not in saneado
    assert "zorro" not in saneado
    assert "https://sqlalche.me/e/20/e3q8" in saneado


def test_sanear_mensaje_oculta_las_credenciales_de_dos_urls_del_mismo_mensaje():
    """Dos URLs con credenciales distintas: ninguna sobrevive, el texto sí.

    Cada URL trae un delimitador hostil dentro de la contraseña: la primera un
    `@` sin codificar, la segunda una barra codificada.
    """
    mensaje = (
        "fallo postgresql://ardilla:zorro@lince@host-alfa/base-alfa"
        " y al reintentar "
        "postgresql://tejon:nutria%2Fgamo@host-beta/base-beta tampoco conectó"
    )

    saneado = sanear_mensaje(mensaje)

    for usuario in ("ardilla", "tejon"):
        assert usuario not in saneado
    for fragmento in ("zorro", "lince", "nutria", "gamo", "%2F"):
        assert fragmento not in saneado
    assert saneado.count("<credenciales>") == 2
    assert " y al reintentar " in saneado
    assert saneado == (
        "fallo postgresql://<credenciales> y al reintentar "
        "postgresql://<credenciales> tampoco conectó"
    )


def test_el_motor_no_soportado_no_incluye_la_url():
    with pytest.raises(MotorNoSoportado) as error:
        verificar_url("mysql+pymysql://usuario:clave_secreta@localhost/base")

    assert "clave_secreta" not in str(error.value)


# ---------------------------------------------------------------------------
# Secuencias: aritmética sin servidor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("last_value", "is_called", "esperado"),
    [
        (3, True, 4),  # secuencia ya usada: el próximo es el siguiente
        (140, False, 140),  # recién reiniciada: el próximo es ese mismo valor
    ],
)
def test_proximo_valor_de_una_secuencia(last_value, is_called, esperado):
    assert proximo_valor(last_value, is_called) == esperado


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------


def _resultado(insertados: int, existentes: int) -> ResultadoCarga:
    return ResultadoCarga(
        revision="rev",
        tablas=(
            ResultadoTabla(
                tabla="clinica",
                total=insertados + existentes,
                insertados=insertados,
                existentes=existentes,
            ),
        ),
        secuencias=(),
        sesiones_verificadas=732,
        lecturas_verificadas=1180,
        ruta=Path("data/generated/dataset_fetalalert.json"),
    )


def test_el_resumen_reporta_archivo_tablas_insertados_existentes_y_commit():
    resumen = formatear_resumen(_resultado(2270, 0), confirmada=True)

    assert "dataset_fetalalert.json" in resumen
    assert "Tablas procesadas: 1" in resumen
    assert "Registros insertados: 2270" in resumen
    assert "Registros existentes sin cambios: 0" in resumen
    assert "Sesiones verificadas: 732" in resumen
    assert "Lecturas verificadas: 1180" in resumen
    assert "commit confirmado" in resumen
    assert "Sin duplicados" in resumen


def test_el_resumen_de_una_carga_sin_inserciones_lo_dice_explicitamente():
    resumen = formatear_resumen(_resultado(0, 2270), confirmada=True)

    assert "Registros insertados: 0" in resumen
    assert "Registros existentes sin cambios: 2270" in resumen


def test_el_resumen_no_afirma_commit_cuando_no_lo_hubo():
    resumen = formatear_resumen(_resultado(0, 2270), confirmada=False)

    assert "sin commit" in resumen


# ---------------------------------------------------------------------------
# Comando
# ---------------------------------------------------------------------------


def test_el_cli_usa_la_ruta_por_defecto_del_dataset():
    argumentos = cli.construir_parser().parse_args([])

    assert argumentos.ruta == RUTA_POR_DEFECTO


def test_el_cli_acepta_una_ruta_alternativa(tmp_path):
    alternativa = tmp_path / "otro.json"

    argumentos = cli.construir_parser().parse_args([str(alternativa)])

    assert argumentos.ruta == alternativa


@pytest.mark.parametrize(
    "argumento",
    ["--database-url", "--dsn", "--password"],
)
def test_el_cli_no_acepta_la_url_de_conexion_como_argumento(argumento):
    with pytest.raises(SystemExit):
        cli.construir_parser().parse_args([argumento, "postgresql://x/y"])


def test_el_cli_termina_con_codigo_distinto_de_cero_ante_un_error(
    monkeypatch, tmp_path, capsys
):
    def explotar(*args, **kwargs):
        raise AssertionError("no debió construirse ningún engine")

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(cli, "create_engine", explotar)

    assert cli.main([str(tmp_path / "no_existe.json")]) == 1
    assert "generate_mock_data.py" in capsys.readouterr().err
