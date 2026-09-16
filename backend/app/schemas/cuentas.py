"""HTTP contract of account provisioning and life cycle (SCRUM-97).

Four messages, and what each one deliberately does **not** carry:

* :class:`ProvisionEntrada` -- the body of both provisioning routes. An email
  and a password, and nothing else. There is no ``rol``: the role comes from the
  route, so a body cannot ask for ADMIN, or for anything. There is no ``activo``,
  no ``id_usuario``, no ``password_hash`` and no clinical field either:
  ``extra="forbid"`` answers any of them with 422 instead of ignoring it.
* :class:`CuentaPacienteProvisionada` / :class:`CuentaMedicoProvisionada` -- the
  201. Internal identifiers, the role and the state. No email, no password, no
  digest, no token, no name, national id, phone, clinic or pregnancy.
* :class:`EstadoCuentaEntrada` -- ``{"activo": false}`` or ``true``. A JSON
  boolean only: ``"false"``, ``0`` or ``"no"`` are 422, because a coerced string
  is exactly how a deactivation silently becomes a no-op.
* :class:`EstadoCuentaSalida` -- identifier, role and the resulting state.

The email is :data:`app.schemas.autenticacion.EmailDeAcceso`, the very type the
login uses, so an address is stored under the same canonical rule it will be
compared with. The password reuses the login bounds: at least one character, at
most 512, carried as ``SecretStr``. SCRUM-70 approved no complexity policy and
SCRUM-97 does not invent one.

All accounts and profiles are fictitious and simulated.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.models.enums import NombreRol
from app.schemas.autenticacion import LONGITUD_MAXIMA_PASSWORD, EmailDeAcceso


class ProvisionEntrada(BaseModel):
    """Credentials for a new account bound to an existing clinical profile."""

    model_config = ConfigDict(extra="forbid")

    email: EmailDeAcceso = Field(
        description=(
            "Correo de acceso de la cuenta simulada. Se guarda en forma canónica: "
            "sin espacios exteriores y en minúsculas."
        ),
    )
    password: SecretStr = Field(
        min_length=1,
        max_length=LONGITUD_MAXIMA_PASSWORD,
        description=(
            "Contraseña inicial de la cuenta simulada. Solo se guarda su digest "
            "Argon2id; no se registra en ningún log ni se devuelve."
        ),
    )


class CuentaPacienteProvisionada(BaseModel):
    """A PACIENTE account just created and linked to its patient profile."""

    id_usuario: int = Field(description="Identificador interno de la cuenta creada.")
    id_paciente: int = Field(description="Perfil de paciente al que quedó vinculada.")
    rol: NombreRol = Field(description="Siempre PACIENTE en esta ruta.")
    activo: bool = Field(description="Una cuenta nueva se crea activa.")


class CuentaMedicoProvisionada(BaseModel):
    """A MEDICO account just created and linked to its physician profile."""

    id_usuario: int = Field(description="Identificador interno de la cuenta creada.")
    id_medico: int = Field(description="Perfil de médico al que quedó vinculada.")
    rol: NombreRol = Field(description="Siempre MEDICO en esta ruta.")
    activo: bool = Field(description="Una cuenta nueva se crea activa.")


class EstadoCuentaEntrada(BaseModel):
    """The state an administrator wants an account to be in."""

    model_config = ConfigDict(extra="forbid")

    activo: bool = Field(
        strict=True,
        description="false desactiva la cuenta; true la reactiva. Solo booleanos JSON.",
    )


class EstadoCuentaSalida(BaseModel):
    """The state the account is in once the request finished."""

    id_usuario: int = Field(description="Identificador interno de la cuenta.")
    rol: NombreRol = Field(description="Rol vigente de la cuenta.")
    activo: bool = Field(description="Estado resultante.")
