"""Garantias de seguridad del adaptador de la interfaz de la gestante.

Cada prueba de este modulo comprueba **una promesa concreta** que el diseno hace
y que seria facil romper sin darse cuenta:

* la contrasena no se escribe en ningun sitio;
* el token del servidor central no sale de la memoria del proceso;
* ``EDGE_API_TOKEN`` no aparece en ninguna respuesta ni en la interfaz;
* la cookie es ``HttpOnly``, ``SameSite=Strict`` y persistente;
* el 422 de la ruta con credenciales no devuelve el valor rechazado;
* las negativas no repiten el correo que se intento;
* la interfaz sigue abriendose con el servidor caido y con el nodo sin
  inicializar.

Varias de ellas leen el archivo SQLite **en crudo** y buscan la cadena. Es un
control tosco a proposito: no depende de que el esquema sea el esperado, ni de
que la consulta sea la correcta, ni de que nadie haya anadido una columna. Si el
valor esta en el archivo, aparece.

No hace falta PostgreSQL ni la API central.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.gestante.config import NOMBRE_DE_COOKIE, GestanteSettings
from tests.test_gestante_rutas import (
    EMAIL_DE_PRUEBA,
    PASSWORD_DE_PRUEBA,
    TOKEN_DE_PRUEBA,
    ClienteCentralDoble,
    construir_cliente,
    iniciar_sesion,
)

DIRECTORIO_FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "gestante"
ARCHIVOS_DE_LA_INTERFAZ = ("index.html", "styles.css", "app.js")


def bytes_del_almacen(settings: GestanteSettings) -> bytes:
    """El archivo de sesiones tal cual esta en disco."""
    ruta = Path(settings.sqlite_path)
    assert ruta.exists(), "la prueba deberia haber creado el almacen"
    return ruta.read_bytes()


# ---------------------------------------------------------------------------
# Credenciales que no se persisten
# ---------------------------------------------------------------------------


def test_la_contrasena_no_queda_en_el_almacen_local(tmp_path):
    cliente, _, _, settings = construir_cliente(tmp_path)

    iniciar_sesion(cliente)

    assert PASSWORD_DE_PRUEBA.encode() not in bytes_del_almacen(settings)


def test_el_token_central_no_queda_en_el_almacen_local(tmp_path):
    """Vive solo en memoria del proceso: no hay ruta desde ahi hasta el disco."""
    cliente, _, _, settings = construir_cliente(tmp_path)

    iniciar_sesion(cliente)

    assert TOKEN_DE_PRUEBA.encode() not in bytes_del_almacen(settings)


def test_el_correo_no_queda_en_el_almacen_local(tmp_path):
    """Guardarlo convertiria este archivo en una lista de cuentas."""
    cliente, _, _, settings = construir_cliente(tmp_path)

    iniciar_sesion(cliente)

    assert EMAIL_DE_PRUEBA.encode() not in bytes_del_almacen(settings)


def test_el_almacen_solo_guarda_las_columnas_previstas(tmp_path):
    """Un esquema que no puede crecer sin que esta prueba lo note."""
    from app.gestante import almacen

    cliente, _, _, settings = construir_cliente(tmp_path)
    iniciar_sesion(cliente)

    with almacen.conectar(settings.sqlite_path) as conexion:
        columnas = {
            fila["name"]
            for fila in conexion.execute(
                f"PRAGMA table_info({almacen.TABLA_SESION})"
            ).fetchall()
        }
        tablas = almacen.tablas_presentes(conexion)

    assert columnas == {
        "id_sesion",
        "hash_sesion",
        "id_usuario",
        "rol",
        "validada_en",
        "expira_en",
        "cerrada_en",
    }
    assert tablas == {almacen.TABLA_SESION}


# ---------------------------------------------------------------------------
# Secretos que no salen al navegador
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metodo, ruta",
    [
        ("get", "/adaptador/sesion"),
        ("get", "/adaptador/conectividad"),
        ("get", "/adaptador/estado-local"),
        ("post", "/adaptador/cerrar-sesion"),
    ],
)
def test_ninguna_respuesta_del_adaptador_lleva_el_token(tmp_path, metodo, ruta):
    cliente, _, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)

    respuesta = getattr(cliente, metodo)(ruta)

    assert TOKEN_DE_PRUEBA not in respuesta.text
    assert PASSWORD_DE_PRUEBA not in respuesta.text
    assert "Bearer" not in respuesta.text


def test_la_respuesta_del_login_no_devuelve_credenciales(tmp_path):
    cliente, _, _, _ = construir_cliente(tmp_path)

    respuesta = iniciar_sesion(cliente)

    cuerpo = respuesta.json()
    assert set(cuerpo) == {"autenticada", "rol", "expira_en", "segundos_restantes"}
    assert TOKEN_DE_PRUEBA not in respuesta.text
    assert PASSWORD_DE_PRUEBA not in respuesta.text
    assert EMAIL_DE_PRUEBA not in respuesta.text


def test_una_negativa_no_repite_el_correo_intentado(tmp_path):
    """Un mensaje que devolviera el correo lo dejaria en capturas y registros."""
    from app.gestante.central import EstadoRespuesta

    central = ClienteCentralDoble(estado_token=EstadoRespuesta.RECHAZADO)
    cliente, _, _, _ = construir_cliente(tmp_path, central=central)

    respuesta = iniciar_sesion(cliente, email="alguien@example.com")

    assert "alguien@example.com" not in respuesta.text


def test_el_422_de_la_ruta_con_credenciales_no_devuelve_la_contrasena(tmp_path):
    """El problema que resuelve es concreto y estaba ahi.

    Pydantic adjunta a cada error el valor que lo provoco. ``SecretStr``
    enmascara la contrasena *una vez construido el modelo*, asi que no protege
    nada cuando la validacion falla por otro campo: una peticion sin ``email``
    devolveria un 422 con la contrasena en claro dentro del cuerpo.
    """
    cliente, _, _, _ = construir_cliente(tmp_path)

    respuesta = cliente.post(
        "/adaptador/iniciar-sesion", json={"password": PASSWORD_DE_PRUEBA}
    )

    assert respuesta.status_code == 422
    assert PASSWORD_DE_PRUEBA not in respuesta.text
    # El diagnostico se conserva: que campo falta y por que.
    assert "email" in respuesta.text


# ---------------------------------------------------------------------------
# La cookie
# ---------------------------------------------------------------------------


def test_la_cookie_es_httponly_samesite_strict_y_persistente(tmp_path):
    cliente, _, _, _ = construir_cliente(tmp_path)

    cabecera = iniciar_sesion(cliente).headers["set-cookie"].lower()

    assert "httponly" in cabecera
    assert "samesite=strict" in cabecera
    assert "max-age=" in cabecera
    assert "path=/" in cabecera


def test_secure_es_configurable_y_no_esta_cableado(tmp_path):
    """El MVP sirve HTTP local, pero la decision no queda fijada en el codigo."""
    cliente, _, _, _ = construir_cliente(tmp_path, cookie_secure=True)

    assert "secure" in iniciar_sesion(cliente).headers["set-cookie"].lower()


def test_la_cookie_no_lleva_el_token_ni_datos_de_la_cuenta(tmp_path):
    """Es un identificador opaco: no codifica usuario, rol ni expiracion."""
    cliente, _, _, _ = construir_cliente(tmp_path)

    iniciar_sesion(cliente)
    valor = cliente.cookies[NOMBRE_DE_COOKIE]

    assert TOKEN_DE_PRUEBA not in valor
    assert EMAIL_DE_PRUEBA not in valor
    assert "PACIENTE" not in valor
    assert "4242" not in valor


# ---------------------------------------------------------------------------
# EDGE_API_TOKEN
# ---------------------------------------------------------------------------


def test_edge_api_token_no_aparece_en_la_interfaz():
    """La credencial del nodo es del nodo. La interfaz no la conoce."""
    for nombre in ARCHIVOS_DE_LA_INTERFAZ:
        contenido = (DIRECTORIO_FRONTEND / nombre).read_text(encoding="utf-8")
        assert "EDGE_API_TOKEN" not in contenido, nombre


def test_el_adaptador_no_lee_edge_api_token(tmp_path, monkeypatch):
    """Ni siquiera con la variable definida: este proceso no la mira.

    Lo comprueba con un valor ficticio en el entorno y buscandolo despues en
    todo lo que el adaptador devuelve y en lo que escribe en disco.
    """
    centinela = "valor-centinela-que-no-debe-aparecer-jamas"
    monkeypatch.setenv("EDGE_API_TOKEN", centinela)

    cliente, _, _, settings = construir_cliente(tmp_path)
    respuestas = [
        iniciar_sesion(cliente).text,
        cliente.get("/adaptador/sesion").text,
        cliente.get("/adaptador/conectividad").text,
        cliente.get("/adaptador/estado-local").text,
        cliente.get("/").text,
        cliente.get("/app.js").text,
    ]

    for texto in respuestas:
        assert centinela not in texto
    assert centinela.encode() not in bytes_del_almacen(settings)


def test_la_configuracion_del_adaptador_no_tiene_campos_para_secretos():
    """No hay donde poner una credencial, que es la forma mas segura de no tenerla."""
    campos = set(GestanteSettings.model_fields)

    assert not campos & {"api_token", "password", "jwt_secret_key", "database_url"}
    assert not any("token" in campo or "secret" in campo for campo in campos)


# ---------------------------------------------------------------------------
# Disponibilidad en condiciones degradadas
# ---------------------------------------------------------------------------


def test_el_adaptador_arranca_con_la_api_central_caida(tmp_path):
    """La interfaz se abre; lo que necesita al servidor dice que no hay conexion."""
    from app.gestante.central import EstadoRespuesta

    central = ClienteCentralDoble(
        estado_token=EstadoRespuesta.SIN_CONEXION, disponible_=False
    )
    cliente, _, _, _ = construir_cliente(tmp_path, central=central)

    assert cliente.get("/").status_code == 200
    assert cliente.get("/adaptador/sesion").status_code == 200
    assert cliente.get("/adaptador/conectividad").json()["api_central"] == "no_disponible"
    assert iniciar_sesion(cliente).status_code == 503


def test_el_adaptador_arranca_con_el_nodo_edge_sin_inicializar(tmp_path, monkeypatch):
    from app.gestante import rutas

    monkeypatch.setattr(
        rutas, "_ruta_del_nodo", lambda _settings: tmp_path / "no-existe.sqlite3"
    )
    cliente, _, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)

    respuesta = cliente.get("/adaptador/estado-local")

    assert respuesta.status_code == 200
    assert respuesta.json()["inicializado"] is False


def test_un_almacen_del_nodo_ilegible_no_tumba_la_peticion(tmp_path, monkeypatch):
    """Un archivo que no es una base de datos se reporta, no se propaga."""
    from app.gestante import rutas

    roto = tmp_path / "roto.sqlite3"
    roto.write_bytes(b"esto no es una base de datos SQLite")
    monkeypatch.setattr(rutas, "_ruta_del_nodo", lambda _settings: roto)

    cliente, _, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)

    respuesta = cliente.get("/adaptador/estado-local")

    assert respuesta.status_code == 200
    assert respuesta.json()["inicializado"] is False
    # Ni la ruta del archivo ni el mensaje del driver salen al navegador.
    assert str(roto) not in respuesta.text
