"""HTTP contract of the authentication endpoints (SCRUM-70).

Three messages, and what each one deliberately does **not** carry:

* :class:`CredencialesEntrada` -- the login body. The password arrives as a
  ``SecretStr``, so a validation error, a logged model or a debugger repr shows
  ``SecretStr('**********')`` instead of the value.
* :class:`TokenEmitido` -- the successful answer. A token, its type and its
  lifetime. No identifier, no role, no email, no digest, no refresh token: the
  client learns nothing about the account it just authenticated as.
* :class:`IdentidadActual` -- the answer of ``/yo``. Exactly two technical
  fields. No name, no national id, no contact, no clinical profile and no list
  of patients or clinics.

The login identifier is the account's email because that is the only unique,
non-synthetic column ``operacional.usuario`` has. Its bound is read from the
column itself rather than written down again, so a schema change cannot leave
the contract describing a width the database no longer has.

``EmailStr`` is **not** used, and that is a security decision rather than a
dependency one: a malformed address would then be answered 422 while a
well-formed unknown one is answered 401, and the difference would tell a caller
which addresses are worth trying. A bounded string keeps every bad credential
on the same path.

**The identifier is canonicalised before anything else (SCRUM-97).**
:data:`EmailDeAcceso` runs ``app.services.correo.canonizar_email`` -- strip,
lowercase, refuse empty or internal whitespace, then the length -- so
``Paciente31@Example.com`` logs in as the account stored as
``paciente31@example.com``. The same type is the email of the provisioning body,
so an address is accepted at the door under exactly the rule it was stored
under. The few values refused here are refused for their shape alone, the same
way for every caller; nothing about which accounts exist changes the answer.

All accounts are fictitious and simulated.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SecretStr

from app.models.enums import NombreRol
from app.services.correo import LONGITUD_MAXIMA_EMAIL, canonizar_email

# A password is not stored, so nothing constrains its length from the database
# side. The ceiling exists only so an unbounded body cannot force an Argon2id
# hash over a megabyte of input.
LONGITUD_MAXIMA_PASSWORD = 512


def _canonizar_si_es_texto(valor: Any) -> Any:
    """Canonical form of a string; anything else is left for Pydantic to refuse."""
    return canonizar_email(valor) if isinstance(valor, str) else valor


# The access email of every contract that carries one. ``BeforeValidator`` runs
# before the length bounds, so they are measured on the canonical value; the
# bounds stay declared so OpenAPI still publishes them.
EmailDeAcceso = Annotated[
    str,
    BeforeValidator(_canonizar_si_es_texto),
    Field(min_length=1, max_length=LONGITUD_MAXIMA_EMAIL),
]


class CredencialesEntrada(BaseModel):
    """The credentials of one login attempt."""

    model_config = ConfigDict(extra="forbid")

    email: EmailDeAcceso = Field(
        description=(
            "Identificador de la cuenta simulada. Se compara en forma canónica: "
            "sin espacios exteriores y en minúsculas."
        ),
    )
    password: SecretStr = Field(
        min_length=1,
        max_length=LONGITUD_MAXIMA_PASSWORD,
        description="Contraseña de la cuenta simulada. No se registra en ningún log.",
    )


class TokenEmitido(BaseModel):
    """A freshly issued session credential, and nothing else."""

    access_token: str = Field(description="Token de sesión que se envía como 'Bearer'.")
    token_type: Literal["bearer"] = Field(
        default="bearer", description="Esquema de autorización: siempre 'bearer'."
    )
    expires_in: int = Field(
        description="Segundos de validez del token desde su emisión."
    )


class IdentidadActual(BaseModel):
    """Technical identity of the caller. Two fields, by design.

    This endpoint answers "who am I and what role do I have", which is what the
    edge node's preflight needs and what makes the role check observable. It is
    **not** evidence of differentiated business permissions, and it never grows
    a clinical field.
    """

    id_usuario: int = Field(description="Identificador interno de la cuenta.")
    rol: NombreRol = Field(description="Rol vigente en PostgreSQL: ADMIN, MEDICO o PACIENTE.")
