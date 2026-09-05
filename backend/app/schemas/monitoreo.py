"""HTTP contract for the biometric ingestion endpoint (SCRUM-62).

What is validated here, and what is deliberately left out, is the whole design:

* **Here** -- the *shape* of the message, and every rule that can be decided by
  reading the message alone. Types, which fields are required and which are
  optional, controlled vocabularies, timezone-aware timestamps, the biometric
  shape of a single reading, the agreement between the session type and the
  shape of every reading it carries, the fact that a reading must have been
  captured during its own session, and the agreement between ``estado_sesion``
  and ``fecha_fin``.
* **Not here** -- value ranges (``hr_valor > 0``, ``spo2_valor`` between 0 and
  100, ``mov_valor >= 0``) and referential integrity. Those belong to
  PostgreSQL, which stays the final authority. Restating them in this module
  would be the first step towards a second copy of the clinical criteria living
  inside the API, and SCRUM-62 must not start one.

Three rules below *are* also SQL CHECK constraints: the biometric shape, a
synchronisation that cannot precede its capture, and a session that cannot end
before it starts. The duplication is intentional and narrow: catching them here
turns an opaque database failure into a 422 that names the offending field,
while PostgreSQL keeps the last word for anything that reaches it.

Identifiers: ``id_sesion`` and ``id_lectura`` are **not** part of the input.
PostgreSQL generates them from its own sequences. The four identifiers the
client does send -- ``id_embarazo``, ``id_dispositivo``, ``id_tiempo_gest`` and
``id_semaforo`` -- are references to rows that must already exist; this endpoint
never creates them as a side effect.

Every example is fictitious simulated data.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    model_validator,
)

from app.models.enums import EstadoSesion, OrigenDato, TipoSesion

# Physical storage limits of the columns, not clinical criteria. ``mov_valor``
# is a SMALLINT and ``hr_valor``/``spo2_valor`` are NUMERIC(5, 2): a value
# outside these bounds cannot be stored at all, and rejecting it here turns what
# would be an opaque driver-level DataError into a 422 that names the field.
# The clinical bounds (positive HR, SpO2 within 0-100, non-negative movement)
# are CHECK constraints and stay with PostgreSQL.
SMALLINT_MINIMO = -32768
SMALLINT_MAXIMO = 32767
DIGITOS_BIOMETRICOS = 5
DECIMALES_BIOMETRICOS = 2

# Integer fields use ``StrictInt`` so that ``true`` is not silently read as 1
# and ``"119"`` is not silently read as 119. It is the same line the SCRUM-61
# loader draws when it normalises a value for an Integer column, and drawing it
# differently in the API would mean the same dataset is accepted through one
# door and rejected through the other. Decimals keep the lax rule, also as in
# the loader: a numeric string is accepted, a boolean is not.

MENSAJE_FORMA_INVALIDA = (
    "Una lectura debe traer 'hr_valor' y 'spo2_valor' con 'mov_valor' en null, "
    "o 'mov_valor' con 'hr_valor' y 'spo2_valor' en null. Una métrica que no "
    "aplica se envía como null, nunca como cero."
)

MENSAJE_FORMA_NO_CORRESPONDE = {
    TipoSesion.SIGNOS_MATERNOS: (
        "Una sesión 'SIGNOS_MATERNOS' solo admite lecturas de frecuencia "
        "cardíaca y SpO2."
    ),
    TipoSesion.MOVIMIENTOS_FETALES: (
        "Una sesión 'MOVIMIENTOS_FETALES' solo admite lecturas de movimiento "
        "fetal."
    ),
}

# Coherencia entre el estado de la sesión y su cierre. No es una máquina de
# estados nueva: es literalmente lo que ya documenta ``SesionMonitoreo``, donde
# ``fecha_fin`` permanece NULL mientras la sesión sigue PENDIENTE o quedó
# INTERRUMPIDA. Una sesión que declara haber terminado tiene que decir cuándo.
ESTADOS_SIN_FECHA_FIN = frozenset({EstadoSesion.PENDIENTE, EstadoSesion.INTERRUMPIDA})
ESTADOS_CON_FECHA_FIN = frozenset({EstadoSesion.COMPLETADA, EstadoSesion.PROCESADA})

# Estado que la base aplica cuando el cliente omite el campo. Se usa para
# validar, de modo que omitir el estado y enviar ``fecha_fin`` sea tan
# incoherente como declarar PENDIENTE con ``fecha_fin``.
ESTADO_POR_OMISION = EstadoSesion.PENDIENTE


class _Entrada(BaseModel):
    """Common configuration of every request schema.

    ``extra="forbid"`` mirrors what the SCRUM-61 loader already does with the
    dataset file: a field the contract does not declare is a mistake worth
    reporting, not something to ignore in silence. A typo in a field name would
    otherwise be read as "the caller omitted it".
    """

    model_config = ConfigDict(extra="forbid")


class LecturaBiometricaEntrada(_Entrada):
    """One consolidated reading inside an incoming monitoring session.

    Never a raw sensor sample: what travels here is already consolidated, in the
    same sense as the ``lectura_biometrica`` table.

    A metric that does not apply to the event travels as ``null`` -- or is
    omitted, which means the same thing -- and reaches PostgreSQL as NULL. It is
    never replaced by zero or by an empty string, because the ETL has to be able
    to tell "not measured" from "measured as zero".
    """

    id_tiempo_gest: StrictInt = Field(
        description="Semana gestacional del catálogo 'tiempo_gestacional'. Debe existir.",
    )
    id_semaforo: StrictInt = Field(
        description=(
            "Clasificación del catálogo 'semaforo'. Debe existir. La API no "
            "recalcula el semáforo: lo recibe ya clasificado."
        ),
    )
    fecha_hora_captura: AwareDatetime = Field(
        description="Instante de captura, con offset de zona horaria obligatorio.",
    )
    fecha_hora_sincronizacion: AwareDatetime | None = Field(
        default=None,
        description=(
            "Instante de sincronización, con offset. Nulo mientras la lectura "
            "no se haya sincronizado. Nunca anterior a la captura."
        ),
    )
    hr_valor: Decimal | None = Field(
        default=None,
        max_digits=DIGITOS_BIOMETRICOS,
        decimal_places=DECIMALES_BIOMETRICOS,
        description="Frecuencia cardíaca materna. Nulo en una lectura de movimiento.",
    )
    spo2_valor: Decimal | None = Field(
        default=None,
        max_digits=DIGITOS_BIOMETRICOS,
        decimal_places=DECIMALES_BIOMETRICOS,
        description="Saturación de oxígeno. Nulo en una lectura de movimiento.",
    )
    mov_valor: StrictInt | None = Field(
        default=None,
        ge=SMALLINT_MINIMO,
        le=SMALLINT_MAXIMO,
        description=(
            "Conteo consolidado de movimientos fetales de la sesión. Nulo en "
            "una lectura de signos maternos."
        ),
    )

    @property
    def es_de_movimiento(self) -> bool:
        """True when the reading carries fetal movement instead of vitals."""
        return self.mov_valor is not None

    @model_validator(mode="after")
    def _validar_forma(self) -> LecturaBiometricaEntrada:
        """Exactly one of the two accepted biometric shapes, never a mixture."""
        signos_maternos = (
            self.hr_valor is not None
            and self.spo2_valor is not None
            and self.mov_valor is None
        )
        movimiento_fetal = (
            self.mov_valor is not None
            and self.hr_valor is None
            and self.spo2_valor is None
        )
        if not (signos_maternos or movimiento_fetal):
            raise ValueError(MENSAJE_FORMA_INVALIDA)
        return self

    @model_validator(mode="after")
    def _validar_orden_temporal(self) -> LecturaBiometricaEntrada:
        """A reading cannot be synchronised before it was captured."""
        if (
            self.fecha_hora_sincronizacion is not None
            and self.fecha_hora_sincronizacion < self.fecha_hora_captura
        ):
            raise ValueError(
                "'fecha_hora_sincronizacion' no puede ser anterior a "
                "'fecha_hora_captura'."
            )
        return self


class SesionMonitoreoEntrada(_Entrada):
    """One monitoring session together with the readings captured during it.

    The relation is 1:N, as in the operational schema: a session carries one or
    more readings and the list is never empty. Sending several readings in a
    single request is the normal case, not an exception.
    """

    id_embarazo: StrictInt = Field(
        description="Embarazo existente al que pertenece la sesión.",
    )
    id_dispositivo: StrictInt = Field(
        description="Dispositivo existente que capturó la sesión.",
    )
    tipo_sesion: TipoSesion = Field(
        description=(
            "Qué mide la sesión. Determina la forma admitida de todas sus "
            "lecturas."
        ),
    )
    fecha_inicio: AwareDatetime = Field(
        description="Inicio de la sesión, con offset de zona horaria obligatorio.",
    )
    fecha_fin: AwareDatetime | None = Field(
        default=None,
        description=(
            "Fin de la sesión, con offset. Nulo mientras la sesión sigue "
            "pendiente o quedó interrumpida."
        ),
    )
    estado_sesion: EstadoSesion | None = Field(
        default=None,
        description="Si se omite, la base de datos aplica su propio valor por omisión.",
    )
    origen_dato: OrigenDato | None = Field(
        default=None,
        description="Si se omite, la base de datos aplica su propio valor por omisión.",
    )
    lecturas: list[LecturaBiometricaEntrada] = Field(
        min_length=1,
        description="Al menos una lectura. Una sesión sin lecturas no se acepta.",
    )

    @model_validator(mode="after")
    def _validar_orden_temporal(self) -> SesionMonitoreoEntrada:
        """A session cannot end before it starts."""
        if self.fecha_fin is not None and self.fecha_fin < self.fecha_inicio:
            raise ValueError(
                "'fecha_fin' no puede ser anterior a 'fecha_inicio'."
            )
        return self

    @model_validator(mode="after")
    def _validar_coherencia_con_el_tipo(self) -> SesionMonitoreoEntrada:
        """Every reading must match what the session declares it measures.

        A session measures maternal vitals or it counts fetal movement, and the
        two never share a consolidated reading -- the same statement the
        ``TipoSesion`` vocabulary makes. No SQL CHECK can enforce this: the rule
        spans ``sesion_monitoreo`` and ``lectura_biometrica``, so this validator
        is the only place where it lives.
        """
        se_espera_movimiento = self.tipo_sesion is TipoSesion.MOVIMIENTOS_FETALES
        for indice, lectura in enumerate(self.lecturas):
            if lectura.es_de_movimiento is not se_espera_movimiento:
                raise ValueError(
                    f"lecturas[{indice}]: "
                    f"{MENSAJE_FORMA_NO_CORRESPONDE[self.tipo_sesion]}"
                )
        return self

    @model_validator(mode="after")
    def _validar_capturas_dentro_de_la_sesion(self) -> SesionMonitoreoEntrada:
        """Cada lectura tiene que haberse capturado durante su propia sesión.

        Sin esto, una sesión de media hora podía traer una lectura capturada
        semanas antes o después y el paquete se aceptaba: la sesión y sus
        lecturas quedaban unidas por la llave foránea pero no por el tiempo.
        Los extremos cuentan como dentro.

        ``fecha_hora_sincronizacion`` queda deliberadamente fuera de esta
        regla. Una lectura puede sincronizarse mucho después de que la sesión
        terminó, y en un sistema pensado para conectividad intermitente eso es
        el caso normal, no una anomalía.
        """
        for indice, lectura in enumerate(self.lecturas):
            if lectura.fecha_hora_captura < self.fecha_inicio:
                raise ValueError(
                    f"lecturas[{indice}]: 'fecha_hora_captura' es anterior al "
                    "inicio de la sesión."
                )
            if (
                self.fecha_fin is not None
                and lectura.fecha_hora_captura > self.fecha_fin
            ):
                raise ValueError(
                    f"lecturas[{indice}]: 'fecha_hora_captura' es posterior al "
                    "fin de la sesión."
                )
        return self

    @model_validator(mode="after")
    def _validar_estado_y_cierre(self) -> SesionMonitoreoEntrada:
        """El estado de la sesión y su ``fecha_fin`` tienen que decir lo mismo.

        Es la semántica que el modelo ya documenta, aplicada al mensaje. Omitir
        el estado equivale a declararlo PENDIENTE, porque eso es lo que la base
        va a guardar: por eso omitirlo y mandar ``fecha_fin`` es tan incoherente
        como enviar PENDIENTE con ``fecha_fin``.
        """
        estado = self.estado_sesion if self.estado_sesion is not None else ESTADO_POR_OMISION

        if estado in ESTADOS_SIN_FECHA_FIN and self.fecha_fin is not None:
            raise ValueError(
                f"una sesión '{estado.value}' no puede traer 'fecha_fin': "
                "todavía no ha terminado."
            )
        if estado in ESTADOS_CON_FECHA_FIN and self.fecha_fin is None:
            raise ValueError(
                f"una sesión '{estado.value}' tiene que traer 'fecha_fin': "
                "declara haber terminado y no dice cuándo."
            )
        return self


class SesionMonitoreoCreada(BaseModel):
    """What the endpoint returns once the whole package is committed.

    Identifiers and a count, and nothing else. No hashes, no credentials, no
    connection settings, no SQL and no internals. ``ids_lectura`` is also the
    direct evidence that the 1:N relation held: several readings under a single
    ``id_sesion``.
    """

    id_sesion: int = Field(description="Identificador que PostgreSQL asignó a la sesión.")
    lecturas_creadas: int = Field(description="Cantidad de lecturas persistidas.")
    ids_lectura: list[int] = Field(
        description="Identificadores que PostgreSQL asignó a cada lectura, en orden.",
    )
