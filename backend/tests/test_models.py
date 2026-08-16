"""Metadata and mapper tests for the operational ORM model.

Everything here runs offline: the DDL is compiled against the PostgreSQL dialect
with a mock engine, so no database connection is ever opened.
"""

import pytest
from sqlalchemy import CheckConstraint, Index, UniqueConstraint, create_mock_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import configure_mappers
from sqlalchemy.schema import CreateTable

import app.models  # noqa: F401  -- registers every model on Base.metadata
from app.db.base import SCHEMA_OPERACIONAL, Base
from app.models.enums import TipoContacto

# PostgreSQL truncates identifiers at 63 bytes; a silently truncated constraint
# name would make Alembic downgrades unable to find what they must drop.
PG_MAX_IDENTIFIER_LENGTH = 63

TABLAS_ESPERADAS = {
    # Catálogos
    "especialidad",
    "factor_riesgo",
    "tiempo_gestacional",
    "semaforo",
    "rol",
    # Dominio clínico
    "clinica",
    "paciente",
    "telefono_paciente",
    "medico",
    "telefono_medico",
    "medico_clinica",
    "embarazo",
    "seguimiento_clinico",
    "embarazo_factor_riesgo",
    # Monitoreo
    "dispositivo",
    "asignacion_dispositivo",
    "sesion_monitoreo",
    "lectura_biometrica",
    # Seguridad
    "usuario",
    "usuario_paciente",
    "usuario_medico",
    "auditoria_log",
}

PRIMARY_KEYS_ESPERADAS = {
    "especialidad": ("id_especialidad",),
    "factor_riesgo": ("id_factor_riesgo",),
    "tiempo_gestacional": ("id_tiempo_gest",),
    "semaforo": ("id_semaforo",),
    "rol": ("id_rol",),
    "clinica": ("id_clinica",),
    "paciente": ("id_paciente",),
    "telefono_paciente": ("id_telefono_paciente",),
    "medico": ("id_medico",),
    "telefono_medico": ("id_telefono_medico",),
    "medico_clinica": ("id_medico", "id_clinica"),
    "embarazo": ("id_embarazo",),
    "seguimiento_clinico": ("id_seguimiento",),
    "embarazo_factor_riesgo": ("id_embarazo", "id_factor_riesgo"),
    "dispositivo": ("id_dispositivo",),
    "asignacion_dispositivo": ("id_asignacion",),
    "sesion_monitoreo": ("id_sesion",),
    "lectura_biometrica": ("id_lectura",),
    "usuario": ("id_usuario",),
    "usuario_paciente": ("id_usuario",),
    "usuario_medico": ("id_usuario",),
    "auditoria_log": ("id_log",),
}

FOREIGN_KEYS_ESPERADAS = {
    ("telefono_paciente", "id_paciente"): ("paciente", "id_paciente"),
    ("medico", "id_especialidad"): ("especialidad", "id_especialidad"),
    ("telefono_medico", "id_medico"): ("medico", "id_medico"),
    ("medico_clinica", "id_medico"): ("medico", "id_medico"),
    ("medico_clinica", "id_clinica"): ("clinica", "id_clinica"),
    ("embarazo", "id_paciente"): ("paciente", "id_paciente"),
    ("embarazo", "id_clinica"): ("clinica", "id_clinica"),
    ("seguimiento_clinico", "id_embarazo"): ("embarazo", "id_embarazo"),
    ("seguimiento_clinico", "id_medico"): ("medico", "id_medico"),
    ("embarazo_factor_riesgo", "id_embarazo"): ("embarazo", "id_embarazo"),
    ("embarazo_factor_riesgo", "id_factor_riesgo"): ("factor_riesgo", "id_factor_riesgo"),
    ("dispositivo", "id_clinica"): ("clinica", "id_clinica"),
    ("asignacion_dispositivo", "id_dispositivo"): ("dispositivo", "id_dispositivo"),
    ("asignacion_dispositivo", "id_embarazo"): ("embarazo", "id_embarazo"),
    ("sesion_monitoreo", "id_embarazo"): ("embarazo", "id_embarazo"),
    ("sesion_monitoreo", "id_dispositivo"): ("dispositivo", "id_dispositivo"),
    ("lectura_biometrica", "id_sesion"): ("sesion_monitoreo", "id_sesion"),
    ("lectura_biometrica", "id_tiempo_gest"): ("tiempo_gestacional", "id_tiempo_gest"),
    ("lectura_biometrica", "id_semaforo"): ("semaforo", "id_semaforo"),
    ("usuario", "id_rol"): ("rol", "id_rol"),
    ("usuario_paciente", "id_usuario"): ("usuario", "id_usuario"),
    ("usuario_paciente", "id_paciente"): ("paciente", "id_paciente"),
    ("usuario_medico", "id_usuario"): ("usuario", "id_usuario"),
    ("usuario_medico", "id_medico"): ("medico", "id_medico"),
    ("auditoria_log", "id_usuario"): ("usuario", "id_usuario"),
}

# Deletion policy of every foreign key. CASCADE belongs to rows that cannot exist
# without their parent -- contact details, account bridges and the association
# rows of a pregnancy. Everything clinical, historical or auditable is RESTRICT,
# so PostgreSQL refuses the delete instead of erasing traceable history.
# A silent CASCADE<->RESTRICT swap would change what the database allows to
# disappear, which is why each policy is pinned here rather than merely reviewed.
ONDELETE_ESPERADOS = {
    ("asignacion_dispositivo", "id_dispositivo"): "RESTRICT",
    ("asignacion_dispositivo", "id_embarazo"): "RESTRICT",
    ("auditoria_log", "id_usuario"): "RESTRICT",
    ("dispositivo", "id_clinica"): "RESTRICT",
    ("embarazo", "id_clinica"): "RESTRICT",
    ("embarazo", "id_paciente"): "RESTRICT",
    ("embarazo_factor_riesgo", "id_embarazo"): "CASCADE",
    ("embarazo_factor_riesgo", "id_factor_riesgo"): "RESTRICT",
    ("lectura_biometrica", "id_semaforo"): "RESTRICT",
    ("lectura_biometrica", "id_sesion"): "CASCADE",
    ("lectura_biometrica", "id_tiempo_gest"): "RESTRICT",
    ("medico", "id_especialidad"): "RESTRICT",
    ("medico_clinica", "id_clinica"): "CASCADE",
    ("medico_clinica", "id_medico"): "CASCADE",
    ("seguimiento_clinico", "id_embarazo"): "RESTRICT",
    ("seguimiento_clinico", "id_medico"): "RESTRICT",
    ("sesion_monitoreo", "id_dispositivo"): "RESTRICT",
    ("sesion_monitoreo", "id_embarazo"): "RESTRICT",
    ("telefono_medico", "id_medico"): "CASCADE",
    ("telefono_paciente", "id_paciente"): "CASCADE",
    ("usuario", "id_rol"): "RESTRICT",
    ("usuario_medico", "id_medico"): "CASCADE",
    ("usuario_medico", "id_usuario"): "CASCADE",
    ("usuario_paciente", "id_paciente"): "CASCADE",
    ("usuario_paciente", "id_usuario"): "CASCADE",
}

# Only these columns may be NULL. Anything absent here must be NOT NULL.
COLUMNAS_NULLABLE_ESPERADAS = {
    "especialidad": set(),
    "factor_riesgo": {"descripcion"},
    "tiempo_gestacional": {"descripcion"},
    "semaforo": set(),
    "rol": set(),
    "clinica": set(),
    "paciente": {"segundo_nombre", "apellido_materno"},
    "telefono_paciente": set(),
    "medico": {"segundo_nombre", "apellido_materno"},
    "telefono_medico": set(),
    "medico_clinica": {"fecha_final"},
    "embarazo": {"fecha_cierre"},
    "seguimiento_clinico": {"fecha_fin"},
    "embarazo_factor_riesgo": {"fecha_fin", "observaciones"},
    "dispositivo": set(),
    "asignacion_dispositivo": {"fecha_fin"},
    "sesion_monitoreo": {"fecha_fin"},
    "lectura_biometrica": {
        "fecha_hora_sincronizacion",
        "hr_valor",
        "spo2_valor",
        "mov_valor",
    },
    "usuario": set(),
    "usuario_paciente": set(),
    "usuario_medico": set(),
    # An event such as LOGIN_FALLIDO has no identified actor and no affected row.
    "auditoria_log": {
        "id_usuario",
        "nombre_entidad_afectada",
        "id_entidad_afectada",
    },
}

TIPOS_DE_CONTACTO_ESPERADOS = ["CELULAR", "TELEFONO_DOMICILIO", "CORREO_ALTERNO"]

# valor_contacto holds a phone number or an alternate email, so it is sized like
# the dedicated email columns rather than like a phone number.
LONGITUD_VALOR_CONTACTO = 120

# Only a child whose existence depends entirely on its parent may be deleted by
# the ORM when detached from the collection. Clinical, historical, monitoring and
# audit rows must survive.
RELACIONES_CON_DELETE_ORPHAN_PERMITIDAS = {
    ("Paciente", "telefonos"),
    ("Medico", "telefonos"),
}


def tabla(nombre: str):
    """Fetch a table from the operational metadata by its unqualified name."""
    return Base.metadata.tables[f"{SCHEMA_OPERACIONAL}.{nombre}"]


def nombres_de_constraints(nombre_tabla: str) -> set[str]:
    return {c.name for c in tabla(nombre_tabla).constraints if c.name is not None}


# --------------------------------------------------------------------------
# 1. Mappers
# --------------------------------------------------------------------------


def test_configure_mappers_sin_errores():
    configure_mappers()


# --------------------------------------------------------------------------
# 2-3. Registro de tablas y esquema
# --------------------------------------------------------------------------


def test_metadata_contiene_exactamente_las_22_tablas_operacionales():
    registradas = {t.name for t in Base.metadata.tables.values()}

    assert registradas == TABLAS_ESPERADAS
    assert len(Base.metadata.tables) == 22


def test_todas_las_tablas_pertenecen_al_esquema_operacional():
    for t in Base.metadata.tables.values():
        assert t.schema == SCHEMA_OPERACIONAL, t.name


# --------------------------------------------------------------------------
# 4-5. Llaves primarias
# --------------------------------------------------------------------------


@pytest.mark.parametrize("nombre_tabla", sorted(PRIMARY_KEYS_ESPERADAS))
def test_llaves_primarias_correctas(nombre_tabla):
    columnas_pk = tuple(c.name for c in tabla(nombre_tabla).primary_key.columns)

    assert columnas_pk == PRIMARY_KEYS_ESPERADAS[nombre_tabla]


@pytest.mark.parametrize(
    ("nombre_tabla", "columnas"),
    [
        ("medico_clinica", ("id_medico", "id_clinica")),
        ("embarazo_factor_riesgo", ("id_embarazo", "id_factor_riesgo")),
    ],
)
def test_llaves_primarias_compuestas(nombre_tabla, columnas):
    pk = tabla(nombre_tabla).primary_key

    assert len(pk.columns) == 2
    assert tuple(c.name for c in pk.columns) == columnas


# --------------------------------------------------------------------------
# 6. Llaves foráneas
# --------------------------------------------------------------------------


def test_llaves_foraneas_apuntan_a_tabla_y_columna_correctas():
    encontradas = {}
    for t in Base.metadata.tables.values():
        for fk in t.foreign_keys:
            encontradas[(t.name, fk.parent.name)] = (
                fk.column.table.name,
                fk.column.name,
            )

    assert encontradas == FOREIGN_KEYS_ESPERADAS


def test_llaves_foraneas_referencian_el_esquema_operacional():
    for t in Base.metadata.tables.values():
        for fk in t.foreign_keys:
            assert fk.column.table.schema == SCHEMA_OPERACIONAL


@pytest.mark.parametrize(
    ("nombre_tabla", "nombre_columna", "ondelete_esperado"),
    [
        (nombre_tabla, nombre_columna, politica)
        for (nombre_tabla, nombre_columna), politica in sorted(ONDELETE_ESPERADOS.items())
    ],
)
def test_politica_de_borrado_de_cada_llave_foranea(
    nombre_tabla, nombre_columna, ondelete_esperado
):
    """Pin ON DELETE per foreign key so a CASCADE<->RESTRICT swap is a failure."""
    encontradas = [
        fk for fk in tabla(nombre_tabla).foreign_keys if fk.parent.name == nombre_columna
    ]

    assert len(encontradas) == 1
    assert encontradas[0].ondelete == ondelete_esperado


def test_ninguna_llave_foranea_queda_sin_politica_declarada():
    """Adding or removing a foreign key must force an update of ONDELETE_ESPERADOS."""
    encontradas = {
        (t.name, fk.parent.name)
        for t in Base.metadata.tables.values()
        for fk in t.foreign_keys
    }

    assert encontradas == set(ONDELETE_ESPERADOS)


def test_auditoria_log_usa_politica_de_borrado_conservadora():
    """Audit history must not disappear with the account that produced it."""
    fk = next(iter(tabla("auditoria_log").foreign_keys))

    assert fk.ondelete == "RESTRICT"


def test_usuario_no_cascadea_el_borrado_hacia_auditoria():
    relacion = app.models.Usuario.__mapper__.relationships["logs_auditoria"]

    assert relacion.cascade.delete is False
    assert relacion.cascade.delete_orphan is False
    assert relacion.passive_deletes == "all"


def test_auditoria_log_admite_eventos_sin_actor_ni_entidad():
    """LOGIN_FALLIDO has no identified user and touches no row."""
    auditoria = tabla("auditoria_log")

    assert auditoria.c.id_usuario.nullable is True
    assert auditoria.c.nombre_entidad_afectada.nullable is True
    assert auditoria.c.id_entidad_afectada.nullable is True


def test_auditoria_log_conserva_campos_obligatorios():
    auditoria = tabla("auditoria_log")

    assert auditoria.c.accion.nullable is False
    assert auditoria.c.ip_origen.nullable is False
    assert auditoria.c.fecha_hora.nullable is False


def test_id_entidad_afectada_compila_como_varchar_255():
    """Text keeps composite primary keys representable in the trail."""
    columna = tabla("auditoria_log").c.id_entidad_afectada

    assert columna.type.compile(dialect=postgresql.dialect()) == "VARCHAR(255)"


def test_auditoria_log_no_agrego_columnas_nuevas():
    esperadas = {
        "id_log",
        "id_usuario",
        "accion",
        "nombre_entidad_afectada",
        "id_entidad_afectada",
        "ip_origen",
        "fecha_hora",
    }

    assert {c.name for c in tabla("auditoria_log").columns} == esperadas


# --------------------------------------------------------------------------
# Cascadas ORM
# --------------------------------------------------------------------------


def relaciones_con_delete_orphan() -> set[tuple[str, str]]:
    configure_mappers()
    return {
        (mapper.class_.__name__, relacion.key)
        for mapper in Base.registry.mappers
        for relacion in mapper.relationships
        if relacion.cascade.delete_orphan
    }


def test_delete_orphan_solo_en_telefonos():
    assert relaciones_con_delete_orphan() == RELACIONES_CON_DELETE_ORPHAN_PERMITIDAS


@pytest.mark.parametrize(
    ("clase", "relacion"),
    [
        ("Clinica", "medicos_clinica"),
        ("Medico", "clinicas_medico"),
        ("Embarazo", "factores_riesgo"),
        ("SesionMonitoreo", "lecturas"),
        ("Usuario", "usuario_paciente"),
        ("Usuario", "usuario_medico"),
        ("Usuario", "logs_auditoria"),
    ],
)
def test_relaciones_clinicas_e_historicas_sin_delete_orphan(clase, relacion):
    assert (clase, relacion) not in relaciones_con_delete_orphan()


def test_ninguna_relacion_borra_datos_clinicos_al_desasociar():
    """No relationship outside phones may carry a destructive ORM cascade."""
    configure_mappers()
    destructivas = {
        (mapper.class_.__name__, relacion.key)
        for mapper in Base.registry.mappers
        for relacion in mapper.relationships
        if relacion.cascade.delete or relacion.cascade.delete_orphan
    }

    assert destructivas == RELACIONES_CON_DELETE_ORPHAN_PERMITIDAS


# --------------------------------------------------------------------------
# 7. Una sesión agrupa varias lecturas consolidadas (1:N)
# --------------------------------------------------------------------------


def test_la_sesion_expone_las_lecturas_como_coleccion():
    """One monitoring event yields several processed readings, not just one."""
    sesion = app.models.SesionMonitoreo.__mapper__.relationships["lecturas"]

    assert sesion.uselist is True
    assert sesion.back_populates == "sesion"


def test_cada_lectura_pertenece_a_una_sola_sesion():
    """The child side stays scalar: a reading never spans two sessions."""
    lectura = app.models.LecturaBiometrica.__mapper__.relationships["sesion"]

    assert lectura.uselist is False
    assert lectura.back_populates == "lecturas"


def test_una_sesion_admite_varias_lecturas_en_memoria():
    """Two readings attached to the same session must both stay in the collection.

    Purely in memory: back_populates keeps both sides in sync without a session
    or a database, which is what SCRUM-54 relies on to hand five representative
    readings to a single monitoring event.
    """
    configure_mappers()
    sesion = app.models.SesionMonitoreo()
    primera = app.models.LecturaBiometrica()
    segunda = app.models.LecturaBiometrica()

    sesion.lecturas.append(primera)
    sesion.lecturas.append(segunda)

    assert sesion.lecturas == [primera, segunda]
    assert primera.sesion is sesion
    assert segunda.sesion is sesion


def test_id_sesion_ya_no_es_unico_en_lectura_biometrica():
    """A UNIQUE(id_sesion) would cap the session at a single reading."""
    lectura = tabla("lectura_biometrica")
    unicos = {
        tuple(c.name for c in constraint.columns)
        for constraint in lectura.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("id_sesion",) not in unicos
    assert unicos == set()


def test_el_ddl_de_lectura_biometrica_no_declara_unicidad_de_id_sesion():
    ddl = str(CreateTable(tabla("lectura_biometrica")).compile(dialect=postgresql.dialect()))

    assert "UNIQUE (id_sesion)" not in ddl
    assert "uq_lectura_biometrica_id_sesion" not in ddl


def test_id_sesion_conserva_su_llave_foranea_y_politica_de_borrado():
    """Loosening the cardinality must not touch the FK, its target or ON DELETE."""
    columna = tabla("lectura_biometrica").c.id_sesion
    fk = next(iter(columna.foreign_keys))

    assert columna.nullable is False
    assert columna.type.compile(dialect=postgresql.dialect()) == "INTEGER"
    assert fk.column.table.schema == SCHEMA_OPERACIONAL
    assert fk.column.table.name == "sesion_monitoreo"
    assert fk.column.name == "id_sesion"
    assert fk.ondelete == ONDELETE_ESPERADOS[("lectura_biometrica", "id_sesion")]
    assert fk.ondelete == "CASCADE"


@pytest.mark.parametrize(
    ("nombre_tabla", "columna"),
    [
        ("usuario_paciente", "id_paciente"),
        ("usuario_medico", "id_medico"),
    ],
)
def test_asociaciones_uno_a_uno_de_usuario(nombre_tabla, columna):
    t = tabla(nombre_tabla)
    unicos = {
        tuple(c.name for c in constraint.columns)
        for constraint in t.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert (columna,) in unicos


# --------------------------------------------------------------------------
# 8-9. Nulabilidad
# --------------------------------------------------------------------------


@pytest.mark.parametrize("nombre_tabla", sorted(COLUMNAS_NULLABLE_ESPERADAS))
def test_nulabilidad_por_tabla(nombre_tabla):
    t = tabla(nombre_tabla)
    nullable = {c.name for c in t.columns if c.nullable}

    assert nullable == COLUMNAS_NULLABLE_ESPERADAS[nombre_tabla]


# --------------------------------------------------------------------------
# 10. Restricciones clínicas y de fechas
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nombre_tabla", "nombre_constraint"),
    [
        ("tiempo_gestacional", "ck_tiempo_gestacional_semana_rango"),
        ("tiempo_gestacional", "ck_tiempo_gestacional_mes_rango"),
        ("tiempo_gestacional", "ck_tiempo_gestacional_trimestre_rango"),
        ("semaforo", "ck_semaforo_prioridad_rango"),
        ("embarazo", "ck_embarazo_gestas_minimo"),
        ("embarazo", "ck_embarazo_partos_no_negativo"),
        ("embarazo", "ck_embarazo_partos_menor_gestas"),
        ("embarazo", "ck_embarazo_fpp_posterior_inicio"),
        ("embarazo", "ck_embarazo_cierre_posterior_inicio"),
        ("medico_clinica", "ck_medico_clinica_fechas_coherentes"),
        ("seguimiento_clinico", "ck_seguimiento_clinico_fechas_coherentes"),
        ("embarazo_factor_riesgo", "ck_embarazo_factor_riesgo_fechas_coherentes"),
        ("asignacion_dispositivo", "ck_asignacion_dispositivo_fechas_coherentes"),
        ("sesion_monitoreo", "ck_sesion_monitoreo_fechas_coherentes"),
        ("lectura_biometrica", "ck_lectura_biometrica_hr_positiva"),
        ("lectura_biometrica", "ck_lectura_biometrica_spo2_rango"),
        ("lectura_biometrica", "ck_lectura_biometrica_mov_no_negativo"),
        ("lectura_biometrica", "ck_lectura_biometrica_sincronizacion_posterior"),
        ("lectura_biometrica", "ck_lectura_biometrica_forma_valida"),
    ],
)
def test_restricciones_check_existen(nombre_tabla, nombre_constraint):
    assert nombre_constraint in nombres_de_constraints(nombre_tabla)


@pytest.mark.parametrize(
    ("nombre_tabla", "nombre_constraint"),
    [
        ("semaforo", "ck_semaforo_codigo_semaforo"),
        ("rol", "ck_rol_nombre_rol"),
        ("telefono_paciente", "ck_telefono_paciente_tipo_contacto_paciente"),
        ("telefono_medico", "ck_telefono_medico_tipo_contacto_medico"),
        ("embarazo", "ck_embarazo_estado_embarazo"),
        ("seguimiento_clinico", "ck_seguimiento_clinico_rol_seguimiento"),
        ("dispositivo", "ck_dispositivo_estado_dispositivo"),
        ("sesion_monitoreo", "ck_sesion_monitoreo_tipo_sesion"),
        ("sesion_monitoreo", "ck_sesion_monitoreo_estado_sesion"),
        ("sesion_monitoreo", "ck_sesion_monitoreo_origen_dato"),
    ],
)
def test_enums_generan_check_constraint_nombrado(nombre_tabla, nombre_constraint):
    """native_enum=False must materialise as a named CHECK, not a PostgreSQL type."""
    assert nombre_constraint in nombres_de_constraints(nombre_tabla)


def test_unicidad_de_asignacion_de_dispositivo_permite_reasignacion():
    """The device may be lent again later; only an identical period is rejected."""
    t = tabla("asignacion_dispositivo")
    unicos = {
        tuple(c.name for c in constraint.columns)
        for constraint in t.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("id_dispositivo", "id_embarazo", "fecha_inicio") in unicos
    assert ("id_dispositivo",) not in unicos


def test_tipo_contacto_tiene_exactamente_los_tres_valores_vigentes():
    assert [miembro.value for miembro in TipoContacto] == TIPOS_DE_CONTACTO_ESPERADOS
    assert [miembro.name for miembro in TipoContacto] == TIPOS_DE_CONTACTO_ESPERADOS


@pytest.mark.parametrize("obsoleto", ["FIJO", "EMERGENCIA", "MOVIL"])
def test_tipo_contacto_ya_no_expone_valores_obsoletos(obsoleto):
    assert obsoleto not in {miembro.name for miembro in TipoContacto}
    assert obsoleto not in {miembro.value for miembro in TipoContacto}

    with pytest.raises(KeyError):
        TipoContacto[obsoleto]
    with pytest.raises(ValueError):
        TipoContacto(obsoleto)


@pytest.mark.parametrize("nombre_tabla", ["telefono_paciente", "telefono_medico"])
def test_check_de_tipo_contacto_refleja_los_valores_vigentes(nombre_tabla):
    ddl = str(CreateTable(tabla(nombre_tabla)).compile(dialect=postgresql.dialect()))
    valores = ", ".join(f"'{valor}'" for valor in TIPOS_DE_CONTACTO_ESPERADOS)

    assert f"tipo_contacto IN ({valores})" in ddl
    assert "'FIJO'" not in ddl
    assert "'EMERGENCIA'" not in ddl


@pytest.mark.parametrize("nombre_tabla", ["telefono_paciente", "telefono_medico"])
def test_tipo_contacto_rechaza_un_valor_invalido_antes_de_llegar_a_postgresql(nombre_tabla):
    """validate_strings=True guards the bind step, not attribute assignment.

    Setting an unknown string on the mapped attribute stays silent; the type only
    rejects it while preparing the value for a statement, and it raises
    LookupError -- not ValueError. Asserting it here keeps the CHECK constraint
    from being the sole line of defence, and stays offline: the bind processor is
    built from the PostgreSQL dialect without opening a connection.
    """
    procesador = tabla(nombre_tabla).c.tipo_contacto.type.bind_processor(
        postgresql.dialect()
    )

    assert procesador is not None
    # Positive control: a current value must survive the same processor.
    assert procesador("CELULAR") == "CELULAR"

    with pytest.raises(LookupError):
        procesador("VALOR_NO_PERMITIDO")


@pytest.mark.parametrize("nombre_tabla", ["telefono_paciente", "telefono_medico"])
def test_valor_contacto_admite_un_correo_alterno(nombre_tabla):
    """CORREO_ALTERNO shares this column, so it matches email_pac / email_med."""
    columna = tabla(nombre_tabla).c.valor_contacto

    assert columna.type.length == LONGITUD_VALOR_CONTACTO
    assert columna.nullable is False
    assert columna.type.compile(dialect=postgresql.dialect()) == "VARCHAR(120)"


@pytest.mark.parametrize("nombre_tabla", ["telefono_paciente", "telefono_medico"])
def test_valor_contacto_compila_como_varchar_120_en_el_ddl(nombre_tabla):
    ddl = str(CreateTable(tabla(nombre_tabla)).compile(dialect=postgresql.dialect()))

    assert "valor_contacto VARCHAR(120) NOT NULL" in ddl


@pytest.mark.parametrize(
    ("nombre_tabla", "columnas"),
    [
        ("telefono_paciente", ("id_paciente", "tipo_contacto", "valor_contacto")),
        ("telefono_medico", ("id_medico", "tipo_contacto", "valor_contacto")),
    ],
)
def test_contactos_duplicados_bloqueados(nombre_tabla, columnas):
    unicos = {
        tuple(c.name for c in constraint.columns)
        for constraint in tabla(nombre_tabla).constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert columnas in unicos


# --------------------------------------------------------------------------
# 11. Constraints con nombre determinista
# --------------------------------------------------------------------------


def test_ninguna_constraint_es_anonima():
    anonimas = []
    for t in Base.metadata.tables.values():
        for constraint in t.constraints:
            if not isinstance(constraint.name, str) or not constraint.name:
                anonimas.append((t.name, type(constraint).__name__))
        for indice in t.indexes:
            if not isinstance(indice.name, str) or not indice.name:
                anonimas.append((t.name, "Index"))

    assert anonimas == []


def test_nombres_de_constraints_siguen_la_convencion():
    prefijos = {
        "PrimaryKeyConstraint": "pk_",
        "ForeignKeyConstraint": "fk_",
        "UniqueConstraint": "uq_",
        "CheckConstraint": "ck_",
    }
    incorrectos = []
    for t in Base.metadata.tables.values():
        for constraint in t.constraints:
            prefijo = prefijos.get(type(constraint).__name__)
            if prefijo and not constraint.name.startswith(prefijo):
                incorrectos.append(constraint.name)
        for indice in t.indexes:
            if not indice.name.startswith("ix_"):
                incorrectos.append(indice.name)

    assert incorrectos == []


def test_identificadores_dentro_del_limite_de_postgresql():
    """A name over 63 bytes would be truncated by the server and drift from Alembic."""
    demasiado_largos = []
    for t in Base.metadata.tables.values():
        candidatos = [c.name for c in t.constraints if isinstance(c.name, str)]
        candidatos += [i.name for i in t.indexes if isinstance(i.name, str)]
        candidatos.append(t.name)
        demasiado_largos += [n for n in candidatos if len(n) > PG_MAX_IDENTIFIER_LENGTH]

    assert demasiado_largos == []


# --------------------------------------------------------------------------
# 12. Compilación del DDL con el dialecto PostgreSQL
# --------------------------------------------------------------------------


def test_ddl_compila_con_dialecto_postgresql_sin_conexion():
    sentencias: list[str] = []

    def recolectar(sql, *args, **kwargs):
        sentencias.append(str(sql.compile(dialect=motor.dialect)))

    motor = create_mock_engine("postgresql+psycopg://", recolectar)
    Base.metadata.create_all(motor, checkfirst=False)

    ddl = "\n".join(sentencias)

    assert len(sentencias) >= len(TABLAS_ESPERADAS)
    for nombre in TABLAS_ESPERADAS:
        assert f"CREATE TABLE {SCHEMA_OPERACIONAL}.{nombre} " in ddl
    # Enums must render as VARCHAR + CHECK, never as a native PostgreSQL type.
    assert "CREATE TYPE" not in ddl
    assert "TIMESTAMP WITH TIME ZONE" in ddl
    assert f"REFERENCES {SCHEMA_OPERACIONAL}." in ddl


def test_lectura_biometrica_exige_una_de_las_dos_formas_validas():
    lectura = tabla("lectura_biometrica")
    forma = next(
        c
        for c in lectura.constraints
        if isinstance(c, CheckConstraint) and c.name.endswith("forma_valida")
    )
    texto = str(forma.sqltext)

    assert "hr_valor IS NOT NULL AND spo2_valor IS NOT NULL AND mov_valor IS NULL" in texto
    assert "mov_valor IS NOT NULL AND hr_valor IS NULL AND spo2_valor IS NULL" in texto


def test_indices_declarados_en_llaves_foraneas_de_alta_cardinalidad():
    indices = {
        (t.name, tuple(c.name for c in i.columns))
        for t in Base.metadata.tables.values()
        for i in t.indexes
    }

    assert ("embarazo", ("id_paciente",)) in indices
    assert ("sesion_monitoreo", ("id_embarazo",)) in indices
    assert ("lectura_biometrica", ("id_semaforo",)) in indices
    assert ("auditoria_log", ("id_usuario",)) in indices


def test_indices_son_objetos_index_validos():
    for t in Base.metadata.tables.values():
        for indice in t.indexes:
            assert isinstance(indice, Index)
            assert indice.table is t
