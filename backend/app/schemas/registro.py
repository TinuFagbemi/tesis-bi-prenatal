"""HTTP contract of the patient self-registration (SCRUM-110).

**What the body carries, and what it deliberately cannot.** The fields of the
patient profile, one email for both the profile and the access, one mobile
phone, the password and its confirmation, and the two acceptances with the
version of each text. There is no ``rol``, no ``activo``, no ``id_paciente``, no
``id_usuario``, no clinic, pregnancy or device: ``extra="forbid"`` answers any of
them with 422 instead of ignoring it. The server fixes the role (PACIENTE) and
the state (active); a client cannot choose them and cannot point the new account
at an existing profile.

**Every limit comes from the model, none is invented.** Lengths are read from
the columns -- ``operacional.paciente``, ``telefono_paciente`` and
``usuario`` -- and the email is :data:`EmailDeAcceso`, the type the login uses,
so the address is stored under the rule it will be compared with. On top of the
columns there are exactly three rules, each one forced by something real:

* a required text made only of spaces is refused, and surrounding spaces are
  removed: ``NOT NULL`` accepts ``'   '``, and a name nobody can read is not a
  name;
* an optional text left empty is stored as NULL, which is how the model says
  «not given»;
* the birth date must be a real date and not in the future, measured on the
  clinical day of Panama -- the same day the ETL uses --, because a future birth
  date cannot exist.

No age range, no national-id format and no password policy are added: none was
approved, and SCRUM-70 explicitly approved none for passwords. The password is
**never** trimmed or transformed: what she types is what is hashed.

**The two acceptances are checked here and again in the router.** Here, that
each is the JSON boolean ``true`` and that each version is the one in force; in
the router, the stored version and digest come from :mod:`app.legal`, never from
the request.

All data handled by this prototype is fictitious and simulated.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    StrictBool,
    StrictStr,
    field_validator,
    model_validator,
)

from app.etl.reglas import ZONA_HORARIA_CLINICA
from app.legal import VERSION_POLITICA_PRIVACIDAD, VERSION_TERMINOS_CONDICIONES
from app.models.clinico import Paciente, TelefonoPaciente
from app.models.enums import DocumentoLegal, NombreRol
from app.schemas.autenticacion import LONGITUD_MAXIMA_PASSWORD, EmailDeAcceso


def _longitud(modelo, columna: str) -> int:
    return modelo.__table__.c[columna].type.length


LONGITUD_CEDULA = _longitud(Paciente, "cedula")
LONGITUD_NOMBRE = _longitud(Paciente, "primer_nombre")
LONGITUD_CELULAR = _longitud(TelefonoPaciente, "valor_contacto")

# Fixed messages. None quotes the value received: a 422 is echoed to the caller
# and may end up in a log or a screenshot.
MENSAJE_OBLIGATORIO_VACIO = "Este campo es obligatorio y no puede quedar vacío."
MENSAJE_FECHA_FUTURA = "La fecha de nacimiento no puede ser posterior a hoy."
MENSAJE_FECHA_FORMATO = "La fecha de nacimiento debe escribirse como AAAA-MM-DD."
MENSAJE_PASSWORDS_DISTINTAS = "La contraseña y su confirmación no coinciden."
MENSAJE_ACEPTACION_REQUERIDA = "Hay que aceptar este documento para crear la cuenta."
MENSAJE_VERSION_NO_VIGENTE = (
    "La versión del documento aceptado no es la vigente. Vuelve a abrir el "
    "documento y acéptalo de nuevo."
)
MENSAJE_EMAIL_FORMATO = (
    "El correo de acceso debe tener el formato pacienteNN@example.com: "
    "«paciente», al menos dos dígitos y el dominio example.com. Por ejemplo, "
    "paciente31@example.com."
)

# The access email the clinic staff hands out: ``paciente`` + at least two
# digits + exactly ``example.com``, the same shape as the simulated dataset's
# accounts. Checked on the canonical form (stripped and lowercased by
# ``EmailDeAcceso``), so it applies to what is stored. Only self-registration
# enforces it: login and the accounts that already exist keep their own rules.
PATRON_EMAIL_REGISTRO = re.compile(r"paciente[0-9]{2,}@example\.com")


def _sin_espacios_exteriores(valor: Any) -> Any:
    """Strip a string; anything else is left for Pydantic to refuse."""
    return valor.strip() if isinstance(valor, str) else valor


def _vacio_es_nulo(valor: Any) -> Any:
    """An optional text: stripped, and empty means NULL."""
    if isinstance(valor, str):
        valor = valor.strip()
        return valor or None
    return valor


def _texto_obligatorio(longitud: int):
    """A required string: stripped first, so ``'   '`` arrives empty and fails."""
    return Annotated[
        StrictStr,
        BeforeValidator(_sin_espacios_exteriores),
        Field(min_length=1, max_length=longitud),
    ]


def _texto_opcional(longitud: int):
    # The bound sits on the string branch: applied to the union it would try to
    # measure a NULL.
    return Annotated[
        Annotated[StrictStr, Field(min_length=1, max_length=longitud)] | None,
        BeforeValidator(_vacio_es_nulo),
    ]


_PATRON_FECHA_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _solo_fecha_iso(valor: Any) -> Any:
    """Only ``AAAA-MM-DD`` reaches the date parser.

    Pydantic's lax mode would otherwise accept a Unix timestamp or a full
    datetime and quietly read a date out of it. A birth date is a calendar day,
    written as one; the parser still decides whether that day exists.
    """
    if not isinstance(valor, str) or not _PATRON_FECHA_ISO.fullmatch(valor):
        raise ValueError(MENSAJE_FECHA_FORMATO)
    return valor


def hoy_clinico() -> date:
    """Today in Panama. A function, so the boundary can be fixed in a test."""
    return datetime.now(ZONA_HORARIA_CLINICA).date()


class AutorregistroEntrada(BaseModel):
    """A patient registering herself. Every field is fictitious in this prototype."""

    model_config = ConfigDict(extra="forbid")

    primer_nombre: _texto_obligatorio(LONGITUD_NOMBRE)
    segundo_nombre: _texto_opcional(LONGITUD_NOMBRE) = None
    apellido_paterno: _texto_obligatorio(LONGITUD_NOMBRE)
    apellido_materno: _texto_opcional(LONGITUD_NOMBRE) = None
    cedula: _texto_obligatorio(LONGITUD_CEDULA)
    fecha_nac: Annotated[date, BeforeValidator(_solo_fecha_iso)] = Field(
        description="Fecha de calendario, AAAA-MM-DD."
    )
    email: EmailDeAcceso = Field(
        description=(
            "Correo del perfil y de acceso, el mismo, asignado por el personal "
            "de salud con el formato pacienteNN@example.com. Se guarda en forma "
            "canónica: sin espacios exteriores y en minúsculas."
        )
    )
    celular: _texto_obligatorio(LONGITUD_CELULAR)
    password: SecretStr = Field(min_length=1, max_length=LONGITUD_MAXIMA_PASSWORD)
    confirmacion_password: SecretStr = Field(
        min_length=1, max_length=LONGITUD_MAXIMA_PASSWORD
    )
    acepta_politica_privacidad: StrictBool
    version_politica_privacidad: StrictStr
    acepta_terminos_condiciones: StrictBool
    version_terminos_condiciones: StrictStr

    @field_validator("email")
    @classmethod
    def _correo_asignado(cls, valor: str) -> str:
        if PATRON_EMAIL_REGISTRO.fullmatch(valor) is None:
            raise ValueError(MENSAJE_EMAIL_FORMATO)
        return valor

    @field_validator("fecha_nac")
    @classmethod
    def _no_futura(cls, valor: date) -> date:
        if valor > hoy_clinico():
            raise ValueError(MENSAJE_FECHA_FUTURA)
        return valor

    @field_validator("acepta_politica_privacidad", "acepta_terminos_condiciones")
    @classmethod
    def _aceptada(cls, valor: bool) -> bool:
        if valor is not True:
            raise ValueError(MENSAJE_ACEPTACION_REQUERIDA)
        return valor

    @field_validator("version_politica_privacidad")
    @classmethod
    def _privacidad_vigente(cls, valor: str) -> str:
        if valor != VERSION_POLITICA_PRIVACIDAD:
            raise ValueError(MENSAJE_VERSION_NO_VIGENTE)
        return valor

    @field_validator("version_terminos_condiciones")
    @classmethod
    def _terminos_vigentes(cls, valor: str) -> str:
        if valor != VERSION_TERMINOS_CONDICIONES:
            raise ValueError(MENSAJE_VERSION_NO_VIGENTE)
        return valor

    @model_validator(mode="after")
    def _confirmacion_igual(self) -> "AutorregistroEntrada":
        # Compared exactly, character by character: no trimming, no case folding.
        if self.password.get_secret_value() != self.confirmacion_password.get_secret_value():
            raise ValueError(MENSAJE_PASSWORDS_DISTINTAS)
        return self


class CuentaAutorregistrada(BaseModel):
    """The 201: identifiers, role and state. No email, name, id, phone or digest."""

    id_usuario: int = Field(description="Identificador interno de la cuenta creada.")
    id_paciente: int = Field(description="Perfil de paciente creado y vinculado.")
    rol: NombreRol = Field(description="Siempre PACIENTE.")
    activo: bool = Field(description="Siempre verdadero: la cuenta puede iniciar sesión ya.")


class DocumentoPublicado(BaseModel):
    """A legal text in force, exactly as the server hashes and records it."""

    documento: DocumentoLegal
    version: str
    titulo: str
    texto: str
    sha256: str


class DocumentosVigentes(BaseModel):
    documentos: list[DocumentoPublicado]
