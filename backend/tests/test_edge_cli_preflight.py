"""El preflight de credencial visto desde el comando (SCRUM-70).

El preflight existe para proteger el presupuesto de intentos de la cola:
``outbox.reclamar_intento`` incrementa ``intentos`` **antes** de enviar nada, asi
que una ejecucion con la credencial equivocada gastaria un intento de cada
paquete solo para descubrir un 401, y repetirla agotaria eventos que nunca
estuvieron mal. Por eso lo que afirma casi cada prueba de aqui es la misma cosa:
**la cola sigue con cero intentos consumidos**.

Vive en un archivo propio y no dentro de ``test_edge_cli.py`` porque el andamiaje
es el mismo --``cli``, ``base``, ``paquete`` y ``_con_transporte`` se importan de
alli-- pero lo que se prueba es la frontera que anade este ticket.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import httpx
import pytest

from app.edge import almacenamiento as alm
from app.edge.cliente import CABECERA_REPLAY

# Fixtures y ayudantes del modulo del CLI. pytest los reconoce igual al
# importarlos aqui, y asi no existe una segunda copia del andamiaje.
from tests.test_edge_cli import (  # noqa: F401
    _con_transporte,
    base,
    cli,
    ejecutar,
    paquete,
)


def intentos_de_la_cola(ruta) -> list[int]:
    with alm.conectar(ruta) as conexion:
        return [
            fila["intentos"]
            for fila in conexion.execute(
                "SELECT intentos FROM outbox ORDER BY id_outbox"
            )
        ]


def entrega_correcta(id_sesion: int = 840, id_lectura: int = 1290):
    def manejador(peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                "id_sesion": id_sesion,
                "lecturas_creadas": 1,
                "ids_lectura": [id_lectura],
            },
            headers={CABECERA_REPLAY: "false"},
        )

    return manejador


def rechazo(codigo: int = 401):
    return lambda peticion: httpx.Response(codigo, json={"detail": "no"})


# ---------------------------------------------------------------------------
# 1. Nada empieza sin una credencial utilizable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("orden", ["enviar", "sincronizar"])
def test_sin_token_no_se_reclama_nada(cli, base, paquete, capsys, monkeypatch, orden):
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(monkeypatch, cli, entrega_correcta(), token=None)

    codigo = ejecutar(cli, base, orden)

    assert codigo == cli.CODIGO_DE_ERROR
    assert "EDGE_API_TOKEN" in capsys.readouterr().err
    assert intentos_de_la_cola(base) == [0]


@pytest.mark.parametrize("orden", ["enviar", "sincronizar"])
@pytest.mark.parametrize("codigo_identidad", [401, 403])
def test_una_credencial_rechazada_no_reclama_nada(
    cli, base, paquete, capsys, monkeypatch, orden, codigo_identidad
):
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(
        monkeypatch,
        cli,
        entrega_correcta(),
        identidad=httpx.Response(codigo_identidad, json={"detail": "no"}),
    )

    codigo = ejecutar(cli, base, orden)

    assert codigo == cli.CODIGO_DE_CREDENCIAL
    assert intentos_de_la_cola(base) == [0]


@pytest.mark.parametrize("orden", ["enviar", "sincronizar"])
@pytest.mark.parametrize("rol", ["ADMIN", "MEDICO"])
def test_un_rol_que_no_puede_ingerir_no_reclama_nada(
    cli, base, paquete, capsys, monkeypatch, orden, rol
):
    """La ingesta exige PACIENTE, y el nodo lo sabe antes de gastar nada."""
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(
        monkeypatch,
        cli,
        entrega_correcta(),
        identidad=httpx.Response(200, json={"id_usuario": 101, "rol": rol}),
    )

    codigo = ejecutar(cli, base, orden)
    salida = capsys.readouterr().err

    assert codigo == cli.CODIGO_DE_CREDENCIAL
    assert "PACIENTE" in salida
    assert intentos_de_la_cola(base) == [0]


@pytest.mark.parametrize("orden", ["enviar", "sincronizar"])
def test_la_api_caida_en_el_preflight_no_gasta_intentos(
    cli, base, paquete, capsys, monkeypatch, orden
):
    """Decision del ticket: si no se puede verificar, no se empieza.

    Es un cambio de comportamiento respecto a SCRUM-65 y conviene tenerlo por
    escrito: antes, una API caida consumia un intento por evento con errores de
    transporte y programaba su backoff. Ahora la ejecucion se detiene antes de
    abrir la outbox y la cola conserva intacto su presupuesto. La **politica**
    de transporte no cambio; lo que cambio es si el comando llega a entrar en el
    bucle.
    """
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()

    def caida(peticion):
        raise httpx.ConnectError("sin ruta al host")

    _con_transporte(monkeypatch, cli, caida, identidad=caida)

    codigo = ejecutar(cli, base, orden)

    assert codigo == cli.CODIGO_DE_CREDENCIAL
    assert "No se inicio la ejecucion" in capsys.readouterr().err
    assert intentos_de_la_cola(base) == [0]


@pytest.mark.parametrize("orden", ["enviar", "sincronizar"])
def test_un_cuerpo_de_identidad_ilegible_no_gasta_intentos(
    cli, base, paquete, capsys, monkeypatch, orden
):
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(
        monkeypatch,
        cli,
        entrega_correcta(),
        identidad=httpx.Response(200, content=b"<html>no soy json</html>"),
    )

    assert ejecutar(cli, base, orden) == cli.CODIGO_DE_CREDENCIAL
    assert intentos_de_la_cola(base) == [0]


def test_varios_eventos_conservan_todos_su_presupuesto(
    cli, base, paquete, capsys, monkeypatch
):
    """La propiedad que motivo el preflight, con una cola de verdad."""
    import json

    for indice in range(3):
        cuerpo = json.loads(paquete.read_text(encoding="utf-8"))
        cuerpo["fecha_inicio"] = cuerpo["fecha_inicio"].replace(
            "T10", f"T1{indice}"
        )
        otro = paquete.parent / f"paquete_{indice}.json"
        otro.write_text(json.dumps(cuerpo), encoding="utf-8")
        ejecutar(cli, base, "capturar", str(otro))
    capsys.readouterr()

    _con_transporte(
        monkeypatch,
        cli,
        entrega_correcta(),
        identidad=httpx.Response(401, json={"detail": "no"}),
    )

    ejecutar(cli, base, "sincronizar")

    assert intentos_de_la_cola(base) == [0, 0, 0]


# ---------------------------------------------------------------------------
# 2. Con la credencial buena, el flujo de siempre
# ---------------------------------------------------------------------------


def test_el_preflight_valido_deja_continuar(cli, base, paquete, capsys, monkeypatch):
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(monkeypatch, cli, entrega_correcta())

    assert ejecutar(cli, base, "enviar") == 0
    assert "entregados                : 1" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 3. El 401 a mitad de vuelo
# ---------------------------------------------------------------------------


def test_un_401_posterior_al_preflight_conserva_el_evento(
    cli, base, paquete, capsys, monkeypatch
):
    """El token expiro en la ventana entre el preflight y el POST."""
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(monkeypatch, cli, rechazo(401))

    codigo = ejecutar(cli, base, "enviar")
    salida = capsys.readouterr().out

    assert codigo == cli.CODIGO_DE_CREDENCIAL
    assert "rechazo la credencial" in salida
    assert intentos_de_la_cola(base) == [1]


def test_tras_corregir_la_credencial_el_evento_se_entrega(
    cli, base, paquete, capsys, monkeypatch
):
    """Cero perdida y cero duplicacion: la misma clave, el mismo paquete."""
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()

    _con_transporte(monkeypatch, cli, rechazo(401))
    ejecutar(cli, base, "enviar")
    capsys.readouterr()

    _con_transporte(monkeypatch, cli, entrega_correcta(id_sesion=841, id_lectura=1291))

    assert ejecutar(cli, base, "enviar") == 0
    salida = capsys.readouterr().out
    assert "entregados                : 1" in salida

    with alm.conectar(base) as conexion:
        filas = list(conexion.execute("SELECT estado FROM outbox"))
    assert [fila["estado"] for fila in filas] == ["ENVIADO"]


def test_sincronizar_informa_del_token_expirado(cli, base, paquete, capsys, monkeypatch):
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(monkeypatch, cli, rechazo(401))

    codigo = ejecutar(cli, base, "sincronizar")
    salida = capsys.readouterr().err

    assert codigo == cli.CODIGO_CREDENCIAL
    assert "EDGE_API_TOKEN" in salida


# ---------------------------------------------------------------------------
# 4. Lo offline no depende de la credencial
# ---------------------------------------------------------------------------


def test_init_capturar_estado_y_traza_funcionan_sin_token(
    cli, base, paquete, capsys, monkeypatch
):
    """La captura sin conexion no puede depender de una cuenta central."""
    monkeypatch.delenv("EDGE_API_TOKEN", raising=False)

    assert ejecutar(cli, base, "init") == 0
    assert ejecutar(cli, base, "capturar", str(paquete)) == 0
    assert ejecutar(cli, base, "estado") == 0
    salida = capsys.readouterr().out
    assert "PENDIENTE" in salida

    assert ejecutar(cli, base, "traza", "--id-outbox", "1") == 0


# ---------------------------------------------------------------------------
# 5. El token no se imprime, no se guarda y no se pasa por argumento
# ---------------------------------------------------------------------------


def test_el_token_no_aparece_en_la_salida(cli, base, paquete, capsys, monkeypatch):
    delator = "token-que-no-debe-imprimirse-nunca"
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(monkeypatch, cli, entrega_correcta(), token=delator)

    ejecutar(cli, base, "enviar")
    envio = capsys.readouterr()
    ejecutar(cli, base, "traza", "--id-outbox", "1")
    traza = capsys.readouterr()

    for texto in (envio.out, envio.err, traza.out, traza.err):
        assert delator not in texto
        assert "Bearer" not in texto
        assert "Authorization" not in texto


def test_el_token_no_queda_en_el_archivo_sqlite(
    cli, base, paquete, capsys, monkeypatch
):
    delator = "token-que-no-debe-persistirse"
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()
    _con_transporte(monkeypatch, cli, entrega_correcta(), token=delator)

    ejecutar(cli, base, "enviar")

    crudo = base.read_bytes()
    assert delator.encode() not in crudo
    assert b"Authorization" not in crudo
    assert b"Bearer" not in crudo


@pytest.mark.parametrize("bandera", ["--token", "--api-token", "--credencial"])
def test_el_parser_no_admite_el_token_por_argumento(cli, base, bandera):
    """Un JWT en la linea de comandos acabaria en el historial del shell."""
    with pytest.raises(SystemExit):
        ejecutar(cli, base, "sincronizar", bandera, "algo")


def test_la_ayuda_no_ofrece_una_opcion_de_credencial(cli, capsys):
    from app.edge.config import cargar_settings_edge

    with pytest.raises(SystemExit):
        cli.construir_parser(cargar_settings_edge()).parse_args(["--help"])

    ayuda = capsys.readouterr().out
    assert "--token" not in ayuda
    assert "password" not in ayuda.lower()


# ---------------------------------------------------------------------------
# 6. Un token vacio o en blanco es un token ausente (code review de SCRUM-70)
# ---------------------------------------------------------------------------
#
# ``.env.example`` trae ``EDGE_API_TOKEN=`` vacio. Antes de la correccion eso no
# era ``None``: el comando construia ``Authorization: Bearer `` y gastaba una
# peticion a ``/yo`` para acabar con codigo 4 y un mensaje de «credencial
# rechazada». Ahora es la misma situacion que no definir la variable: codigo 1,
# ninguna peticion construida y la outbox sin tocar.

EN_BLANCO = ["", " ", "   ", "\t", " \t\r\n "]
IDS_EN_BLANCO = ["vacio", "un-espacio", "espacios", "tabulador", "mezcla"]


class ClienteHttpProhibido(httpx.Client):
    """Un cliente que la prueba no permite construir."""

    construidos = 0

    def __init__(self, **argumentos):
        type(self).construidos += 1
        raise AssertionError("No debia construirse ningun cliente HTTP.")


@pytest.mark.parametrize("valor", EN_BLANCO, ids=IDS_EN_BLANCO)
def test_la_configuracion_trata_el_token_en_blanco_como_ausente(valor):
    from app.edge.config import EdgeSettings

    assert EdgeSettings(api_token=valor).api_token is None


def test_un_token_no_vacio_no_se_recorta():
    """Recortarlo enviaria una credencial distinta de la configurada."""
    from app.edge.config import EdgeSettings

    con_espacios = "  token-simulado  "

    assert EdgeSettings(api_token=con_espacios).api_token.get_secret_value() == con_espacios


def test_un_token_vacio_en_el_archivo_env_es_ausente(tmp_path, monkeypatch):
    from app.edge.config import EdgeSettings

    monkeypatch.delenv("EDGE_API_TOKEN", raising=False)
    archivo = tmp_path / ".env"
    archivo.write_text("EDGE_API_TOKEN=\n", encoding="utf-8")

    assert EdgeSettings(_env_file=archivo).api_token is None


@pytest.mark.parametrize("orden", ["enviar", "sincronizar"])
@pytest.mark.parametrize("valor", EN_BLANCO, ids=IDS_EN_BLANCO)
def test_un_token_en_blanco_no_construye_peticiones_ni_toca_la_outbox(
    cli, base, paquete, capsys, monkeypatch, orden, valor
):
    """Codigo 1, ni una peticion, cero intentos consumidos.

    La configuracion se entrega ya construida con el valor en blanco, en lugar de
    pasar por una variable de entorno: en Windows, asignar una cadena vacia a una
    variable del entorno la elimina, y la prueba dejaria de ejercer el caso
    vacio para ejercer el ausente.
    """
    from app.edge.config import EdgeSettings

    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()

    ClienteHttpProhibido.construidos = 0
    monkeypatch.setattr(cli.httpx, "Client", ClienteHttpProhibido)
    monkeypatch.setattr(
        cli, "cargar_settings_edge", lambda: EdgeSettings(api_token=valor)
    )

    codigo = ejecutar(cli, base, orden)

    assert codigo == cli.CODIGO_DE_ERROR
    assert "Falta EDGE_API_TOKEN" in capsys.readouterr().err
    assert ClienteHttpProhibido.construidos == 0
    assert intentos_de_la_cola(base) == [0]


def test_un_token_en_blanco_por_variable_de_entorno_tambien_es_ausente(
    cli, base, paquete, capsys, monkeypatch
):
    """El camino real del comando, con una variable que si existe en Windows."""
    ejecutar(cli, base, "capturar", str(paquete))
    capsys.readouterr()

    ClienteHttpProhibido.construidos = 0
    monkeypatch.setattr(cli.httpx, "Client", ClienteHttpProhibido)
    monkeypatch.setenv("EDGE_API_TOKEN", "   ")

    assert ejecutar(cli, base, "sincronizar") == cli.CODIGO_DE_ERROR
    assert ClienteHttpProhibido.construidos == 0
    assert intentos_de_la_cola(base) == [0]
