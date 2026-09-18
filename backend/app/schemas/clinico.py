"""What a clinical read answers, and deliberately nothing more (SCRUM-98).

These three models are the contract of the read routes, and every one of them is
shorter than the table behind it. That is the design, not an oversight: a
response schema is the last place where data leaves the system, so what it omits
is as much a security decision as what the policies filter.

**What is left out, and why.**

* ``embarazo.id_paciente`` and ``embarazo.id_clinica``. A patient already knows
  whose pregnancy it is; a physician is answering about a pregnancy, not about a
  person. An internal clinical id is a persistent, linkable identifier -- it is
  not anonymous just because it is a number -- so it travels only when something
  cannot be answered without it.
* ``embarazo.numero_gestas`` and ``numero_partos``. Obstetric history is exactly
  the kind of detail a listing does not need: these routes enumerate episodes and
  their readings, and neither question requires it.
* ``sesion_monitoreo.id_dispositivo`` and ``origen_dato``. Which device wrote a
  session and through which channel are operational facts about the deployment,
  not clinical information about the pregnancy.
* ``lectura_biometrica.fecha_hora_sincronizacion``. The capture instant is the
  clinical one; when the edge node managed to deliver it says something about the
  network, not about the patient.
* ``sesion_monitoreo.id_embarazo``. The caller named it in the path to get here,
  so echoing it back adds nothing and invites reading the field instead of
  trusting the route.

**Catalogue values, not catalogue keys.** A reading used to carry
``id_semaforo``, which is a surrogate key of ``operacional.semaforo`` and means
nothing outside this database: a client would have had to fetch the catalogue --
or hard-code the mapping -- to know whether 100 was a warning. It now carries
``codigo_semaforo`` and ``semana_gestacion``, the two values that make the series
readable, resolved by an explicit join. Sending a key and expecting the reader to
resolve it is how an internal identifier ends up copied into somebody else's
system.

**Identifiers that do travel.** ``id_embarazo`` in the episode listing and
``id_sesion`` in the session listing, because those are what the caller needs to
ask the next question. ``id_lectura`` identifies a row inside a series the caller
is already entitled to read. Every one of them is a handle for a further request,
never a fact about a person.

All data in this project is simulated and completely fictitious.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class EmbarazoResumen(BaseModel):
    """One pregnancy episode. Episodes are never merged, not even for a patient.

    A patient may have had several, and a finished one stays readable by her --
    that is the approved scope. Each is a separate row here, with its own dates
    and its own state, so that «her history» never becomes one undifferentiated
    series of readings.
    """

    model_config = ConfigDict(from_attributes=True)

    id_embarazo: int = Field(description="Identificador del episodio.")
    fecha_inicio: date = Field(description="Inicio del embarazo.")
    fecha_probable_parto: date | None = Field(
        default=None, description="Fecha probable de parto, si se registro."
    )
    estado_embarazo: str = Field(description="Estado del episodio.")
    fecha_cierre: date | None = Field(
        default=None,
        description="Fecha de cierre del episodio, o null si sigue en curso.",
    )


class SesionResumen(BaseModel):
    """One monitoring session of one pregnancy."""

    model_config = ConfigDict(from_attributes=True)

    id_sesion: int
    tipo_sesion: str
    estado_sesion: str
    fecha_inicio: datetime
    fecha_fin: datetime | None = None


class LecturaResumen(BaseModel):
    """One biometric reading of one session.

    ``hr_valor`` and ``spo2_valor`` travel together and exclude ``mov_valor``, or
    the other way round: the model that produced them admits one shape or the
    other, never a mixture, and a metric that does not apply is ``null`` and not
    zero.
    """

    model_config = ConfigDict(from_attributes=True)

    id_lectura: int
    fecha_hora_captura: datetime
    codigo_semaforo: str = Field(
        description=(
            "Clasificacion de riesgo de la lectura, por su codigo de catalogo "
            "-- no por la clave tecnica de la tabla."
        )
    )
    semana_gestacion: int = Field(
        description="Semana gestacional en la que cae la captura."
    )
    hr_valor: Decimal | None = None
    spo2_valor: Decimal | None = None
    mov_valor: int | None = None
