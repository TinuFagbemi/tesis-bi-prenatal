"""Orchestration of one ETL run, and the summary it prints.

One run is one unit of work, and it either commits whole or leaves nothing:

1. a single **REPEATABLE READ** transaction is opened;
2. its **first statement** takes the transaction-level advisory lock with
   ``pg_try_advisory_xact_lock``. A second run that finds it taken stops at
   once -- it does not wait, and it has read and written nothing;
3. inside the same transaction: preconditions (PostgreSQL at the Alembic head),
   extraction of the dimensions, type 1 upserts, the bridge, the anti-join of
   new readings, classification, the comparison of the derived traffic light
   with the operational one, the plain INSERT of the new facts and the
   **complete reconciliation**, which sees the same snapshot plus what was
   just written;
4. commit only if everything held; any error or discrepancy rolls back the
   whole run, dimensions and bridge included;
5. PostgreSQL releases the lock by itself at that commit or rollback. There is
   no ``pg_advisory_unlock`` anywhere, so no code path can leave it held.

**The residual window, and why it is safe.** Under REPEATABLE READ the
snapshot is fixed by the first statement -- the lock itself. A run that starts
exactly while another is committing may take its snapshot an instant before
that commit becomes visible and acquire the lock an instant after it is
released. Its anti-join would then select readings the other run has just
inserted, and the plain INSERT would hit their primary keys: the run fails and
rolls back, nothing is duplicated, and running it again succeeds.

There is no checkpoint table: the state of progress *is* the set of keys the
fact holds, and it only moves when the transaction commits.

**Two read-only companions.** :func:`conciliar_sin_escribir` repeats the
reconciliation, and :func:`previsualizar_sin_escribir` (SCRUM-104) repeats the
extraction and the transformation of a run and stops before loading. Both open
a REPEATABLE READ transaction declared READ ONLY and neither takes the lock, so
they can run at any time and cannot write: PostgreSQL itself refuses.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from app.db.base import SCHEMA_OPERACIONAL

from app.etl import carga, conciliacion, extraccion, transformacion
from app.etl.carga import CargaInconsistente, ResultadoUpsert
from app.etl.conciliacion import InformeDeConciliacion
from app.etl.modelos import (
    BridgeEmbarazoFactorRiesgo,
    DimClinica,
    DimEmbarazo,
    DimFactorRiesgo,
    DimMedico,
    DimPaciente,
    DimSemaforo,
    DimTiempoGestacional,
)
from app.etl.reglas import VERSION_SIM_1_0, ErrorDeEtl, fecha_clinica
from app.loader.postgres import preflight, sanear_mensaje

# Key of the advisory lock that serialises runs. Any bigint works as long as
# nothing else in the database uses it: 69 is the ticket, 1 the lock's purpose.
CLAVE_DEL_CANDADO = 690_001

AISLAMIENTO = "REPEATABLE READ"

RESULTADO_EXITO = "SUCCESS"
RESULTADO_FALLO = "FAILED"

__all__ = [
    "AISLAMIENTO",
    "AVISO_DE_CANDIDATA",
    "CLAVE_DEL_CANDADO",
    "RESULTADO_EXITO",
    "RESULTADO_FALLO",
    "RESULTADO_PREVISUALIZACION",
    "CandadoOcupado",
    "CandidataDeCarga",
    "CargaInconsistente",
    "ConciliacionFallida",
    "Previsualizacion",
    "ResultadoEjecucion",
    "candado_ocupado",
    "conciliar_sin_escribir",
    "describir_error_de_base",
    "ejecutar_etl",
    "formatear_conciliacion",
    "formatear_previsualizacion",
    "formatear_resumen",
    "previsualizar_sin_escribir",
]


class CandadoOcupado(ErrorDeEtl):
    """Another run holds the lock."""


class ConciliacionFallida(ErrorDeEtl):
    """The complete reconciliation found blocking discrepancies."""

    def __init__(self, informe: InformeDeConciliacion) -> None:
        self.informe = informe
        fallidas = ", ".join(
            f"{v.nombre}={v.discrepancias}" for v in informe.fallidas
        )
        super().__init__(
            f"La conciliación encontró diferencias ({fallidas}). La ejecución "
            "completa se revirtió: no se conservó ningún cambio."
        )


@dataclass(frozen=True)
class ResultadoEjecucion:
    """Everything a committed run observed."""

    revision: str
    version_umbrales: str
    dimensiones: tuple[ResultadoUpsert, ...]
    bridge: ResultadoUpsert
    hechos_nuevos: int
    hechos_existentes: int
    informe: InformeDeConciliacion
    duracion_s: float
    # Seudonimos emitidos en *esta* ejecucion, por tabla del mapa privado. En
    # una carga base son todos; en una incremental, solo los de las filas
    # aparecidas desde la anterior, y lo normal es cero. Que el numero baje a
    # cero es la senal observable de que los seudonimos se estan reutilizando.
    seudonimos_emitidos: dict[str, int]


# Tablas del universo operacional que el ETL extrae. La lista vive aqui porque
# es la que hay que auditar, no la que hay que leer: la extraccion las nombra por
# su cuenta.
TABLAS_DE_ORIGEN = (
    "clinica", "embarazo", "embarazo_factor_riesgo", "especialidad",
    "factor_riesgo", "lectura_biometrica", "medico", "medico_clinica",
    "paciente", "seguimiento_clinico", "semaforo", "sesion_monitoreo",
    "telefono_medico", "telefono_paciente", "tiempo_gestacional",
)


class OrigenPodriaFiltrarse(ErrorDeEtl):
    """Alguna tabla de origen podria devolver menos filas de las que tiene."""


# Forma que debe tener la proteccion de cada tabla de origen para que el ETL
# lea el universo completo. Se lee del catalogo, no de los datos.
_FORMA_DE_LAS_POLITICAS = text(
    """
    SELECT c.relname AS tabla,
           c.relrowsecurity AS con_rls,
           count(*) FILTER (
               WHERE p.polpermissive
                 AND p.polcmd IN ('r', '*')
                 AND pg_catalog.pg_get_expr(p.polqual, p.polrelid) IS NOT DISTINCT FROM 'true'
                 AND EXISTS (
                     SELECT 1 FROM unnest(p.polroles) AS r(oid)
                     WHERE r.oid = 0 OR pg_has_role(current_user, r.oid, 'USAGE')
                 )
           ) AS permisivas_totales,
           count(*) FILTER (
               WHERE NOT p.polpermissive
                 AND p.polcmd IN ('r', '*')
                 AND EXISTS (
                     SELECT 1 FROM unnest(p.polroles) AS r(oid)
                     WHERE r.oid = 0 OR pg_has_role(current_user, r.oid, 'USAGE')
                 )
           ) AS restrictivas
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_policy p ON p.polrelid = c.oid
    WHERE n.nspname = :esquema AND c.relname = ANY(:tablas)
    GROUP BY c.relname, c.relrowsecurity
    """
)


def verificar_lectura_completa(conexion: Connection) -> None:
    """Aborta si alguna tabla de origen pudiera entregar un subconjunto.

    **Por que no basta con la conciliacion.** El informe compara ``operacional``
    con ``analitico`` dentro de la misma transaccion. Si una politica filtrara
    el origen, la carga base escribiria ese mismo subconjunto y las dos consultas
    coincidirian: cero discrepancias, cero datos y el ETL terminando en verde.
    Lo que se comprueba aqui no son filas, es la configuracion -- que es donde
    vive ese fallo.

    **Por que es una demostracion y no una heuristica.** PostgreSQL combina las
    politicas permisivas con OR y las restrictivas con AND. Una permisiva cuyo
    qualifier sea literalmente ``true``, aplicable a este rol, sin ninguna
    restrictiva aplicable, implica que ninguna fila puede ocultarse. Una tabla
    sin RLS tampoco puede ocultar nada.

    Se consideran aplicables las politicas dirigidas a ``PUBLIC`` (OID 0) y las
    dirigidas a cualquier rol que este usuario herede, no solo las que lleven su
    nombre: una membresia tambien alcanza.

    No lee una sola fila de negocio.
    """
    filas = conexion.execute(
        _FORMA_DE_LAS_POLITICAS,
        {"esquema": SCHEMA_OPERACIONAL, "tablas": list(TABLAS_DE_ORIGEN)},
    ).mappings().all()

    vistas = {fila["tabla"] for fila in filas}
    ausentes = sorted(set(TABLAS_DE_ORIGEN) - vistas)
    if ausentes:
        raise OrigenPodriaFiltrarse(
            "No se encontraron en el catalogo las tablas de origen "
            + ", ".join(ausentes)
            + ". El ETL no puede afirmar que lee el universo completo."
        )

    problemas = []
    for fila in filas:
        if not fila["con_rls"]:
            continue
        if fila["restrictivas"]:
            problemas.append(
                f"{fila['tabla']} tiene {fila['restrictivas']} politica(s) "
                "restrictiva(s) aplicables a este rol"
            )
        elif not fila["permisivas_totales"]:
            problemas.append(
                f"{fila['tabla']} tiene RLS y ninguna politica permisiva con "
                "qualifier 'true' aplicable a este rol"
            )

    if problemas:
        raise OrigenPodriaFiltrarse(
            "El ETL podria estar leyendo un subconjunto: "
            + "; ".join(problemas)
            + ". Se aborta antes de extraer: una carga incompleta que concilia "
            "consigo misma terminaria en verde."
        )


def _tomar_candado(conexion: Connection) -> None:
    """First statement of the transaction: the transaction-level lock.

    ``pg_try_advisory_xact_lock`` does not wait: it answers at once whether the
    lock was free. PostgreSQL holds it until this transaction ends -- commit or
    rollback -- and releases it by itself.
    """
    obtenido = conexion.execute(
        select(func.pg_try_advisory_xact_lock(CLAVE_DEL_CANDADO))
    ).scalar_one()
    if not obtenido:
        raise CandadoOcupado(
            "Otra ejecución del ETL tiene el candado. Esta se detiene sin leer ni "
            "escribir datos; vuelve a ejecutarla cuando la otra termine."
        )


def _preparar_dimensiones(conexion: Connection, *, version: str) -> tuple:
    """Extract and transform the dimensions. Reads only; shared with the preview."""
    origen = extraccion.extraer_dimensiones(conexion)
    return origen, transformacion.transformar_dimensiones(origen, version=version)


def _preparar_hechos(
    conexion: Connection, origen, dimensiones, *, version: str
) -> tuple[list, list]:
    """Select the new readings and transform them into facts. Reads only.

    Shared by the run and the preview, so the two cannot select or classify
    differently: there is one sequence of extraction and transformation.
    """
    lecturas = extraccion.extraer_lecturas_nuevas(conexion)
    seguimientos = extraccion.extraer_seguimientos_principales(
        conexion, (lectura["id_embarazo"] for lectura in lecturas)
    )
    hechos = transformacion.transformar_hechos(
        lecturas,
        seguimientos,
        origen.afiliaciones_de_medico,
        dimensiones.id_semaforo_por_codigo,
        version=version,
    )
    return lecturas, hechos


def _cargar(conexion: Connection, *, version: str) -> tuple:
    """The body of a run, inside the transaction that already holds the lock."""
    revision = preflight(conexion)

    origen, dimensiones = _preparar_dimensiones(conexion, version=version)
    resultados = tuple(
        carga.upsert_tipo_1(conexion, modelo.__table__, filas)
        for modelo, filas in (
            (DimClinica, dimensiones.clinicas),
            (DimSemaforo, dimensiones.semaforos),
            (DimTiempoGestacional, dimensiones.tiempos_gestacionales),
            (DimFactorRiesgo, dimensiones.factores_riesgo),
            (DimMedico, dimensiones.medicos),
            (DimPaciente, dimensiones.pacientes),
            (DimEmbarazo, dimensiones.embarazos),
        )
    )
    bridge = carga.upsert_tipo_1(
        conexion, BridgeEmbarazoFactorRiesgo.__table__, dimensiones.bridge
    )

    existentes = extraccion.contar_hechos(conexion)
    lecturas, hechos = _preparar_hechos(conexion, origen, dimensiones, version=version)
    nuevos = carga.insertar_hechos(conexion, hechos)
    if nuevos != len(lecturas):
        raise CargaInconsistente(
            f"El hecho aceptó {nuevos} filas y el anti-join detectó {len(lecturas)} "
            "lecturas nuevas. La ejecución se revierte."
        )

    # Los seudonimos, despues de cargar las dimensiones y antes de conciliar.
    # Despues, porque un embarazo que el ETL rechazara no debe recibir uno;
    # antes, porque la conciliacion ya puede contar con que la publicacion esta
    # completa. Es idempotente: la carga base los emite y cada incremental
    # reutiliza los mismos.
    seudonimos = carga.emitir_seudonimos(conexion)

    informe = conciliacion.conciliar(conexion)
    if not informe.correcta:
        raise ConciliacionFallida(informe)

    return revision, resultados, bridge, nuevos, existentes, informe, seudonimos


def ejecutar_etl(
    engine: Engine,
    *,
    version: str = VERSION_SIM_1_0,
    reloj: Callable[[], float] = time.perf_counter,
) -> ResultadoEjecucion:
    """Run the ETL once: commit everything or nothing."""
    inicio = reloj()
    with engine.connect() as conexion:
        conexion.execution_options(isolation_level=AISLAMIENTO)
        # Leaving this block with an exception -- CandadoOcupado included --
        # rolls the transaction back, and the lock goes with it.
        with conexion.begin():
            _tomar_candado(conexion)
            # Despues del candado -- que sigue siendo la primera sentencia -- y
            # antes de extraer: si el rol pudiera ver menos de lo que hay, la
            # carga entera seria un subconjunto silencioso.
            verificar_lectura_completa(conexion)
            (
                revision,
                resultados,
                bridge,
                nuevos,
                existentes,
                informe,
                seudonimos,
            ) = _cargar(conexion, version=version)

    return ResultadoEjecucion(
        revision=revision,
        version_umbrales=version,
        dimensiones=resultados,
        bridge=bridge,
        hechos_nuevos=nuevos,
        hechos_existentes=existentes,
        informe=informe,
        duracion_s=reloj() - inicio,
        seudonimos_emitidos=seudonimos,
    )


def conciliar_sin_escribir(engine: Engine) -> InformeDeConciliacion:
    """The complete reconciliation in a read-only REPEATABLE READ transaction.

    It takes no lock and writes nothing -- PostgreSQL refuses any write inside a
    read-only transaction -- so it can run at any time, even during a load.
    """
    with engine.connect() as conexion:
        conexion.execution_options(
            isolation_level=AISLAMIENTO, postgresql_readonly=True
        )
        with conexion.begin():
            preflight(conexion)
            return conciliacion.conciliar(conexion)


# ---------------------------------------------------------------------------
# Previsualizacion de la proxima carga (SCRUM-104)
# ---------------------------------------------------------------------------

# Lo que la previsualizacion puede afirmar y lo que no. Una lectura que las
# reglas transforman sin error es una candidata: el commit de la ejecucion real
# todavia depende de la carga, de los seudonimos, de la conciliacion completa y
# de que nada cambie entre esta instantanea y esa ejecucion.
AVISO_DE_CANDIDATA = (
    "Una candidata no es un hecho confirmado: la ejecución real todavía puede "
    "revertirse en la carga, en los seudónimos o en la conciliación, y el origen "
    "puede cambiar entre esta instantánea y la ejecución."
)

# El candado del ETL leido de ``pg_locks``, sin pedirlo. Una clave bigint se
# reparte en ``classid`` (32 bits altos) y ``objid`` (32 bits bajos), con
# ``objsubid = 1``. El ``CAST`` a bigint no es decorativo: el driver envia un
# entero pequeno como int4, y en int4 un desplazamiento de 32 bits no desplaza
# nada.
_CANDADO_TOMADO = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM pg_catalog.pg_locks
        WHERE locktype = 'advisory'
          AND granted
          AND database = (SELECT oid FROM pg_catalog.pg_database
                          WHERE datname = current_database())
          AND classid = (CAST(:clave AS bigint) >> 32)::oid
          AND objid = (CAST(:clave AS bigint) & 4294967295)::oid
          AND objsubid = 1
    )
    """
)


def candado_ocupado(conexion: Connection) -> bool:
    """Si otra transaccion tiene ahora el candado del ETL. No lo pide.

    Solo consulta ``pg_locks``: no llama a ninguna funcion ``pg_*advisory*``,
    asi que no puede retrasar ni bloquear una ejecucion real.
    """
    return bool(
        conexion.execute(_CANDADO_TOMADO, {"clave": CLAVE_DEL_CANDADO}).scalar_one()
    )


@dataclass(frozen=True)
class CandidataDeCarga:
    """Una lectura que la proxima ejecucion intentaria cargar.

    Solo identificadores, contexto y clasificaciones: ningun valor biometrico
    ni dato personal.
    """

    id_lectura: int
    id_sesion: int
    id_embarazo: int
    id_clinica: int
    id_medico: int
    semana_gestacion: int
    trimestre: int
    fecha_clinica: str
    estado_hr: str | None
    estado_spo2: str | None
    estado_mov: str | None
    semaforo_derivado: str
    semaforo_origen: str
    embarazo_en_dimension: bool


@dataclass(frozen=True)
class Previsualizacion:
    """Lo que la proxima ejecucion procesaria, observado sin escribir."""

    revision: str
    version_umbrales: str
    instantanea: str
    candado_ocupado: bool
    hechos_existentes: int
    filas_de_dimension: tuple[tuple[str, int], ...]
    filas_de_bridge: int
    candidatas: tuple[CandidataDeCarga, ...]


def previsualizar_sin_escribir(
    engine: Engine, *, version: str = VERSION_SIM_1_0
) -> Previsualizacion:
    """Extract and transform exactly as a run would, and stop before loading.

    Same transaction shape as :func:`conciliar_sin_escribir`: REPEATABLE READ
    and READ ONLY, so PostgreSQL itself refuses any write -- an INSERT, an
    UPDATE, a ``nextval`` -- and the preview takes no lock, so it never delays
    nor blocks a real run. The extraction and the transformation are the ones
    the run uses (:func:`_preparar_dimensiones`, :func:`_preparar_hechos`); no
    rule is restated here.

    A source the rules refuse raises the same :class:`ErrorDeDatos` the run
    would raise, before anything could have been written.
    """
    with engine.connect() as conexion:
        conexion.execution_options(
            isolation_level=AISLAMIENTO, postgresql_readonly=True
        )
        with conexion.begin():
            ocupado = candado_ocupado(conexion)
            instantanea = conexion.execute(
                text("SELECT transaction_timestamp()")
            ).scalar_one()
            verificar_lectura_completa(conexion)
            revision = preflight(conexion)

            origen, dimensiones = _preparar_dimensiones(conexion, version=version)
            existentes = extraccion.contar_hechos(conexion)
            lecturas, hechos = _preparar_hechos(
                conexion, origen, dimensiones, version=version
            )

            ids_embarazo = sorted({hecho["id_embarazo"] for hecho in hechos})
            en_dimension: set[int] = set()
            if ids_embarazo:
                tabla = DimEmbarazo.__table__
                en_dimension = set(
                    conexion.execute(
                        select(tabla.c.id_embarazo).where(
                            tabla.c.id_embarazo.in_(ids_embarazo)
                        )
                    ).scalars()
                )

    codigo_de = {
        identificador: codigo
        for codigo, identificador in dimensiones.id_semaforo_por_codigo.items()
    }
    lectura_de = {lectura["id_lectura"]: lectura for lectura in lecturas}
    candidatas = []
    for hecho in hechos:
        lectura = lectura_de[hecho["id_lectura"]]
        candidatas.append(
            CandidataDeCarga(
                id_lectura=hecho["id_lectura"],
                id_sesion=hecho["id_sesion"],
                id_embarazo=hecho["id_embarazo"],
                id_clinica=hecho["id_clinica"],
                id_medico=hecho["id_medico"],
                semana_gestacion=lectura["semana_gestacion"],
                trimestre=lectura["trimestre"],
                fecha_clinica=fecha_clinica(lectura["fecha_hora_captura"]).isoformat(),
                estado_hr=hecho["estado_hr"],
                estado_spo2=hecho["estado_spo2"],
                estado_mov=hecho["estado_mov"],
                # El derivado es el nivel que la transformacion asigno al hecho;
                # el de origen, el que la lectura ya trae en operacional. Son
                # dos fuentes distintas: si no coincidieran, la transformacion
                # habria rechazado el origen antes de llegar aqui.
                semaforo_derivado=codigo_de[hecho["id_semaforo"]],
                semaforo_origen=codigo_de[lectura["id_semaforo"]],
                embarazo_en_dimension=hecho["id_embarazo"] in en_dimension,
            )
        )

    filas_de_dimension = tuple(
        (modelo.__tablename__, len(filas))
        for modelo, filas in (
            (DimClinica, dimensiones.clinicas),
            (DimSemaforo, dimensiones.semaforos),
            (DimTiempoGestacional, dimensiones.tiempos_gestacionales),
            (DimFactorRiesgo, dimensiones.factores_riesgo),
            (DimMedico, dimensiones.medicos),
            (DimPaciente, dimensiones.pacientes),
            (DimEmbarazo, dimensiones.embarazos),
        )
    )
    return Previsualizacion(
        revision=revision,
        version_umbrales=version,
        instantanea=instantanea.isoformat(),
        candado_ocupado=ocupado,
        hechos_existentes=existentes,
        filas_de_dimension=filas_de_dimension,
        filas_de_bridge=len(dimensiones.bridge),
        candidatas=tuple(candidatas),
    )


# ---------------------------------------------------------------------------
# Salida segura
# ---------------------------------------------------------------------------

# Only identifiers, counts, codes and names of checks are ever printed: never a
# name, an identity number, a phone, an email, a biometric value or a URL.


def _codigos(distribucion) -> str:
    return " ".join(
        f"{codigo}={distribucion.get(codigo, 0)}" for codigo in ("OK", "WARNING", "ERROR")
    )


def formatear_conciliacion(informe: InformeDeConciliacion) -> str:
    conteos = informe.conteos
    lineas = [
        f"conciliacion={'OK' if informe.correcta else 'FALLIDA'} "
        f"verificaciones={len(informe.verificaciones)} "
        f"fallidas={len(informe.fallidas)}",
        f"lecturas_origen={conteos.get('lecturas_origen', 0)} "
        f"hechos={conteos.get('hechos', 0)}",
        f"sesiones_con_lecturas_origen={conteos.get('sesiones_con_lecturas_origen', 0)} "
        f"sesiones_en_hecho={conteos.get('sesiones_en_hecho', 0)} "
        f"sesiones_monitoreo={conteos.get('sesiones_monitoreo', 0)}",
        f"forma_signos_maternos={conteos.get('hechos_signos_maternos', 0)} "
        f"forma_movimiento={conteos.get('hechos_movimiento', 0)}",
        f"semaforo: {_codigos(informe.semaforo)}",
    ]
    lineas += [
        f"{metrica}: {_codigos(distribucion)}"
        for metrica, distribucion in informe.estados_por_metrica.items()
    ]
    lineas += [
        f"{nombre}={conteos.get(nombre, 0)}"
        for nombre in (
            "dim_clinica",
            "dim_semaforo",
            "dim_tiempo_gestacional",
            "dim_factor_riesgo",
            "dim_medico",
            "dim_paciente",
            "dim_embarazo",
            "bridge_embarazo_factor_riesgo",
        )
    ]
    lineas += [
        f"{v.nombre}={v.discrepancias}"
        for v in informe.verificaciones
        if not v.bloqueante
    ]
    lineas += [
        f"DISCREPANCIA {v.nombre}={v.discrepancias}: {v.descripcion}"
        for v in informe.fallidas
    ]
    return "\n".join(lineas)


def formatear_resumen(resultado: ResultadoEjecucion) -> str:
    lineas = [
        "ETL analítico FetalAlert",
        f"resultado={RESULTADO_EXITO}",
        f"revision_alembic={resultado.revision}",
        f"version_umbrales={resultado.version_umbrales}",
        f"duracion_s={resultado.duracion_s:.3f}",
    ]
    lineas += [
        f"{r.tabla}: insertadas={r.insertadas} actualizadas={r.actualizadas} "
        f"sin_cambios={r.sin_cambios}"
        for r in (*resultado.dimensiones, resultado.bridge)
    ]
    lineas += [
        f"hechos_nuevos={resultado.hechos_nuevos}",
        f"hechos_existentes={resultado.hechos_existentes}",
    ]
    lineas += [
        f"seudonimos_{tabla}={emitidos}"
        for tabla, emitidos in sorted(resultado.seudonimos_emitidos.items())
    ]
    lineas.append(formatear_conciliacion(resultado.informe))
    return "\n".join(lineas)


RESULTADO_PREVISUALIZACION = "PREVIEW"


def _estado(valor: str | None) -> str:
    return "NA" if valor is None else valor


def formatear_previsualizacion(previsualizacion: Previsualizacion) -> str:
    """One ``clave=valor`` line per fact, stable enough to be read by a program.

    Like the run summary, it prints identifiers, counts, codes and dates --
    never a biometric value, a name or any other personal datum.
    """
    p = previsualizacion
    lineas = [
        "ETL analítico FetalAlert — previsualización",
        "modo=SOLO_LECTURA",
        f"resultado={RESULTADO_PREVISUALIZACION}",
        f"revision_alembic={p.revision}",
        f"version_umbrales={p.version_umbrales}",
        f"instantanea={p.instantanea}",
        f"candado_etl={'OCUPADO' if p.candado_ocupado else 'LIBRE'}",
        f"hechos_existentes={p.hechos_existentes}",
        f"candidatas={len(p.candidatas)}",
    ]
    lineas += [
        f"revalidar tabla={tabla} filas={filas}" for tabla, filas in p.filas_de_dimension
    ]
    lineas.append(f"revalidar tabla=bridge_embarazo_factor_riesgo filas={p.filas_de_bridge}")
    lineas += [
        "candidata "
        f"id_lectura={c.id_lectura} id_sesion={c.id_sesion} "
        f"id_embarazo={c.id_embarazo} id_clinica={c.id_clinica} "
        f"id_medico={c.id_medico} semana={c.semana_gestacion} "
        f"trimestre={c.trimestre} fecha_clinica={c.fecha_clinica} "
        f"estado_hr={_estado(c.estado_hr)} estado_spo2={_estado(c.estado_spo2)} "
        f"estado_mov={_estado(c.estado_mov)} "
        f"semaforo_derivado={c.semaforo_derivado} "
        f"semaforo_origen={c.semaforo_origen} "
        f"validacion={'COINCIDE' if c.semaforo_derivado == c.semaforo_origen else 'DIFIERE'} "
        f"en_dim_embarazo={'SI' if c.embarazo_en_dimension else 'NO'} "
        "destino=fact_lectura_biometrica"
        for c in p.candidatas
    ]
    lineas.append(f"aviso={AVISO_DE_CANDIDATA}")
    lineas.append("escrituras=0")
    return "\n".join(lineas)


def describir_error_de_base(error: SQLAlchemyError) -> str:
    """A database failure described without SQL, parameters or credentials.

    ``str(error)`` would append the statement and its bound parameters, and an
    upsert of a dimension carries names and identity numbers. Only the class,
    the SQLSTATE and -- for a failure that happened before any statement, such
    as a refused connection -- the first line of the driver message, with any
    URL redacted, are kept.
    """
    original = getattr(error, "orig", None)
    sqlstate = getattr(original, "sqlstate", None)
    partes = [type(error).__name__] + ([f"SQLSTATE {sqlstate}"] if sqlstate else [])
    descripcion = f"Error de base de datos ({', '.join(partes)})"
    if getattr(error, "statement", None) is None and original is not None:
        primera_linea = str(original).strip().splitlines()[0] if str(original).strip() else ""
        if primera_linea:
            descripcion += f": {sanear_mensaje(primera_linea)}"
    return descripcion
