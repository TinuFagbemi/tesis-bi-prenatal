"""La sesion local de la interfaz de la gestante: ventana, cookie y cierre.

Lo que se comprueba aqui es el comportamiento que hace util la interfaz sin
conexion, y los limites que impiden que esa comodidad se convierta en un agujero:

* la ventana dura lo que dice la configuracion, y el ``Max-Age`` de la cookie
  sale del mismo calculo;
* reabrir sin conexion **consume** la ventana y no la alarga, por muchas veces
  que se haga;
* solo una revalidacion en linea correcta la renueva;
* cuando termina, hay que autenticarse otra vez;
* el cierre de sesion invalida del lado del servidor, asi que una cookie copiada
  antes deja de servir.

El reloj se inyecta, asi que recorrer tres dias cuesta microsegundos y la suite
no duerme. No hace falta PostgreSQL ni la API central.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

from datetime import timezone

import pytest

from app.gestante import almacen, sesion as sesion_local
from app.gestante.central import EstadoRespuesta
from app.gestante.config import (
    NOMBRE_DE_COOKIE,
    VENTANA_POR_OMISION_HORAS,
    GestanteSettings,
)
from app.gestante.sesion import RolNoAutorizado
from app.models.enums import NombreRol
from tests.test_gestante_rutas import (
    ClienteCentralDoble,
    RelojFalso,
    construir_cliente,
    iniciar_sesion,
)

SEGUNDOS_POR_HORA = 3600


def cookie_de(respuesta) -> str:
    return respuesta.headers.get("set-cookie", "")


def max_age_de(respuesta) -> int:
    """El ``Max-Age`` que la respuesta fija, como entero."""
    for parte in cookie_de(respuesta).split(";"):
        clave, _, valor = parte.strip().partition("=")
        if clave.lower() == "max-age":
            return int(valor)
    raise AssertionError(f"La cookie no trae Max-Age: {cookie_de(respuesta)!r}")


# ---------------------------------------------------------------------------
# La ventana y su unica declaracion
# ---------------------------------------------------------------------------


def test_la_ventana_por_omision_es_de_setenta_y_dos_horas():
    """El valor acordado, declarado una sola vez en el dominio."""
    assert VENTANA_POR_OMISION_HORAS == 72.0
    assert GestanteSettings().ventana_en_segundos == 72 * SEGUNDOS_POR_HORA


def test_la_cookie_es_persistente_y_su_max_age_sale_de_la_ventana(tmp_path):
    cliente, _, _, settings = construir_cliente(tmp_path)

    respuesta = iniciar_sesion(cliente)

    assert max_age_de(respuesta) > 0
    assert max_age_de(respuesta) == settings.ventana_en_segundos


@pytest.mark.parametrize("horas", [0.5, 1.0, 12.0, 72.0, 240.0])
def test_el_max_age_sigue_a_la_configuracion(tmp_path, horas):
    """Prueba de que 72 no esta cableado en ningun sitio: cambiarlo cambia todo."""
    cliente, _, _, _ = construir_cliente(tmp_path, ventana_sesion_horas=horas)

    respuesta = iniciar_sesion(cliente)

    assert max_age_de(respuesta) == int(horas * SEGUNDOS_POR_HORA)
    assert respuesta.json()["segundos_restantes"] == int(horas * SEGUNDOS_POR_HORA)


@pytest.mark.parametrize("horas", [0.0, -1.0, 1000.0])
def test_una_ventana_fuera_de_las_cotas_se_rechaza(horas):
    """Ni una ventana que nace expirada ni uno que autoriza durante meses."""
    with pytest.raises(Exception):
        GestanteSettings(ventana_sesion_horas=horas)


# ---------------------------------------------------------------------------
# Reapertura dentro y fuera de la ventana
# ---------------------------------------------------------------------------


def test_reapertura_dentro_de_la_ventana_sigue_autenticada(tmp_path):
    """Cerrar y abrir el navegador no obliga a volver a escribir credenciales."""
    reloj = RelojFalso()
    cliente, _, _, _ = construir_cliente(tmp_path, reloj=reloj)
    respuesta = iniciar_sesion(cliente)
    identificador = cliente.cookies[NOMBRE_DE_COOKIE]

    reloj.avanzar(hours=71)

    # Un cliente nuevo con la misma cookie: es lo que hace un navegador que se
    # cerro y se volvio a abrir.
    otro = construir_cliente(tmp_path, reloj=reloj)[0]
    otro.cookies.set(NOMBRE_DE_COOKIE, identificador)

    assert otro.get("/adaptador/sesion").json()["autenticada"] is True


def test_reapertura_despues_de_expirar_obliga_a_autenticar(tmp_path):
    reloj = RelojFalso()
    cliente, _, _, _ = construir_cliente(tmp_path, reloj=reloj)
    iniciar_sesion(cliente)
    identificador = cliente.cookies[NOMBRE_DE_COOKIE]

    reloj.avanzar(hours=72, seconds=1)

    otro = construir_cliente(tmp_path, reloj=reloj)[0]
    otro.cookies.set(NOMBRE_DE_COOKIE, identificador)

    assert otro.get("/adaptador/sesion").json() == {"autenticada": False}
    assert otro.get("/adaptador/estado-local").status_code == 401


def test_muchas_reaperturas_sin_conexion_no_alargan_la_ventana(tmp_path):
    """La ventana se limita por tiempo desde la ultima validacion en linea.

    Sin token en memoria --el caso de un proceso reiniciado-- el adaptador no
    toca la red, y por tanto no hay revalidacion que pueda renovar nada. Diez
    aperturas dejan ``expira_en`` exactamente donde estaba.
    """
    reloj = RelojFalso()
    cliente, _, _, _ = construir_cliente(tmp_path, reloj=reloj)
    iniciar_sesion(cliente)
    identificador = cliente.cookies[NOMBRE_DE_COOKIE]
    expira_original = cliente.get("/adaptador/sesion").json()["expira_en"]

    central = ClienteCentralDoble(disponible_=False)
    for _ in range(10):
        reloj.avanzar(hours=6)
        # Proceso nuevo: el token se perdio, que es lo que ocurre al reiniciar.
        otro, _, _, _ = construir_cliente(tmp_path, central=central, reloj=reloj)
        otro.cookies.set(NOMBRE_DE_COOKIE, identificador)
        respuesta = otro.get("/adaptador/sesion")
        assert respuesta.json()["autenticada"] is True
        assert respuesta.json()["expira_en"] == expira_original
        assert central.llamadas_identidad == 0

    # El bucle dejo el reloj en la hora 60, y la sesion sigue viva porque la
    # ventana es de 72: las reaperturas la consumieron, no la movieron.
    respuesta = otro.get("/adaptador/sesion")
    assert respuesta.json()["segundos_restantes"] == 12 * SEGUNDOS_POR_HORA

    # Pasada la hora 72 se acabo, y ninguna reapertura pudo evitarlo.
    reloj.avanzar(hours=12, seconds=1)
    otro, _, _, _ = construir_cliente(tmp_path, central=central, reloj=reloj)
    otro.cookies.set(NOMBRE_DE_COOKIE, identificador)
    assert otro.get("/adaptador/sesion").json() == {"autenticada": False}


def test_una_revalidacion_en_linea_correcta_renueva_la_ventana(tmp_path):
    """Lo unico que mueve ``expira_en`` hacia adelante."""
    reloj = RelojFalso()
    cliente, central, _, _ = construir_cliente(tmp_path, reloj=reloj)
    iniciar_sesion(cliente)
    expira_original = cliente.get("/adaptador/sesion").json()["expira_en"]

    reloj.avanzar(hours=10)
    respuesta = cliente.get("/adaptador/sesion")

    assert respuesta.json()["autenticada"] is True
    assert respuesta.json()["expira_en"] > expira_original
    assert respuesta.json()["segundos_restantes"] == 72 * SEGUNDOS_POR_HORA
    assert central.llamadas_identidad >= 2


def test_un_token_central_expirado_no_cierra_la_sesion_local(tmp_path):
    """Comportamiento deliberado, y la razon esta en el contrato de SCRUM-70.

    ``GET /yo`` responde 401 tanto por un token vencido --lo normal, porque dura
    treinta minutos por omision-- como por una cuenta desactivada, y los hace
    indistinguibles a proposito. Cerrar la sesion local ante ese 401 expulsaria
    a toda paciente media hora despues de entrar, que es justo lo que el modo
    sin conexion existe para evitar.
    """
    reloj = RelojFalso()
    cliente, central, _, _ = construir_cliente(tmp_path, reloj=reloj)
    iniciar_sesion(cliente)

    central.estado_identidad = EstadoRespuesta.RECHAZADO

    reloj.avanzar(hours=1)
    respuesta = cliente.get("/adaptador/sesion")

    assert respuesta.json()["autenticada"] is True
    # Y el token se olvido, asi que ya no se vuelve a preguntar por el.
    llamadas = central.llamadas_identidad
    reloj.avanzar(hours=1)
    cliente.get("/adaptador/sesion")
    assert central.llamadas_identidad == llamadas


# ---------------------------------------------------------------------------
# Cierre de sesion
# ---------------------------------------------------------------------------


def test_el_cierre_de_sesion_invalida_la_cookie_anterior(tmp_path):
    """La invalidacion es del lado del servidor: una copia de la cookie no sirve."""
    cliente, _, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)
    identificador = cliente.cookies[NOMBRE_DE_COOKIE]

    cliente.post("/adaptador/cerrar-sesion")

    # Un cliente distinto que guardo la cookie antes del cierre.
    otro, _, _, _ = construir_cliente(tmp_path)
    otro.cookies.set(NOMBRE_DE_COOKIE, identificador)

    assert otro.get("/adaptador/sesion").json() == {"autenticada": False}
    assert otro.get("/adaptador/estado-local").status_code == 401


def test_el_cierre_de_sesion_caduca_la_cookie_en_el_navegador(tmp_path):
    cliente, _, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)

    respuesta = cliente.post("/adaptador/cerrar-sesion")

    assert max_age_de(respuesta) == 0


# ---------------------------------------------------------------------------
# El modulo de sesion, directamente
# ---------------------------------------------------------------------------


def test_abrir_una_sesion_para_un_rol_que_no_es_paciente_se_rechaza(tmp_path):
    ruta = tmp_path / "s.sqlite3"
    with almacen.abrir_almacen(ruta) as conexion:
        almacen.inicializar(conexion)
        with pytest.raises(RolNoAutorizado):
            sesion_local.abrir(
                conexion,
                id_usuario=1,
                rol=NombreRol.MEDICO,
                ventana_segundos=3600,
                ahora=RelojFalso()(),
            )


def test_el_identificador_solo_se_guarda_como_digest(tmp_path):
    """Quien lea el archivo no obtiene con ello una cookie utilizable."""
    reloj = RelojFalso()
    ruta = tmp_path / "s.sqlite3"
    with almacen.abrir_almacen(ruta) as conexion:
        almacen.inicializar(conexion)
        identificador, _ = sesion_local.abrir(
            conexion,
            id_usuario=7,
            rol=NombreRol.PACIENTE,
            ventana_segundos=3600,
            ahora=reloj(),
        )

    crudo = ruta.read_bytes()
    assert identificador.encode() not in crudo
    assert sesion_local.digest(identificador).encode() in crudo


def test_renovar_no_resucita_una_sesion_cerrada(tmp_path):
    reloj = RelojFalso()
    ruta = tmp_path / "s.sqlite3"
    with almacen.abrir_almacen(ruta) as conexion:
        almacen.inicializar(conexion)
        identificador, _ = sesion_local.abrir(
            conexion,
            id_usuario=7,
            rol=NombreRol.PACIENTE,
            ventana_segundos=3600,
            ahora=reloj(),
        )
        assert sesion_local.cerrar(conexion, identificador, ahora=reloj()) is True

        assert (
            sesion_local.renovar(
                conexion, identificador, ventana_segundos=3600, ahora=reloj()
            )
            is None
        )
        assert sesion_local.validar(conexion, identificador, ahora=reloj()) is None


def test_cerrar_es_idempotente(tmp_path):
    reloj = RelojFalso()
    ruta = tmp_path / "s.sqlite3"
    with almacen.abrir_almacen(ruta) as conexion:
        almacen.inicializar(conexion)
        identificador, _ = sesion_local.abrir(
            conexion,
            id_usuario=7,
            rol=NombreRol.PACIENTE,
            ventana_segundos=3600,
            ahora=reloj(),
        )
        assert sesion_local.cerrar(conexion, identificador, ahora=reloj()) is True
        assert sesion_local.cerrar(conexion, identificador, ahora=reloj()) is False


def test_los_instantes_se_guardan_en_utc(tmp_path):
    """Misma convencion que app.edge.outbox: ordenar por la columna es ordenar en el tiempo."""
    reloj = RelojFalso()
    ruta = tmp_path / "s.sqlite3"
    with almacen.abrir_almacen(ruta) as conexion:
        almacen.inicializar(conexion)
        identificador, sesion = sesion_local.abrir(
            conexion,
            id_usuario=7,
            rol=NombreRol.PACIENTE,
            ventana_segundos=3600,
            ahora=reloj(),
        )
        fila = conexion.execute(
            f"SELECT validada_en, expira_en FROM {almacen.TABLA_SESION}"
        ).fetchone()

    assert fila["validada_en"].endswith("+00:00")
    assert fila["expira_en"].endswith("+00:00")
    assert sesion.expira_en.tzinfo is timezone.utc
