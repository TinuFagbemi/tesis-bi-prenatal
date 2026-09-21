"""La interfaz de la gestante: lo que ya no contiene, y lo que debe contener.

Son comprobaciones sobre los archivos, leidos como texto. No levantan un
navegador y no ejecutan JavaScript: comprueban que ciertas cosas **no estan**,
que es justo lo que una prueba de texto hace bien y lo que este ticket necesita
garantizar.

Tres familias:

1. **Nada de hardware.** ``/xml``, ``/status``, ``/start_mov`` y ``/stop_mov``
   desaparecieron del diseno junto con el sondeo del sensor.
2. **Nada de reglas clinicas.** ``getHRStatus``, ``getSpO2Status``,
   ``getMovStatus`` y ``setAlertLevel`` desaparecieron, y con ellas sus
   umbrales. El frontend pinta la clasificacion que recibe; no la calcula. Los
   umbrales del legado ademas **contradecian** las reglas SIM-1.0 de
   ``app.etl.reglas``, asi que conservarlos habria sido mantener una segunda
   version equivocada.
3. **La ausencia no es cero.** El marcador de un valor que no existe es "—" o
   "No disponible", nunca 0.

Tambien se comprueba lo que la identidad visual debe conservar, para que una
limpieza futura no se lleve por delante el aspecto de FetalAlert.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DIRECTORIO_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "gestante"

HTML = DIRECTORIO_FRONTEND / "index.html"
CSS = DIRECTORIO_FRONTEND / "styles.css"
JS = DIRECTORIO_FRONTEND / "app.js"

ARCHIVOS = (HTML, CSS, JS)


def leer(ruta: Path) -> str:
    return ruta.read_text(encoding="utf-8")


def todo_el_frontend() -> str:
    return "\n".join(leer(ruta) for ruta in ARCHIVOS)


# ---------------------------------------------------------------------------
# Los archivos existen
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ruta", ARCHIVOS, ids=lambda r: r.name)
def test_el_archivo_existe_y_no_esta_vacio(ruta):
    assert ruta.is_file()
    assert len(leer(ruta)) > 500


# ---------------------------------------------------------------------------
# Dependencias del hardware, retiradas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "legado", ["/xml", "/status", "/start_mov", "/stop_mov"]
)
def test_la_interfaz_no_depende_de_los_endpoints_del_prototipo_anterior(legado):
    assert legado not in todo_el_frontend()


@pytest.mark.parametrize(
    "legado",
    ["fetchDataXML", "fetchStatus", "DOMParser", "Valid", "missCount", "CALIB_LIMIT"],
)
def test_la_interfaz_no_conserva_el_sondeo_del_sensor(legado):
    assert legado not in leer(JS)


def test_no_quedan_mensajes_de_hardware():
    contenido = todo_el_frontend()
    for mensaje in ("coloque su dedo", "Dedo detectado", "Calibrando"):
        assert mensaje not in contenido


def test_no_se_conserva_la_descarga_del_historial_legacy():
    """Un archivo estatico sin control de acceso no vuelve a esta interfaz."""
    assert "historial.csv" not in todo_el_frontend()


# ---------------------------------------------------------------------------
# Reglas clinicas, fuera del navegador
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "funcion", ["getHRStatus", "getSpO2Status", "getMovStatus", "setAlertLevel"]
)
def test_la_interfaz_no_clasifica_lecturas(funcion):
    assert funcion not in todo_el_frontend()


@pytest.mark.parametrize("umbral", ["55", "60", "90", "92", "95", "100", "110", "120"])
def test_no_hay_umbrales_clinicos_en_el_javascript(umbral):
    """Ningun numero de decision clinica vive aqui.

    Se comprueba sobre ``app.js`` porque es donde estaban: el CSS tiene medidas
    en pixeles y porcentajes que coincidirian con estas cifras sin significar
    nada clinico.
    """
    import re

    contenido = leer(JS)
    assert re.search(rf"\b{umbral}\b", contenido) is None


def test_el_semaforo_solo_traduce_codigos_a_clases():
    """Se pinta lo que llega; no se decide nada.

    Los tres codigos son los de ``CodigoSemaforo``, y el mapa los convierte en
    clases CSS. Un nivel desconocido cae en el estado neutro en lugar de
    adivinarse.
    """
    contenido = leer(JS)

    assert "CLASES_DE_SEMAFORO" in contenido
    for codigo in ("OK", "WARNING", "ERROR"):
        assert codigo in contenido
    assert "SIN_CLASIFICACION" in contenido
    # No hay comparacion de magnitudes en ninguna parte del archivo.
    assert " < " not in contenido.replace("for (", "")
    assert " > " not in contenido.replace("=>", "").replace("->", "")


# ---------------------------------------------------------------------------
# La ausencia no es cero
# ---------------------------------------------------------------------------


def test_los_marcadores_de_ausencia_son_guion_o_texto():
    contenido = leer(JS)
    assert "const SIN_DATO = '—'" in contenido
    assert "No disponible" in contenido


def test_la_funcion_que_sustituye_ausencias_no_trata_el_cero_como_ausente():
    """El defecto del prototipo anterior, cerrado explicitamente.

    Antes, ``ui.movsValue.textContent = mov`` convertia un conteo ausente en 0.
    La guarda de ``texto()`` enumera exactamente los tres casos de ausencia, y
    el cero no es uno de ellos.
    """
    contenido = leer(JS)

    assert "valor === null || valor === undefined || valor === ''" in contenido
    # Las formas tipicas de colapsar un nulo en cero no aparecen.
    assert "|| 0" not in contenido
    assert "?? 0" not in contenido
    assert "Number(" not in contenido
    assert "parseInt(" not in contenido


def test_el_marcado_no_inicializa_ninguna_metrica_en_cero():
    """Lo que se ve antes de que lleguen datos es "—", nunca un cero."""
    import re

    contenido = leer(HTML)
    for identificador in ("hr-value", "spo2-value", "movs-value"):
        coincidencia = re.search(
            rf'id="{identificador}"[^>]*>([^<]*)<', contenido
        )
        assert coincidencia is not None, identificador
        assert coincidencia.group(1).strip() == "—", identificador


def test_los_conteos_del_estado_local_empiezan_sin_dato():
    import re

    contenido = leer(HTML)
    for identificador in (
        "outbox-pendientes",
        "outbox-enviados",
        "outbox-reintentables",
        "outbox-revision",
    ):
        coincidencia = re.search(rf'id="{identificador}"[^>]*>([^<]*)<', contenido)
        assert coincidencia is not None, identificador
        assert coincidencia.group(1).strip() == "—", identificador


# ---------------------------------------------------------------------------
# Sin secretos y sin almacenamiento del navegador
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prohibido",
    ["localStorage", "sessionStorage", "indexedDB", "document.cookie", "Bearer"],
)
def test_la_interfaz_no_guarda_nada_ni_manipula_credenciales(prohibido):
    assert prohibido not in todo_el_frontend()


def test_la_interfaz_solo_llama_al_adaptador_local():
    """Ninguna URL absoluta: el navegador no habla con el servidor central."""
    contenido = leer(JS)

    assert "http://" not in contenido
    assert "https://" not in contenido
    assert "/adaptador/" in contenido


def test_la_contrasena_se_limpia_despues_de_enviarla():
    assert "ui.password.value = ''" in leer(JS)


# ---------------------------------------------------------------------------
# Identidad visual conservada
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marca",
    [
        "Bienvenida a",
        "FetalAlert",
        "Tecnología al cuidado de tu embarazo",
    ],
)
def test_se_conserva_la_identidad_del_encabezado(marca):
    assert marca in leer(HTML)


@pytest.mark.parametrize(
    "regla",
    [
        "--clr-purple-1:#8b5cf6",
        "--clr-purple-2:#a855f7",
        "--clr-count-start:#db7093",
        "--clr-header-grad-1:#0f172a",
        "-webkit-background-clip:text",
        "@keyframes pulse",
        "@keyframes urFade",
        ".card::before",
        ".connection-status",
        ".qa-btn",
        ".result-card",
        ".data-row",
        ".semaforo-item",
        ".footer",
    ],
)
def test_se_conserva_la_identidad_visual(regla):
    assert regla in leer(CSS)


@pytest.mark.parametrize(
    "corte", ["max-width:1200px", "max-width:768px", "max-width:414px", "max-width:360px", "max-width:320px"]
)
def test_se_conservan_los_cortes_responsive(corte):
    assert corte in leer(CSS).replace(" ", "")


def test_no_quedan_bloques_duplicados_exactos():
    """Los grupos que estaban declarados dos veces, ahora una sola.

    Se cuentan **bloques de regla**, no menciones del nombre: el comentario de
    cabecera documenta que ciertos selectores se retiraron, y contar la palabra
    encontraria esa prosa.
    """
    contenido = leer(CSS)

    assert contenido.count(".values-section{") <= 1
    assert contenido.count("@media screen and (max-width:360px)") == 1
    assert contenido.count("@media screen and (max-width:414px)") == 1
    assert contenido.count("@media screen and (max-width:320px)") == 1


def test_la_regla_back_link_se_retiro_junto_con_las_subpaginas():
    """Ya no hay paginas separadas a las que volver, asi que la regla sobraba.

    Se comprueba que no existe **ni el bloque de regla ni ningun elemento** que
    la use. El nombre aparece en el comentario de cabecera, que explica
    precisamente esta retirada.
    """
    css = leer(CSS)

    assert ".back-link{" not in css.replace(" ", "")
    assert ".back-link:" not in css
    assert "back-link" not in leer(HTML)


def test_la_clase_del_marcado_que_no_tenia_definicion_ahora_la_tiene():
    """``.movements-text`` se usaba en el HTML y no existia en el CSS."""
    assert "movements-text" in leer(HTML)
    assert ".movements-text{" in leer(CSS)


# ---------------------------------------------------------------------------
# Menu y vistas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "opcion",
    ["Inicio", "Mi historial", "Manual de uso", "Acerca de FetalAlert", "Cerrar sesión"],
)
def test_el_menu_tiene_las_cinco_opciones(opcion):
    assert opcion in leer(HTML)


@pytest.mark.parametrize(
    "vista",
    ["vista-login", "vista-inicio", "vista-historial", "vista-manual", "vista-acerca"],
)
def test_existen_las_cinco_vistas(vista):
    assert f'id="{vista}"' in leer(HTML)


def test_ultimo_resultado_ya_no_es_una_pagina():
    contenido = todo_el_frontend()
    assert "ultimo.html" not in contenido
    assert "quienes.html" not in contenido
    assert "manual.html" not in contenido


# ---------------------------------------------------------------------------
# Lo que queda deliberadamente pendiente
# ---------------------------------------------------------------------------


def test_el_registro_de_movimientos_esta_preparado_pero_deshabilitado():
    """La accion existe y se ve, y dice por que todavia no opera.

    No se conecta porque un paquete de monitoreo exige referencias clinicas
    --embarazo, dispositivo, semana gestacional, semaforo-- que hoy no tienen
    fuente autorizada. Un boton operativo tendria que inventarlas.
    """
    contenido = leer(HTML)

    assert 'id="btn-registrar-movimientos"' in contenido
    assert "disabled" in contenido
    assert "Registrar sesión de movimientos" in contenido
    assert "contexto clínico autorizado" in contenido


def test_no_se_inventan_identificadores_clinicos():
    contenido = todo_el_frontend()
    for campo in ("id_embarazo", "id_dispositivo", "id_tiempo_gest", "id_semaforo"):
        assert campo not in contenido


def test_se_declara_que_los_datos_son_simulados():
    contenido = leer(HTML)
    assert "simulados" in contenido
    assert "académico" in contenido
