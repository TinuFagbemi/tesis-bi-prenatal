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
    # No hay comparacion de magnitudes clinicas en ninguna parte del archivo.
    # La unica comparacion admitida es la antiguedad de los datos mostrados,
    # en milisegundos de reloj, y se quita literal antes de buscar: cualquier
    # otra sigue haciendo fallar esta prueba.
    antiguedad = "Date.now() - datosConsultadosEn > ANTIGUEDAD_MAXIMA_DATOS_MS"
    assert contenido.count(antiguedad) == 1
    contenido = contenido.replace(antiguedad, "")
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
    import re

    contenido = leer(JS)

    assert "valor === null || valor === undefined || valor === ''" in contenido
    # Las formas tipicas de colapsar un nulo en cero no aparecen.
    assert "|| 0" not in contenido
    assert "?? 0" not in contenido

    # **Ningun campo clinico pasa por una conversion numerica.** La prohibicion
    # apunta a los campos, no a la funcion: ``Number()`` sobre el valor de un
    # ``<select>`` --que es texto de un identificador-- no tiene nada que ver
    # con convertir una medicion ausente en cero, y prohibirla en bloque
    # confundia las dos cosas.
    for campo in ("hr_valor", "spo2_valor", "mov_valor", "semana_gestacion"):
        assert re.search(rf"Number\(\s*\w*\.?{campo}", contenido) is None, campo
        assert re.search(rf"parseInt\(\s*\w*\.?{campo}", contenido) is None, campo
        assert re.search(rf"\+\s*\w+\.{campo}\b", contenido) is None, campo


def test_la_unica_conversion_numerica_es_la_del_identificador_del_selector():
    """Y esta protegida: solo se usa si coincide con un episodio conocido."""
    import re

    contenido = leer(JS)

    conversiones = re.findall(r"Number\(([^)]*)\)", contenido)
    assert conversiones == ["ui.selectorEmbarazo.value"]
    # Y su resultado se comprueba contra la lista del adaptador antes de usarse.
    assert "conocido" in contenido
    assert "e.id_embarazo === elegido" in contenido


def test_el_marcado_no_inicializa_ninguna_metrica_en_cero():
    """Lo que se ve antes de que lleguen datos es "—", nunca un cero."""
    import re

    contenido = leer(HTML)
    for identificador in ("hr-value", "spo2-value", "mov-value"):
        coincidencia = re.search(
            rf'id="{identificador}"[^>]*>([^<]*)<', contenido
        )
        assert coincidencia is not None, identificador
        assert coincidencia.group(1).strip() == "—", identificador


def test_no_quedan_paneles_de_contadores_tecnicos():
    """Los dos paneles de conteos por estado se sustituyeron por una frase.

    La persistencia, los reintentos y la trazabilidad siguen en el adaptador y
    en ``app.edge``; lo que desaparece es mostrar sus nombres internos a la
    gestante.
    """
    html = leer(HTML)
    for retirado in (
        "outbox-pendientes",
        "movimientos-pendientes",
        "estado-conteo",
        "FALLIDO (reintentable)",
        "FALLIDO (requiere revisión)",
    ):
        assert retirado not in html, retirado
    assert 'id="envio-estado"' in html


def test_la_interfaz_no_muestra_comandos_ni_rutas():
    """Ni comandos, ni rutas, ni explicaciones de implementación a la vista."""
    html = leer(HTML)
    for tecnico in ("python ", "scripts/", "/adaptador/", "/api/v1", "SQLite", "outbox"):
        assert tecnico not in html, tecnico


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


def test_el_registro_de_movimientos_simulados_existe_y_arranca_deshabilitado():
    """El boton existe, se ve como simulado, y arranca deshabilitado en el HTML.

    Arranca deshabilitado porque el marcado estatico no sabe todavia si hay un
    embarazo elegido -- eso lo decide `app.js` en tiempo de ejecucion, contra
    la lista que entrega el adaptador. La sincronizacion es una accion
    aparte, siempre disponible, porque no depende de tener un embarazo
    elegido.
    """
    import re

    contenido = leer(HTML)

    boton = re.search(r'<button id="btn-registrar-movimientos"[^>]*>', contenido)
    assert boton is not None
    assert "disabled" in boton.group(0)
    assert 'id="btn-sincronizar-movimientos"' in contenido
    # Que es simulado se explica una vez, en «Acerca de».
    acerca = contenido.split('id="vista-acerca"', 1)[1]
    assert "valores" in acerca and "simulados fijos" in acerca


def test_el_registro_de_movimientos_simulados_esta_conectado_en_app_js():
    """La accion ya llama al adaptador: no es un boton decorativo.

    El paquete concreto -- valores fijos, referencias de catalogo -- lo arma
    el servidor en ``app.gestante.simulacion``; este archivo solo elige el
    tipo de sesion y llama a la ruta.
    """
    contenido = leer(JS)

    assert "sesionesSimuladas" in contenido
    assert "movimientosSincronizar" in contenido
    assert "movimientosEstado" in contenido
    assert "manejarRegistrarMovimiento" in contenido
    assert "manejarSincronizarMovimientos" in contenido
    assert "TIPO_SESION_SIMULADA" in contenido


def test_no_aparecen_los_identificadores_que_solo_sirven_para_escribir():
    """Los tres que un paquete de monitoreo exige y nadie publica.

    ``id_embarazo`` **si** aparece, y debe: lo entrega el adaptador en la lista
    de episodios y es lo que identifica cual se esta consultando. Lo que no
    puede aparecer son los que harian falta para *escribir* una sesion, porque
    ninguna fuente autorizada los expone y tenerlos aqui solo podria significar
    que se inventaron.
    """
    contenido = todo_el_frontend()
    for campo in ("id_dispositivo", "id_tiempo_gest", "id_semaforo"):
        assert campo not in contenido, campo


def test_el_identificador_de_episodio_nunca_se_fabrica_en_la_pagina():
    """Solo se usa el que vino del adaptador."""
    import re

    contenido = leer(JS)

    # No hay ningún id_embarazo literal escrito en el código.
    assert re.search(r"id_embarazo\s*[:=]\s*\d+", contenido) is None
    assert "episodio.id_embarazo" in contenido


def test_se_declara_que_los_datos_son_simulados():
    contenido = leer(HTML)
    assert "simulados" in contenido
    assert "académico" in contenido


# ---------------------------------------------------------------------------
# Clasificación centralizada de respuestas (lectura clínica)
# ---------------------------------------------------------------------------


def test_la_clasificacion_de_respuestas_vive_en_un_solo_sitio():
    """Sin un punto central, cada vista acabaría decidiendo por su cuenta."""
    contenido = leer(JS)

    assert "function clasificar(resultado)" in contenido
    for clase in (
        "SESION_LOCAL_INVALIDA",
        "ACCESO_DENEGADO",
        "RECURSO_NO_DISPONIBLE",
        "ERROR_UPSTREAM",
        "NO_DISPONIBLE",
        "DATOS",
    ):
        assert clase in contenido, clase


def test_solo_el_401_del_adaptador_devuelve_al_login():
    """Un 403, un 404 o un 502 no pueden expulsar a la paciente."""
    import re

    contenido = leer(JS)

    lineas = contenido.splitlines()
    for indice, linea in enumerate(lineas):
        if "mostrarLogin()" not in linea:
            continue
        contexto = "\n".join(lineas[max(0, indice - 3) : indice + 1])
        if "clasificacion.clase" in contexto:
            assert "SESION_LOCAL_INVALIDA" in contexto

    assert not re.search(r"estado\s*===\s*403[\s\S]{0,140}mostrarLogin", contenido)
    assert not re.search(r"estado\s*===\s*404[\s\S]{0,140}mostrarLogin", contenido)
    assert not re.search(r"estado\s*>=\s*500[\s\S]{0,140}mostrarLogin", contenido)


@pytest.mark.parametrize("motivo", ["reautenticacion_requerida", "sin_conexion"])
def test_los_dos_motivos_que_mantienen_el_portal_abierto_estan_contemplados(motivo):
    assert motivo in leer(JS)


def test_la_interfaz_consume_solo_las_rutas_clinicas_del_adaptador():
    """El navegador nunca habla con el servidor central."""
    contenido = leer(JS)

    assert "'/adaptador/embarazos'" in contenido
    assert "/monitoreo" in contenido
    assert "/api/v1/clinico" not in contenido
    assert "/api/v1/autenticacion" not in contenido


def test_el_semaforo_solo_se_traduce_a_presentacion():
    """El código llega clasificado; aquí sólo se mapea a una clase CSS."""
    contenido = leer(JS)

    assert "pintarSemaforo(lectura.codigo_semaforo" in contenido
    for comparacion in (
        "hr_valor >",
        "hr_valor <",
        "spo2_valor >",
        "spo2_valor <",
        "mov_valor >",
        "mov_valor <",
    ):
        assert comparacion not in contenido, comparacion


def test_el_selector_solo_acepta_identificadores_que_vinieron_del_adaptador():
    contenido = leer(JS)

    assert "episodios.todos" in contenido
    assert "conocido" in contenido


def test_cambiar_de_episodio_vacia_lo_anterior_antes_de_pedir():
    """Ni una fila del episodio anterior puede quedar a la vista.

    Y solo se vacía Historial: el cambio de episodio no toca las métricas de
    Inicio. El comportamiento completo, con respuestas tardías incluidas, lo
    ejecuta ``test_gestante_frontend_comportamiento.py``.
    """
    contenido = leer(JS)

    manejador = contenido.split("ui.selectorEmbarazo.addEventListener('change'", 1)[1]
    manejador = manejador.split("function arrancar()", 1)[0]
    assert "ui.historialLista.innerHTML = ''" in manejador
    assert "cargarHistorial()" in manejador
    assert "limpiarMetricas" not in manejador
    assert "idInicio" not in manejador


# ---------------------------------------------------------------------------
# Presentación simplificada y cierre de sesión
# ---------------------------------------------------------------------------


def test_el_atributo_hidden_no_lo_anula_ningun_display():
    """Sin esta regla, ``.main-menu{display:flex}`` dejaba el menú visible en el login."""
    assert "[hidden]{display:none !important;}" in leer(CSS)


def test_el_aviso_academico_no_se_repite_fuera_de_acerca_de():
    html = leer(HTML)
    assert "aviso-simulado" not in html
    antes_de_acerca = html.split('id="vista-acerca"', 1)[0]
    assert "Prototipo académico" not in antes_de_acerca
    pie = html.split('<footer', 1)[1]
    assert "simulad" not in pie and "académico" not in pie


def test_cerrar_sesion_esta_en_el_area_de_cuenta_y_pide_confirmacion():
    import re

    html = leer(HTML)
    menu = re.search(r'<nav[^>]*id="main-menu"[^>]*>([\s\S]*?)</nav>', html).group(1)
    assert "Cerrar sesión" not in menu

    cuenta = re.search(r'<div[^>]*id="area-cuenta"[^>]*>([\s\S]*?)</div>', html)
    assert cuenta is not None and "hidden" in cuenta.group(0)
    assert 'id="btn-cerrar-sesion"' in cuenta.group(1)

    assert "¿Deseas cerrar sesión?" in html
    assert 'id="btn-cancelar-cierre"' in html
    assert 'id="btn-confirmar-cierre"' in html


def test_la_ambiguedad_no_se_presenta_como_embarazo_actual():
    contenido = leer(JS)

    assert "Sin determinar" in contenido
    assert "No se pudo determinar automáticamente" in contenido


def test_el_panel_pinta_una_sola_lectura_coherente():
    """Las tres métricas y el instante salen del mismo objeto."""
    contenido = leer(JS)

    assert "function pintarUltimaLectura(lectura)" in contenido
    for campo in (
        "lectura.hr_valor",
        "lectura.spo2_valor",
        "lectura.mov_valor",
        "lectura.fecha_hora_captura",
        "lectura.semana_gestacion",
    ):
        assert campo in contenido, campo
