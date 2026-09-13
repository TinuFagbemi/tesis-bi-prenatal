"""ORM models of the FetalAlert analytic star schema (SCRUM-69).

Nine structures -- the ones of the approved dimensional model (Draw.io v6 and
section 3.5.5 of Chapter III) -- each one a table of the PostgreSQL
``analitico`` schema. The logical names of the model are kept in the docstrings;
the physical names are snake_case so no identifier ever needs quoting.

What was decided, and where the drawing is not followed literally:

* **Grain.** ``fact_lectura_biometrica`` holds one row per
  ``operacional.lectura_biometrica`` row, keyed by the same ``id_lectura``.
* **Keys.** Every dimension reuses the stable operational identifier as its
  primary key: no surrogate keys, no sequences (``autoincrement=False``), no
  slowly changing dimensions. Dimensions are overwritten in place (type 1).
* **One additive refinement.** ``id_sesion`` is a degenerate dimension of the
  fact. It is what lets sessions be counted -- 732 sessions against 1,180
  readings -- and what traces a reading back to its session.
* **Physical corrections, never business changes.** Where a type in the drawing
  could truncate or round a value the operational schema accepts, the wider
  type is used: NUMERIC(5,2) for the vitals, TIMESTAMPTZ for the instant, the
  operational lengths for text. ``especialidad`` is VARCHAR(100), the
  operational length, instead of the 504 of the drawing.
* **No foreign key leaves this schema.** The analytic tables reference each
  other and never ``operacional``: the warehouse must not be coupled to the
  transactional life cycle. Traceability back to the source is kept through
  the copied operational keys and proved by the reconciliation.

Controlled vocabularies are explicit CHECK constraints over VARCHAR, not
SQLAlchemy ``Enum`` types, so no type-bound CHECK is created here. The value
lists come from the operational enums, so both schemas share one vocabulary.

All data is fictitious and simulated.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.naming import conv

from app.db.base_analitica import BaseAnalitica
from app.models.enums import CodigoSemaforo, EstadoEmbarazo

# The four parts of a name are VARCHAR(60) each in the operational schema; three
# single spaces separate them. A shorter column could truncate a valid name.
LONGITUD_NOMBRE_COMPLETO = 4 * 60 + 3

# Same length as ``telefono_paciente.valor_contacto``/``telefono_medico``: the
# nine characters of the drawing fit the simulated numbers, not the column.
LONGITUD_CONTACTO = 120

CODIGOS_DE_ESTADO = tuple(codigo.value for codigo in CodigoSemaforo)
ESTADOS_DE_EMBARAZO = tuple(estado.value for estado in EstadoEmbarazo)

# PostgreSQL truncates identifiers at 63 bytes. The naming convention would
# produce 71 and 67 characters for these two foreign keys, so they are named
# explicitly -- short and deterministic -- instead of trusting truncation.
FK_FACT_TIEMPO_GESTACIONAL = "fk_fact_lectura_id_tiempo_gestacional"
FK_BRIDGE_FACTOR_RIESGO = "fk_bridge_embarazo_factor_id_factor_riesgo"


def _valores(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{valor}'" for valor in valores)


def _estado_valido(columna: str) -> CheckConstraint:
    """``estado_*`` is NULL when the metric does not apply, a code otherwise."""
    return CheckConstraint(
        f"{columna} IS NULL OR {columna} IN ({_valores(CODIGOS_DE_ESTADO)})",
        name=f"{columna}_valido",
    )


# ---------------------------------------------------------------------------
# Dimensiones sin dependencias
# ---------------------------------------------------------------------------


class DimClinica(BaseAnalitica):
    """Dim_Clinica: one row per operational clinic."""

    __tablename__ = "dim_clinica"

    id_clinica: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )
    nombre_clinica: Mapped[str] = mapped_column(String(150), nullable=False)
    provincia: Mapped[str] = mapped_column(String(60), nullable=False)
    distrito: Mapped[str] = mapped_column(String(60), nullable=False)


class DimSemaforo(BaseAnalitica):
    """Dim_Semaforo: one row per level of the global traffic light."""

    __tablename__ = "dim_semaforo"
    __table_args__ = (
        CheckConstraint(
            f"codigo_nivel IN ({_valores(CODIGOS_DE_ESTADO)})",
            name="codigo_nivel_valido",
        ),
        CheckConstraint("prioridad BETWEEN 1 AND 3", name="prioridad_rango"),
    )

    id_semaforo: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )
    # VARCHAR(7) as drawn: the CHECK admits three codes and the longest,
    # "WARNING", has exactly seven characters.
    codigo_nivel: Mapped[str] = mapped_column(String(7), nullable=False, unique=True)
    etiqueta_visual: Mapped[str] = mapped_column(String(60), nullable=False)
    color_hex: Mapped[str] = mapped_column(String(7), nullable=False)
    prioridad: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    mensaje_app: Mapped[str] = mapped_column(String(255), nullable=False)
    version_referencia: Mapped[str] = mapped_column(String(30), nullable=False)


class DimTiempoGestacional(BaseAnalitica):
    """Dim_TiempoGestacional: one row per gestational week of the catalogue."""

    __tablename__ = "dim_tiempo_gestacional"
    __table_args__ = (
        CheckConstraint("semana_gestacion BETWEEN 1 AND 42", name="semana_rango"),
        CheckConstraint("mes_gestacion BETWEEN 1 AND 10", name="mes_rango"),
        CheckConstraint("trimestre BETWEEN 1 AND 3", name="trimestre_rango"),
    )

    id_tiempo_gest: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )
    semana_gestacion: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    mes_gestacion: Mapped[int] = mapped_column(Integer, nullable=False)
    trimestre: Mapped[int] = mapped_column(Integer, nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)


class DimFactorRiesgo(BaseAnalitica):
    """Dim_FactorRiesgo: one row per risk factor of the catalogue."""

    __tablename__ = "dim_factor_riesgo"

    id_factor_riesgo: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )
    clave_factor: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    nombre_factor: Mapped[str] = mapped_column(String(150), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    activo: Mapped[bool] = mapped_column(nullable=False)


# ---------------------------------------------------------------------------
# Dimensiones que dependen de Dim_Clinica
# ---------------------------------------------------------------------------


class DimMedico(BaseAnalitica):
    """Dim_Medico: one row per physician.

    ``id_clinica`` is a context attribute derived from ``medico_clinica``. It is
    not the route by which readings are filtered by clinic -- that is
    ``fact_lectura_biometrica.id_clinica``. NULL means the physician has no
    affiliation registered yet: an absent relation, never an invented clinic.
    A physician a fact points at always has one, checked by date.
    """

    __tablename__ = "dim_medico"

    id_medico: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )
    id_clinica: Mapped[int | None] = mapped_column(
        ForeignKey("dim_clinica.id_clinica", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    nombre_completo: Mapped[str] = mapped_column(
        String(LONGITUD_NOMBRE_COMPLETO), nullable=False
    )
    especialidad: Mapped[str] = mapped_column(String(100), nullable=False)
    email_med: Mapped[str] = mapped_column(String(120), nullable=False)
    telefono_med: Mapped[str | None] = mapped_column(
        String(LONGITUD_CONTACTO), nullable=True
    )


class DimPaciente(BaseAnalitica):
    """Dim_Paciente: one row per patient.

    ``id_clinica`` is derived from the clinics of her pregnancies and, like the
    physician's, is context: it is not a second filtering route. NULL means no
    pregnancy of hers is registered yet -- an absent relation, never an
    invented clinic.
    """

    __tablename__ = "dim_paciente"

    id_paciente: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )
    id_clinica: Mapped[int | None] = mapped_column(
        ForeignKey("dim_clinica.id_clinica", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    cedula: Mapped[str] = mapped_column(String(20), nullable=False)
    nombre_completo: Mapped[str] = mapped_column(
        String(LONGITUD_NOMBRE_COMPLETO), nullable=False
    )
    telefono_pac: Mapped[str | None] = mapped_column(
        String(LONGITUD_CONTACTO), nullable=True
    )
    fecha_nac: Mapped[date] = mapped_column(Date, nullable=False)


# ---------------------------------------------------------------------------
# Embarazo y su relación con los factores de riesgo
# ---------------------------------------------------------------------------


class DimEmbarazo(BaseAnalitica):
    """Dim_Embarazo: one row per pregnancy.

    ``clasificacion_embarazo`` is part of the approved model, but no business
    source defines a rule for it yet. It is nullable, carries no CHECK and the
    ETL leaves it NULL -- and reports how many rows are pending -- rather than
    inventing clinical meaning.
    """

    __tablename__ = "dim_embarazo"
    __table_args__ = (
        CheckConstraint(
            f"estado_embarazo IN ({_valores(ESTADOS_DE_EMBARAZO)})",
            name="estado_embarazo_valido",
        ),
        CheckConstraint("duracion_est_semanas >= 0", name="duracion_no_negativa"),
    )

    id_embarazo: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )
    id_paciente: Mapped[int] = mapped_column(
        ForeignKey("dim_paciente.id_paciente", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    numero_gestas: Mapped[int] = mapped_column(Integer, nullable=False)
    numero_partos: Mapped[int] = mapped_column(Integer, nullable=False)
    estado_embarazo: Mapped[str] = mapped_column(String(20), nullable=False)
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_probable_parto: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_cierre: Mapped[date | None] = mapped_column(Date, nullable=True)
    duracion_est_semanas: Mapped[int] = mapped_column(Integer, nullable=False)
    clasificacion_embarazo: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )


class BridgeEmbarazoFactorRiesgo(BaseAnalitica):
    """Bridge_EmbarazoFactorRiesgo: one row per (pregnancy, risk factor) pair.

    The composite primary key is the idempotency guarantee of the bridge: the
    grain is the relation, so the relation can exist only once.
    """

    __tablename__ = "bridge_embarazo_factor_riesgo"

    id_embarazo: Mapped[int] = mapped_column(
        ForeignKey("dim_embarazo.id_embarazo", ondelete="RESTRICT"),
        primary_key=True,
        autoincrement=False,
    )
    id_factor_riesgo: Mapped[int] = mapped_column(
        ForeignKey(
            "dim_factor_riesgo.id_factor_riesgo",
            ondelete="RESTRICT",
            name=conv(FK_BRIDGE_FACTOR_RIESGO),
        ),
        primary_key=True,
        autoincrement=False,
        index=True,
    )
    fecha_diagnostico: Mapped[date] = mapped_column(Date, nullable=False)
    activo: Mapped[bool] = mapped_column(nullable=False)
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Tabla de hechos
# ---------------------------------------------------------------------------


class FactLecturaBiometrica(BaseAnalitica):
    """Fact_LecturaBiometrica: one row per operational biometric reading.

    A reading has exactly one of two shapes, and the per-metric states follow
    the values they classify:

    * maternal vitals -- HR and SpO2 and their states present, movement and
      ``estado_mov`` NULL;
    * fetal movement -- movement and ``estado_mov`` present, HR, SpO2 and their
      states NULL.

    A metric that does not apply is NULL, never zero: ``mov_valor = 0`` is a
    real count. ``id_semaforo`` is the global level, the highest severity among
    the metric states; the ETL derives it and checks it against the
    operational classification before a single row is committed.
    """

    __tablename__ = "fact_lectura_biometrica"
    __table_args__ = (
        CheckConstraint(
            "(hr_valor IS NOT NULL AND spo2_valor IS NOT NULL AND mov_valor IS NULL "
            "AND estado_hr IS NOT NULL AND estado_spo2 IS NOT NULL "
            "AND estado_mov IS NULL) "
            "OR (mov_valor IS NOT NULL AND hr_valor IS NULL AND spo2_valor IS NULL "
            "AND estado_mov IS NOT NULL AND estado_hr IS NULL "
            "AND estado_spo2 IS NULL)",
            name="forma_valida",
        ),
        _estado_valido("estado_hr"),
        _estado_valido("estado_spo2"),
        _estado_valido("estado_mov"),
    )

    id_lectura: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    # Degenerate dimension: traceability to the session and COUNT(DISTINCT),
    # with no foreign key to the operational schema.
    id_sesion: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    id_paciente: Mapped[int] = mapped_column(
        ForeignKey("dim_paciente.id_paciente", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    id_medico: Mapped[int] = mapped_column(
        ForeignKey("dim_medico.id_medico", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    id_clinica: Mapped[int] = mapped_column(
        ForeignKey("dim_clinica.id_clinica", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # A foreign key does not need the name of the key it points at: the logical
    # model calls it id_tiempo_gestacional and the dimension id_tiempo_gest.
    id_tiempo_gestacional: Mapped[int] = mapped_column(
        ForeignKey(
            "dim_tiempo_gestacional.id_tiempo_gest",
            ondelete="RESTRICT",
            name=conv(FK_FACT_TIEMPO_GESTACIONAL),
        ),
        nullable=False,
        index=True,
    )
    id_embarazo: Mapped[int] = mapped_column(
        ForeignKey("dim_embarazo.id_embarazo", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    id_semaforo: Mapped[int] = mapped_column(
        ForeignKey("dim_semaforo.id_semaforo", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    hr_valor: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    spo2_valor: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    mov_valor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estado_hr: Mapped[str | None] = mapped_column(String(10), nullable=True)
    estado_spo2: Mapped[str | None] = mapped_column(String(10), nullable=True)
    estado_mov: Mapped[str | None] = mapped_column(String(10), nullable=True)
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


# Load order imposed by the foreign keys inside the schema. The reconciliation,
# the tests and the documentation read it from here.
TABLAS_ANALITICAS = (
    DimClinica.__table__,
    DimSemaforo.__table__,
    DimTiempoGestacional.__table__,
    DimFactorRiesgo.__table__,
    DimMedico.__table__,
    DimPaciente.__table__,
    DimEmbarazo.__table__,
    BridgeEmbarazoFactorRiesgo.__table__,
    FactLecturaBiometrica.__table__,
)
