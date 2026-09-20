"""El cliente del adaptador contra la API central (SCRUM-72).

Es la unica pieza de esta interfaz que habla con el servidor, y habla con el
**por HTTP**, igual que lo hace el nodo edge. No abre una conexion a PostgreSQL,
no importa modelos de SQLAlchemy y no consulta tablas: si algo no esta publicado
como endpoint, para este adaptador no existe.

**Tres operaciones, y ninguna mas.**

* :meth:`ClienteCentral.autenticar` cambia unas credenciales por un token, con
  ``POST /api/v1/autenticacion/token``.
* :meth:`ClienteCentral.identidad` pregunta quien es el portador de un token y
  con que rol, con ``GET /api/v1/autenticacion/yo``.
* :meth:`ClienteCentral.disponible` comprueba si el servidor responde, con
  ``GET /health``.

Las tres existen hoy y este ticket no anade ninguna. En particular, aqui no hay
--ni debe haber-- nada que pida un embarazo, una lectura o un historial: esos
contratos pertenecen a SCRUM-98 y todavia no existen. Inventarlos aqui seria
escribir contra un servidor imaginario.

**Lo que este modulo no hace con lo que recibe.** La contrasena entra como
argumento, se envia una vez y no se guarda en ningun atributo, ningun registro
ni ninguna excepcion. El token que devuelve el servidor se entrega al llamador y
este modulo no lo persiste: quien decide donde vive es
``app.gestante.aplicacion``, y decide que viva en memoria.

**Nada de lo que se registra lleva un valor.** Ni el correo intentado, ni la
contrasena, ni el token, ni la cabecera ``Authorization``, ni el cuerpo de una
respuesta. Es la misma regla que siguen ``app.services.errores`` y
``app.edge.cliente``: elegir los campos seguros de uno en uno, nunca formatear
la excepcion.

**Se distingue "no se pudo preguntar" de "el servidor dijo que no".** Un fallo
de transporte no es una credencial invalida, y confundirlos haria que una caida
de red se le presentara a la paciente como «tu contrasena esta mal». Por eso
:class:`EstadoRespuesta` los separa.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

# Reutilizadas del cliente del nodo edge, que ya las declara publicamente para
# esta misma ruta y este mismo esquema. Escribirlas otra vez aqui crearia dos
# definiciones de una sola cosa, libres de separarse.
from app.edge.cliente import CABECERA_AUTORIZACION, ESQUEMA_BEARER, RUTA_IDENTIDAD
from app.models.enums import NombreRol

registrador = logging.getLogger(__name__)

# Rutas que el nodo edge no usa --nunca inicia sesion ni sondea la salud-- y que
# por tanto no existen como constante compartida.
RUTA_TOKEN = "/api/v1/autenticacion/token"
RUTA_SALUD = "/health"

CODIGO_OK = 200
CODIGO_NO_AUTENTICADO = 401
CODIGO_PROHIBIDO = 403


class EstadoRespuesta(enum.Enum):
    """Como termino una conversacion con el servidor central."""

    OK = "OK"
    # El servidor respondio, y respondio que no. Cubre credenciales que no
    # sirven y cuentas desactivadas: SCRUM-70 los hace deliberadamente
    # indistinguibles, y este adaptador no intenta distinguirlos.
    RECHAZADO = "RECHAZADO"
    # No se pudo preguntar: no hay red, el servidor no escucha, o la peticion
    # agoto su tiempo. No dice nada sobre las credenciales.
    SIN_CONEXION = "SIN_CONEXION"
    # El servidor respondio algo que no encaja en el contrato: un 5xx, un
    # cuerpo ilegible, un 2xx inesperado.
    ERROR_REMOTO = "ERROR_REMOTO"


@dataclass(frozen=True)
class RespuestaToken:
    """Resultado de un intento de autenticacion.

    ``token`` solo lleva valor cuando ``estado`` es ``OK``. No se guarda en
    ninguna parte por el hecho de pasar por aqui.
    """

    estado: EstadoRespuesta
    token: str | None = None


@dataclass(frozen=True)
class RespuestaIdentidad:
    """Quien es el portador de un token, segun el servidor.

    Dos campos tecnicos y nada mas, porque eso es exactamente lo que ``/yo``
    devuelve: sin nombre, sin cedula, sin contacto y sin perfil clinico.
    """

    estado: EstadoRespuesta
    id_usuario: int | None = None
    rol: NombreRol | None = None


class ClienteCentral(Protocol):
    """Lo que el adaptador necesita del servidor central.

    Declarado como ``Protocol`` para que las pruebas puedan sustituirlo por un
    doble sin levantar la API ni PostgreSQL, y para que el adaptador dependa de
    estas tres operaciones y no de httpx.
    """

    def autenticar(self, *, email: str, password: str) -> RespuestaToken: ...

    def identidad(self, token: str) -> RespuestaIdentidad: ...

    def disponible(self) -> bool: ...


class ClienteCentralHTTP:
    """Implementacion real, sobre un ``httpx.Client`` ya configurado.

    Recibe el cliente en lugar de construirlo, igual que ``ClienteEdge``: la URL
    base y el tiempo de espera son configuracion, y quien la posee es el
    arranque del adaptador.
    """

    def __init__(self, http: httpx.Client) -> None:
        self._http = http

    # -- Autenticacion ---------------------------------------------------

    def autenticar(self, *, email: str, password: str) -> RespuestaToken:
        """Cambia credenciales por un token, o explica por que no pudo.

        La contrasena se usa aqui y solo aqui. No se asigna a ningun atributo y
        no aparece en ningun registro, tampoco cuando la peticion falla.
        """
        try:
            respuesta = self._http.post(
                RUTA_TOKEN, json={"email": email, "password": password}
            )
        except httpx.HTTPError:
            # Sin ``exc_info`` y sin formatear la excepcion: el traceback de un
            # error de transporte puede llevar la URL y el cuerpo enviado, y ese
            # cuerpo es la contrasena.
            registrador.warning("Autenticacion: el servidor central no respondio.")
            return RespuestaToken(EstadoRespuesta.SIN_CONEXION)

        if respuesta.status_code == CODIGO_NO_AUTENTICADO:
            registrador.info("Autenticacion: el servidor central rechazo el intento.")
            return RespuestaToken(EstadoRespuesta.RECHAZADO)

        if respuesta.status_code != CODIGO_OK:
            registrador.warning(
                "Autenticacion: respuesta inesperada http=%s", respuesta.status_code
            )
            return RespuestaToken(EstadoRespuesta.ERROR_REMOTO)

        token = _texto_de(respuesta, "access_token")
        if token is None:
            registrador.warning("Autenticacion: el cuerpo no trae un token utilizable.")
            return RespuestaToken(EstadoRespuesta.ERROR_REMOTO)

        return RespuestaToken(EstadoRespuesta.OK, token=token)

    # -- Identidad -------------------------------------------------------

    def identidad(self, token: str) -> RespuestaIdentidad:
        """Quien habla con ese token, segun PostgreSQL en este instante.

        Importa que sea una pregunta al servidor y no una lectura del token:
        ``app.services.principal`` resuelve el rol y el estado de la cuenta
        contra la base en cada peticion, asi que una cuenta desactivada deja de
        valer aqui aunque su token siga sin caducar.
        """
        try:
            respuesta = self._http.get(
                RUTA_IDENTIDAD,
                headers={CABECERA_AUTORIZACION: f"{ESQUEMA_BEARER} {token}"},
            )
        except httpx.HTTPError:
            registrador.warning("Identidad: el servidor central no respondio.")
            return RespuestaIdentidad(EstadoRespuesta.SIN_CONEXION)

        if respuesta.status_code in (CODIGO_NO_AUTENTICADO, CODIGO_PROHIBIDO):
            return RespuestaIdentidad(EstadoRespuesta.RECHAZADO)

        if respuesta.status_code != CODIGO_OK:
            registrador.warning(
                "Identidad: respuesta inesperada http=%s", respuesta.status_code
            )
            return RespuestaIdentidad(EstadoRespuesta.ERROR_REMOTO)

        cuerpo = _cuerpo_de(respuesta)
        if cuerpo is None:
            return RespuestaIdentidad(EstadoRespuesta.ERROR_REMOTO)

        try:
            id_usuario = int(cuerpo["id_usuario"])
            rol = NombreRol(cuerpo["rol"])
        except (KeyError, TypeError, ValueError):
            # Un rol que este enum no conoce, o un cuerpo con otra forma. No se
            # adivina: se trata como respuesta que no cumple el contrato.
            registrador.warning("Identidad: el cuerpo no cumple el contrato de /yo.")
            return RespuestaIdentidad(EstadoRespuesta.ERROR_REMOTO)

        return RespuestaIdentidad(EstadoRespuesta.OK, id_usuario=id_usuario, rol=rol)

    # -- Salud -----------------------------------------------------------

    def disponible(self) -> bool:
        """Si el servidor central responde ahora mismo.

        ``/health`` es publico a proposito y no dice nada del sistema mas alla
        de que el proceso contesta, que es justo lo que hace falta para decidir
        si la interfaz se muestra en linea o sin conexion.
        """
        try:
            respuesta = self._http.get(RUTA_SALUD)
        except httpx.HTTPError:
            return False
        return respuesta.status_code == CODIGO_OK


def _cuerpo_de(respuesta: httpx.Response) -> dict | None:
    """El cuerpo como diccionario, o ``None`` si no lo es.

    Un cuerpo ilegible no se registra ni se adjunta a nada: podria ser
    cualquier cosa, incluido el eco de lo que se envio.
    """
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None
    return cuerpo if isinstance(cuerpo, dict) else None


def _texto_de(respuesta: httpx.Response, campo: str) -> str | None:
    """Un campo de texto no vacio del cuerpo, o ``None``."""
    cuerpo = _cuerpo_de(respuesta)
    if cuerpo is None:
        return None
    valor = cuerpo.get(campo)
    return valor if isinstance(valor, str) and valor else None
