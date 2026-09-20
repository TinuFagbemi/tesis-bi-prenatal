"""La sesion local de la interfaz de la gestante (SCRUM-72).

Resuelve un problema concreto: que una paciente en una zona con conectividad
intermitente no tenga que escribir su correo y su contrasena cada vez que abre
la interfaz, **sin** guardar esa contrasena en ningun sitio y **sin** tocar la
autenticacion del servidor central.

**Dos relojes, y no deben confundirse.**

* El del **servidor central** gobierna su token. Lo fija SCRUM-70, dura treinta
  minutos por omision, tiene un techo de veinticuatro horas y no existen tokens
  de refresco. Este modulo no lo emite, no lo renueva y no lo prolonga.
* El **local** gobierna la ventana de uso de este dispositivo. Empieza en la
  ultima validacion **en linea** que salio bien y dura lo que diga la
  configuracion.

Que el segundo sea mas largo que el primero no es una contradiccion: no
autorizan lo mismo. La ventana local autoriza a abrir la interfaz y usar lo que
vive en el dispositivo. Cualquier operacion que necesite al servidor central
necesita una credencial valida contra el servidor central, y cuando no la hay,
se vuelve a pedir correo y contrasena. Nada de lo que hay aqui permite acceder a
datos centrales sin una credencial central.

**Una reapertura sin conexion consume la ventana; no la alarga.** Solo
:func:`renovar`, que el adaptador llama despues de una revalidacion en linea
correcta, mueve ``expira_en``. Abrir la interfaz cien veces sin internet deja la
fecha de expiracion exactamente donde estaba.

**La sesion pertenece a la identidad que se valido.** ``id_usuario`` se escribe
al abrirla y no se modifica nunca: no hay operacion en este modulo que cambie el
titular de una sesion, asi que no es posible pasar de una paciente a otra sin
volver a autenticarse.

**Lo que viaja y lo que se guarda son cosas distintas.** El identificador es un
valor aleatorio que existe una sola vez, se entrega al navegador en la cookie y
no se vuelve a conocer; en disco queda unicamente su SHA-256. Buscar una sesion
es buscar un digest, asi que no hay comparacion de secretos en este codigo.

El reloj se recibe como argumento en todas las operaciones, de modo que una
prueba pueda recorrer tres dias sin esperarlos.

Todos los datos que maneja esta interfaz son ficticios y simulados.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.gestante.almacen import ROL_PERMITIDO, TABLA_SESION, transaccion
from app.models.enums import NombreRol

# Bytes de aleatoriedad del identificador de sesion. ``token_urlsafe`` los
# codifica en base64 seguro para URL, asi que el texto es mas largo que esto.
# 32 bytes son 256 bits: un identificador no adivinable por fuerza bruta.
BYTES_DE_IDENTIFICADOR = 32


class ErrorDeSesion(Exception):
    """Base de los fallos que este modulo reporta con un mensaje propio."""

    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


class RolNoAutorizado(ErrorDeSesion):
    """Se intento abrir una sesion local para un rol que no es PACIENTE."""


@dataclass(frozen=True)
class SesionLocal:
    """Una sesion local vigente. Nunca lleva el identificador ni el token.

    Es lo que las rutas reciben tras validar una cookie, y contiene lo minimo
    para decidir: de quien es la sesion, con que rol, desde cuando esta
    validada y hasta cuando vale.
    """

    id_usuario: int
    rol: NombreRol
    validada_en: datetime
    expira_en: datetime

    def segundos_restantes(self, ahora: datetime) -> int:
        """Lo que le queda de vigencia, nunca negativo.

        Es lo que se usa para el ``Max-Age`` de la cookie, de modo que la cookie
        y la fila de SQLite caduquen por el mismo calculo y no puedan discrepar.
        """
        restante = int((self.expira_en - ahora).total_seconds())
        return max(0, restante)


def ahora_utc() -> datetime:
    """Instante actual en UTC. Inyectable donde una prueba necesite determinismo."""
    return datetime.now(timezone.utc)


def _texto(momento: datetime) -> str:
    """Instante como texto, siempre en UTC.

    Misma convencion que ``app.edge.outbox``: un formato y un desfase, siempre
    ``+00:00``, de modo que comparar dos columnas como texto sea compararlas en
    el tiempo.
    """
    return momento.astimezone(timezone.utc).isoformat()


def _instante(texto: str) -> datetime:
    """Vuelve a leer uno de nuestros propios instantes."""
    return datetime.fromisoformat(texto)


def generar_identificador() -> str:
    """Un identificador de sesion nuevo. Aleatorio, opaco y sin significado.

    No codifica el usuario, ni el rol, ni la expiracion, ni nada que pudiera
    leerse desde el navegador. Todo eso vive en la fila que el digest localiza.
    """
    return secrets.token_urlsafe(BYTES_DE_IDENTIFICADOR)


def digest(identificador: str) -> str:
    """SHA-256 del identificador, que es lo unico que se guarda."""
    return hashlib.sha256(identificador.encode("utf-8")).hexdigest()


def _sesion_de(fila: sqlite3.Row) -> SesionLocal:
    return SesionLocal(
        id_usuario=int(fila["id_usuario"]),
        rol=NombreRol(fila["rol"]),
        validada_en=_instante(fila["validada_en"]),
        expira_en=_instante(fila["expira_en"]),
    )


def abrir(
    conexion: sqlite3.Connection,
    *,
    id_usuario: int,
    rol: NombreRol,
    ventana_segundos: int,
    ahora: datetime,
) -> tuple[str, SesionLocal]:
    """Abre una sesion local y devuelve su identificador **una sola vez**.

    El identificador que sale de aqui es el unico momento en que ese valor
    existe fuera de la cookie: a partir de esta llamada el sistema solo conoce
    su digest.

    Se llama exclusivamente **despues** de una autenticacion en linea correcta
    en la que el servidor central confirmo la identidad y el rol. Este modulo no
    autentica a nadie; materializa una autorizacion que ya se concedio.

    Un rol que no sea PACIENTE se rechaza aqui, antes de tocar la base, y la
    base lo rechazaria igualmente por su ``CHECK``. Las dos comprobaciones son
    defensa en profundidad: la autoridad sigue siendo el servidor central.
    """
    if rol is not ROL_PERMITIDO:
        raise RolNoAutorizado(
            "Esta interfaz es exclusiva de las cuentas de pacientes."
        )

    identificador = generar_identificador()
    expira_en = ahora + timedelta(seconds=ventana_segundos)

    with transaccion(conexion):
        conexion.execute(
            f"INSERT INTO {TABLA_SESION}"
            " (hash_sesion, id_usuario, rol, validada_en, expira_en, cerrada_en)"
            " VALUES (?, ?, ?, ?, ?, NULL)",
            (
                digest(identificador),
                int(id_usuario),
                rol.value,
                _texto(ahora),
                _texto(expira_en),
            ),
        )

    return identificador, SesionLocal(
        id_usuario=int(id_usuario),
        rol=rol,
        validada_en=ahora,
        expira_en=expira_en,
    )


def validar(
    conexion: sqlite3.Connection, identificador: str, *, ahora: datetime
) -> SesionLocal | None:
    """La sesion que ese identificador nombra, si sigue valiendo.

    Devuelve ``None`` en cuatro situaciones que el llamador **no** debe
    distinguir: el identificador no corresponde a ninguna sesion, la sesion se
    cerro, la ventana vencio, o el valor es vacio. Las cuatro significan lo
    mismo para quien pregunta --esta cookie ya no autoriza nada-- y separarlas
    solo serviria para que alguien aprendiera cual de ellas provoco.

    **Es una lectura y no modifica nada.** En particular, no mueve
    ``expira_en``: validar una sesion consume la ventana, no la renueva. Esa es
    la razon de que abrir la interfaz sin conexion, una vez o cien, no alargue
    la autorizacion.
    """
    if not identificador:
        return None

    fila = conexion.execute(
        f"SELECT id_usuario, rol, validada_en, expira_en FROM {TABLA_SESION}"
        " WHERE hash_sesion = ? AND cerrada_en IS NULL AND expira_en > ?",
        (digest(identificador), _texto(ahora)),
    ).fetchone()

    return None if fila is None else _sesion_de(fila)


def renovar(
    conexion: sqlite3.Connection,
    identificador: str,
    *,
    ventana_segundos: int,
    ahora: datetime,
) -> SesionLocal | None:
    """Ancla la ventana en este instante. Solo tras validar en linea.

    Es la unica operacion que mueve ``expira_en`` hacia adelante, y el adaptador
    la invoca en un solo caso: el servidor central acaba de confirmar que la
    cuenta sigue existiendo, sigue activa y sigue siendo PACIENTE.

    La condicion del ``UPDATE`` repite las guardas de :func:`validar`, asi que
    una sesion ya cerrada o ya vencida no puede resucitarse por esta via: si no
    valia un instante antes, esta llamada no la revive.
    """
    expira_en = ahora + timedelta(seconds=ventana_segundos)

    with transaccion(conexion):
        cursor = conexion.execute(
            f"UPDATE {TABLA_SESION} SET validada_en = ?, expira_en = ?"
            " WHERE hash_sesion = ? AND cerrada_en IS NULL AND expira_en > ?",
            (
                _texto(ahora),
                _texto(expira_en),
                digest(identificador),
                _texto(ahora),
            ),
        )
        renovada = cursor.rowcount == 1

    return validar(conexion, identificador, ahora=ahora) if renovada else None


def cerrar(
    conexion: sqlite3.Connection, identificador: str, *, ahora: datetime
) -> bool:
    """Cierra la sesion en el servidor. Devuelve si habia algo que cerrar.

    Esta es la invalidacion de verdad, y por eso es lo primero que hace el
    cierre de sesion. Borrar la cookie del navegador es cosmetico --una copia
    tomada antes seguiria existiendo--; escribir ``cerrada_en`` hace que esa
    copia deje de servir, porque :func:`validar` exige ``cerrada_en IS NULL``.

    Es idempotente: cerrar dos veces la misma sesion deja el primer instante y
    devuelve ``False`` la segunda vez.
    """
    if not identificador:
        return False

    with transaccion(conexion):
        cursor = conexion.execute(
            f"UPDATE {TABLA_SESION} SET cerrada_en = ?"
            " WHERE hash_sesion = ? AND cerrada_en IS NULL",
            (_texto(ahora), digest(identificador)),
        )
        return cursor.rowcount == 1
