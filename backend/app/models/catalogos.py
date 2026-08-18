"""Reference catalogues: rarely-mutated lookup tables the clinical domain points at."""

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Integer, SmallInteger, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import CodigoSemaforo, NombreRol, enum_column

if TYPE_CHECKING:
    from app.models.clinico import EmbarazoFactorRiesgo, Medico
    from app.models.monitoreo import LecturaBiometrica
    from app.models.seguridad import Usuario


class Especialidad(Base):
    """Medical specialty a physician practises."""

    __tablename__ = "especialidad"

    id_especialidad: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre_especialidad: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    medicos: Mapped[list["Medico"]] = relationship(
        back_populates="especialidad",
        passive_deletes=True,
    )


class FactorRiesgo(Base):
    """Catalogue of prenatal risk factors that can be diagnosed on a pregnancy."""

    __tablename__ = "factor_riesgo"

    id_factor_riesgo: Mapped[int] = mapped_column(Integer, primary_key=True)
    clave_factor: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    nombre_factor: Mapped[str] = mapped_column(String(150), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)
    activo: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=text("true")
    )

    embarazos_factor: Mapped[list["EmbarazoFactorRiesgo"]] = relationship(
        back_populates="factor_riesgo",
        passive_deletes=True,
    )


class TiempoGestacional(Base):
    """One row per gestational week, pre-computed for ETL and dashboard grouping."""

    __tablename__ = "tiempo_gestacional"
    __table_args__ = (
        CheckConstraint(
            "semana_gestacion BETWEEN 1 AND 42",
            name="semana_rango",
        ),
        CheckConstraint(
            "mes_gestacion BETWEEN 1 AND 10",
            name="mes_rango",
        ),
        CheckConstraint(
            "trimestre BETWEEN 1 AND 3",
            name="trimestre_rango",
        ),
    )

    id_tiempo_gest: Mapped[int] = mapped_column(Integer, primary_key=True)
    semana_gestacion: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    mes_gestacion: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    trimestre: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    grupo_clinico: Mapped[str] = mapped_column(String(60), nullable=False)
    descripcion: Mapped[str | None] = mapped_column(Text, nullable=True)

    lecturas: Mapped[list["LecturaBiometrica"]] = relationship(
        back_populates="tiempo_gestacional",
        passive_deletes=True,
    )


class Semaforo(Base):
    """Traffic-light classification applied to a consolidated reading."""

    __tablename__ = "semaforo"
    __table_args__ = (
        CheckConstraint(
            "prioridad BETWEEN 1 AND 3",
            name="prioridad_rango",
        ),
    )

    id_semaforo: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo_nivel: Mapped[CodigoSemaforo] = mapped_column(
        enum_column(CodigoSemaforo, name="codigo_semaforo"),
        nullable=False,
        unique=True,
    )
    etiqueta_visual: Mapped[str] = mapped_column(String(60), nullable=False)
    color_hex: Mapped[str] = mapped_column(String(7), nullable=False)
    prioridad: Mapped[int] = mapped_column(SmallInteger, nullable=False, unique=True)
    mensaje_app: Mapped[str] = mapped_column(String(255), nullable=False)
    version_referencia: Mapped[str] = mapped_column(String(30), nullable=False)

    lecturas: Mapped[list["LecturaBiometrica"]] = relationship(
        back_populates="semaforo",
        passive_deletes=True,
    )


class Rol(Base):
    """RBAC role granted to an application account."""

    __tablename__ = "rol"

    id_rol: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre_rol: Mapped[NombreRol] = mapped_column(
        enum_column(NombreRol, name="nombre_rol"),
        nullable=False,
        unique=True,
    )

    usuarios: Mapped[list["Usuario"]] = relationship(
        back_populates="rol",
        passive_deletes=True,
    )
