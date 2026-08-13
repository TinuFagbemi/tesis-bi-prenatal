"""Monitoring domain: devices, their assignment, sessions and consolidated readings."""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import (
    EstadoDispositivo,
    EstadoSesion,
    OrigenDato,
    TipoSesion,
    enum_column,
)

if TYPE_CHECKING:
    from app.models.catalogos import Semaforo, TiempoGestacional
    from app.models.clinico import Clinica, Embarazo


class Dispositivo(Base):
    """Monitoring device owned by a clinic and lent to a pregnancy."""

    __tablename__ = "dispositivo"

    id_dispositivo: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_clinica: Mapped[int] = mapped_column(
        ForeignKey("clinica.id_clinica", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    codigo_dispositivo: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    modelo: Mapped[str] = mapped_column(String(80), nullable=False)
    version_firmware: Mapped[str] = mapped_column(String(30), nullable=False)
    estado: Mapped[EstadoDispositivo] = mapped_column(
        enum_column(EstadoDispositivo, name="estado_dispositivo"),
        nullable=False,
        default=EstadoDispositivo.DISPONIBLE,
        server_default=text("'DISPONIBLE'"),
    )
    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    clinica: Mapped["Clinica"] = relationship(back_populates="dispositivos")
    asignaciones: Mapped[list["AsignacionDispositivo"]] = relationship(
        back_populates="dispositivo",
        passive_deletes=True,
    )
    sesiones: Mapped[list["SesionMonitoreo"]] = relationship(
        back_populates="dispositivo",
        passive_deletes=True,
    )


class AsignacionDispositivo(Base):
    """Lending period of a device to a pregnancy.

    The unique constraint blocks re-inserting the very same assignment while
    still allowing the device to be handed over again on a later ``fecha_inicio``.
    """

    __tablename__ = "asignacion_dispositivo"
    __table_args__ = (
        UniqueConstraint(
            "id_dispositivo",
            "id_embarazo",
            "fecha_inicio",
            name="uq_asignacion_dispositivo_periodo",
        ),
        CheckConstraint(
            "fecha_fin IS NULL OR fecha_fin >= fecha_inicio",
            name="fechas_coherentes",
        ),
    )

    id_asignacion: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_dispositivo: Mapped[int] = mapped_column(
        ForeignKey("dispositivo.id_dispositivo", ondelete="RESTRICT"),
        nullable=False,
    )
    id_embarazo: Mapped[int] = mapped_column(
        ForeignKey("embarazo.id_embarazo", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    fecha_inicio: Mapped[date] = mapped_column(Date, nullable=False)
    fecha_fin: Mapped[date | None] = mapped_column(Date, nullable=True)
    activo: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=text("true")
    )

    dispositivo: Mapped["Dispositivo"] = relationship(back_populates="asignaciones")
    embarazo: Mapped["Embarazo"] = relationship(back_populates="asignaciones")


class SesionMonitoreo(Base):
    """A monitoring session captured on the edge node and later synchronised.

    ``fecha_fin`` stays NULL while the session is PENDIENTE or INTERRUMPIDA.
    """

    __tablename__ = "sesion_monitoreo"
    __table_args__ = (
        CheckConstraint(
            "fecha_fin IS NULL OR fecha_fin >= fecha_inicio",
            name="fechas_coherentes",
        ),
    )

    id_sesion: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_embarazo: Mapped[int] = mapped_column(
        ForeignKey("embarazo.id_embarazo", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    id_dispositivo: Mapped[int] = mapped_column(
        ForeignKey("dispositivo.id_dispositivo", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    tipo_sesion: Mapped[TipoSesion] = mapped_column(
        enum_column(TipoSesion, name="tipo_sesion"),
        nullable=False,
    )
    fecha_inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fecha_fin: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estado_sesion: Mapped[EstadoSesion] = mapped_column(
        enum_column(EstadoSesion, name="estado_sesion"),
        nullable=False,
        default=EstadoSesion.PENDIENTE,
        server_default=text("'PENDIENTE'"),
    )
    origen_dato: Mapped[OrigenDato] = mapped_column(
        enum_column(OrigenDato, name="origen_dato"),
        nullable=False,
        default=OrigenDato.DISPOSITIVO,
        server_default=text("'DISPOSITIVO'"),
    )

    embarazo: Mapped["Embarazo"] = relationship(back_populates="sesiones")
    dispositivo: Mapped["Dispositivo"] = relationship(back_populates="sesiones")
    lectura: Mapped["LecturaBiometrica | None"] = relationship(
        back_populates="sesion",
        uselist=False,
        passive_deletes=True,
    )


class LecturaBiometrica(Base):
    """One consolidated reading per monitoring session -- never raw sensor samples.

    ``hr_valor`` is the *maternal* heart rate, not the fetal one. A session
    yields exactly one of two shapes, enforced by ``ck_..._forma_valida``:

    * maternal vitals -- ``hr_valor`` and ``spo2_valor`` present, ``mov_valor`` NULL;
    * fetal movement -- ``mov_valor`` present, ``hr_valor`` and ``spo2_valor`` NULL.

    A non-applicable metric is NULL, never zero, so the ETL can tell "not measured"
    apart from "measured as zero".

    Not enforced here: fetal movement is only clinically meaningful from week 20
    onwards. That rule spans ``sesion_monitoreo`` and ``tiempo_gestacional``, so a
    row-level SQL CHECK cannot express it; it is left for service-layer validation
    in the monitoring module.
    """

    __tablename__ = "lectura_biometrica"
    __table_args__ = (
        CheckConstraint(
            "hr_valor IS NULL OR hr_valor > 0",
            name="hr_positiva",
        ),
        CheckConstraint(
            "spo2_valor IS NULL OR (spo2_valor >= 0 AND spo2_valor <= 100)",
            name="spo2_rango",
        ),
        CheckConstraint(
            "mov_valor IS NULL OR mov_valor >= 0",
            name="mov_no_negativo",
        ),
        CheckConstraint(
            "fecha_hora_sincronizacion IS NULL "
            "OR fecha_hora_sincronizacion >= fecha_hora_captura",
            name="sincronizacion_posterior",
        ),
        CheckConstraint(
            "(hr_valor IS NOT NULL AND spo2_valor IS NOT NULL AND mov_valor IS NULL) "
            "OR (mov_valor IS NOT NULL AND hr_valor IS NULL AND spo2_valor IS NULL)",
            name="forma_valida",
        ),
    )

    id_lectura: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_sesion: Mapped[int] = mapped_column(
        ForeignKey("sesion_monitoreo.id_sesion", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    id_tiempo_gest: Mapped[int] = mapped_column(
        ForeignKey("tiempo_gestacional.id_tiempo_gest", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    id_semaforo: Mapped[int] = mapped_column(
        ForeignKey("semaforo.id_semaforo", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    fecha_hora_captura: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    fecha_hora_sincronizacion: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    hr_valor: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    spo2_valor: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    mov_valor: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    sesion: Mapped["SesionMonitoreo"] = relationship(back_populates="lectura")
    tiempo_gestacional: Mapped["TiempoGestacional"] = relationship(back_populates="lecturas")
    semaforo: Mapped["Semaforo"] = relationship(back_populates="lecturas")
