"""Emision y validacion del token de sesion (SCRUM-70).

Catorce situaciones, y ninguna duerme. La expiracion se ejerce emitiendo un token
con un reloj inyectado que devuelve un instante del pasado: la biblioteca lo
rechaza con su propio reloj, que es el que tiene que rechazarlo en produccion.
Esperar de verdad a que un token caduque seria pagar minutos de CI para observar
lo mismo.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.config import ConfiguracionJWT
from app.services.tokens import (
    ALGORITMO,
    CLAIMS_REQUERIDOS,
    MENSAJE_TOKEN_INVALIDO,
    TokenInvalido,
    emitir,
    validar,
)

SECRETO = "secreto-ficticio-de-pruebas-con-longitud-suficiente"
OTRO_SECRETO = "otro-secreto-ficticio-igualmente-largo-para-pruebas"
ID_USUARIO = 137


@pytest.fixture
def configuracion() -> ConfiguracionJWT:
    return ConfiguracionJWT(secreto=SECRETO, expiracion=timedelta(minutes=30))


def reloj_fijo(momento: datetime):
    return lambda: momento


def forjar(payload: dict, *, secreto: str = SECRETO, alg: str = "HS256") -> str:
    """Un token construido a mano, para escribir claims que PyJWT no emitiria."""

    def segmento(datos: dict) -> str:
        crudo = json.dumps(datos, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(crudo).rstrip(b"=").decode("ascii")

    cabecera = segmento({"alg": alg, "typ": "JWT"})
    cuerpo = segmento(payload)
    firmado = f"{cabecera}.{cuerpo}".encode("ascii")
    firma = hmac.new(secreto.encode("utf-8"), firmado, hashlib.sha256).digest()
    return f"{cabecera}.{cuerpo}.{base64.urlsafe_b64encode(firma).rstrip(b'=').decode()}"


def payload_valido(**cambios) -> dict:
    ahora = datetime.now(timezone.utc)
    base = {
        "sub": str(ID_USUARIO),
        "iat": int(ahora.timestamp()),
        "exp": int((ahora + timedelta(minutes=30)).timestamp()),
    }
    base.update(cambios)
    return base


# ---------------------------------------------------------------------------
# 1. Emision y decodificacion
# ---------------------------------------------------------------------------


def test_emite_con_los_claims_minimos(configuracion):
    token = emitir(ID_USUARIO, configuracion)
    contenido = jwt.decode(token.access_token, SECRETO, algorithms=[ALGORITMO])

    assert set(CLAIMS_REQUERIDOS) <= set(contenido)


def test_un_token_valido_devuelve_el_identificador(configuracion):
    token = emitir(ID_USUARIO, configuracion)

    assert validar(token.access_token, configuracion) == ID_USUARIO


def test_el_sub_es_una_cadena_decimal(configuracion):
    """RFC 7519: el sujeto es un string. No un entero que lo parezca."""
    token = emitir(ID_USUARIO, configuracion)
    contenido = jwt.decode(token.access_token, SECRETO, algorithms=[ALGORITMO])

    assert contenido["sub"] == str(ID_USUARIO)
    assert isinstance(contenido["sub"], str)


def test_la_vigencia_publicada_coincide_con_la_configurada(configuracion):
    assert emitir(ID_USUARIO, configuracion).expira_en_segundos == 30 * 60


def test_el_token_no_lleva_rol(configuracion):
    """El rol se lee de PostgreSQL. Un claim que no existe no se puede falsificar."""
    token = emitir(ID_USUARIO, configuracion)
    contenido = jwt.decode(token.access_token, SECRETO, algorithms=[ALGORITMO])

    assert "rol" not in contenido
    assert set(contenido) == set(CLAIMS_REQUERIDOS)


def test_el_token_no_lleva_datos_personales(configuracion):
    """Un JWT esta firmado, no cifrado: su payload se lee en claro."""
    token = emitir(ID_USUARIO, configuracion)
    cuerpo = token.access_token.split(".")[1]
    crudo = base64.urlsafe_b64decode(cuerpo + "=" * (-len(cuerpo) % 4)).decode()

    for prohibido in ("@", "email", "correo", "cedula", "telefono", "nombre"):
        assert prohibido not in crudo.lower()


def test_el_secreto_no_viaja_dentro_del_token(configuracion):
    assert SECRETO not in emitir(ID_USUARIO, configuracion).access_token


# ---------------------------------------------------------------------------
# 2. Rechazos
# ---------------------------------------------------------------------------


def test_una_firma_manipulada_se_rechaza(configuracion):
    token = emitir(ID_USUARIO, configuracion).access_token
    cabecera, cuerpo, firma = token.split(".")
    alterada = ("A" if firma[0] != "A" else "B") + firma[1:]

    with pytest.raises(TokenInvalido):
        validar(f"{cabecera}.{cuerpo}.{alterada}", configuracion)


def test_un_payload_manipulado_se_rechaza(configuracion):
    """Cambiar el sujeto invalida la firma: el claim no se lee siquiera."""
    ajeno = forjar(payload_valido(sub="999999"), secreto=OTRO_SECRETO)

    with pytest.raises(TokenInvalido):
        validar(ajeno, configuracion)


@pytest.mark.parametrize(
    "malformado",
    ["", "x", "a.b", "a.b.c.d", "no.es.token", "....", "Bearer algo"],
)
def test_un_token_malformado_se_rechaza(configuracion, malformado):
    with pytest.raises(TokenInvalido):
        validar(malformado, configuracion)


def test_un_token_expirado_se_rechaza(configuracion):
    """Sin dormir: se emite con un reloj del pasado y caduca por si solo."""
    pasado = datetime.now(timezone.utc) - timedelta(hours=2)
    token = emitir(ID_USUARIO, configuracion, reloj=reloj_fijo(pasado))

    with pytest.raises(TokenInvalido):
        validar(token.access_token, configuracion)


def test_la_frontera_de_expiracion_se_respeta(configuracion):
    """Un segundo antes vale; un segundo despues, no. Sin margen de tolerancia."""
    ahora = datetime.now(timezone.utc)

    vivo = emitir(
        ID_USUARIO, configuracion, reloj=reloj_fijo(ahora - timedelta(minutes=29))
    )
    muerto = emitir(
        ID_USUARIO, configuracion, reloj=reloj_fijo(ahora - timedelta(minutes=31))
    )

    assert validar(vivo.access_token, configuracion) == ID_USUARIO
    with pytest.raises(TokenInvalido):
        validar(muerto.access_token, configuracion)


def test_el_tiempo_se_maneja_en_utc_consciente(configuracion):
    """Un reloj ingenuo no puede colarse: ``emitir`` trabaja con instantes aware."""
    token = emitir(ID_USUARIO, configuracion)
    contenido = jwt.decode(token.access_token, SECRETO, algorithms=[ALGORITMO])

    emitido = datetime.fromtimestamp(contenido["iat"], tz=timezone.utc)
    expira = datetime.fromtimestamp(contenido["exp"], tz=timezone.utc)

    assert emitido.tzinfo is timezone.utc
    assert expira - emitido == timedelta(minutes=30)


@pytest.mark.parametrize("ausente", ["exp", "iat", "sub"])
def test_un_claim_requerido_ausente_se_rechaza(configuracion, ausente):
    payload = payload_valido()
    del payload[ausente]

    with pytest.raises(TokenInvalido):
        validar(forjar(payload), configuracion)


@pytest.mark.parametrize(
    "sujeto",
    ["", "abc", "-1", " 137", "137 ", "13.7", "١٣٧", "1e3", "0x89"],
    ids=[
        "vacio",
        "letras",
        "negativo",
        "espacio-delante",
        "espacio-detras",
        "decimal",
        "digitos-arabigos",
        "notacion-cientifica",
        "hexadecimal",
    ],
)
def test_un_sub_que_no_es_decimal_ascii_se_rechaza(configuracion, sujeto):
    """``str.isdigit()`` aceptaria los digitos arabigos; el patron ASCII no."""
    with pytest.raises(TokenInvalido):
        validar(forjar(payload_valido(sub=sujeto)), configuracion)


def test_un_sub_numerico_se_rechaza(configuracion):
    """El estandar exige una cadena, y un JSON numerico no lo es."""
    with pytest.raises(TokenInvalido):
        validar(forjar(payload_valido(sub=ID_USUARIO)), configuracion)


def test_un_rol_inyectado_en_el_token_se_ignora(configuracion):
    """La prueba que hace solida la decision de dejar el rol fuera del JWT.

    Un token perfectamente firmado que trae ``rol: ADMIN`` devuelve exactamente
    lo mismo que uno sin el: un identificador. El rol no se lee de aqui, asi que
    inyectarlo no eleva nada.
    """
    con_rol = forjar(payload_valido(rol="ADMIN", admin=True))

    assert validar(con_rol, configuracion) == ID_USUARIO


def test_el_algoritmo_no_permitido_se_rechaza():
    """Firmado con HS512: la lista blanca la fija el servidor, no la cabecera.

    El secreto se alarga a 64 bytes solo para este caso, porque PyJWT avisa --con
    razon-- cuando una clave HS512 es mas corta que su digest. El aviso no tiene
    nada que ver con lo que se prueba aqui, y dejarlo sonando ensuciaria el
    reporte de CI con una advertencia que nadie debe aprender a ignorar.
    """
    secreto_largo = SECRETO.ljust(64, "0")
    configuracion = ConfiguracionJWT(
        secreto=secreto_largo, expiracion=timedelta(minutes=30)
    )
    ahora = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": str(ID_USUARIO),
            "iat": ahora,
            "exp": ahora + timedelta(minutes=30),
        },
        secreto_largo,
        algorithm="HS512",
    )

    with pytest.raises(TokenInvalido):
        validar(token, configuracion)


def test_alg_none_se_rechaza(configuracion):
    """El ataque clasico: una cabecera que declara que no hay firma."""

    def segmento(datos: dict) -> str:
        crudo = json.dumps(datos, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(crudo).rstrip(b"=").decode("ascii")

    sin_firma = f'{segmento({"alg": "none", "typ": "JWT"})}.{segmento(payload_valido())}.'

    with pytest.raises(TokenInvalido):
        validar(sin_firma, configuracion)


def test_un_token_de_otro_secreto_se_rechaza(configuracion):
    otra = ConfiguracionJWT(secreto=OTRO_SECRETO, expiracion=timedelta(minutes=30))
    ajeno = emitir(ID_USUARIO, otra)

    with pytest.raises(TokenInvalido):
        validar(ajeno.access_token, configuracion)


# ---------------------------------------------------------------------------
# 3. El mensaje publico no dice nada
# ---------------------------------------------------------------------------


def test_todos_los_rechazos_dan_el_mismo_mensaje(configuracion):
    """Ni el claim que falto, ni si fallo la firma o la expiracion."""
    pasado = datetime.now(timezone.utc) - timedelta(hours=2)
    casos = [
        "no.es.un.token",
        forjar(payload_valido(sub="abc")),
        emitir(ID_USUARIO, configuracion, reloj=reloj_fijo(pasado)).access_token,
        emitir(
            ID_USUARIO,
            ConfiguracionJWT(secreto=OTRO_SECRETO, expiracion=timedelta(minutes=5)),
        ).access_token,
    ]

    mensajes = set()
    for token in casos:
        with pytest.raises(TokenInvalido) as capturado:
            validar(token, configuracion)
        mensajes.add(capturado.value.detalle)

    assert mensajes == {MENSAJE_TOKEN_INVALIDO}


def test_el_mensaje_no_nombra_la_biblioteca_ni_el_secreto(configuracion):
    with pytest.raises(TokenInvalido) as capturado:
        validar("no.es.un.token", configuracion)

    mensaje = str(capturado.value)
    for prohibido in ("jwt", "PyJWT", "Signature", SECRETO, "HS256", "claim"):
        assert prohibido not in mensaje


def test_la_excepcion_de_la_biblioteca_queda_encadenada_pero_no_publicada(
    configuracion,
):
    """Util para el diagnostico interno, invisible para el cliente."""
    with pytest.raises(TokenInvalido) as capturado:
        validar("no.es.un.token", configuracion)

    assert isinstance(capturado.value.__cause__, jwt.PyJWTError)
    assert str(capturado.value) == MENSAJE_TOKEN_INVALIDO
