"""Pruebas de la capa HTTP del endpoint de ingesta (SCRUM-62).

Aíslan el router: la persistencia se reemplaza por un doble, de modo que lo que
se comprueba aquí es la traducción de errores, la forma de la respuesta, la
propiedad de la transacción y el saneamiento. La integración real vive en
``test_ingestion_api_postgresql.py`` y estas pruebas no la sustituyen.

El error de driver que se fabrica lleva a propósito una contraseña, una URL de
conexión y una sentencia SQL dentro de su mensaje. Ninguna de las tres debe
aparecer jamás en la respuesta ni en el log: eso es lo que varias de estas
pruebas verifican.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import DataError, IntegrityError, OperationalError

from app.api.v1 import sesiones as modulo_router
from app.db.session import get_db
from app.main import app
from app.services.errores import (
    MENSAJE_CHECK,
    MENSAJE_CONFLICTO,
    MENSAJE_INESPERADO,
    SQLSTATE_CHECK,
    SQLSTATE_FOREIGN_KEY,
    SQLSTATE_NOT_NULL,
    SQLSTATE_UNIQUE,
    DiagnosticoSeguro,
    clasificar_error_de_base,
    diagnostico_seguro,
    extraer_sqlstate,
    registrar_fallo,
)
from app.services.ingesta import (
    ReferenciaInexistente,
    ReglaDeNegocioViolada,
    ResultadoIngesta,
)
from tests.test_ingestion_schemas import lectura_hr, paquete

RUTA = "/api/v1/sesiones-monitoreo"

DIRECTORIO_APP = Path(__file__).resolve().parents[1] / "app"

# Módulos que atiende la petición. Ninguno puede invocar el cargador.
MODULOS_DE_LA_PETICION = (
    DIRECTORIO_APP / "api" / "v1" / "sesiones.py",
    DIRECTORIO_APP / "services" / "ingesta.py",
    DIRECTORIO_APP / "services" / "errores.py",
    DIRECTORIO_APP / "schemas" / "monitoreo.py",
    DIRECTORIO_APP / "db" / "session.py",
)

# Lo que el endpoint tiene prohibido ejecutar: el cargador de SCRUM-61 y la
# creación de esquema. Reutilizar una constante sin efectos laterales sí está
# permitido, y por eso la prohibición se expresa sobre llamadas, no sobre
# importaciones.
LLAMADAS_PROHIBIDAS = frozenset(
    {
        "cargar_dataset",
        "validar_dataset",
        "leer_dataset",
        "ajustar_secuencias",
        "preflight",
        "formatear_resumen",
        "load_mock_data",
        "create_all",
        "drop_all",
    }
)

# Lo único que el endpoint puede tomar de SCRUM-61.
IMPORTES_PERMITIDOS_DEL_CARGADOR = frozenset({"SEMANA_MINIMA_DE_MOVIMIENTO"})

# Fragmentos que un mensaje de driver arrastra y que no deben salir del proceso.
CLAVE_FICTICIA = "clave_super_secreta_ficticia"
URL_FICTICIA = f"postgresql+psycopg://fetalalert_dev:{CLAVE_FICTICIA}@127.0.0.1:5433/fetalalert_dev"
SQL_PELIGROSO = (
    "INSERT INTO operacional.sesion_monitoreo (id_embarazo, id_dispositivo) "
    "VALUES (%(id_embarazo)s, %(id_dispositivo)s) RETURNING id_sesion"
)
TEXTO_DE_DRIVER = (
    'duplicate key value violates unique constraint "pk_sesion_monitoreo"\n'
    "DETAIL:  Key (id_sesion)=(733) already exists.\n"
    f"[SQL: {SQL_PELIGROSO}]\n"
    f"(Background on this error at {URL_FICTICIA})"
)
FRAGMENTOS_PROHIBIDOS = (
    CLAVE_FICTICIA,
    "postgresql+psycopg://",
    "INSERT INTO",
    "duplicate key",
    "Traceback",
    "id_embarazo)s",
)


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


class DiagnosticoFalso:
    """Imita ``psycopg.Error.diag`` con los campos que el proyecto consulta."""

    def __init__(
        self,
        constraint_name: str | None = None,
        table_name: str | None = None,
        column_name: str | None = None,
    ) -> None:
        self.constraint_name = constraint_name
        self.table_name = table_name
        self.column_name = column_name


class ErrorDeDriverFalso(Exception):
    """Imita una excepción de psycopg: expone ``sqlstate`` y ``diag``."""

    def __init__(self, sqlstate: str | None, **campos: str | None) -> None:
        super().__init__(TEXTO_DE_DRIVER)
        self.sqlstate = sqlstate
        self.diag = DiagnosticoFalso(**campos)


def error_de_integridad(sqlstate: str | None, **campos: str | None) -> IntegrityError:
    return IntegrityError(SQL_PELIGROSO, {"id_embarazo": 100}, ErrorDeDriverFalso(sqlstate, **campos))


def error_de_datos(sqlstate: str = "22003") -> DataError:
    return DataError(SQL_PELIGROSO, {"mov_valor": 99999}, ErrorDeDriverFalso(sqlstate))


class SesionFalsa:
    """Doble de la Session: solo cuenta commits, rollbacks y cierres."""

    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:  # pragma: no cover -- la fixture es la dueña
        pass


@pytest.fixture
def sesion_falsa() -> SesionFalsa:
    return SesionFalsa()


@pytest.fixture
def cliente(sesion_falsa) -> TestClient:
    app.dependency_overrides[get_db] = lambda: sesion_falsa
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def servicio(monkeypatch):
    """Sustituye la persistencia por una función que la prueba controla."""

    def instalar(comportamiento):
        registro: dict[str, Any] = {"llamadas": 0, "entrada": None}

        def falso(sesion_bd, entrada):
            registro["llamadas"] += 1
            registro["entrada"] = entrada
            if isinstance(comportamiento, BaseException):
                raise comportamiento
            return comportamiento

        monkeypatch.setattr(modulo_router, "registrar_sesion", falso)
        return registro

    return instalar


RESULTADO_DE_EJEMPLO = ResultadoIngesta(id_sesion=733, ids_lectura=(1181, 1182, 1183))


# ---------------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------------


def test_un_paquete_valido_devuelve_201(cliente, servicio, sesion_falsa):
    servicio(RESULTADO_DE_EJEMPLO)

    respuesta = cliente.post(RUTA, json=paquete(lecturas=[lectura_hr() for _ in range(3)]))

    assert respuesta.status_code == 201
    assert sesion_falsa.commits == 1
    assert sesion_falsa.rollbacks == 0


def test_la_respuesta_cumple_el_schema_acordado(cliente, servicio):
    servicio(RESULTADO_DE_EJEMPLO)

    respuesta = cliente.post(RUTA, json=paquete(lecturas=[lectura_hr() for _ in range(3)]))

    assert respuesta.json() == {
        "id_sesion": 733,
        "lecturas_creadas": 3,
        "ids_lectura": [1181, 1182, 1183],
    }


def test_el_servicio_recibe_el_paquete_ya_validado(cliente, servicio):
    registro = servicio(RESULTADO_DE_EJEMPLO)

    cliente.post(RUTA, json=paquete())

    assert registro["llamadas"] == 1
    assert registro["entrada"].id_embarazo == 100
    assert len(registro["entrada"].lecturas) == 1


# ---------------------------------------------------------------------------
# Payload inválido: 422 de Pydantic, sin tocar la transacción
# ---------------------------------------------------------------------------


def test_un_payload_invalido_devuelve_422_y_no_llega_al_servicio(
    cliente, servicio, sesion_falsa
):
    registro = servicio(RESULTADO_DE_EJEMPLO)

    respuesta = cliente.post(RUTA, json=paquete(lecturas=[]))

    assert respuesta.status_code == 422
    assert registro["llamadas"] == 0
    assert sesion_falsa.commits == 0
    assert sesion_falsa.rollbacks == 0


def test_un_campo_desconocido_devuelve_422(cliente, servicio):
    servicio(RESULTADO_DE_EJEMPLO)

    respuesta = cliente.post(RUTA, json=paquete(id_sesion=733))

    assert respuesta.status_code == 422


# ---------------------------------------------------------------------------
# Errores del dominio
# ---------------------------------------------------------------------------


def test_una_referencia_inexistente_devuelve_404_y_revierte(
    cliente, servicio, sesion_falsa
):
    servicio(ReferenciaInexistente("No existe un embarazo con id_embarazo=999."))

    respuesta = cliente.post(RUTA, json=paquete())

    assert respuesta.status_code == 404
    assert respuesta.json()["detail"] == "No existe un embarazo con id_embarazo=999."
    assert sesion_falsa.commits == 0
    assert sesion_falsa.rollbacks == 1


def test_una_regla_de_negocio_violada_devuelve_422_y_revierte(
    cliente, servicio, sesion_falsa
):
    servicio(ReglaDeNegocioViolada("lecturas[0]: movimiento en la semana 12."))

    respuesta = cliente.post(RUTA, json=paquete())

    assert respuesta.status_code == 422
    assert "semana 12" in respuesta.json()["detail"]
    assert sesion_falsa.commits == 0
    assert sesion_falsa.rollbacks == 1


# ---------------------------------------------------------------------------
# Errores de base de datos: la clasificación aprobada, extremo a extremo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sqlstate", "esperado"),
    [
        (SQLSTATE_CHECK, 422),
        (SQLSTATE_FOREIGN_KEY, 409),
        (SQLSTATE_UNIQUE, 500),
        (SQLSTATE_NOT_NULL, 500),
        ("42P01", 500),
        (None, 500),
    ],
)
def test_la_clasificacion_por_sqlstate_llega_hasta_la_respuesta(
    cliente, servicio, sesion_falsa, sqlstate, esperado
):
    servicio(error_de_integridad(sqlstate))

    respuesta = cliente.post(RUTA, json=paquete())

    assert respuesta.status_code == esperado
    assert sesion_falsa.commits == 0
    assert sesion_falsa.rollbacks == 1


def test_una_colision_de_pk_generada_es_un_error_interno(cliente, servicio):
    """``23505`` sobre una PK que genera la base no puede culpar al cliente.

    El cliente no envía ``id_sesion`` ni ``id_lectura``, y ninguna de las dos
    tablas tiene un UNIQUE de negocio con el que pudiera chocar. Una llave
    duplicada aquí solo puede venir de una secuencia desincronizada, que es un
    defecto del servidor: 500, no 409. Responder 409 además sugeriría que se
    detectó un reenvío, y SCRUM-62 no detecta reenvíos.

    La colisión real contra el servidor vive en
    ``test_ingestion_api_postgresql.py``.
    """
    servicio(error_de_integridad(SQLSTATE_UNIQUE, constraint_name="pk_sesion_monitoreo"))

    respuesta = cliente.post(RUTA, json=paquete())

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_INESPERADO
    assert "id_sesion" not in respuesta.text


def test_una_carrera_de_llave_foranea_si_es_un_conflicto(cliente, servicio):
    """``23503`` es lo único que hoy merece un 409: la referencia se esfumó."""
    servicio(
        error_de_integridad(
            SQLSTATE_FOREIGN_KEY, constraint_name="fk_sesion_monitoreo_id_embarazo_embarazo"
        )
    )

    respuesta = cliente.post(RUTA, json=paquete())

    assert respuesta.status_code == 409
    assert respuesta.json()["detail"] == MENSAJE_CONFLICTO


def test_un_data_error_devuelve_500_por_defecto(cliente, servicio, sesion_falsa):
    servicio(error_de_datos())

    respuesta = cliente.post(RUTA, json=paquete())

    assert respuesta.status_code == 500
    assert sesion_falsa.rollbacks == 1


def test_un_error_operacional_devuelve_500(cliente, servicio, sesion_falsa):
    servicio(OperationalError(SQL_PELIGROSO, {}, ErrorDeDriverFalso("08006")))

    respuesta = cliente.post(RUTA, json=paquete())

    assert respuesta.status_code == 500
    assert sesion_falsa.rollbacks == 1


def test_un_error_inesperado_devuelve_500_y_revierte(cliente, servicio, sesion_falsa):
    servicio(RuntimeError(TEXTO_DE_DRIVER))

    respuesta = cliente.post(RUTA, json=paquete())

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_INESPERADO
    assert sesion_falsa.commits == 0
    assert sesion_falsa.rollbacks == 1


# ---------------------------------------------------------------------------
# Saneamiento
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sqlstate", [SQLSTATE_CHECK, SQLSTATE_UNIQUE, SQLSTATE_FOREIGN_KEY, SQLSTATE_NOT_NULL]
)
def test_la_respuesta_no_filtra_nada_del_driver(cliente, servicio, sqlstate):
    servicio(error_de_integridad(sqlstate, constraint_name="ck_lectura_biometrica_spo2_rango"))

    respuesta = cliente.post(RUTA, json=paquete())

    for fragmento in FRAGMENTOS_PROHIBIDOS:
        assert fragmento not in respuesta.text


def test_la_respuesta_no_filtra_nada_ante_un_error_inesperado(cliente, servicio):
    servicio(RuntimeError(TEXTO_DE_DRIVER))

    respuesta = cliente.post(RUTA, json=paquete())

    for fragmento in FRAGMENTOS_PROHIBIDOS:
        assert fragmento not in respuesta.text


def test_el_log_registra_solo_el_diagnostico_seguro(cliente, servicio, caplog):
    servicio(
        error_de_integridad(
            SQLSTATE_CHECK,
            constraint_name="ck_lectura_biometrica_spo2_rango",
            table_name="lectura_biometrica",
            column_name="spo2_valor",
        )
    )

    with caplog.at_level(logging.WARNING, logger="app.services.errores"):
        cliente.post(RUTA, json=paquete())

    registrado = "\n".join(registro.getMessage() for registro in caplog.records)

    assert "sqlstate=23514" in registrado
    assert "restriccion=ck_lectura_biometrica_spo2_rango" in registrado
    assert "tabla=lectura_biometrica" in registrado
    for fragmento in FRAGMENTOS_PROHIBIDOS:
        assert fragmento not in registrado


# ---------------------------------------------------------------------------
# Traducción de errores, probada directamente
# ---------------------------------------------------------------------------


def test_extraer_sqlstate_lee_el_codigo_del_driver():
    assert extraer_sqlstate(error_de_integridad(SQLSTATE_UNIQUE)) == SQLSTATE_UNIQUE


def test_extraer_sqlstate_tolera_un_error_sin_driver():
    assert extraer_sqlstate(RuntimeError("sin orig")) is None


def test_el_diagnostico_seguro_solo_expone_campos_de_nuestro_esquema():
    diagnostico = diagnostico_seguro(
        error_de_integridad(
            SQLSTATE_CHECK,
            constraint_name="ck_lectura_biometrica_spo2_rango",
            table_name="lectura_biometrica",
            column_name="spo2_valor",
        )
    )

    assert diagnostico.excepcion == "IntegrityError"
    assert diagnostico.sqlstate == SQLSTATE_CHECK
    assert diagnostico.restriccion == "ck_lectura_biometrica_spo2_rango"
    for fragmento in FRAGMENTOS_PROHIBIDOS:
        assert fragmento not in diagnostico.como_texto()


def test_el_diagnostico_seguro_no_tiene_campo_para_el_mensaje_del_driver():
    """Estructural: no se puede filtrar lo que no se puede representar."""
    campos = set(DiagnosticoSeguro.__dataclass_fields__)

    assert campos == {"excepcion", "sqlstate", "restriccion", "tabla", "columna"}


def test_clasificar_un_check_da_422_con_su_mensaje():
    respuesta = clasificar_error_de_base(error_de_integridad(SQLSTATE_CHECK))

    assert respuesta.status_code == 422
    assert respuesta.detalle == MENSAJE_CHECK


def test_clasificar_un_unique_da_500():
    """Hoy ningún UNIQUE de estas tablas puede originarlo el cliente."""
    respuesta = clasificar_error_de_base(error_de_integridad(SQLSTATE_UNIQUE))

    assert respuesta.status_code == 500
    assert respuesta.detalle == MENSAJE_INESPERADO


def test_clasificar_una_fk_da_409():
    respuesta = clasificar_error_de_base(error_de_integridad(SQLSTATE_FOREIGN_KEY))

    assert respuesta.status_code == 409
    assert respuesta.detalle == MENSAJE_CONFLICTO


def test_clasificar_un_not_null_da_500():
    """Pydantic ya cubre los obligatorios: un NULL aquí delata al servidor."""
    respuesta = clasificar_error_de_base(error_de_integridad(SQLSTATE_NOT_NULL))

    assert respuesta.status_code == 500
    assert respuesta.detalle == MENSAJE_INESPERADO


def test_clasificar_un_data_error_da_500():
    assert clasificar_error_de_base(error_de_datos()).status_code == 500


def test_clasificar_un_sqlstate_desconocido_da_500():
    assert clasificar_error_de_base(error_de_integridad("42P01")).status_code == 500


def test_clasificar_nunca_lanza_ante_un_error_sin_diagnostico():
    respuesta = clasificar_error_de_base(IntegrityError("SELECT 1", {}, RuntimeError("x")))

    assert respuesta.status_code == 500


def test_registrar_fallo_usa_error_para_los_500(caplog):
    error = error_de_integridad(SQLSTATE_NOT_NULL)
    respuesta = clasificar_error_de_base(error)

    with caplog.at_level(logging.DEBUG, logger="app.services.errores"):
        registrar_fallo(error, respuesta)

    assert [registro.levelname for registro in caplog.records] == ["ERROR"]


def test_registrar_fallo_usa_warning_para_los_4xx(caplog):
    error = error_de_integridad(SQLSTATE_FOREIGN_KEY)
    respuesta = clasificar_error_de_base(error)

    with caplog.at_level(logging.DEBUG, logger="app.services.errores"):
        registrar_fallo(error, respuesta)

    assert [registro.levelname for registro in caplog.records] == ["WARNING"]


# ---------------------------------------------------------------------------
# El endpoint no invoca al cargador ni crea esquema
# ---------------------------------------------------------------------------


def nombres_invocados(ruta: Path) -> set[str]:
    """Nombres de todo lo que el módulo *llama*, leídos del árbol sintáctico.

    Se mira la llamada y no el texto del archivo: estos módulos mencionan al
    cargador en su documentación, y explicar que no se usa no puede hacer
    fallar la prueba.
    """
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    nombres: set[str] = set()
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.Call):
            continue
        objetivo = nodo.func
        if isinstance(objetivo, ast.Name):
            nombres.add(objetivo.id)
        elif isinstance(objetivo, ast.Attribute):
            nombres.add(objetivo.attr)
    return nombres


def importes_del_cargador(ruta: Path) -> set[str]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    nombres: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom) and (nodo.module or "").startswith(
            "app.loader"
        ):
            nombres.update(alias.name for alias in nodo.names)
        if isinstance(nodo, ast.Import):
            nombres.update(
                alias.name for alias in nodo.names if alias.name.startswith("app.loader")
            )
    return nombres


@pytest.mark.parametrize("ruta", MODULOS_DE_LA_PETICION, ids=lambda ruta: ruta.name)
def test_el_endpoint_no_invoca_el_cargador_ni_crea_el_esquema(ruta):
    assert LLAMADAS_PROHIBIDAS.isdisjoint(nombres_invocados(ruta)), (
        f"{ruta.name} invoca algo que SCRUM-62 tiene prohibido ejecutar"
    )


@pytest.mark.parametrize("ruta", MODULOS_DE_LA_PETICION, ids=lambda ruta: ruta.name)
def test_del_cargador_solo_se_reutiliza_la_constante_aprobada(ruta):
    """Importar la constante está permitido; traerse el cargador entero, no."""
    assert importes_del_cargador(ruta) <= IMPORTES_PERMITIDOS_DEL_CARGADOR


def test_la_constante_de_semana_minima_se_reutiliza_y_no_se_redefine():
    from app.loader.dataset import SEMANA_MINIMA_DE_MOVIMIENTO
    from app.services import ingesta

    assert ingesta.SEMANA_MINIMA_DE_MOVIMIENTO is SEMANA_MINIMA_DE_MOVIMIENTO
    assert "SEMANA_MINIMA_DE_MOVIMIENTO =" not in (
        (DIRECTORIO_APP / "services" / "ingesta.py").read_text(encoding="utf-8")
    )


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def test_el_esquema_openapi_se_genera_sin_error():
    esquema = app.openapi()

    assert esquema["openapi"].startswith("3.")


def test_openapi_documenta_la_ruta_y_el_metodo():
    esquema = app.openapi()

    assert RUTA in esquema["paths"]
    assert list(esquema["paths"][RUTA]) == ["post"]


def test_openapi_declara_los_codigos_de_respuesta_acordados():
    respuestas = app.openapi()["paths"][RUTA]["post"]["responses"]

    assert {"201", "404", "409", "422", "500"} <= set(respuestas)


def test_openapi_publica_los_schemas_de_entrada_y_salida():
    schemas = app.openapi()["components"]["schemas"]

    assert {
        "SesionMonitoreoEntrada",
        "LecturaBiometricaEntrada",
        "SesionMonitoreoCreada",
        "TipoSesion",
        "EstadoSesion",
        "OrigenDato",
    } <= set(schemas)


def test_openapi_no_admite_campos_adicionales_en_la_entrada():
    schemas = app.openapi()["components"]["schemas"]

    assert schemas["SesionMonitoreoEntrada"]["additionalProperties"] is False
    assert schemas["LecturaBiometricaEntrada"]["additionalProperties"] is False


def test_openapi_exige_al_menos_una_lectura():
    entrada = app.openapi()["components"]["schemas"]["SesionMonitoreoEntrada"]

    assert entrada["properties"]["lecturas"]["minItems"] == 1


def test_el_endpoint_de_salud_sigue_publicado():
    """SCRUM-62 añade una ruta; no toca la que ya existía."""
    assert "/health" in app.openapi()["paths"]
