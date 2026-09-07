"""Idempotencia del endpoint de ingesta, capa HTTP (SCRUM-63).

Aíslan el router con dobles: la persistencia se reemplaza por una función que la
prueba controla y la ``Session`` por un doble que guiona las tres sentencias que
el endpoint ejecuta y **anota su orden**. Lo que se comprueba aquí es la
decisión: cuándo se reclama la clave, cuándo se reproduce una respuesta, cuándo
se confirma y cuándo se revierte.

El orden importa tanto como el conteo, y por eso el doble lo registra. Tres
afirmaciones del diseño son afirmaciones sobre el orden y no sobre cantidades:

* la reclamación ocurre **antes** de verificar referencias y de escribir nada;
* la recuperación del ganador ocurre **sin un rollback previo**, en la misma
  transacción;
* un reenvío reconocido no escribe una sola fila.

La integración real contra PostgreSQL 16 -- concurrencia incluida -- no vive
aquí y estas pruebas no la sustituyen.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.v1.sesiones import (
    CABECERA_IDEMPOTENCIA,
    CABECERA_REPLAY,
    REPLAY_NO,
    REPLAY_SI,
)
from app.db.session import get_db
from app.main import app
from app.schemas.monitoreo import SesionMonitoreoEntrada
from app.services import idempotencia as modulo_idempotencia
from app.services.errores import MENSAJE_INESPERADO
from app.services.idempotencia import (
    LONGITUD_MAXIMA_DE_CLAVE,
    LONGITUD_MINIMA_DE_CLAVE,
    MENSAJE_CLAVE_INVALIDA,
    MENSAJE_COLISION,
    PATRON_DE_CLAVE,
    RECURSO_SESIONES_MONITOREO,
    hash_de_clave,
    huella_del_paquete,
)
from app.services.ingesta import ReferenciaInexistente, ResultadoIngesta
from tests.test_ingestion_api import (
    CLAVE_VALIDA,
    RUTA,
    SesionFalsa,
    error_de_integridad,
    reclamacion_falsa,
)
from tests.test_ingestion_schemas import lectura_hr, paquete

from app.services.errores import SQLSTATE_FOREIGN_KEY, SQLSTATE_UNIQUE

# Identificadores ficticios que el doble devuelve como si los hubiera asignado
# PostgreSQL. El orden de ``IDS_LECTURA`` no es ascendente a propósito: es el
# orden en que se guardaron, y es el que la respuesta tiene que reproducir.
ID_SESION = 733
IDS_LECTURA = (1181, 1182, 1183)

RESULTADO_DE_EJEMPLO = ResultadoIngesta(id_sesion=ID_SESION, ids_lectura=IDS_LECTURA)


def huella_de(cuerpo: dict[str, Any]) -> str:
    return huella_del_paquete(SesionMonitoreoEntrada.model_validate(cuerpo))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def servicio(monkeypatch):
    """Sustituye la persistencia por una función que la prueba controla."""

    def instalar(comportamiento=RESULTADO_DE_EJEMPLO):
        registro: dict[str, Any] = {"llamadas": 0}

        def falso(sesion_bd, entrada):
            registro["llamadas"] += 1
            if isinstance(comportamiento, BaseException):
                raise comportamiento
            return comportamiento

        monkeypatch.setattr(modulo_idempotencia, "registrar_sesion", falso)
        return registro

    return instalar


def cliente_con(sesion_falsa, *, clave: str | None = CLAVE_VALIDA) -> TestClient:
    """Cliente atado a un doble de Session concreto.

    Se construye a mano en vez de con una fixture parametrizada porque cada
    prueba necesita guionar su propio doble antes de que exista el cliente.
    """
    app.dependency_overrides[get_db] = lambda: sesion_falsa
    cabeceras = {} if clave is None else {CABECERA_IDEMPOTENCIA: clave}
    return TestClient(app, headers=cabeceras)


@pytest.fixture(autouse=True)
def limpiar_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. La clave: obligatoria, con formato, y sin escribir nada cuando falla
# ---------------------------------------------------------------------------


CLAVES_INVALIDAS = {
    "vacia": "",
    "corta": "1234567",
    "larga": "a" * 129,
    "con_espacio": "clave con espacio",
    "con_barra": "clave/invalida",
    "con_punto": "clave.invalida",
    "con_mas": "clave+invalida",
    "con_porcentaje": "clave%20invalida",
}


def test_una_clave_valida_se_acepta(servicio):
    servicio()
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 201


def test_sin_cabecera_la_solicitud_se_rechaza_con_400(servicio):
    registro = servicio()
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion, clave=None).post(RUTA, json=paquete())

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == MENSAJE_CLAVE_INVALIDA
    assert registro["llamadas"] == 0
    assert sesion.pasos == []


@pytest.mark.parametrize(
    "clave", list(CLAVES_INVALIDAS.values()), ids=list(CLAVES_INVALIDAS)
)
def test_una_clave_mal_formada_se_rechaza_con_400(clave, servicio):
    registro = servicio()
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion, clave=clave).post(RUTA, json=paquete())

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == MENSAJE_CLAVE_INVALIDA
    assert registro["llamadas"] == 0
    assert sesion.pasos == []


@pytest.mark.parametrize(
    "clave",
    ["12345678", "a" * 128, "550e8400-e29b-41d4-a716-446655440000", "A_b-9_z-0000"],
    ids=["minima", "maxima", "uuid", "mezcla"],
)
def test_las_claves_del_patron_aprobado_se_aceptan(clave, servicio):
    servicio()

    respuesta = cliente_con(SesionFalsa(), clave=clave).post(RUTA, json=paquete())

    assert respuesta.status_code == 201


def test_una_clave_no_ascii_no_llega_siquiera_al_endpoint():
    """El transporte HTTP la rechaza antes: no es un caso que el router deba cubrir.

    Se deja escrito porque el patrón aprobado excluye los caracteres no ASCII y
    podría parecer que falta la prueba de que el endpoint los rechaza. No falta:
    una cabecera con esos caracteres no puede enviarse, así que el 400 por
    formato solo es alcanzable con claves ASCII mal formadas.
    """
    with pytest.raises(UnicodeEncodeError):
        cliente_con(SesionFalsa(), clave="clave-invalidña").post(
            RUTA, json=paquete()
        )


def test_una_clave_ausente_gana_a_un_cuerpo_invalido(servicio):
    """Precedencia aprobada: sin encuadre válido, el cuerpo ni se mira.

    La dependencia se resuelve antes de que FastAPI valide el cuerpo contra el
    modelo, así que el 400 llega aunque el paquete también esté mal. Fijarlo por
    escrito hace que un cambio de versión del framework que invirtiera el orden
    se note aquí y no en producción.
    """
    registro = servicio()
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion, clave=None).post(
        RUTA, json={"id_embarazo": "no-es-un-entero", "campo_inventado": 1}
    )

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == MENSAJE_CLAVE_INVALIDA
    assert registro["llamadas"] == 0
    assert sesion.pasos == []


def test_con_clave_valida_un_cuerpo_invalido_sigue_siendo_422(servicio):
    registro = servicio()
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion).post(RUTA, json={"campo_inventado": 1})

    assert respuesta.status_code == 422
    assert registro["llamadas"] == 0
    assert sesion.pasos == []


# ---------------------------------------------------------------------------
# 2. Primera ejecución
# ---------------------------------------------------------------------------


def test_la_primera_solicitud_crea_el_paquete(servicio):
    registro = servicio()
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 201
    assert respuesta.json() == {
        "id_sesion": ID_SESION,
        "lecturas_creadas": len(IDS_LECTURA),
        "ids_lectura": list(IDS_LECTURA),
    }
    assert registro["llamadas"] == 1


def test_la_primera_solicitud_no_se_marca_como_reenvio(servicio):
    servicio()

    respuesta = cliente_con(SesionFalsa()).post(RUTA, json=paquete())

    assert respuesta.headers[CABECERA_REPLAY] == REPLAY_NO


def test_la_primera_solicitud_reclama_la_clave_antes_de_escribir_el_paquete(servicio):
    """El orden es el diseño: consultar, reclamar, escribir, completar, confirmar."""
    servicio()
    sesion = SesionFalsa()

    cliente_con(sesion).post(RUTA, json=paquete())

    assert sesion.pasos == ["select", "insert", "update", "commit"]
    assert sesion.commits == 1
    assert sesion.rollbacks == 0


def test_la_reclamacion_se_completa_con_la_sesion_y_las_lecturas(servicio):
    servicio()
    sesion = SesionFalsa()

    cliente_con(sesion).post(RUTA, json=paquete())

    assert sesion.valores_actualizados["id_sesion"] == ID_SESION
    assert sesion.valores_actualizados["ids_lectura"] == list(IDS_LECTURA)


def test_la_clave_se_reclama_aunque_el_paquete_acabe_fallando(servicio):
    """Reclamar antes de verificar es lo que hace observable «falló después».

    El servicio rechaza una referencia, que es un fallo *posterior* a la
    reclamación. La reclamación existe, el rollback se la lleva, y por eso la
    clave no queda envenenada.
    """
    servicio(ReferenciaInexistente("No existe un embarazo con id_embarazo=100."))
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 404
    assert sesion.pasos == ["select", "insert", "rollback"]
    assert sesion.commits == 0


# ---------------------------------------------------------------------------
# 3. Replay secuencial por la vía rápida
# ---------------------------------------------------------------------------


def test_un_reenvio_identico_devuelve_201_y_el_mismo_cuerpo(servicio):
    cuerpo = paquete()
    registro = servicio()
    sesion = SesionFalsa(
        reclamaciones=[reclamacion_falsa(huella_de(cuerpo), ID_SESION, IDS_LECTURA)]
    )

    respuesta = cliente_con(sesion).post(RUTA, json=cuerpo)

    assert respuesta.status_code == 201
    assert respuesta.json() == {
        "id_sesion": ID_SESION,
        "lecturas_creadas": len(IDS_LECTURA),
        "ids_lectura": list(IDS_LECTURA),
    }
    assert registro["llamadas"] == 0


def test_un_reenvio_identico_se_marca_como_reenvio(servicio):
    cuerpo = paquete()
    servicio()
    sesion = SesionFalsa(reclamaciones=[reclamacion_falsa(huella_de(cuerpo))])

    respuesta = cliente_con(sesion).post(RUTA, json=cuerpo)

    assert respuesta.headers[CABECERA_REPLAY] == REPLAY_SI


def test_un_reenvio_identico_no_escribe_ni_una_fila(servicio):
    cuerpo = paquete()
    servicio()
    sesion = SesionFalsa(reclamaciones=[reclamacion_falsa(huella_de(cuerpo))])

    cliente_con(sesion).post(RUTA, json=cuerpo)

    assert sesion.pasos == ["select", "rollback"]
    assert sesion.inserts == 0
    assert sesion.updates == 0
    assert sesion.commits == 0
    assert sesion.rollbacks == 1


def test_el_reenvio_reproduce_el_orden_almacenado_de_ids_lectura(servicio):
    """El orden sale del array guardado, no de ordenar llaves primarias."""
    cuerpo = paquete()
    desordenados = (1183, 1181, 1182)
    servicio()
    sesion = SesionFalsa(
        reclamaciones=[reclamacion_falsa(huella_de(cuerpo), ID_SESION, desordenados)]
    )

    respuesta = cliente_con(sesion).post(RUTA, json=cuerpo)

    assert respuesta.json()["ids_lectura"] == list(desordenados)
    assert respuesta.json()["lecturas_creadas"] == len(desordenados)


def test_dos_solicitudes_seguidas_devuelven_exactamente_lo_mismo(servicio):
    """Primera y reenvío, uno detrás del otro, con el estado que dejaría la base."""
    cuerpo = paquete()
    servicio()

    primera_sesion = SesionFalsa()
    primera = cliente_con(primera_sesion).post(RUTA, json=cuerpo)

    segunda_sesion = SesionFalsa(
        reclamaciones=[reclamacion_falsa(huella_de(cuerpo), ID_SESION, IDS_LECTURA)]
    )
    segunda = cliente_con(segunda_sesion).post(RUTA, json=cuerpo)

    assert primera.status_code == segunda.status_code == 201
    assert primera.json() == segunda.json()
    assert primera.headers[CABECERA_REPLAY] == REPLAY_NO
    assert segunda.headers[CABECERA_REPLAY] == REPLAY_SI


# ---------------------------------------------------------------------------
# 4. Colisión: misma clave, otro contenido
# ---------------------------------------------------------------------------


def test_la_misma_clave_con_otro_contenido_devuelve_409(servicio):
    registro = servicio()
    sesion = SesionFalsa(reclamaciones=[reclamacion_falsa("otra-huella-distinta")])

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 409
    assert respuesta.json()["detail"] == MENSAJE_COLISION
    assert registro["llamadas"] == 0


def test_la_colision_no_escribe_nada_y_revierte_una_vez(servicio):
    servicio()
    sesion = SesionFalsa(reclamaciones=[reclamacion_falsa("otra-huella-distinta")])

    cliente_con(sesion).post(RUTA, json=paquete())

    assert sesion.pasos == ["select", "rollback"]
    assert sesion.commits == 0
    assert sesion.rollbacks == 1


def test_la_colision_no_devuelve_identificadores(servicio):
    servicio()
    sesion = SesionFalsa(reclamaciones=[reclamacion_falsa("otra-huella-distinta")])

    cuerpo = cliente_con(sesion).post(RUTA, json=paquete()).json()

    assert "id_sesion" not in cuerpo
    assert "ids_lectura" not in cuerpo


def test_el_mensaje_de_colision_no_nombra_el_campo_que_difiere(servicio):
    """Decirlo permitiría sondear el paquete guardado comparando respuestas."""
    servicio()
    sesion = SesionFalsa(reclamaciones=[reclamacion_falsa("otra-huella-distinta")])

    detalle = cliente_con(sesion).post(RUTA, json=paquete()).json()["detail"]

    for campo in ("hr_valor", "spo2_valor", "fecha_hora_captura", "id_embarazo"):
        assert campo not in detalle
    assert "otra-huella-distinta" not in detalle


def test_una_clave_distinta_no_colisiona_con_la_anterior(servicio):
    """El 409 es de la clave, no del contenido: otra clave abre otro paquete."""
    servicio()
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion, clave="clave-distinta-0002").post(
        RUTA, json=paquete()
    )

    assert respuesta.status_code == 201


# ---------------------------------------------------------------------------
# 5. Perder la reclamación y recuperar al ganador
# ---------------------------------------------------------------------------


def test_perder_la_reclamacion_reproduce_la_respuesta_del_ganador(servicio):
    cuerpo = paquete()
    registro = servicio()
    sesion = SesionFalsa(
        reclamaciones=[None, reclamacion_falsa(huella_de(cuerpo), ID_SESION, IDS_LECTURA)],
        id_reclamado=None,
    )

    respuesta = cliente_con(sesion).post(RUTA, json=cuerpo)

    assert respuesta.status_code == 201
    assert respuesta.headers[CABECERA_REPLAY] == REPLAY_SI
    assert respuesta.json()["id_sesion"] == ID_SESION
    assert respuesta.json()["ids_lectura"] == list(IDS_LECTURA)
    assert registro["llamadas"] == 0


def test_la_recuperacion_no_lleva_un_rollback_por_delante(servicio):
    """El SELECT de recuperación va en la misma transacción, sin cortarla antes.

    Revertir primero abriría una segunda transacción solo para leer, que
    quedaría abierta hasta que se cerrara la Session. El orden que se afirma
    aquí es exactamente el aprobado.
    """
    cuerpo = paquete()
    servicio()
    sesion = SesionFalsa(
        reclamaciones=[None, reclamacion_falsa(huella_de(cuerpo))],
        id_reclamado=None,
    )

    cliente_con(sesion).post(RUTA, json=cuerpo)

    assert sesion.pasos == ["select", "insert", "select", "rollback"]
    assert sesion.commits == 0
    assert sesion.rollbacks == 1


def test_perder_la_reclamacion_con_otro_contenido_es_409(servicio):
    servicio()
    sesion = SesionFalsa(
        reclamaciones=[None, reclamacion_falsa("otra-huella-distinta")],
        id_reclamado=None,
    )

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 409
    assert respuesta.json()["detail"] == MENSAJE_COLISION
    assert sesion.pasos == ["select", "insert", "select", "rollback"]


# ---------------------------------------------------------------------------
# 6. Anomalías: nunca se reproducen como si fueran una respuesta
# ---------------------------------------------------------------------------


def test_una_reclamacion_sin_resultado_es_un_error_interno(servicio):
    """Una fila incompleta no describe ninguna respuesta; inventarla sería peor."""
    cuerpo = paquete()
    servicio()
    sesion = SesionFalsa(
        reclamaciones=[reclamacion_falsa(huella_de(cuerpo), id_sesion=None, ids_lectura=None)]
    )

    respuesta = cliente_con(sesion).post(RUTA, json=cuerpo)

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_INESPERADO
    assert sesion.commits == 0
    assert sesion.rollbacks == 1


def test_una_reclamacion_con_lecturas_vacias_tambien_es_un_error_interno(servicio):
    cuerpo = paquete()
    servicio()
    sesion = SesionFalsa(
        reclamaciones=[reclamacion_falsa(huella_de(cuerpo), ID_SESION, ids_lectura=())]
    )

    respuesta = cliente_con(sesion).post(RUTA, json=cuerpo)

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_INESPERADO


def test_un_ganador_que_no_aparece_es_un_error_interno_y_no_un_reintento(servicio):
    """La reclamación estaba tomada y no hay fila: no se reintenta, se falla."""
    registro = servicio()
    sesion = SesionFalsa(reclamaciones=[None, None], id_reclamado=None)

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_INESPERADO
    assert registro["llamadas"] == 0
    # Un solo intento: dos SELECT y un INSERT, nunca un segundo INSERT.
    assert sesion.pasos == ["select", "insert", "select", "rollback"]
    assert sesion.inserts == 1


def test_la_anomalia_no_devuelve_identificadores(servicio):
    servicio()
    sesion = SesionFalsa(reclamaciones=[None, None], id_reclamado=None)

    cuerpo = cliente_con(sesion).post(RUTA, json=paquete()).json()

    assert "id_sesion" not in cuerpo
    assert "ids_lectura" not in cuerpo


def test_una_fila_incompleta_con_otra_huella_es_500_y_nunca_409(servicio):
    """La integridad se comprueba antes que la huella, y el orden importa.

    Un 409 afirma algo concreto: «tu clave ya nombra un paquete *distinto*, y la
    respuesta de aquel paquete se mantiene». Una fila sin resultado no sostiene
    esa afirmación -- no hay operación completa a la que apuntar --, así que
    comparar huellas primero convertiría un defecto del servidor en una culpa
    del cliente. Incompleta es anómala, diga lo que diga el paquete entrante.
    """
    cuerpo = paquete()
    servicio()
    sesion = SesionFalsa(
        reclamaciones=[
            reclamacion_falsa("otra-huella-distinta", id_sesion=None, ids_lectura=None)
        ]
    )

    respuesta = cliente_con(sesion).post(RUTA, json=cuerpo)

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_INESPERADO
    assert respuesta.json()["detail"] != MENSAJE_COLISION
    assert sesion.commits == 0
    assert sesion.rollbacks == 1


def test_una_fila_incompleta_con_otra_huella_tambien_es_500_al_recuperar(servicio):
    """Misma prioridad en el otro camino: recuperando al ganador."""
    servicio()
    sesion = SesionFalsa(
        reclamaciones=[
            None,
            reclamacion_falsa("otra-huella-distinta", id_sesion=None, ids_lectura=None),
        ],
        id_reclamado=None,
    )

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_INESPERADO


def test_un_update_que_no_toca_ninguna_fila_impide_confirmar(servicio):
    """Sin reclamación enlazada no se confirma una sesión: se revierte.

    Si el ``UPDATE`` que ata la reclamación al resultado no encuentra su fila,
    confirmar dejaría una sesión cuya reclamación no dice nada, y el siguiente
    reenvío la leería como anomalía. Fallar aquí convierte una inconsistencia
    silenciosa en un rollback.
    """
    registro = servicio()
    sesion = SesionFalsa(filas_completadas=0)

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_INESPERADO
    assert registro["llamadas"] == 1
    assert sesion.pasos == ["select", "insert", "update", "rollback"]
    assert sesion.commits == 0


def test_un_update_que_toca_varias_filas_tambien_impide_confirmar(servicio):
    servicio()
    sesion = SesionFalsa(filas_completadas=2)

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 500
    assert sesion.commits == 0
    assert sesion.rollbacks == 1


# ---------------------------------------------------------------------------
# 7. Lo heredado de SCRUM-62 sigue igual
# ---------------------------------------------------------------------------


def test_una_carrera_de_llave_foranea_sigue_siendo_409(servicio):
    """23503 conserva su significado: no es una colisión de idempotencia."""
    servicio(error_de_integridad(SQLSTATE_FOREIGN_KEY))
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 409
    assert respuesta.json()["detail"] != MENSAJE_COLISION
    assert sesion.rollbacks == 1


def test_una_colision_de_llave_primaria_sigue_siendo_500(servicio):
    """23505 no se remapea: ``ON CONFLICT DO NOTHING`` no lo levanta nunca."""
    servicio(error_de_integridad(SQLSTATE_UNIQUE))
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_INESPERADO


def test_una_regla_del_dominio_sigue_siendo_422(servicio):
    from app.services.ingesta import ReglaDeNegocioViolada

    servicio(ReglaDeNegocioViolada("lecturas[0]: registra movimiento fetal."))
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 422
    assert sesion.rollbacks == 1


def test_un_error_inesperado_sigue_siendo_500_saneado(servicio):
    servicio(RuntimeError("detalle interno que no debe salir"))
    sesion = SesionFalsa()

    respuesta = cliente_con(sesion).post(RUTA, json=paquete())

    assert respuesta.status_code == 500
    assert respuesta.json()["detail"] == MENSAJE_INESPERADO
    assert "detalle interno" not in respuesta.text
    assert sesion.rollbacks == 1


def test_el_endpoint_de_salud_sigue_intacto():
    """SCRUM-63 no le añade cabeceras ni requisitos a /health."""
    respuesta = TestClient(app).get("/health")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"status": "ok"}
    assert CABECERA_REPLAY not in respuesta.headers


# ---------------------------------------------------------------------------
# 8. Trazabilidad segura
# ---------------------------------------------------------------------------

REGISTRADOR = "app.services.idempotencia"


def test_el_reenvio_queda_registrado_sin_la_clave_en_claro(servicio, caplog):
    cuerpo = paquete()
    servicio()
    sesion = SesionFalsa(reclamaciones=[reclamacion_falsa(huella_de(cuerpo))])

    with caplog.at_level(logging.INFO, logger=REGISTRADOR):
        cliente_con(sesion).post(RUTA, json=cuerpo)

    registro = caplog.text
    assert "evento=replay" in registro
    assert RECURSO_SESIONES_MONITOREO in registro
    assert hash_de_clave(CLAVE_VALIDA) in registro
    assert CLAVE_VALIDA not in registro


def test_la_colision_queda_registrada_como_advertencia(servicio, caplog):
    servicio()
    sesion = SesionFalsa(reclamaciones=[reclamacion_falsa("otra-huella-distinta")])

    with caplog.at_level(logging.WARNING, logger=REGISTRADOR):
        cliente_con(sesion).post(RUTA, json=paquete())

    assert "evento=colision" in caplog.text
    assert caplog.records[-1].levelno == logging.WARNING


def test_la_anomalia_queda_registrada_como_error(servicio, caplog):
    servicio()
    sesion = SesionFalsa(reclamaciones=[None, None], id_reclamado=None)

    with caplog.at_level(logging.ERROR, logger=REGISTRADOR):
        cliente_con(sesion).post(RUTA, json=paquete())

    assert "evento=anomalia" in caplog.text
    assert caplog.records[-1].levelno == logging.ERROR


@pytest.mark.parametrize(
    "escenario",
    ["replay", "colision", "anomalia"],
)
def test_el_registro_no_filtra_el_paquete_ni_la_huella(escenario, servicio, caplog):
    """Ni valores clínicos, ni la huella, ni SQL, ni la URL de conexión."""
    cuerpo = paquete(lecturas=[lectura_hr(hr_valor=88.5, spo2_valor=97.0)])
    huella = huella_de(cuerpo)
    servicio()

    if escenario == "replay":
        sesion = SesionFalsa(reclamaciones=[reclamacion_falsa(huella)])
    elif escenario == "colision":
        sesion = SesionFalsa(reclamaciones=[reclamacion_falsa("otra-huella-distinta")])
    else:
        sesion = SesionFalsa(reclamaciones=[None, None], id_reclamado=None)

    with caplog.at_level(logging.DEBUG, logger=REGISTRADOR):
        cliente_con(sesion).post(RUTA, json=cuerpo)

    registro = caplog.text
    assert registro.strip()
    for prohibido in (
        CLAVE_VALIDA,
        huella,
        "88.5",
        "97.0",
        "postgresql+psycopg://",
        "INSERT INTO",
        "SELECT",
        "Traceback",
    ):
        assert prohibido not in registro


def test_la_hash_de_clave_no_permite_recuperar_la_clave():
    handle = hash_de_clave(CLAVE_VALIDA)

    assert len(handle) == 12
    assert CLAVE_VALIDA not in handle
    assert hash_de_clave(CLAVE_VALIDA) == handle
    assert hash_de_clave("otra-clave-ficticia-0002") != handle


# ---------------------------------------------------------------------------
# 9. OpenAPI
# ---------------------------------------------------------------------------


def parametros_del_endpoint() -> list[dict[str, Any]]:
    return app.openapi()["paths"][RUTA]["post"].get("parameters", [])


def parametro_de_la_clave() -> dict[str, Any]:
    return next(
        p for p in parametros_del_endpoint() if p["name"] == CABECERA_IDEMPOTENCIA
    )


def test_openapi_publica_exactamente_un_parametro_y_es_la_clave_requerida():
    """El esquema tiene que decir la verdad: la cabecera es obligatoria.

    Es la comprobación central de esta corrección. Antes el parámetro salía con
    ``required: false`` -- lo que el ``Header(default=None)`` obligaba -- y la
    descripción textual lo desmentía, de modo que el campo estructurado y la
    prosa se contradecían. Ahora la cabecera se lee de ``Request`` y el
    parámetro se declara a mano, así que el flag coincide con el comportamiento.

    Se afirma «exactamente uno» y no «al menos uno» a propósito: declarar el
    parámetro por fuera podría duplicarlo si alguien volviera a añadirlo como
    ``Header``, y un cliente generado desde este esquema mandaría la cabecera
    dos veces.
    """
    parametros = parametros_del_endpoint()

    assert len(parametros) == 1
    parametro = parametros[0]
    assert parametro["name"] == CABECERA_IDEMPOTENCIA
    assert parametro["in"] == "header"
    assert parametro["required"] is True


def test_openapi_publica_las_restricciones_reales_de_la_clave():
    """Longitudes y patrón salen de las constantes que el servicio aplica."""
    esquema = parametro_de_la_clave()["schema"]

    assert esquema["type"] == "string"
    assert esquema["minLength"] == LONGITUD_MINIMA_DE_CLAVE == 8
    assert esquema["maxLength"] == LONGITUD_MAXIMA_DE_CLAVE == 128
    assert esquema["pattern"] == PATRON_DE_CLAVE.pattern
    assert "[A-Za-z0-9_-]{8,128}" in esquema["pattern"]


def test_el_patron_publicado_acepta_y_rechaza_lo_mismo_que_el_endpoint():
    """El contrato documentado y el exigido son el mismo objeto, no dos copias.

    Se comprueba sobre valores concretos, no solo sobre la igualdad de cadenas:
    una expresión sin anclar aceptaría subcadenas y documentaría un contrato más
    laxo que el que el endpoint aplica.
    """
    import re

    publicado = re.compile(parametro_de_la_clave()["schema"]["pattern"])

    assert publicado.fullmatch(CLAVE_VALIDA)
    for invalida in CLAVES_INVALIDAS.values():
        assert publicado.fullmatch(invalida) is None


def test_openapi_documenta_la_cabecera_de_respuesta_en_el_201():
    """``Idempotency-Replayed`` como header estructurado, no solo en la prosa."""
    respuesta = app.openapi()["paths"][RUTA]["post"]["responses"]["201"]
    cabecera = respuesta["headers"][CABECERA_REPLAY]

    assert cabecera["schema"]["type"] == "string"
    assert set(cabecera["schema"]["enum"]) == {REPLAY_SI, REPLAY_NO}
    assert cabecera["description"].strip()


def test_el_201_conserva_el_esquema_del_cuerpo_junto_a_la_cabecera():
    """Documentar el header no puede costar el ``$ref`` de la respuesta."""
    respuesta = app.openapi()["paths"][RUTA]["post"]["responses"]["201"]
    esquema = respuesta["content"]["application/json"]["schema"]

    assert esquema["$ref"].endswith("/SesionMonitoreoCreada")
    assert CABECERA_REPLAY in respuesta["headers"]


def test_openapi_declara_los_codigos_de_respuesta_de_scrum_63():
    respuestas = app.openapi()["paths"][RUTA]["post"]["responses"]

    assert {"201", "400", "404", "409", "422", "500"} <= set(respuestas)


def test_openapi_explica_las_dos_causas_del_conflicto():
    descripcion = app.openapi()["paths"][RUTA]["post"]["responses"]["409"]["description"]

    assert "idempotencia" in descripcion.lower()
    assert "referencia" in descripcion.lower()


def test_openapi_no_cambio_el_modelo_de_respuesta():
    schemas = app.openapi()["components"]["schemas"]

    assert set(schemas["SesionMonitoreoCreada"]["properties"]) == {
        "id_sesion",
        "lecturas_creadas",
        "ids_lectura",
    }
