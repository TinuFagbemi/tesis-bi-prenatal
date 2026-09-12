"""Reconciliation between ``operacional`` and ``analitico``, in plain SQL.

**Independence is the point.** Nothing here imports the rules or the
transformation it is meant to verify: every check is a SQL statement written
against both schemas, so a defect in the ETL cannot also hide in the check that
should catch it. Where an attribute is derived -- a full name, a principal
phone, a clinic, the responsible physician, the estimated duration -- the SQL
derives it again on its own and compares.

The screening thresholds are deliberately **not** re-implemented here: a second
copy of them is exactly what the project forbids. The per-metric states are
verified by the boundary tests of the rules and, here, by two independent
signals -- the global level must be the highest per-metric state, and it must
equal the level ``operacional`` already recorded for the reading.

Each check counts discrepancies. A blocking check with a count above zero makes
the run fail and roll back; an informative one is only reported.

Runs inside the caller's transaction and never writes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.db.base import SCHEMA_OPERACIONAL as O
from app.db.base_analitica import SCHEMA_ANALITICO as A
from app.loader.dataset import SEMANA_MINIMA_DE_MOVIMIENTO

# The clinical day of a reading, in the zone the follow-up dates belong to.
DIA_CLINICO = "(f.fecha_hora AT TIME ZONE 'America/Panama')::date"


def _nombre(alias: str) -> str:
    """The full name, derived again: present parts joined by single spaces."""
    return (
        f"concat_ws(' ', NULLIF({alias}.primer_nombre, ''), "
        f"NULLIF({alias}.segundo_nombre, ''), NULLIF({alias}.apellido_paterno, ''), "
        f"NULLIF({alias}.apellido_materno, ''))"
    )


def _telefono(tabla: str, llave: str, alias: str) -> str:
    """The single principal CELULAR contact, or NULL when there is not exactly one."""
    return (
        f"(SELECT CASE WHEN count(*) = 1 THEN max(t.valor_contacto) END "
        f"FROM {O}.{tabla} t WHERE t.{llave} = {alias}.{llave} "
        f"AND t.principal AND t.tipo_contacto = 'CELULAR')"
    )


def _diferencia_de_conjuntos(origen: str, destino: str) -> str:
    """Rows in either projection that are missing from the other."""
    return (
        f"SELECT (SELECT count(*) FROM (({origen}) EXCEPT ({destino})) AS faltantes) "
        f"+ (SELECT count(*) FROM (({destino}) EXCEPT ({origen})) AS sobrantes)"
    )


@dataclass(frozen=True)
class Chequeo:
    nombre: str
    descripcion: str
    sql: str
    bloqueante: bool = True


CHEQUEOS: tuple[Chequeo, ...] = (
    # --- Hechos frente a lecturas ------------------------------------------
    Chequeo(
        "lecturas_sin_hecho",
        "Lecturas operacionales que no están en el hecho.",
        f"SELECT count(*) FROM (SELECT id_lectura FROM {O}.lectura_biometrica "
        f"EXCEPT SELECT id_lectura FROM {A}.fact_lectura_biometrica) AS x",
    ),
    Chequeo(
        "hechos_sin_lectura_origen",
        "Hechos cuyo id_lectura ya no existe en operacional.",
        f"SELECT count(*) FROM (SELECT id_lectura FROM {A}.fact_lectura_biometrica "
        f"EXCEPT SELECT id_lectura FROM {O}.lectura_biometrica) AS x",
    ),
    Chequeo(
        "sesiones_distintas",
        "Diferencia entre el conjunto de id_sesion de las lecturas de origen y el "
        "del hecho.",
        _diferencia_de_conjuntos(
            f"SELECT DISTINCT id_sesion FROM {O}.lectura_biometrica",
            f"SELECT DISTINCT id_sesion FROM {A}.fact_lectura_biometrica",
        ),
    ),
    Chequeo(
        "contenido_divergente",
        "Hechos cuyo contenido copiado difiere del de su lectura, sesión o embarazo.",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica f "
        f"JOIN {O}.lectura_biometrica l ON l.id_lectura = f.id_lectura "
        f"JOIN {O}.sesion_monitoreo s ON s.id_sesion = l.id_sesion "
        f"JOIN {O}.embarazo e ON e.id_embarazo = s.id_embarazo "
        "WHERE (f.id_sesion, f.id_tiempo_gestacional, f.hr_valor, f.spo2_valor, "
        "f.mov_valor, f.fecha_hora, f.id_embarazo, f.id_paciente, f.id_clinica) "
        "IS DISTINCT FROM (l.id_sesion, l.id_tiempo_gest, l.hr_valor, l.spo2_valor, "
        "l.mov_valor, l.fecha_hora_captura, s.id_embarazo, e.id_paciente, e.id_clinica)",
    ),
    Chequeo(
        "semaforo_distinto_del_origen",
        "Hechos cuyo semáforo analítico difiere del registrado en operacional.",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica f "
        f"JOIN {O}.lectura_biometrica l ON l.id_lectura = f.id_lectura "
        "WHERE f.id_semaforo <> l.id_semaforo",
    ),
    Chequeo(
        "semaforo_distinto_de_la_mayor_severidad",
        "Hechos cuyo semáforo global no es el estado más severo de sus métricas.",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica f "
        f"JOIN {A}.dim_semaforo s ON s.id_semaforo = f.id_semaforo "
        "WHERE s.codigo_nivel <> CASE "
        "WHEN 'ERROR' IN (f.estado_hr, f.estado_spo2, f.estado_mov) THEN 'ERROR' "
        "WHEN 'WARNING' IN (f.estado_hr, f.estado_spo2, f.estado_mov) THEN 'WARNING' "
        "ELSE 'OK' END",
    ),
    Chequeo(
        "forma_invalida",
        "Hechos con una combinación de valores y estados que no es ninguna de las "
        "dos formas admitidas.",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica WHERE NOT ("
        "(hr_valor IS NOT NULL AND spo2_valor IS NOT NULL AND mov_valor IS NULL "
        "AND estado_hr IS NOT NULL AND estado_spo2 IS NOT NULL AND estado_mov IS NULL) "
        "OR (mov_valor IS NOT NULL AND hr_valor IS NULL AND spo2_valor IS NULL "
        "AND estado_mov IS NOT NULL AND estado_hr IS NULL AND estado_spo2 IS NULL))",
    ),
    Chequeo(
        "movimiento_antes_de_semana_20",
        "Movimientos fetales en una semana anterior a la mínima admitida.",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica f "
        f"JOIN {A}.dim_tiempo_gestacional t ON t.id_tiempo_gest = f.id_tiempo_gestacional "
        f"WHERE f.mov_valor IS NOT NULL AND t.semana_gestacion < {SEMANA_MINIMA_DE_MOVIMIENTO}",
    ),
    Chequeo(
        "fecha_fuera_del_embarazo",
        "Hechos cuyo día clínico (America/Panama) cae antes del inicio o después "
        "del cierre de su embarazo.",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica f "
        f"JOIN {O}.embarazo e ON e.id_embarazo = f.id_embarazo "
        f"WHERE {DIA_CLINICO} < e.fecha_inicio "
        f"OR (e.fecha_cierre IS NOT NULL AND {DIA_CLINICO} > e.fecha_cierre)",
    ),
    Chequeo(
        "medico_no_resoluble",
        "Hechos cuya fecha clínica no está cubierta por exactamente un seguimiento "
        "PRINCIPAL de su embarazo.",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica f WHERE ("
        f"SELECT count(*) FROM {O}.seguimiento_clinico sc "
        "WHERE sc.id_embarazo = f.id_embarazo AND sc.rol_seguimiento = 'PRINCIPAL' "
        f"AND sc.fecha_asignacion <= {DIA_CLINICO} "
        f"AND (sc.fecha_fin IS NULL OR sc.fecha_fin >= {DIA_CLINICO})) <> 1",
    ),
    Chequeo(
        "medico_distinto_del_responsable",
        "Hechos cuyo médico no es el del seguimiento PRINCIPAL que cubre su fecha "
        "clínica.",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica f WHERE NOT EXISTS ("
        f"SELECT 1 FROM {O}.seguimiento_clinico sc "
        "WHERE sc.id_embarazo = f.id_embarazo AND sc.id_medico = f.id_medico "
        "AND sc.rol_seguimiento = 'PRINCIPAL' "
        f"AND sc.fecha_asignacion <= {DIA_CLINICO} "
        f"AND (sc.fecha_fin IS NULL OR sc.fecha_fin >= {DIA_CLINICO}))",
    ),
    Chequeo(
        "claves_duplicadas",
        "Claves repetidas en el hecho o en el bridge.",
        f"SELECT (SELECT count(*) - count(DISTINCT id_lectura) "
        f"FROM {A}.fact_lectura_biometrica) + (SELECT count(*) - "
        f"count(DISTINCT (id_embarazo, id_factor_riesgo)) "
        f"FROM {A}.bridge_embarazo_factor_riesgo)",
    ),
    Chequeo(
        "huerfanos_del_hecho",
        "Hechos que apuntan a una fila inexistente de alguna dimensión.",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica f "
        f"LEFT JOIN {A}.dim_paciente p ON p.id_paciente = f.id_paciente "
        f"LEFT JOIN {A}.dim_medico m ON m.id_medico = f.id_medico "
        f"LEFT JOIN {A}.dim_clinica c ON c.id_clinica = f.id_clinica "
        f"LEFT JOIN {A}.dim_tiempo_gestacional t ON t.id_tiempo_gest = f.id_tiempo_gestacional "
        f"LEFT JOIN {A}.dim_embarazo e ON e.id_embarazo = f.id_embarazo "
        f"LEFT JOIN {A}.dim_semaforo s ON s.id_semaforo = f.id_semaforo "
        "WHERE p.id_paciente IS NULL OR m.id_medico IS NULL OR c.id_clinica IS NULL "
        "OR t.id_tiempo_gest IS NULL OR e.id_embarazo IS NULL OR s.id_semaforo IS NULL",
    ),
    Chequeo(
        "afiliacion_no_aplicable",
        "Hechos cuyo médico no tiene exactamente una afiliación que cubra su fecha "
        "clínica (America/Panama), o la tiene con una clínica distinta de la del "
        "embarazo.",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica f WHERE ("
        f"SELECT count(*) FROM {O}.medico_clinica mc WHERE mc.id_medico = f.id_medico "
        f"AND mc.fecha_inicio <= {DIA_CLINICO} "
        f"AND (mc.fecha_final IS NULL OR mc.fecha_final >= {DIA_CLINICO})) <> 1 "
        f"OR NOT EXISTS (SELECT 1 FROM {O}.medico_clinica mc "
        "WHERE mc.id_medico = f.id_medico AND mc.id_clinica = f.id_clinica "
        f"AND mc.fecha_inicio <= {DIA_CLINICO} "
        f"AND (mc.fecha_final IS NULL OR mc.fecha_final >= {DIA_CLINICO}))",
    ),
    Chequeo(
        "huerfanos_de_dimensiones",
        "Filas de dimensiones o del bridge que apuntan a otra dimensión inexistente. "
        "Una clínica contextual en NULL es ausencia de relación, no un huérfano.",
        f"SELECT (SELECT count(*) FROM {A}.dim_medico m LEFT JOIN {A}.dim_clinica c "
        "ON c.id_clinica = m.id_clinica "
        "WHERE m.id_clinica IS NOT NULL AND c.id_clinica IS NULL) "
        f"+ (SELECT count(*) FROM {A}.dim_paciente p LEFT JOIN {A}.dim_clinica c "
        "ON c.id_clinica = p.id_clinica "
        "WHERE p.id_clinica IS NOT NULL AND c.id_clinica IS NULL) "
        f"+ (SELECT count(*) FROM {A}.dim_embarazo e LEFT JOIN {A}.dim_paciente p "
        "ON p.id_paciente = e.id_paciente WHERE p.id_paciente IS NULL) "
        f"+ (SELECT count(*) FROM {A}.bridge_embarazo_factor_riesgo b "
        f"LEFT JOIN {A}.dim_embarazo e ON e.id_embarazo = b.id_embarazo "
        f"LEFT JOIN {A}.dim_factor_riesgo r ON r.id_factor_riesgo = b.id_factor_riesgo "
        "WHERE e.id_embarazo IS NULL OR r.id_factor_riesgo IS NULL)",
    ),
    # --- Dimensiones y bridge frente a su origen -----------------------------
    Chequeo(
        "dim_clinica_distinta_del_origen",
        "Filas de Dim_Clinica que faltan, sobran o difieren del origen.",
        _diferencia_de_conjuntos(
            f"SELECT id_clinica, nombre_clinica, provincia, distrito FROM {O}.clinica",
            f"SELECT id_clinica, nombre_clinica, provincia, distrito FROM {A}.dim_clinica",
        ),
    ),
    Chequeo(
        "dim_semaforo_distinta_del_origen",
        "Filas de Dim_Semaforo que faltan, sobran o difieren del origen.",
        _diferencia_de_conjuntos(
            "SELECT id_semaforo, codigo_nivel, etiqueta_visual, color_hex, prioridad, "
            f"mensaje_app, version_referencia FROM {O}.semaforo",
            "SELECT id_semaforo, codigo_nivel, etiqueta_visual, color_hex, prioridad, "
            f"mensaje_app, version_referencia FROM {A}.dim_semaforo",
        ),
    ),
    Chequeo(
        "catalogo_semaforo_incoherente",
        "Niveles de Dim_Semaforo cuya prioridad no sigue OK < WARNING < ERROR.",
        f"SELECT count(*) FROM {A}.dim_semaforo WHERE (codigo_nivel, prioridad) NOT IN "
        "(('OK', 1), ('WARNING', 2), ('ERROR', 3))",
    ),
    Chequeo(
        "dim_tiempo_gestacional_distinta_del_origen",
        "Filas de Dim_TiempoGestacional que faltan, sobran o difieren del origen.",
        _diferencia_de_conjuntos(
            "SELECT id_tiempo_gest, semana_gestacion, mes_gestacion, trimestre, "
            f"descripcion FROM {O}.tiempo_gestacional",
            "SELECT id_tiempo_gest, semana_gestacion, mes_gestacion, trimestre, "
            f"descripcion FROM {A}.dim_tiempo_gestacional",
        ),
    ),
    Chequeo(
        "dim_factor_riesgo_distinta_del_origen",
        "Filas de Dim_FactorRiesgo que faltan, sobran o difieren del origen.",
        _diferencia_de_conjuntos(
            "SELECT id_factor_riesgo, clave_factor, nombre_factor, descripcion, activo "
            f"FROM {O}.factor_riesgo",
            "SELECT id_factor_riesgo, clave_factor, nombre_factor, descripcion, activo "
            f"FROM {A}.dim_factor_riesgo",
        ),
    ),
    Chequeo(
        "dim_medico_distinta_del_origen",
        "Filas de Dim_Medico que faltan, sobran o difieren de lo que el origen "
        "determina (nombre, especialidad, correo, teléfono principal y clínica).",
        _diferencia_de_conjuntos(
            f"SELECT m.id_medico, (SELECT CASE WHEN count(DISTINCT mc.id_clinica) = 1 "
            f"THEN max(mc.id_clinica) END FROM {O}.medico_clinica mc "
            f"WHERE mc.id_medico = m.id_medico), {_nombre('m')}, es.nombre_especialidad, "
            f"m.email_med, {_telefono('telefono_medico', 'id_medico', 'm')} "
            f"FROM {O}.medico m JOIN {O}.especialidad es "
            "ON es.id_especialidad = m.id_especialidad",
            "SELECT id_medico, id_clinica, nombre_completo, especialidad, email_med, "
            f"telefono_med FROM {A}.dim_medico",
        ),
    ),
    Chequeo(
        "dim_paciente_distinta_del_origen",
        "Filas de Dim_Paciente que faltan, sobran o difieren de lo que el origen "
        "determina (cédula, nombre, teléfono principal, nacimiento y clínica).",
        _diferencia_de_conjuntos(
            f"SELECT p.id_paciente, (SELECT CASE WHEN count(DISTINCT e.id_clinica) = 1 "
            f"THEN max(e.id_clinica) END FROM {O}.embarazo e "
            f"WHERE e.id_paciente = p.id_paciente), p.cedula, {_nombre('p')}, "
            f"{_telefono('telefono_paciente', 'id_paciente', 'p')}, p.fecha_nac "
            f"FROM {O}.paciente p",
            "SELECT id_paciente, id_clinica, cedula, nombre_completo, telefono_pac, "
            f"fecha_nac FROM {A}.dim_paciente",
        ),
    ),
    Chequeo(
        "dim_embarazo_distinta_del_origen",
        "Filas de Dim_Embarazo que faltan, sobran o difieren del origen, incluida "
        "la duración estimada en semanas.",
        _diferencia_de_conjuntos(
            "SELECT id_embarazo, id_paciente, numero_gestas, numero_partos, "
            "estado_embarazo, fecha_inicio, fecha_probable_parto, fecha_cierre, "
            f"(fecha_probable_parto - fecha_inicio) / 7 FROM {O}.embarazo "
            "WHERE (fecha_probable_parto - fecha_inicio) % 7 = 0",
            "SELECT id_embarazo, id_paciente, numero_gestas, numero_partos, "
            "estado_embarazo, fecha_inicio, fecha_probable_parto, fecha_cierre, "
            f"duracion_est_semanas FROM {A}.dim_embarazo",
        ),
    ),
    Chequeo(
        "bridge_distinto_del_origen",
        "Relaciones embarazo/factor que faltan, sobran o difieren del origen. Una "
        "relación que desaparece del origen es una inconsistencia: no se borra.",
        _diferencia_de_conjuntos(
            "SELECT id_embarazo, id_factor_riesgo, fecha_diagnostico, activo, "
            f"observaciones FROM {O}.embarazo_factor_riesgo",
            "SELECT id_embarazo, id_factor_riesgo, fecha_diagnostico, activo, "
            f"observaciones FROM {A}.bridge_embarazo_factor_riesgo",
        ),
    ),
    # --- Informativas: se reportan, no bloquean -------------------------------
    Chequeo(
        "clasificacion_embarazo_pendiente",
        "Embarazos sin clasificación: el modelo v6 prevé el atributo, pero las "
        "fuentes de negocio aún no definen su regla.",
        f"SELECT count(*) FROM {A}.dim_embarazo WHERE clasificacion_embarazo IS NULL",
        bloqueante=False,
    ),
    Chequeo(
        "semana_recalculada_distinta",
        "Hechos cuya semana recalculada con el día clínico de Panamá no coincide "
        "con la del catálogo. Diagnóstico: la API validó la semana con el offset "
        "recibido, que TIMESTAMPTZ no conserva.",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica f "
        f"JOIN {A}.dim_tiempo_gestacional t ON t.id_tiempo_gest = f.id_tiempo_gestacional "
        f"JOIN {O}.embarazo e ON e.id_embarazo = f.id_embarazo "
        f"WHERE ({DIA_CLINICO} - e.fecha_inicio) / 7 + 1 <> t.semana_gestacion",
        bloqueante=False,
    ),
    Chequeo(
        "sesiones_sin_lecturas",
        "Sesiones operacionales sin ninguna lectura: no generan hechos, porque el "
        "grano del hecho es la lectura.",
        f"SELECT count(*) FROM {O}.sesion_monitoreo s WHERE NOT EXISTS ("
        f"SELECT 1 FROM {O}.lectura_biometrica l WHERE l.id_sesion = s.id_sesion)",
        bloqueante=False,
    ),
)

# Totals shown next to the checks: what was reconciled, not whether it matched.
CONTEOS: tuple[tuple[str, str], ...] = (
    ("lecturas_origen", f"SELECT count(*) FROM {O}.lectura_biometrica"),
    ("hechos", f"SELECT count(*) FROM {A}.fact_lectura_biometrica"),
    (
        "sesiones_con_lecturas_origen",
        f"SELECT count(DISTINCT id_sesion) FROM {O}.lectura_biometrica",
    ),
    ("sesiones_en_hecho", f"SELECT count(DISTINCT id_sesion) FROM {A}.fact_lectura_biometrica"),
    ("sesiones_monitoreo", f"SELECT count(*) FROM {O}.sesion_monitoreo"),
    (
        "hechos_signos_maternos",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica WHERE hr_valor IS NOT NULL",
    ),
    (
        "hechos_movimiento",
        f"SELECT count(*) FROM {A}.fact_lectura_biometrica WHERE mov_valor IS NOT NULL",
    ),
    ("dim_clinica", f"SELECT count(*) FROM {A}.dim_clinica"),
    ("dim_semaforo", f"SELECT count(*) FROM {A}.dim_semaforo"),
    ("dim_tiempo_gestacional", f"SELECT count(*) FROM {A}.dim_tiempo_gestacional"),
    ("dim_factor_riesgo", f"SELECT count(*) FROM {A}.dim_factor_riesgo"),
    ("dim_medico", f"SELECT count(*) FROM {A}.dim_medico"),
    ("dim_paciente", f"SELECT count(*) FROM {A}.dim_paciente"),
    ("dim_embarazo", f"SELECT count(*) FROM {A}.dim_embarazo"),
    (
        "bridge_embarazo_factor_riesgo",
        f"SELECT count(*) FROM {A}.bridge_embarazo_factor_riesgo",
    ),
)


@dataclass(frozen=True)
class Verificacion:
    nombre: str
    descripcion: str
    discrepancias: int
    bloqueante: bool

    @property
    def correcta(self) -> bool:
        return not self.bloqueante or self.discrepancias == 0


@dataclass(frozen=True)
class InformeDeConciliacion:
    verificaciones: tuple[Verificacion, ...]
    conteos: Mapping[str, int] = field(default_factory=dict)
    semaforo: Mapping[str, int] = field(default_factory=dict)
    estados_por_metrica: Mapping[str, Mapping[str, int]] = field(default_factory=dict)

    @property
    def correcta(self) -> bool:
        return all(verificacion.correcta for verificacion in self.verificaciones)

    @property
    def fallidas(self) -> tuple[Verificacion, ...]:
        return tuple(v for v in self.verificaciones if not v.correcta)

    def discrepancias(self, nombre: str) -> int:
        return next(v.discrepancias for v in self.verificaciones if v.nombre == nombre)


def _distribucion(conexion: Connection, sql: str) -> Mapping[str, int]:
    return MappingProxyType(
        {codigo: cantidad for codigo, cantidad in conexion.execute(text(sql)) if codigo}
    )


def conciliar(conexion: Connection) -> InformeDeConciliacion:
    """Run every check and collect the totals, inside the caller's transaction."""
    verificaciones = tuple(
        Verificacion(
            nombre=chequeo.nombre,
            descripcion=chequeo.descripcion,
            discrepancias=int(conexion.execute(text(chequeo.sql)).scalar_one()),
            bloqueante=chequeo.bloqueante,
        )
        for chequeo in CHEQUEOS
    )
    conteos = MappingProxyType(
        {nombre: int(conexion.execute(text(sql)).scalar_one()) for nombre, sql in CONTEOS}
    )
    semaforo = _distribucion(
        conexion,
        f"SELECT s.codigo_nivel, count(*) FROM {A}.fact_lectura_biometrica f "
        f"JOIN {A}.dim_semaforo s ON s.id_semaforo = f.id_semaforo GROUP BY 1",
    )
    estados = MappingProxyType(
        {
            metrica: _distribucion(
                conexion,
                f"SELECT {metrica}, count(*) FROM {A}.fact_lectura_biometrica "
                f"WHERE {metrica} IS NOT NULL GROUP BY 1",
            )
            for metrica in ("estado_hr", "estado_spo2", "estado_mov")
        }
    )
    return InformeDeConciliacion(
        verificaciones=verificaciones,
        conteos=conteos,
        semaforo=semaforo,
        estados_por_metrica=estados,
    )
