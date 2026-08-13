"""Security domain: accounts, their link to a clinical profile, and the audit trail."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.catalogos import Rol
    from app.models.clinico import Medico, Paciente


class Usuario(Base):
    """Application account. Only the Argon2id digest is stored, never a password."""

    __tablename__ = "usuario"

    id_usuario: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_rol: Mapped[int] = mapped_column(
        ForeignKey("rol.id_rol", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    activo: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=text("true")
    )

    rol: Mapped["Rol"] = relationship(back_populates="usuarios")
    usuario_paciente: Mapped["UsuarioPaciente | None"] = relationship(
        back_populates="usuario",
        uselist=False,
        passive_deletes=True,
    )
    usuario_medico: Mapped["UsuarioMedico | None"] = relationship(
        back_populates="usuario",
        uselist=False,
        passive_deletes=True,
    )
    # Deliberately no delete cascade: the audit trail must outlive the account.
    # passive_deletes="all" stops the ORM from nulling out auditoria_log.id_usuario,
    # so the RESTRICT foreign key is what decides, and a user with history cannot
    # be deleted at all -- only deactivated via Usuario.activo.
    logs_auditoria: Mapped[list["AuditoriaLog"]] = relationship(
        back_populates="usuario",
        passive_deletes="all",
    )


class UsuarioPaciente(Base):
    """One-to-one bridge between an account and a patient profile."""

    __tablename__ = "usuario_paciente"

    id_usuario: Mapped[int] = mapped_column(
        ForeignKey("usuario.id_usuario", ondelete="CASCADE"),
        primary_key=True,
    )
    id_paciente: Mapped[int] = mapped_column(
        ForeignKey("paciente.id_paciente", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    usuario: Mapped["Usuario"] = relationship(back_populates="usuario_paciente")
    paciente: Mapped["Paciente"] = relationship(back_populates="usuario_paciente")


class UsuarioMedico(Base):
    """One-to-one bridge between an account and a physician profile."""

    __tablename__ = "usuario_medico"

    id_usuario: Mapped[int] = mapped_column(
        ForeignKey("usuario.id_usuario", ondelete="CASCADE"),
        primary_key=True,
    )
    id_medico: Mapped[int] = mapped_column(
        ForeignKey("medico.id_medico", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    usuario: Mapped["Usuario"] = relationship(back_populates="usuario_medico")
    medico: Mapped["Medico"] = relationship(back_populates="usuario_medico")


class AuditoriaLog(Base):
    """Append-only trail of relevant actions, per Ley 81 (2019) traceability.

    The foreign key is RESTRICT on purpose: deleting an account that produced
    audit entries is refused by the database rather than silently erasing them.

    Three columns are nullable so the trail can also record events that have no
    identified actor or no affected row -- ``LOGIN_FALLIDO`` being the driving
    case. ``id_entidad_afectada`` is text rather than an integer so entities with
    a composite primary key (``medico_clinica``, ``embarazo_factor_riesgo``) can
    be referenced too. ``accion``, ``ip_origen`` and ``fecha_hora`` stay
    mandatory: every entry must say what happened, from where, and when.
    """

    __tablename__ = "auditoria_log"

    id_log: Mapped[int] = mapped_column(Integer, primary_key=True)
    id_usuario: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id_usuario", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    accion: Mapped[str] = mapped_column(String(60), nullable=False)
    nombre_entidad_afectada: Mapped[str | None] = mapped_column(String(80), nullable=True)
    id_entidad_afectada: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_origen: Mapped[str] = mapped_column(String(45), nullable=False)
    fecha_hora: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    usuario: Mapped["Usuario | None"] = relationship(back_populates="logs_auditoria")
