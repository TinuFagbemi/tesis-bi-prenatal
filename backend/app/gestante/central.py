"""El cliente del adaptador contra la API central (SCRUM-72).

Es la unica pieza de esta interfaz que habla con el servidor, y habla con el
**por HTTP**, igual que lo hace el nodo edge. No abre una conexion a PostgreSQL,
no importa modelos de SQLAlchemy y no consulta tablas: si algo no esta publicado
como endpoint, para este adaptador no existe.

**Ocho operaciones, y ninguna mas.**

* :meth:`ClienteCentral.autenticar` cambia unas credenciales por un token, con
  ``POST /api/v1/autenticacion/token``.
* :meth:`ClienteCentral.identidad` pregunta quien es el portador de un token y
  con que rol, con ``GET /api/v1/autenticacion/yo``.
* :meth:`ClienteCentral.disponible` comprueba si el servidor responde, con
  ``GET /health``.
* :meth:`ClienteCentral.embarazos`, :meth:`ClienteCentral.sesiones` y
  :meth:`ClienteCentral.lecturas` leen la lectura clinica minima que SCRUM-98
  publica en ``/api/v1/clinico/*``, para PACIENTE y MEDICO.

Desde SCRUM-110 hay dos mas, sin token porque la paciente todavia no tiene
cuenta:

* :meth:`ClienteCentral.documentos_registro` lee la politica de privacidad y los
  terminos vigentes, con ``GET /api/v1/registro/documentos``.
* :meth:`ClienteCentral.autorregistrar` envia el formulario de «Crear cuenta»,
  con ``POST /api/v1/registro/pacientes``.

No hay --ni debe haber-- un metodo generico que reenvie cualquier ruta: la
lista de lo que el navegador puede pedir esta escrita aqui y en ninguna otra
parte.

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
from typing import Generic, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

# Reutilizadas del cliente del nodo edge, que ya las declara publicamente para
# esta misma ruta y este mismo esquema. Escribirlas otra vez aqui crearia dos
# definiciones de una sola cosa, libres de separarse.
from app.edge.cliente import CABECERA_AUTORIZACION, ESQUEMA_BEARER, RUTA_IDENTIDAD
from app.models.enums import NombreRol

# Los contratos de lectura clinica de SCRUM-98, reutilizados tal cual. Validar
# cada fila con el mismo modelo con el que el servidor la serializo es lo que
# garantiza que este adaptador no invente, renombre ni pierda un campo: si el
# contrato cambiara, la validacion fallaria aqui en vez de producir un panel con
# huecos silenciosos.
from app.schemas.clinico import EmbarazoResumen, LecturaResumen, SesionResumen

# El contrato de los documentos del autorregistro (SCRUM-110), reutilizado por
# la misma razon: si el servidor cambiara su forma, fallaria aqui.
from app.schemas.registro import AutorregistroEntrada, DocumentoPublicado, DocumentosVigentes

registrador = logging.getLogger(__name__)

# Rutas que el nodo edge no usa --nunca inicia sesion ni sondea la salud-- y que
# por tanto no existen como constante compartida.
RUTA_TOKEN = "/api/v1/autenticacion/token"
RUTA_SALUD = "/health"

# Lectura clinica (SCRUM-98). Tres rutas, y ninguna mas: este adaptador no
# consulta nada que el servidor no publique.
RUTA_EMBARAZOS = "/api/v1/clinico/embarazos"


# Autorregistro (SCRUM-110). Publicas: no llevan token.
RUTA_REGISTRO_DOCUMENTOS = "/api/v1/registro/documentos"
RUTA_REGISTRO_PACIENTES = "/api/v1/registro/pacientes"


def ruta_de_sesiones(id_embarazo: int) -> str:
    """Las sesiones de un episodio.

    Pedirlas **dentro de** un ``id_embarazo`` es lo que mantiene los episodios
    separados. ``int()`` impide que un valor con otra forma acabe interpolado en
    una URL.
    """
    return f"{RUTA_EMBARAZOS}/{int(id_embarazo)}/sesiones"


def ruta_de_lecturas(id_sesion: int) -> str:
    """Las lecturas de una sesion."""
    return f"/api/v1/clinico/sesiones/{int(id_sesion)}/lecturas"


CODIGO_OK = 200
CODIGO_CREADO = 201
CODIGO_NO_AUTENTICADO = 401
CODIGO_PROHIBIDO = 403
CODIGO_NO_ENCONTRADO = 404
CODIGO_CONFLICTO = 409
CODIGO_NO_PROCESABLE = 422
CODIGO_DEMASIADAS_SOLICITUDES = 429

# Lo que se acepta de ``Retry-After``: segundos enteros, dentro de un margen
# razonable. Un valor fuera de el no se cree y se trata como ausente.
REINTENTO_MAXIMO_SEGUNDOS = 3600

# Los unicos nombres de campo que un 422 del registro puede devolver hacia el
# navegador. Cualquier otra cosa de ``loc`` se descarta: no se reenvia nada que
# este modulo no reconozca.
CAMPOS_DEL_REGISTRO = frozenset(AutorregistroEntrada.model_fields)

# Las excepciones de httpx que garantizan que la peticion **no salio**: no se
# pudo abrir la conexion o no habia una conexion libre en el pool. Ante
# cualquier otra --tiempo de lectura agotado, conexion cortada, protocolo roto--
# la peticion pudo llegar entera y el servidor pudo confirmarla.
FALLOS_ANTES_DE_ENVIAR = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class EstadoRegistro(enum.Enum):
    """Como termino un envio del formulario de «Crear cuenta».

    La diferencia que importa es entre ``SIN_CONEXION`` y ``DESCONOCIDO``. En el
    primero la peticion no salio, y se puede decir con verdad que no se envio. En
    el segundo salio y se perdio la respuesta: la cuenta pudo crearse, y nadie
    puede afirmar lo contrario.
    """

    CREADA = "CREADA"
    DUPLICADA = "DUPLICADA"  # 409: cedula o correo ya registrados, o carrera.
    DATOS_RECHAZADOS = "DATOS_RECHAZADOS"  # 422
    LIMITE = "LIMITE"  # 429
    SIN_CONEXION = "SIN_CONEXION"
    DESCONOCIDO = "DESCONOCIDO"
    # 500 u otra respuesta fuera del contrato. R1 revierte la transaccion entera
    # antes de un 500.
    ERROR_REMOTO = "ERROR_REMOTO"


@dataclass(frozen=True)
class RespuestaRegistro:
    """El desenlace de un registro. Sin ningun dato de la paciente."""

    estado: EstadoRegistro
    # Solo para ``DATOS_RECHAZADOS``: los nombres de campo que el servidor
    # rechazo, filtrados contra :data:`CAMPOS_DEL_REGISTRO`.
    campos: tuple[str, ...] = ()
    # Solo para ``LIMITE``: los segundos de ``Retry-After``, si eran validos.
    reintentar_en_segundos: int | None = None


@dataclass(frozen=True)
class RespuestaDocumentos:
    """Los textos vigentes, o por que no se pudieron leer."""

    estado: EstadoRespuesta
    documentos: tuple[DocumentoPublicado, ...] = ()


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
    # El recurso no esta al alcance de quien pregunta. SCRUM-98 responde 404 con
    # la misma frase para un episodio inexistente y para uno ajeno, a proposito,
    # y este adaptador conserva esa indistinguibilidad: no es un error de
    # credencial --el token sirve-- ni un fallo del servidor.
    NO_ENCONTRADO = "NO_ENCONTRADO"
    # 403: la identidad es valida y el servidor la reconoce, pero su rol no
    # puede hacer esta lectura. Separado de ``RECHAZADO`` a proposito: un 401
    # significa «vuelve a autenticarte» y un 403 significa «tu cuenta no puede
    # hacer esto», y tratarlos igual invitaria a reintentar credenciales que
    # funcionan perfectamente. En este portal solo entra PACIENTE, asi que un
    # 403 clinico es una incoherencia que no debe disfrazarse.
    PROHIBIDO = "PROHIBIDO"


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


# El tipo de fila que devuelve cada lectura clinica. Acotado a ``BaseModel``
# porque lo que viaja son siempre los contratos de SCRUM-98, nunca diccionarios
# sueltos: el modelo es la garantia de que los campos son los suyos.
Fila = TypeVar("Fila", bound=BaseModel)


@dataclass(frozen=True)
class RespuestaClinica(Generic[Fila]):
    """El resultado de una lectura clinica: como fue, y que trajo.

    ``datos`` solo tiene contenido cuando ``estado`` es ``OK``, y es una tupla
    --no una lista-- para que quien la reciba no pueda ampliarla creyendo que
    anade informacion del servidor.

    Una tupla vacia con ``estado`` ``OK`` es una **respuesta legitima y
    distinta de un fallo**: significa que el servidor contesto y que no hay
    filas. Una gestante sin embarazos registrados, o un episodio sin sesiones,
    caen ahi. Confundirla con ``SIN_CONEXION`` seria decirle a la paciente que
    no hay datos cuando lo que no hay es red.
    """

    estado: EstadoRespuesta
    datos: tuple[Fila, ...] = ()

    @property
    def disponible(self) -> bool:
        """Si el servidor contesto la pregunta, aunque la respuesta sea vacia."""
        return self.estado is EstadoRespuesta.OK


class ClienteCentral(Protocol):
    """Lo que el adaptador necesita del servidor central.

    Declarado como ``Protocol`` para que las pruebas puedan sustituirlo por un
    doble sin levantar la API ni PostgreSQL, y para que el adaptador dependa de
    estas tres operaciones y no de httpx.
    """

    def autenticar(self, *, email: str, password: str) -> RespuestaToken: ...

    def identidad(self, token: str) -> RespuestaIdentidad: ...

    def disponible(self) -> bool: ...

    # Lectura clinica. Tres metodos explicitos y no un proxy generico: un
    # reenviador que aceptara cualquier ruta convertiria este adaptador en una
    # puerta abierta a toda la API central, y la lista de lo que el navegador
    # puede pedir dejaria de estar escrita en ningun sitio.
    def embarazos(self, token: str) -> RespuestaClinica[EmbarazoResumen]: ...

    def sesiones(
        self, token: str, id_embarazo: int
    ) -> RespuestaClinica[SesionResumen]: ...

    def lecturas(
        self, token: str, id_sesion: int
    ) -> RespuestaClinica[LecturaResumen]: ...

    # Autorregistro (SCRUM-110), sin token.
    def documentos_registro(self) -> RespuestaDocumentos: ...

    def autorregistrar(self, cuerpo: dict) -> RespuestaRegistro: ...


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

        if respuesta.status_code == CODIGO_NO_AUTENTICADO:
            # Token vencido, invalido o cuenta desactivada: SCRUM-70 los hace
            # indistinguibles. Lo unico que sirve es volver a autenticarse.
            return RespuestaIdentidad(EstadoRespuesta.RECHAZADO)
        if respuesta.status_code == CODIGO_PROHIBIDO:
            # La identidad es valida y el servidor la reconoce, pero no puede
            # hacer esto. No es un token vencido: reautenticarse no lo cambia.
            return RespuestaIdentidad(EstadoRespuesta.PROHIBIDO)

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

    # -- Lectura clinica (SCRUM-98) --------------------------------------

    def embarazos(self, token: str) -> RespuestaClinica[EmbarazoResumen]:
        """Los episodios de embarazo al alcance de quien presenta el token.

        El servidor decide cuales son: aqui no se envia ningun ``id_paciente``
        ni ningun filtro, porque un filtro enviado por el cliente no es una
        prueba de autorizacion. El aislamiento lo aplican las politicas de
        SCRUM-98 sobre la identidad que el token identifica.
        """
        return self._leer_lista(token, RUTA_EMBARAZOS, EmbarazoResumen)

    def sesiones(self, token: str, id_embarazo: int) -> RespuestaClinica[SesionResumen]:
        """Las sesiones de un episodio, y de ese episodio solamente."""
        return self._leer_lista(token, ruta_de_sesiones(id_embarazo), SesionResumen)

    def lecturas(self, token: str, id_sesion: int) -> RespuestaClinica[LecturaResumen]:
        """Las lecturas de una sesion."""
        return self._leer_lista(token, ruta_de_lecturas(id_sesion), LecturaResumen)

    def _leer_lista(
        self, token: str, ruta: str, modelo: type[Fila]
    ) -> RespuestaClinica[Fila]:
        """Una lectura clinica cualquiera, clasificada igual que las demas.

        Los cuatro desenlaces que no son ``OK`` se distinguen porque significan
        cosas distintas para la interfaz:

        * ``SIN_CONEXION`` -- no se pudo preguntar. La interfaz muestra estado
          neutro y **no** cierra la sesion local.
        * ``RECHAZADO`` -- 401. El token ya no sirve; hace falta volver a
          autenticarse contra el servidor. Tampoco cierra la sesion local.
        * ``PROHIBIDO`` -- 403. La cuenta es valida y su rol no puede hacer esta
          lectura. **No** es lo mismo que un 401 y no se mezcla con el.
        * ``NO_ENCONTRADO`` -- 404. El episodio no existe o no es de quien
          pregunta, y SCRUM-98 no permite distinguirlo. La interfaz lo trata
          como «ese episodio no esta disponible», nunca como una lista vacia.
        * ``ERROR_REMOTO`` -- cualquier otra cosa, incluido un cuerpo que no
          cumple el contrato.

        Un cuerpo que no valida es ``ERROR_REMOTO`` y no una lista a medias: la
        alternativa seria pintar un panel con los campos que sobrevivieron, que
        es peor que decir que no se pudo leer.
        """
        try:
            respuesta = self._http.get(
                ruta,
                headers={CABECERA_AUTORIZACION: f"{ESQUEMA_BEARER} {token}"},
            )
        except httpx.HTTPError:
            registrador.warning("Lectura clinica: el servidor central no respondio.")
            return RespuestaClinica(EstadoRespuesta.SIN_CONEXION)

        if respuesta.status_code == CODIGO_NO_AUTENTICADO:
            return RespuestaClinica(EstadoRespuesta.RECHAZADO)

        if respuesta.status_code == CODIGO_PROHIBIDO:
            return RespuestaClinica(EstadoRespuesta.PROHIBIDO)

        if respuesta.status_code == CODIGO_NO_ENCONTRADO:
            return RespuestaClinica(EstadoRespuesta.NO_ENCONTRADO)

        if respuesta.status_code != CODIGO_OK:
            registrador.warning(
                "Lectura clinica: respuesta inesperada http=%s", respuesta.status_code
            )
            return RespuestaClinica(EstadoRespuesta.ERROR_REMOTO)

        try:
            cuerpo = respuesta.json()
        except ValueError:
            registrador.warning("Lectura clinica: el cuerpo no es JSON.")
            return RespuestaClinica(EstadoRespuesta.ERROR_REMOTO)

        if not isinstance(cuerpo, list):
            registrador.warning("Lectura clinica: el cuerpo no es una lista.")
            return RespuestaClinica(EstadoRespuesta.ERROR_REMOTO)

        try:
            filas = tuple(modelo.model_validate(fila) for fila in cuerpo)
        except ValidationError:
            # Sin el detalle de Pydantic: incluye el valor que no valido, y aqui
            # esos valores son la frecuencia cardiaca o la fecha de captura de
            # una paciente.
            registrador.warning(
                "Lectura clinica: una fila no cumple el contrato de %s.",
                modelo.__name__,
            )
            return RespuestaClinica(EstadoRespuesta.ERROR_REMOTO)

        return RespuestaClinica(EstadoRespuesta.OK, datos=filas)

    # -- Autorregistro (SCRUM-110) ---------------------------------------

    def documentos_registro(self) -> RespuestaDocumentos:
        """La politica y los terminos vigentes, exactamente como los sirve R1.

        Se validan con el modelo con el que el servidor los serializo. Un cuerpo
        que no valida no se entrega a medias: sin los dos textos completos no se
        puede ofrecer la aceptacion.
        """
        try:
            respuesta = self._http.get(RUTA_REGISTRO_DOCUMENTOS)
        except httpx.HTTPError:
            registrador.warning("Documentos del registro: el servidor central no respondio.")
            return RespuestaDocumentos(EstadoRespuesta.SIN_CONEXION)

        if respuesta.status_code != CODIGO_OK:
            registrador.warning(
                "Documentos del registro: respuesta inesperada http=%s",
                respuesta.status_code,
            )
            return RespuestaDocumentos(EstadoRespuesta.ERROR_REMOTO)

        try:
            vigentes = DocumentosVigentes.model_validate(_cuerpo_de(respuesta))
        except ValidationError:
            registrador.warning("Documentos del registro: el cuerpo no cumple el contrato.")
            return RespuestaDocumentos(EstadoRespuesta.ERROR_REMOTO)

        return RespuestaDocumentos(EstadoRespuesta.OK, documentos=tuple(vigentes.documentos))

    def autorregistrar(self, cuerpo: dict) -> RespuestaRegistro:
        """Envia el formulario una vez. **Nunca reintenta.**

        Un reintento automatico de un registro cuya respuesta se perdio podria
        crear la cuenta y, a continuacion, mostrar un 409 sobre la cuenta que
        acaba de crearse. Lo decide la persona, con la informacion correcta.

        ``cuerpo`` lleva la contrasena en claro. No se registra ni se adjunta a
        ninguna excepcion; los registros solo llevan el codigo HTTP.
        """
        try:
            respuesta = self._http.post(RUTA_REGISTRO_PACIENTES, json=cuerpo)
        except FALLOS_ANTES_DE_ENVIAR:
            registrador.warning("Registro: el servidor central no acepto la conexion.")
            return RespuestaRegistro(EstadoRegistro.SIN_CONEXION)
        except httpx.HTTPError:
            # La peticion pudo llegar entera. Sin el detalle de la excepcion: su
            # texto puede incluir la URL o el cuerpo.
            registrador.warning("Registro: se perdio la respuesta del servidor central.")
            return RespuestaRegistro(EstadoRegistro.DESCONOCIDO)

        codigo = respuesta.status_code
        if codigo == CODIGO_CREADO:
            # R1 responde 201 solo despues del commit. La cuenta existe aunque el
            # cuerpo no fuera legible, asi que no se mira.
            return RespuestaRegistro(EstadoRegistro.CREADA)
        if codigo == CODIGO_CONFLICTO:
            return RespuestaRegistro(EstadoRegistro.DUPLICADA)
        if codigo == CODIGO_NO_PROCESABLE:
            return RespuestaRegistro(
                EstadoRegistro.DATOS_RECHAZADOS, campos=_campos_rechazados(respuesta)
            )
        if codigo == CODIGO_DEMASIADAS_SOLICITUDES:
            return RespuestaRegistro(
                EstadoRegistro.LIMITE,
                reintentar_en_segundos=_segundos_de_reintento(respuesta),
            )

        registrador.warning("Registro: respuesta inesperada http=%s", codigo)
        return RespuestaRegistro(EstadoRegistro.ERROR_REMOTO)


def _campos_rechazados(respuesta: httpx.Response) -> tuple[str, ...]:
    """Los nombres de campo de un 422, y solo los que el formulario conoce.

    Del diagnostico se toma el ultimo elemento de ``loc`` y nada mas: ni el
    mensaje, ni el tipo, ni un eco del valor.
    """
    cuerpo = _cuerpo_de(respuesta)
    errores = cuerpo.get("detail") if cuerpo else None
    if not isinstance(errores, list):
        return ()
    campos: list[str] = []
    for error in errores:
        ubicacion = error.get("loc") if isinstance(error, dict) else None
        if isinstance(ubicacion, list) and ubicacion:
            campo = ubicacion[-1]
            if campo in CAMPOS_DEL_REGISTRO and campo not in campos:
                campos.append(campo)
    return tuple(campos)


def _segundos_de_reintento(respuesta: httpx.Response) -> int | None:
    """``Retry-After`` en segundos enteros, o ``None`` si no es creible."""
    valor = respuesta.headers.get("Retry-After", "")
    if not valor.isdigit():
        return None
    segundos = int(valor)
    return segundos if 0 < segundos <= REINTENTO_MAXIMO_SEGUNDOS else None


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
