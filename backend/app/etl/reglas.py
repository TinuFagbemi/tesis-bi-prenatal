"""Pure domain rules of the analytic ETL: classification and derivations.

Nothing in this module opens a connection or reads a table. Every rule that
turns an operational row into an analytic one and involves a decision lives
here, and only here -- the screening thresholds above all. There is no second
copy of them anywhere in the repository: the dataset generator produces values
compatible with them but never classifies, and the reconciliation checks the
result against independent evidence instead of calling these functions again.

**The thresholds are version SIM-1.0.** They translate Table 4 of Chapter III
(«Umbrales de Tamizaje FetalAlert») into exhaustive intervals over continuous
values:

* HR (maternal heart rate, lpm): ``hr < 55`` or ``hr > 110`` is ERROR,
  ``55 <= hr < 60`` and ``100 <= hr <= 110`` are WARNING, and ``60 <= hr < 100``
  is OK. The table lists 100 both as normal and as caution; the higher severity
  wins. The intervals are exhaustive over the continuous line: a bradycardia
  that has not reached the alert bound is a screening WARNING, not a value
  without a rule, and it never stops the run.
* SpO2 (%): ``spo2 < 92`` is ERROR, ``92 <= spo2 < 95`` is WARNING and
  ``spo2 >= 95`` is OK. The boundary of the table is 95 %, so a continuous value
  such as 94.5 is WARNING -- there is no gap between 94 and 95.
* Fetal movement (count per session): ``mov >= umbral`` is OK,
  ``umbral/2 <= mov < umbral`` is WARNING and ``mov < umbral/2`` is ERROR. The
  table speaks of a threshold per trimester without giving numbers; SIM-1.0
  uses 10 -- the constant the simulated dataset is built with -- for the
  trimesters in which movement is admitted. It is a validation rule of the
  simulated sample, not a universal clinical statement. No movement is valid
  before week 20.

The global level of a reading is the highest severity among the metrics that
apply to it (OK < WARNING < ERROR).

A value without a rule, an ambiguous derivation or anything that would force
the ETL to guess raises an error that rolls the whole run back. Messages carry
technical identifiers and the rule involved, never a biometric value or a
personal datum.

All data is fictitious and simulated.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from zoneinfo import ZoneInfo

# Read-only reuse of the approved week-20 threshold: one definition of it in
# the repository, the same one the loader and the ingestion endpoint use.
from app.loader.dataset import SEMANA_MINIMA_DE_MOVIMIENTO
from app.models.enums import CodigoSemaforo, TipoContacto

# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------

# Declared in the module every other ETL module depends on, so the pipeline can
# raise them without an import cycle and the CLI can catch the whole family.


class ErrorDeEtl(Exception):
    """Base of every failure the ETL reports with a message of its own.

    ``detalle`` is text written by this project: it never carries a driver
    message, a connection URL, a biometric value or a personal datum.
    """

    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


class ErrorDeDatos(ErrorDeEtl):
    """The source holds something the ETL cannot load without guessing."""


class VersionDeReglasDesconocida(ErrorDeDatos):
    """A threshold version this module does not implement."""


class ReglaNoDefinida(ErrorDeDatos):
    """A value falls where the approved rules say nothing."""


class LecturaInvalida(ErrorDeDatos):
    """A reading whose shape or context breaks the domain."""


class DerivacionImposible(ErrorDeDatos):
    """A derived attribute has no source row to be derived from."""


class DerivacionAmbigua(ErrorDeDatos):
    """A derived attribute has more than one candidate and no rule to choose."""


class CatalogoIncoherente(ErrorDeDatos):
    """The semaphore catalogue does not describe the levels the rules produce."""


class DiscrepanciaDeSemaforo(ErrorDeDatos):
    """The derived global level differs from the operational one."""


# ---------------------------------------------------------------------------
# Códigos y severidad
# ---------------------------------------------------------------------------

OK = CodigoSemaforo.OK.value
WARNING = CodigoSemaforo.WARNING.value
ERROR = CodigoSemaforo.ERROR.value

# OK < WARNING < ERROR. The catalogue's ``prioridad`` must agree with this order,
# and ``validar_catalogo_de_semaforo`` refuses it otherwise.
SEVERIDAD = MappingProxyType({OK: 1, WARNING: 2, ERROR: 3})

# ---------------------------------------------------------------------------
# Umbrales versionados
# ---------------------------------------------------------------------------

VERSION_SIM_1_0 = "SIM-1.0"


@dataclass(frozen=True)
class UmbralesDeTamizaje:
    """One version of the screening thresholds, as exhaustive interval bounds."""

    version: str
    hr_alerta_inferior: Decimal  # hr < this -> ERROR
    hr_normal_minimo: Decimal  # this <= hr < hr_precaucion_minimo -> OK;
    # hr_alerta_inferior <= hr < hr_normal_minimo -> WARNING
    hr_precaucion_minimo: Decimal  # this <= hr <= hr_precaucion_maximo -> WARNING
    hr_precaucion_maximo: Decimal  # hr > this -> ERROR
    spo2_normal_minimo: Decimal  # spo2 >= this -> OK
    spo2_precaucion_minimo: Decimal  # this <= spo2 < spo2_normal_minimo -> WARNING
    movimiento_por_trimestre: MappingProxyType  # trimestre -> umbral


UMBRALES = MappingProxyType(
    {
        VERSION_SIM_1_0: UmbralesDeTamizaje(
            version=VERSION_SIM_1_0,
            hr_alerta_inferior=Decimal("55"),
            hr_normal_minimo=Decimal("60"),
            hr_precaucion_minimo=Decimal("100"),
            hr_precaucion_maximo=Decimal("110"),
            spo2_normal_minimo=Decimal("95"),
            spo2_precaucion_minimo=Decimal("92"),
            # Movement is only admitted from week 20, which is always the
            # second or third trimester; there is no first-trimester threshold.
            movimiento_por_trimestre=MappingProxyType({2: 10, 3: 10}),
        )
    }
)


def umbrales_de(version: str) -> UmbralesDeTamizaje:
    """The thresholds of ``version``, or refuse a version nobody implemented."""
    try:
        return UMBRALES[version]
    except KeyError:
        raise VersionDeReglasDesconocida(
            f"La versión de umbrales '{version}' no está implementada; las "
            f"versiones disponibles son {sorted(UMBRALES)}."
        ) from None


def _decimal(valor: Decimal | int, metrica: str) -> Decimal:
    # ``bool`` is an int subclass and float would compare inexactly: neither is
    # what PostgreSQL returns for a NUMERIC column.
    if isinstance(valor, bool) or not isinstance(valor, (Decimal, int)):
        raise LecturaInvalida(
            f"{metrica} debe ser numérico exacto y llegó {type(valor).__name__}."
        )
    return Decimal(valor)


# ---------------------------------------------------------------------------
# Clasificación por métrica
# ---------------------------------------------------------------------------


def clasificar_hr(valor: Decimal | int, *, version: str = VERSION_SIM_1_0) -> str:
    """State of a maternal heart rate reading. Exhaustive: no value is left out."""
    umbrales = umbrales_de(version)
    hr = _decimal(valor, "hr_valor")

    if hr < umbrales.hr_alerta_inferior or hr > umbrales.hr_precaucion_maximo:
        return ERROR
    # 100 is listed both as normal and as caution: the higher severity wins.
    if hr >= umbrales.hr_precaucion_minimo:
        return WARNING
    if hr >= umbrales.hr_normal_minimo:
        return OK
    # 55 <= hr < 60: bradycardia that has not reached the alert bound. It is a
    # screening WARNING, not a gap in the table.
    return WARNING


def clasificar_spo2(valor: Decimal | int, *, version: str = VERSION_SIM_1_0) -> str:
    """State of an oxygen saturation reading. Exhaustive: no value is left out."""
    umbrales = umbrales_de(version)
    spo2 = _decimal(valor, "spo2_valor")

    if spo2 >= umbrales.spo2_normal_minimo:
        return OK
    if spo2 >= umbrales.spo2_precaucion_minimo:
        return WARNING
    return ERROR


def clasificar_movimiento(
    valor: int, *, semana: int, trimestre: int, version: str = VERSION_SIM_1_0
) -> str:
    """State of a consolidated fetal movement count.

    ``semana`` and ``trimestre`` come from the gestational catalogue row the
    reading already points at; SIM-1.0 uses the same threshold for every
    trimester in which movement is admitted, but the signature does not assume
    a future version will.
    """
    umbrales = umbrales_de(version)
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise LecturaInvalida(
            f"mov_valor debe ser un entero y llegó {type(valor).__name__}."
        )
    if valor < 0:
        raise LecturaInvalida("mov_valor no puede ser negativo.")
    if semana < SEMANA_MINIMA_DE_MOVIMIENTO:
        raise LecturaInvalida(
            f"registra movimiento fetal en la semana {semana}; no existen "
            f"movimientos antes de la semana {SEMANA_MINIMA_DE_MOVIMIENTO}."
        )
    umbral = umbrales.movimiento_por_trimestre.get(trimestre)
    if umbral is None:
        raise ReglaNoDefinida(
            f"la versión {version} no define umbral de movimiento fetal para el "
            f"trimestre {trimestre}."
        )

    if valor >= umbral:
        return OK
    # ``valor * 2 >= umbral`` is «at least 50 % of the threshold» in exact
    # integer arithmetic: no rounding decides a boundary.
    if valor * 2 >= umbral:
        return WARNING
    return ERROR


# ---------------------------------------------------------------------------
# Clasificación de una lectura completa
# ---------------------------------------------------------------------------

MENSAJE_FORMA_INVALIDA = (
    "la lectura debe traer hr_valor y spo2_valor con mov_valor en NULL, o "
    "mov_valor con hr_valor y spo2_valor en NULL."
)


@dataclass(frozen=True)
class EstadosDeLectura:
    """Per-metric states of one reading and its global level.

    A metric that does not apply keeps ``None``: it is never OK, never an empty
    string and never zero.
    """

    estado_hr: str | None
    estado_spo2: str | None
    estado_mov: str | None
    codigo_global: str


def severidad_global(estados: Iterable[str | None]) -> str:
    """Highest severity among the states that apply."""
    aplicables = [estado for estado in estados if estado is not None]
    if not aplicables:
        raise LecturaInvalida("la lectura no tiene ninguna métrica aplicable.")
    desconocidos = sorted(set(aplicables) - set(SEVERIDAD))
    if desconocidos:
        raise LecturaInvalida(f"estados desconocidos: {desconocidos}.")
    return max(aplicables, key=SEVERIDAD.__getitem__)


def clasificar_lectura(
    *,
    hr_valor: Decimal | int | None,
    spo2_valor: Decimal | int | None,
    mov_valor: int | None,
    semana: int,
    trimestre: int,
    version: str = VERSION_SIM_1_0,
) -> EstadosDeLectura:
    """Classify the metrics that apply to a reading and derive its global level."""
    signos_maternos = hr_valor is not None and spo2_valor is not None and mov_valor is None
    movimiento_fetal = mov_valor is not None and hr_valor is None and spo2_valor is None

    if signos_maternos:
        estado_hr = clasificar_hr(hr_valor, version=version)
        estado_spo2 = clasificar_spo2(spo2_valor, version=version)
        estado_mov = None
    elif movimiento_fetal:
        estado_hr = estado_spo2 = None
        estado_mov = clasificar_movimiento(
            mov_valor, semana=semana, trimestre=trimestre, version=version
        )
    else:
        raise LecturaInvalida(MENSAJE_FORMA_INVALIDA)

    return EstadosDeLectura(
        estado_hr=estado_hr,
        estado_spo2=estado_spo2,
        estado_mov=estado_mov,
        codigo_global=severidad_global((estado_hr, estado_spo2, estado_mov)),
    )


# ---------------------------------------------------------------------------
# Catálogo del semáforo
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NivelDeSemaforo:
    id_semaforo: int
    codigo_nivel: str
    prioridad: int
    version_referencia: str


def validar_catalogo_de_semaforo(
    niveles: Iterable[NivelDeSemaforo], *, version: str = VERSION_SIM_1_0
) -> dict[str, int]:
    """The catalogue must describe exactly the levels the rules produce.

    Three levels, their ``prioridad`` in the order of severity and their
    ``version_referencia`` equal to the version the rules implement -- the
    consistency Table 4 demands between the semaphore dimension and the
    classification step. Returns the identifier of each code.
    """
    umbrales_de(version)
    niveles = list(niveles)
    codigos = [nivel.codigo_nivel for nivel in niveles]
    if sorted(codigos) != sorted(SEVERIDAD):
        raise CatalogoIncoherente(
            "El catálogo semaforo debe contener exactamente una vez cada nivel "
            f"{sorted(SEVERIDAD)}; contiene {sorted(codigos)}."
        )
    for nivel in niveles:
        if nivel.prioridad != SEVERIDAD[nivel.codigo_nivel]:
            raise CatalogoIncoherente(
                f"semaforo id_semaforo={nivel.id_semaforo}: la prioridad "
                f"{nivel.prioridad} no corresponde a la severidad de "
                f"{nivel.codigo_nivel} ({SEVERIDAD[nivel.codigo_nivel]})."
            )
        if nivel.version_referencia != version:
            raise VersionDeReglasDesconocida(
                f"semaforo id_semaforo={nivel.id_semaforo}: version_referencia "
                f"'{nivel.version_referencia}' no coincide con la versión de los "
                f"umbrales del ETL ('{version}')."
            )
    return {nivel.codigo_nivel: nivel.id_semaforo for nivel in niveles}


# ---------------------------------------------------------------------------
# Derivaciones dimensionales
# ---------------------------------------------------------------------------

# Follow-up dates are clinical dates of the Panamanian context, so a reading is
# compared against them by the day it happened in Panama -- not by its UTC day.
ZONA_HORARIA_CLINICA = ZoneInfo("America/Panama")
DIAS_POR_SEMANA = 7


def fecha_clinica(instante: datetime) -> date:
    """Calendar day of an instant in America/Panama. Naive instants are refused."""
    if instante.tzinfo is None or instante.utcoffset() is None:
        raise LecturaInvalida(
            "la marca de tiempo no tiene zona horaria; no se puede ubicar en un "
            "día clínico sin suponerla."
        )
    return instante.astimezone(ZONA_HORARIA_CLINICA).date()


@dataclass(frozen=True)
class PeriodoDeSeguimiento:
    """A PRINCIPAL follow-up period of a pregnancy, both ends inclusive."""

    id_seguimiento: int
    id_medico: int
    fecha_asignacion: date
    fecha_fin: date | None

    def cubre(self, dia: date) -> bool:
        return self.fecha_asignacion <= dia and (
            self.fecha_fin is None or dia <= self.fecha_fin
        )


def medico_responsable(
    periodos: Iterable[PeriodoDeSeguimiento], dia: date, *, id_lectura: int
) -> int:
    """The physician whose PRINCIPAL follow-up covers the clinical day.

    Exactly one period must cover it. None is an integrity failure and more than
    one is an ambiguity: there is no «first», no minimum identifier and no NULL.
    APOYO and REEMPLAZO roles are not considered, because no approved source
    says how they replace the PRINCIPAL. ``activo`` is not consulted either: a
    closed follow-up still covers the readings taken while it was open.
    """
    vigentes = sorted(
        (periodo for periodo in periodos if periodo.cubre(dia)),
        key=lambda periodo: periodo.id_seguimiento,
    )
    if not vigentes:
        raise DerivacionImposible(
            f"lectura id_lectura={id_lectura}: ningún seguimiento PRINCIPAL de su "
            "embarazo cubre su fecha clínica (America/Panama)."
        )
    if len(vigentes) > 1:
        raise DerivacionAmbigua(
            f"lectura id_lectura={id_lectura}: {len(vigentes)} seguimientos "
            "PRINCIPAL cubren su fecha clínica "
            f"(id_seguimiento={[periodo.id_seguimiento for periodo in vigentes]})."
        )
    return vigentes[0].id_medico


@dataclass(frozen=True)
class AfiliacionClinica:
    """A physician's affiliation period with a clinic, both ends inclusive."""

    id_clinica: int
    fecha_inicio: date
    fecha_final: date | None

    def cubre(self, dia: date) -> bool:
        return self.fecha_inicio <= dia and (
            self.fecha_final is None or dia <= self.fecha_final
        )


def verificar_afiliacion(
    afiliaciones: Iterable[AfiliacionClinica],
    dia: date,
    *,
    id_medico: int,
    id_clinica_embarazo: int,
    id_lectura: int,
) -> None:
    """The responsible physician must work, that day, at the pregnancy's clinic.

    The applicable affiliations are the ones whose period covers the clinical
    day, whatever ``activo`` says: a closed affiliation still covers the days it
    was open. Exactly one must apply, and it must be the pregnancy's clinic.
    ``medico_clinica`` is keyed by (physician, clinic), so two applicable
    affiliations are always two clinics on the same day -- an ambiguity, and no
    rule says which one wins.
    """
    aplicables = sorted(
        (afiliacion for afiliacion in afiliaciones if afiliacion.cubre(dia)),
        key=lambda afiliacion: afiliacion.id_clinica,
    )
    prefijo = f"lectura id_lectura={id_lectura}: el médico id_medico={id_medico}"
    if not aplicables:
        raise DerivacionImposible(
            f"{prefijo} no tiene ninguna afiliación a una clínica que cubra su "
            "fecha clínica (America/Panama)."
        )
    if len(aplicables) > 1:
        raise DerivacionAmbigua(
            f"{prefijo} tiene {len(aplicables)} afiliaciones que cubren su fecha "
            f"clínica (id_clinica={[a.id_clinica for a in aplicables]})."
        )
    if aplicables[0].id_clinica != id_clinica_embarazo:
        raise DerivacionImposible(
            f"{prefijo} está afiliado en su fecha clínica a la clínica "
            f"id_clinica={aplicables[0].id_clinica}, no a la del embarazo "
            f"(id_clinica={id_clinica_embarazo})."
        )


def clinica_contextual(clinicas: Iterable[int], *, entidad: str) -> int | None:
    """The clinic an entity is associated with, as context; NULL when there is none.

    ``None`` means «no relation yet» -- a patient whose pregnancy is not
    registered, a physician with no affiliation -- and never an invented
    clinic. More than one distinct clinic cannot fit in a single column, and
    choosing one of them is refused.
    """
    distintas = sorted(set(clinicas))
    if len(distintas) > 1:
        raise DerivacionAmbigua(
            f"{entidad} está asociado a {len(distintas)} clínicas distintas "
            f"(id_clinica={distintas}); la dimensión solo admite una."
        )
    return distintas[0] if distintas else None


@dataclass(frozen=True)
class Contacto:
    tipo_contacto: str
    valor_contacto: str
    principal: bool


def telefono_principal(contactos: Iterable[Contacto], *, entidad: str) -> str | None:
    """The principal CELULAR contact: one is used, none is NULL, two are refused."""
    candidatos = [
        contacto.valor_contacto
        for contacto in contactos
        if contacto.principal and contacto.tipo_contacto == TipoContacto.CELULAR.value
    ]
    if len(candidatos) > 1:
        # Only the count: the numbers themselves are personal data.
        raise DerivacionAmbigua(
            f"{entidad} tiene {len(candidatos)} contactos principales de tipo "
            "CELULAR."
        )
    return candidatos[0] if candidatos else None


def nombre_completo(partes: Iterable[str | None], *, entidad: str) -> str:
    """Name parts that are present, joined by single spaces, untrimmed.

    NULL parts and empty strings are skipped -- the latter would leave a double
    space -- and nothing else is altered.
    """
    presentes = [parte for parte in partes if parte]
    if not presentes:
        raise DerivacionImposible(f"{entidad} no tiene ninguna parte del nombre.")
    return " ".join(presentes)


def duracion_estimada_semanas(
    fecha_inicio: date, fecha_probable_parto: date, *, id_embarazo: int
) -> int:
    """Whole weeks between the start and the estimated due date.

    A remainder is not rounded: no approved rule says which way, so it stops
    the run instead of producing a plausible but invented number.
    """
    dias = (fecha_probable_parto - fecha_inicio).days
    if dias < 0:
        raise DerivacionImposible(
            f"embarazo id_embarazo={id_embarazo}: la fecha probable de parto es "
            "anterior al inicio."
        )
    semanas, resto = divmod(dias, DIAS_POR_SEMANA)
    if resto:
        raise ReglaNoDefinida(
            f"embarazo id_embarazo={id_embarazo}: la duración estimada no es un "
            "número entero de semanas y no existe una regla de redondeo aprobada."
        )
    return semanas


def clasificacion_embarazo() -> None:
    """Dim_Embarazo.clasificacion_embarazo: pending an approved business rule.

    The attribute belongs to the dimensional model v6, but the current business
    sources define no classification rule for a pregnancy. The ETL keeps the
    column and leaves it NULL -- and every run reports how many rows are pending
    -- instead of inventing clinical meaning. When a rule is approved, this is
    the one function that changes.
    """
    return None
