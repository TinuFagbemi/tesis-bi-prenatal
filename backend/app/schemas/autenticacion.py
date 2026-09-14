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

All accounts are fictitious and simulated.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.models.enums import NombreRol
from app.models.seguridad import Usuario

# Read from the column, never restated. ``operacional.usuario.email`` is the
# authority on how long an identifier may be.
LONGITUD_MAXIMA_EMAIL = Usuario.__table__.c.email.type.length

# A password is not stored, so nothing constrains its length from the database
# side. The ceiling exists only so an unbounded body cannot force an Argon2id
# hash over a megabyte of input.
LONGITUD_MAXIMA_PASSWORD = 512


class CredencialesEntrada(BaseModel):
    """The credentials of one login attempt."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(
        min_length=1,
        max_length=LONGITUD_MAXIMA_EMAIL,
        description="Identificador de la cuenta simulada.",
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
