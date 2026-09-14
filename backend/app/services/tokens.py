"""Issuing and validating the session token (SCRUM-70).

A JWT is **signed, not encrypted**: anybody holding one can read its payload.
That single fact decides what goes in -- an opaque internal identifier and two
timestamps -- and what never does: no name, no national id, no phone, no email,
no clinical value, and no secret.

**The role is not a claim.** It is read from PostgreSQL on every request by
``app.services.principal``. A claim the token does not carry is a claim nobody
can forge, and there is no second source of truth to disagree with the first.
A token that arrives with a ``rol`` claim injected into it changes nothing: this
module returns only the subject, so the extra claim is never read.

**The algorithm is fixed by this side of the wire.** ``algorithms=[ALGORITMO]``
is what makes PyJWT refuse ``alg=none`` and every algorithm this project did not
choose, because the list is ours and the header's ``alg`` only gets to match it.

**The clock is injected into issuance, not into validation.** A test that needs
an expired token asks for one stamped in the past and lets the library's own
expiry check reject it; re-implementing that check here so it could read an
injected clock would mean writing the very thing the library is here to do. No
test sleeps.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import jwt

from app.config import ConfiguracionJWT

# The only algorithm this project issues and the only one it accepts. HS256 is
# symmetric, and that is the right shape here: one service signs and the same
# service verifies, so there is no public key to distribute and no third party
# to verify on its own.
ALGORITMO = "HS256"

TIPO_DE_TOKEN = "bearer"

# Every claim that must be present before a token is trusted. PyJWT enforces the
# list, so a token missing any of them is rejected before a single value is read.
CLAIMS_REQUERIDOS = ("sub", "exp", "iat")

# ``sub`` is a **decimal ASCII string**, per RFC 7519, which says the claim is a
# string -- not an integer that happens to look like one. ``[0-9]`` is an ASCII
# range, deliberately: ``str.isdigit()`` accepts Unicode digits such as U+0661,
# and ``int()`` would then happily parse an identifier no database row has.
#
# The pattern also fixes the **domain**, not only the alphabet:
#
# * **canonical form** -- a leading ``[1-9]`` refuses ``0``, ``00`` and ``0137``.
#   ``emitir`` writes ``str(id_usuario)``, which never has leading zeros, so a
#   subject with them is a representation this application does not produce;
#   accepting it would give one account several valid spellings.
# * **at most ten digits** -- so ``int()`` below can neither hit Python's limit on
#   digit conversion (a few thousand digits raise ``ValueError``, which would be
#   a 500) nor spend effort on an absurd value.
#
# Ten digits still reach 9,999,999,999, so the range is checked after
# converting: :data:`ID_USUARIO_MAXIMO` is the ceiling of PostgreSQL ``INTEGER``,
# the type of ``operacional.usuario.id_usuario``. Anything above it could only
# fail later, inside the database, as a 500 instead of a 401.
_PATRON_SUB = re.compile(r"[1-9][0-9]{0,9}")
ID_USUARIO_MINIMO = 1
ID_USUARIO_MAXIMO = 2_147_483_647

# One message for every way a token can be wrong. A caller learns that its
# credential was not accepted and nothing else: which claim was missing, whether
# the signature or the expiry failed, or what the library called the problem are
# all facts about our verification that would help somebody probing it.
MENSAJE_TOKEN_INVALIDO = "Credenciales de sesion invalidas o expiradas."


class TokenInvalido(Exception):
    """The token cannot be trusted. The reason stays here; the caller gets a 401.

    Carries the fixed public message and never the exception PyJWT raised, the
    token, or any part of either.
    """

    def __init__(self) -> None:
        super().__init__(MENSAJE_TOKEN_INVALIDO)
        self.detalle = MENSAJE_TOKEN_INVALIDO


@dataclass(frozen=True)
class TokenDeSesion:
    """A freshly issued token and the lifetime the client should assume."""

    access_token: str
    expira_en_segundos: int


def ahora_utc() -> datetime:
    """The current instant, timezone-aware and in UTC. Never ``utcnow()``."""
    return datetime.now(timezone.utc)


def emitir(
    id_usuario: int,
    configuracion: ConfiguracionJWT,
    *,
    reloj: Callable[[], datetime] = ahora_utc,
) -> TokenDeSesion:
    """Sign a token for ``id_usuario``, valid for the configured lifetime.

    ``sub`` is written as ``str(id_usuario)`` because the standard says the
    subject is a string; PyJWT refuses to encode a numeric one, and this project
    would not want it to anyway.
    """
    emitido = reloj()
    expira = emitido + configuracion.expiracion

    token = jwt.encode(
        {"sub": str(id_usuario), "iat": emitido, "exp": expira},
        configuracion.secreto,
        algorithm=ALGORITMO,
    )
    return TokenDeSesion(
        access_token=token,
        expira_en_segundos=int(configuracion.expiracion.total_seconds()),
    )


def validar(token: str, configuracion: ConfiguracionJWT) -> int:
    """The user id this token speaks for, or :class:`TokenInvalido`.

    The order is the design: PyJWT verifies the signature, the algorithm, the
    expiry and the presence of the required claims **before** this function looks
    at a single value, so nothing below is ever reached by an unverified token.
    What is left here is the one check the library cannot make for us -- that the
    subject is the shape this application writes.
    """
    try:
        contenido = jwt.decode(
            token,
            configuracion.secreto,
            algorithms=[ALGORITMO],
            options={"require": list(CLAIMS_REQUERIDOS)},
        )
    except jwt.PyJWTError as error:
        # The library's message is never published and never logged: it names
        # the claim or the failure mode, which is a fact about our verification.
        raise TokenInvalido from error

    sujeto = contenido.get("sub")
    if not isinstance(sujeto, str) or _PATRON_SUB.fullmatch(sujeto) is None:
        raise TokenInvalido

    # At most ten ASCII digits with no leading zero: this conversion cannot raise.
    id_usuario = int(sujeto)
    if not ID_USUARIO_MINIMO <= id_usuario <= ID_USUARIO_MAXIMO:
        raise TokenInvalido

    return id_usuario
