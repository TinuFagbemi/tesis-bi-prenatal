"""Captura offline: validacion, identidad estable y atomicidad local.

Lo que se demuestra aqui es la mitad del ticket que no depende de la red:
capturar funciona con la API apagada, el paquete queda entero, la clave nace una
sola vez y sobrevive, y un fallo a mitad de camino no deja media captura.

La otra mitad --clasificar respuestas y no degradar un ENVIADO-- vive en
``test_edge_emisor.py``; el ciclo real contra la API y PostgreSQL, en
``test_edge_postgresql.py``.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import ast
import inspect
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.edge import almacenamiento as alm
from app.edge import captura as cap
from app.edge import outbox
from app.edge.estados import EstadoEntrega
from app.schemas.monitoreo import SesionMonitoreoEntrada
from app.services.idempotencia import clave_valida, huella_del_paquete

# Referencias ficticias. Coinciden en forma con el ejemplo del README, pero
# ninguna prueba de este archivo toca PostgreSQL: aqui solo importa que el
# paquete cumpla el contrato.
PAQUETE_DE_UNA_LECTURA = {
    "id_embarazo": 100,
    "id_dispositivo": 100,
    "tipo_sesion": "SIGNOS_MATERNOS",
    "fecha_inicio": "2025-02-24T11:20:00+00:00",
    "fecha_fin": "2025-02-24T11:25:00+00:00",
    "estado_sesion": "COMPLETADA",
    "lecturas": [
        {
            "id_tiempo_gest": 107,
            "id_semaforo": 100,
            "fecha_hora_captura": "2025-02-24T11:21:00+00:00",
            "hr_valor": 90,
            "spo2_valor": 97,
        }
    ],
}


def paquete_de_varias_lecturas(cantidad: int = 4) -> dict:
    """Un paquete con varias lecturas, todas dentro de la ventana de la sesion."""
    paquete = json.loads(json.dumps(PAQUETE_DE_UNA_LECTURA))
    paquete["lecturas"] = [
        {
            "id_tiempo_gest": 107,
            "id_semaforo": 100,
            "fecha_hora_captura": f"2025-02-24T11:2{indice}:30+00:00",
            "hr_valor": 88 + indice,
            "spo2_valor": 95 + (indice % 3),
        }
        for indice in range(cantidad)
    ]
    return paquete


@pytest.fixture
def conexion(tmp_path):
    with alm.conectar(tmp_path / "nodo_edge.sqlite3") as conexion:
        alm.inicializar(conexion)
        yield conexion


# ---------------------------------------------------------------------------
# 1. La captura no necesita la red
# ---------------------------------------------------------------------------


def test_el_modulo_de_captura_no_importa_ningun_cliente_http():
    """Capturar no puede depender de la red, y esto lo vuelve estructural.

    Se mira el arbol de importaciones en vez de simular una desconexion: una
    prueba de comportamiento demostraria que hoy no se llama a la red, y esto
    demuestra que no hay forma de llamarla.
    """
    arbol = ast.parse(inspect.getsource(cap))
    importados = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Import):
            importados.update(alias.name.split(".")[0] for alias in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            importados.add(nodo.module.split(".")[0])

    assert "httpx" not in importados
    assert "urllib" not in importados
    assert "socket" not in importados


def test_capturar_funciona_sin_ninguna_api_disponible(conexion):
    """No hay comprobacion previa de conectividad, asi que no hay nada que fallar."""
    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
    assert registro.id_outbox > 0


# ---------------------------------------------------------------------------
# 2. Lo que deja una captura valida
# ---------------------------------------------------------------------------


def test_una_captura_valida_crea_una_captura_y_una_outbox_pendiente(conexion):
    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)

    assert conexion.execute("SELECT count(*) FROM captura_local").fetchone()[0] == 1
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["estado"] == EstadoEntrega.PENDIENTE.value
    assert fila["reintentable"] is None
    assert fila["intentos"] == 0
    assert fila["enviado_en"] is None
    assert fila["id_sesion_remota"] is None
    assert fila["ids_lectura_remotos"] is None


def test_una_captura_de_varias_lecturas_conserva_el_paquete_completo(conexion):
    paquete = paquete_de_varias_lecturas(5)
    registro = cap.capturar(conexion, paquete)

    guardado = json.loads(outbox.leer_payload(conexion, registro.id_captura))
    assert len(guardado["lecturas"]) == 5

    # Se comparan instantes, no cadenas: el volcado normaliza el offset a 'Z',
    # que es el mismo momento escrito de otra manera.
    assert [
        datetime.fromisoformat(lectura["fecha_hora_captura"])
        for lectura in guardado["lecturas"]
    ] == [
        datetime.fromisoformat(lectura["fecha_hora_captura"])
        for lectura in paquete["lecturas"]
    ]


def test_el_orden_de_las_lecturas_se_conserva(conexion):
    """El servidor incluye el orden en la huella y lo devuelve en ``ids_lectura``.

    La lista no se ordena ni se reagrupa en ningun punto del camino: se guarda
    tal como se capturo, y asi es como se enviara.
    """
    paquete = paquete_de_varias_lecturas(4)
    registro = cap.capturar(conexion, paquete)

    guardado = json.loads(outbox.leer_payload(conexion, registro.id_captura))
    assert [Decimal(lectura["hr_valor"]) for lectura in guardado["lecturas"]] == [
        Decimal(88),
        Decimal(89),
        Decimal(90),
        Decimal(91),
    ]


def test_la_captura_no_promete_nada_del_servidor(conexion):
    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
    assert not hasattr(registro, "id_sesion")
    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["ultimo_http"] is None


# ---------------------------------------------------------------------------
# 3. Identidad estable
# ---------------------------------------------------------------------------


def test_la_clave_cumple_el_formato_que_exige_el_servidor(conexion):
    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
    assert clave_valida(registro.clave)


def test_dos_capturas_distintas_reciben_claves_distintas(conexion):
    primera = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
    segunda = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
    assert primera.clave != segunda.clave


def test_la_clave_se_genera_una_sola_vez(conexion):
    """Una captura pide exactamente una clave; ningun camino la recalcula."""
    llamadas = []

    def generador():
        llamadas.append(1)
        return f"clave-determinista-{len(llamadas):04d}"

    cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA, generador_de_clave=generador)
    assert len(llamadas) == 1


def test_la_fila_conserva_su_clave_al_releerse(conexion):
    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
    for _ in range(3):
        fila = outbox.leer_evento(conexion, registro.id_outbox)
        assert fila["clave_idempotencia"] == registro.clave


def test_la_clave_no_puede_derivarse_del_paquete(conexion):
    """La identidad es aleatoria, no una funcion del contenido.

    La comprobacion es estructural y no textual a proposito. Buscar subcadenas
    como "90" o "97" dentro de la clave seria una prueba inestable --dos digitos
    hexadecimales cualesquiera reaparecen por azar en una parte apreciable de
    los UUID-- y ademas no probaria nada: lo que garantiza que la clave no lleva
    dato clinico es que el generador **no recibe el paquete** y produce un UUID4,
    cuyos bits son aleatorios salvo los de version y variante.
    """
    assert inspect.signature(cap.generar_clave).parameters == {}

    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
    identificador = uuid.UUID(registro.clave)
    assert identificador.version == 4
    assert str(identificador) == registro.clave

    # Y no lleva letras fuera del alfabeto hexadecimal, asi que ningun valor
    # textual del paquete --un enum, por ejemplo-- podria estar dentro.
    assert set(registro.clave) <= set("0123456789abcdef-")


# ---------------------------------------------------------------------------
# 4. Atomicidad
# ---------------------------------------------------------------------------


def test_un_fallo_entre_las_dos_inserciones_revierte_todo(conexion, monkeypatch):
    """Si la outbox no puede escribirse, la captura tampoco queda."""
    original = outbox.registrar

    def registrar_que_falla(conexion_bd, **argumentos):
        # Escribe de verdad las dos filas y luego falla: el rollback tiene que
        # deshacer trabajo real, no una llamada que nunca ocurrio.
        original(conexion_bd, **argumentos)
        raise sqlite3.OperationalError("fallo simulado despues de la captura")

    monkeypatch.setattr(cap.outbox, "registrar", registrar_que_falla)

    with pytest.raises(sqlite3.OperationalError):
        cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)

    assert conexion.execute("SELECT count(*) FROM captura_local").fetchone()[0] == 0
    assert conexion.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_tras_un_rollback_se_puede_volver_a_capturar(conexion, monkeypatch):
    # El original se guarda antes de parchear: ``cap.outbox`` y ``outbox`` son
    # el mismo modulo, asi que despues del parche ``outbox.registrar`` ya seria
    # la version que falla.
    original = outbox.registrar

    def registrar_que_falla(conexion_bd, **argumentos):
        raise sqlite3.OperationalError("fallo simulado")

    monkeypatch.setattr(cap.outbox, "registrar", registrar_que_falla)
    with pytest.raises(sqlite3.OperationalError):
        cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)

    monkeypatch.setattr(cap.outbox, "registrar", original)
    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
    assert outbox.leer_evento(conexion, registro.id_outbox) is not None


def test_no_quedan_capturas_huerfanas_ni_outbox_sin_captura(conexion):
    for _ in range(3):
        cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)

    huerfanas = conexion.execute(
        "SELECT count(*) FROM captura_local c "
        "LEFT JOIN outbox o ON o.id_captura = c.id_captura "
        "WHERE o.id_outbox IS NULL"
    ).fetchone()[0]
    assert huerfanas == 0


# ---------------------------------------------------------------------------
# 5. Persistencia y reutilizacion del contrato
# ---------------------------------------------------------------------------


def test_el_evento_sobrevive_a_cerrar_y_reabrir_el_nodo(tmp_path):
    ruta = tmp_path / "nodo_edge.sqlite3"
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        registro = cap.capturar(conexion, paquete_de_varias_lecturas(3))
        payload_original = outbox.leer_payload(conexion, registro.id_captura)

    # Nueva conexion, nuevo objeto, mismo archivo: esto es "reiniciar el nodo".
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        elegibles = outbox.seleccionar_elegibles(conexion, limite=10)
        assert len(elegibles) == 1
        assert elegibles[0].clave == registro.clave
        assert elegibles[0].payload_json == payload_original
        assert elegibles[0].estado is EstadoEntrega.PENDIENTE


def test_el_payload_recuperado_vuelve_a_validar_contra_el_contrato(conexion):
    registro = cap.capturar(conexion, paquete_de_varias_lecturas(3))
    guardado = outbox.leer_payload(conexion, registro.id_captura)

    revalidado = SesionMonitoreoEntrada.model_validate_json(guardado)
    assert len(revalidado.lecturas) == 3


def test_el_payload_recuperado_conserva_la_huella_del_servidor(conexion):
    """La propiedad de la que depende que un reenvio sea replay y no colision.

    Si la ida y vuelta por SQLite cambiara la huella aunque fuera en un decimal
    o en un offset, el segundo intento del mismo paquete llegaria al servidor
    como contenido distinto bajo la misma clave, y la respuesta seria 409.
    """
    paquete = paquete_de_varias_lecturas(3)
    original = SesionMonitoreoEntrada.model_validate(paquete)

    registro = cap.capturar(conexion, paquete)
    recuperado = SesionMonitoreoEntrada.model_validate_json(
        outbox.leer_payload(conexion, registro.id_captura)
    )

    assert huella_del_paquete(recuperado) == huella_del_paquete(original)


def test_el_payload_guardado_es_aceptable_como_cuerpo_de_la_solicitud(conexion):
    """``extra='forbid'``: el volcado no puede traer un campo que el contrato no declare."""
    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
    cuerpo = json.loads(outbox.leer_payload(conexion, registro.id_captura))

    assert set(cuerpo) == set(SesionMonitoreoEntrada.model_fields)
    SesionMonitoreoEntrada.model_validate(cuerpo)


def test_el_payload_guardado_no_incluye_identificadores_remotos(conexion):
    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
    cuerpo = json.loads(outbox.leer_payload(conexion, registro.id_captura))
    assert "id_sesion" not in cuerpo
    assert "ids_lectura" not in cuerpo
    assert "lecturas_creadas" not in cuerpo


# ---------------------------------------------------------------------------
# 6. fecha_hora_sincronizacion
# ---------------------------------------------------------------------------


SINCRONIZACION_VALIDA = "2025-02-24T11:45:00+00:00"


def paquete_con_sincronizacion(valor: str | None = SINCRONIZACION_VALIDA) -> dict:
    paquete = json.loads(json.dumps(PAQUETE_DE_UNA_LECTURA))
    paquete["lecturas"][0]["fecha_hora_sincronizacion"] = valor
    return paquete


def test_la_captura_deja_la_sincronizacion_en_null(conexion):
    """Lo normal: el nodo captura sin conexion, asi que no hay nada sincronizado."""
    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA)
    cuerpo = json.loads(outbox.leer_payload(conexion, registro.id_captura))
    assert cuerpo["lecturas"][0]["fecha_hora_sincronizacion"] is None


def test_una_sincronizacion_explicitamente_nula_se_acepta(conexion):
    assert cap.capturar(conexion, paquete_con_sincronizacion(None)).id_outbox > 0


def test_una_sincronizacion_valida_se_acepta_y_se_guarda_sin_alterarla(conexion):
    """El edge no impone una regla mas estricta que el contrato de la API.

    Lo que hace peligroso a este campo no es que traiga un valor, sino que el
    valor pudiera **cambiar entre intentos**: participa de la huella, asi que un
    instante distinto en el segundo envio convertiria un reenvio legitimo en un
    409. Guardar el paquete validado una sola vez lo congela, y con eso el
    peligro desaparece sin necesidad de prohibir nada.
    """
    registro = cap.capturar(conexion, paquete_con_sincronizacion())

    cuerpo = json.loads(outbox.leer_payload(conexion, registro.id_captura))
    guardada = cuerpo["lecturas"][0]["fecha_hora_sincronizacion"]

    assert guardada is not None
    assert datetime.fromisoformat(guardada) == datetime.fromisoformat(
        SINCRONIZACION_VALIDA
    )


def test_una_sincronizacion_valida_conserva_la_huella_del_servidor(conexion):
    original = SesionMonitoreoEntrada.model_validate(paquete_con_sincronizacion())
    registro = cap.capturar(conexion, paquete_con_sincronizacion())

    recuperado = SesionMonitoreoEntrada.model_validate_json(
        outbox.leer_payload(conexion, registro.id_captura)
    )
    assert huella_del_paquete(recuperado) == huella_del_paquete(original)


def test_una_sincronizacion_con_otro_offset_es_el_mismo_instante(conexion):
    """El servidor normaliza a UTC, asi que el offset escrito no crea colision."""
    en_utc = cap.capturar(conexion, paquete_con_sincronizacion(SINCRONIZACION_VALIDA))
    en_panama = cap.capturar(
        conexion, paquete_con_sincronizacion("2025-02-24T06:45:00-05:00")
    )

    primero = SesionMonitoreoEntrada.model_validate_json(
        outbox.leer_payload(conexion, en_utc.id_captura)
    )
    segundo = SesionMonitoreoEntrada.model_validate_json(
        outbox.leer_payload(conexion, en_panama.id_captura)
    )
    assert huella_del_paquete(primero) == huella_del_paquete(segundo)


def test_una_sincronizacion_valida_sobrevive_al_reinicio(tmp_path):
    ruta = tmp_path / "nodo_edge.sqlite3"
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        registro = cap.capturar(conexion, paquete_con_sincronizacion())
        payload_original = outbox.leer_payload(conexion, registro.id_captura)

    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        evento = outbox.seleccionar_elegibles(conexion, limite=10)[0]

        assert evento.payload_json == payload_original
        cuerpo = json.loads(evento.payload_json)
        assert datetime.fromisoformat(
            cuerpo["lecturas"][0]["fecha_hora_sincronizacion"]
        ) == datetime.fromisoformat(SINCRONIZACION_VALIDA)


@pytest.mark.parametrize(
    "valor",
    [
        pytest.param("2025-02-24T11:20:30+00:00", id="anterior_a_la_captura"),
        pytest.param("2025-02-24T11:45:00", id="sin_offset"),
        pytest.param("no es una fecha", id="no_es_fecha"),
    ],
)
def test_una_sincronizacion_invalida_la_sigue_rechazando_el_contrato(conexion, valor):
    """Lo que se rechaza lo rechaza ``SesionMonitoreoEntrada``, no una regla local."""
    with pytest.raises(cap.PaqueteInvalido):
        cap.capturar(conexion, paquete_con_sincronizacion(valor))

    assert conexion.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_la_captura_no_anade_ninguna_regla_sobre_la_sincronizacion():
    """Todo paquete que el contrato acepta, la captura lo acepta.

    Es la propiedad que impide que el edge acabe con un contrato propio, mas
    estricto que el de la API y libre de separarse de el.
    """
    for valor in (None, SINCRONIZACION_VALIDA, "2025-02-24T06:45:00-05:00"):
        paquete = paquete_con_sincronizacion(valor)
        SesionMonitoreoEntrada.model_validate(paquete)  # el contrato lo acepta
        cap.validar_paquete(paquete)  # y la captura tambien


# ---------------------------------------------------------------------------
# 7. Paquetes invalidos
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutacion",
    [
        pytest.param({"lecturas": []}, id="sin_lecturas"),
        pytest.param({"tipo_sesion": "INVENTADO"}, id="tipo_desconocido"),
        pytest.param({"fecha_inicio": "2025-02-24T11:20:00"}, id="sin_offset"),
        pytest.param({"campo_de_mas": 1}, id="campo_desconocido"),
        pytest.param({"estado_sesion": "PENDIENTE"}, id="pendiente_con_fecha_fin"),
    ],
)
def test_un_paquete_invalido_no_llega_a_sqlite(conexion, mutacion):
    paquete = json.loads(json.dumps(PAQUETE_DE_UNA_LECTURA))
    paquete.update(mutacion)

    with pytest.raises(cap.PaqueteInvalido):
        cap.capturar(conexion, paquete)

    assert conexion.execute("SELECT count(*) FROM captura_local").fetchone()[0] == 0
    assert conexion.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0


def test_el_error_de_validacion_no_repite_el_valor_rechazado(conexion):
    """Pydantic incluye ``input_value`` en su propio texto; aqui no puede salir.

    Para este contrato ese valor es el dato clinico: una frecuencia cardiaca,
    una saturacion, un instante de captura.
    """
    paquete = json.loads(json.dumps(PAQUETE_DE_UNA_LECTURA))
    paquete["lecturas"][0]["hr_valor"] = 1234567

    with pytest.raises(cap.PaqueteInvalido) as excepcion:
        cap.capturar(conexion, paquete)

    detalle = excepcion.value.detalle
    assert "1234567" not in detalle
    # Pero si dice donde esta el problema.
    assert "hr_valor" in detalle


def test_un_modelo_ya_validado_se_acepta_sin_revalidar(conexion):
    modelo = SesionMonitoreoEntrada.model_validate(PAQUETE_DE_UNA_LECTURA)
    registro = cap.capturar(conexion, modelo)
    assert outbox.leer_evento(conexion, registro.id_outbox) is not None


# ---------------------------------------------------------------------------
# 8. Reloj inyectable
# ---------------------------------------------------------------------------


def test_el_reloj_es_inyectable_y_se_guarda_en_utc(conexion):
    instante = datetime(2026, 3, 1, 15, 30, tzinfo=timezone.utc)
    registro = cap.capturar(conexion, PAQUETE_DE_UNA_LECTURA, reloj=lambda: instante)

    fila = outbox.leer_evento(conexion, registro.id_outbox)
    assert fila["creado_en"] == "2026-03-01T15:30:00+00:00"
    assert fila["creado_en"] == fila["actualizado_en"]
