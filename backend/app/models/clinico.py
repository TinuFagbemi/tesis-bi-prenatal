"""Clinical domain: clinics, patients, physicians, pregnancies and their follow-up."""

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    EstadoEmbarazo,
    RolSeguimiento,
    TipoContacto,
    enum_column,
)

if TYPE_CHECKING:
    from app.models.catalogos import Especialidad, FactorRiesgo
    from app.models.monitoreo import AsignacionDispositivo, Dispositivo, SesionMonitoreo
    from app.models.seguridad import UsuarioMedico, UsuarioPaciente


class Clinica(Base):
    """Health facility that owns devices and hosts pregnancy records.

    ``corregimiento`` keeps the Panamanian administrative division; the detailed
    street address lives in ``direccion_fisica``.
    """

    __tablename__ = "clinica"

    id_clinica: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre_clinica: Mapped[str] = mapped_column(String(150), nullable=False)
    ruc: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    provincia: Mapped[str] = mapped_column(String(60), nullable=False)
    distrito: Mapped[str] = mapped_column(String(60), nullable=False)
    corregimiento: Mapped[str] = mapped_column(String(60), nullable=False)
    direccion_fisica: Mapped[str] = mapped_column(String(255), nullable=False)

    embarazos: Mapped[list["Embarazo"]] = relationship(
        back_populates="clinica",
        passive_deletes=True,
    )
    dispositivos: Mapped[list["Dispositivo"]] = relationship(
        back_populates="clinica",
        passive_deletes=True,
    )
    medicos_clinica: Mapped[list["MedicoClinica"]] = relationship(
        back_populates="clinica",
        passive_deletes=True,
    )


class Paciente(Base):
    """Pregnant woman under prenatal monitoring. All records are fictitious."""

    __tablename__ = "paciente"

    id_paciente: Mapped[int] = mapped_column(Integer, primary_key=True)
    cedula: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    primer_nombre: Mapped[str] = mapped_column(String(60), nullable=False)
    segundo_nombre: Mapped[str | None] = mapped_column(String(60), nullable=True)
    apellido_paterno: Mapped[str] = mapped_column(String(60), nullable=False)
    apellido_materno: Mapped[str | None] = mapped_column(String(60), nullable=True)
    email_pac: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    fecha_nac: Mapped[date] = mapped_column(Date, nullable=False)

    telefonos: Mapped[list["TelefonoPaciente"]] = relationship(
        back_populates="paciente",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    embarazos: Mapped[list["Embarazo"]] = relationship(
        back_populates="paciente",
        passive_deletes=True,
    )
    usuario_paciente: Mapped["UsuarioPaciente | None"] = relationship(
        back_populates="paciente",
        uselist=False,
        passive_deletes=True,
    )


class TelefonoPaciente(Base):
    """Phone contact belonging to a patient; meaningless without her."""

    __tablename__ = "telefono_paciente"
    __table_args__ = (
        UniqueConstraint(
            "id_paciente",
            "tipo_contacto",
            "valor_contacto",
            name="uq_telefono_paciente_contacto",
        ),
    )

    id_telefono_paciente: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_paciente: Mapped[int] = mapped_column(
        ForeignKey("paciente.id_paciente", ondelete="CASCADE"),
        nullable=False,
    )
    tipo_contacto: Mapped[TipoContacto] = mapped_column(
        enum_column(TipoContacto, name="tipo_contacto_paciente"),
        nullable=False,
    )
    valor_contacto: Mapped[str] = mapped_column(String(120), nullable=False)
    principal: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    paciente: Mapped["Paciente"] = relationship(back_populates="telefonos")


class Medico(Base):
    """Physician who follows pregnancies. All records are fictitious."""

    __tablename__ = "medico"

    id_medico: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_especialidad: Mapped[int] = mapped_column(
        ForeignKey("especialidad.id_especialidad", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    primer_nombre: Mapped[str] = mapped_column(String(60), nullable=False)
    segundo_nombre: Mapped[str | None] = mapped_column(String(60), nullable=True)
    apellido_paterno: Mapped[str] = mapped_column(String(60), nullable=False)
    apellido_materno: Mapped[str | None] = mapped_column(String(60), nullable=True)
    email_med: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)

    especialidad: Mapped["Especialidad"] = relationship(back_populates="medicos")
    telefonos: Mapped[list["TelefonoMedico"]] = relationship(
        back_populates="medico",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    clinicas_medico: Mapped[list["MedicoClinica"]] = relationship(
        back_populates="medico",
        passive_deletes=True,
    )
    seguimientos: Mapped[list["SeguimientoClinico"]] = relationship(
        back_populates="medico",
        passive_deletes=True,
    )
    usuario_medico: Mapped["UsuarioMedico | None"] = relationship(
        back_populates="medico",
        uselist=False,
        passive_deletes=True,
    )


class TelefonoMedico(Base):
    """Phone contact belonging to a physician; meaningless without them."""

    __tablename__ = "telefono_medico"
    __table_args__ = (
        UniqueConstraint(
            "id_medico",
            "tipo_contacto",
            "valor_contacto",
            name="uq_telefono_medico_contacto",
        ),
    )

    id_telefono_medico: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_medico: Mapped[int] = mapped_column(
        ForeignKey("medico.id_medico", ondelete="CASCADE"),
        nullable=False,
    )
    tipo_contacto: Mapped[TipoContacto] = mapped_column(
        enum_column(TipoContacto, name="tipo_contacto_medico"),
        nullable=False,
    )
    valor_contacto: Mapped[str] = mapped_column(String(120), nullable=False)
    principal: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=text("false")
    )

    medico: Mapped["Medico"] = relationship(back_populates="telefonos")


class MedicoClinica(Base):
    """Association object: a physician's affiliation period with a clinic."""

    __tablename__ = "medico_clinica"
    __table_args__ = (
        CheckConstraint(
            "fecha_final IS NULL OR fecha_final >= fecha_inicio",
            name="fechas_coherentes",
        ),
    )

    id_medico: Mapped[int] = mapped_column(
        ForeignKey("medico.id_medico", ondelete="CASCADE"),
        primary_key=True,
    )
    id_clinica: Mapped[int] = mapped_column(
        ForeignKey("clinica.id_clinica", ondelete="CASCADE"),
        primary_key=True,
    )
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_final: Mapped[date | None] = mapped_column(Date, nullable=True)
    activo: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=text("true")
    )

    medico: Mapped["Medico"] = relationship(back_populates="clinicas_medico")
    clinica: Mapped["Clinica"] = relationship(back_populates="medicos_clinica")


class Embarazo(Base):
    """A single pregnancy episode, the unit every monitoring artefact hangs off."""

    __tablename__ = "embarazo"
    __table_args__ = (
        CheckConstraint("numero_gestas >= 1", name="gestas_minimo"),
        CheckConstraint("numero_partos >= 0", name="partos_no_negativo"),
        CheckConstraint("numero_partos < numero_gestas", name="partos_menor_gestas"),
        CheckConstraint(
            "fecha_probable_parto >= fecha_inicio",
            name="fpp_posterior_inicio",
        ),
        CheckConstraint(
            "fecha_cierre IS NULL OR fecha_cierre >= fecha_inicio",
            name="cierre_posterior_inicio",
        ),
    )

    id_embarazo: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_paciente: Mapped[int] = mapped_column(
        ForeignKey("paciente.id_paciente", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    id_clinica: Mapped[int] = mapped_column(
        ForeignKey("clinica.id_clinica", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    numero_gestas: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    numero_partos: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_probable_parto: Mapped[date] = mapped_column(Date, nullable=False)
    estado_embarazo: Mapped[EstadoEmbarazo] = mapped_column(
        enum_column(EstadoEmbarazo, name="estado_embarazo"),
        nullable=False,
        default=EstadoEmbarazo.ACTIVO,
        server_default=text("'ACTIVO'"),
    )
    fecha_cierre: Mapped[date | None] = mapped_column(Date, nullable=True)

    paciente: Mapped["Paciente"] = relationship(back_populates="embarazos")
    clinica: Mapped["Clinica"] = relationship(back_populates="embarazos")
    seguimientos: Mapped[list["SeguimientoClinico"]] = relationship(
        back_populates="embarazo",
        passive_deletes=True,
    )
    factores_riesgo: Mapped[list["EmbarazoFactorRiesgo"]] = relationship(
        back_populates="embarazo",
        passive_deletes=True,
    )
    asignaciones: Mapped[list["AsignacionDispositivo"]] = relationship(
        back_populates="embarazo",
        passive_deletes=True,
    )
    sesiones: Mapped[list["SesionMonitoreo"]] = relationship(
        back_populates="embarazo",
        passive_deletes=True,
    )


class SeguimientoClinico(Base):
    """Assignment of a physician to a pregnancy for a given period and role."""

    __tablename__ = "seguimiento_clinico"
    __table_args__ = (
        CheckConstraint(
            "fecha_fin IS NULL OR fecha_fin >= fecha_asignacion",
            name="fechas_coherentes",
        ),
    )

    id_seguimiento: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_embarazo: Mapped[int] = mapped_column(
        ForeignKey("embarazo.id_embarazo", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    id_medico: Mapped[int] = mapped_column(
        ForeignKey("medico.id_medico", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    fecha_asignacion: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date | None] = mapped_column(Date, nullable=True)
    rol_seguimiento: Mapped[RolSeguimiento] = mapped_column(
        enum_column(RolSeguimiento, name="rol_seguimiento"),
        nullable=False,
    )
    activo: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=text("true")
    )

    embarazo: Mapped["Embarazo"] = relationship(back_populates="seguimientos")
    medico: Mapped["Medico"] = relationship(back_populates="seguimientos")


class EmbarazoFactorRiesgo(Base):
    """Association object: a risk factor diagnosed on a specific pregnancy.

    The factor is bound to the pregnancy episode, not permanently to the patient.
    """

    __tablename__ = "embarazo_factor_riesgo"
    __table_args__ = (
        CheckConstraint(
            "fecha_fin IS NULL OR fecha_fin >= fecha_diagnostico",
            name="fechas_coherentes",
        ),
    )

    id_embarazo: Mapped[int] = mapped_column(
        ForeignKey("embarazo.id_embarazo", ondelete="CASCADE"),
        primary_key=True,
    )
    id_factor_riesgo: Mapped[int] = mapped_column(
        ForeignKey("factor_riesgo.id_factor_riesgo", ondelete="RESTRICT"),
        primary_key=True,
    )
    fecha_diagnostico: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date | None] = mapped_column(Date, nullable=True)
    activo: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=text("true")
    )
    observaciones: Mapped[str | None] = mapped_column(Text, nullable=True)

    embarazo: Mapped["Embarazo"] = relationship(back_populates="factores_riesgo")
    factor_riesgo: Mapped["FactorRiesgo"] = relationship(back_populates="embarazos_factor")
