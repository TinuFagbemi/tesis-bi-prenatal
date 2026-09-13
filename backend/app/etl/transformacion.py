"""Transformation: operational rows into analytic rows, with no connection.

Pure functions over the rows :mod:`app.etl.extraccion` returns. Every decision
is delegated to :mod:`app.etl.reglas`; what happens here is the assembly --
grouping phones, clinics, affiliations and follow-up periods by their owner and
building the row each analytic table expects.

A row the rules refuse stops the transformation with an error, and the run is
rolled back: nothing is dropped, nothing is corrected and nothing is loaded
partially.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from app.etl import reglas
from app.etl.extraccion import PARTES_DEL_NOMBRE, Fila, OrigenDimensional

# How many identifiers an error message lists before summarising the rest.
IDS_EN_MENSAJE = 10


@dataclass(frozen=True)
class DimensionesTransformadas:
    """Rows ready for the seven dimensions and the bridge, in load order."""

    clinicas: list[Fila]
    semaforos: list[Fila]
    tiempos_gestacionales: list[Fila]
    factores_riesgo: list[Fila]
    medicos: list[Fila]
    pacientes: list[Fila]
    embarazos: list[Fila]
    bridge: list[Fila]
    id_semaforo_por_codigo: dict[str, int]


def _copiar(filas: list[Fila], columnas: tuple[str, ...]) -> list[Fila]:
    return [{columna: fila[columna] for columna in columnas} for fila in filas]


def _agrupar(filas: list[Fila], clave: str) -> dict[int, list[Fila]]:
    grupos: dict[int, list[Fila]] = defaultdict(list)
    for fila in filas:
        grupos[fila[clave]].append(fila)
    return grupos


def _contactos(filas: list[Fila]) -> list[reglas.Contacto]:
    return [
        reglas.Contacto(
            tipo_contacto=fila["tipo_contacto"],
            valor_contacto=fila["valor_contacto"],
            principal=fila["principal"],
        )
        for fila in filas
    ]


def _listar(ids: list[int]) -> str:
    resto = len(ids) - IDS_EN_MENSAJE
    return f"{ids[:IDS_EN_MENSAJE]}" + (f" y {resto} más" if resto > 0 else "")


def afiliaciones_por_medico(filas: list[Fila]) -> dict[int, list[reglas.AfiliacionClinica]]:
    grupos: dict[int, list[reglas.AfiliacionClinica]] = defaultdict(list)
    for fila in filas:
        grupos[fila["id_medico"]].append(
            reglas.AfiliacionClinica(
                id_clinica=fila["id_clinica"],
                fecha_inicio=fila["fecha_inicio"],
                fecha_final=fila["fecha_final"],
            )
        )
    return grupos


def transformar_dimensiones(
    origen: OrigenDimensional, *, version: str = reglas.VERSION_SIM_1_0
) -> DimensionesTransformadas:
    """Build every dimension row and the bridge from the operational source."""
    id_semaforo_por_codigo = reglas.validar_catalogo_de_semaforo(
        (
            reglas.NivelDeSemaforo(
                id_semaforo=fila["id_semaforo"],
                codigo_nivel=fila["codigo_nivel"],
                prioridad=fila["prioridad"],
                version_referencia=fila["version_referencia"],
            )
            for fila in origen.semaforos
        ),
        version=version,
    )

    telefonos_medico = _agrupar(origen.telefonos_medico, "id_medico")
    afiliaciones = _agrupar(origen.afiliaciones_de_medico, "id_medico")
    medicos = []
    for fila in origen.medicos:
        entidad = f"médico id_medico={fila['id_medico']}"
        medicos.append(
            {
                "id_medico": fila["id_medico"],
                # Context only. A physician with no affiliation yet keeps NULL;
                # the facts check the dated affiliation of their own physician.
                "id_clinica": reglas.clinica_contextual(
                    (a["id_clinica"] for a in afiliaciones[fila["id_medico"]]),
                    entidad=entidad,
                ),
                "nombre_completo": reglas.nombre_completo(
                    (fila[parte] for parte in PARTES_DEL_NOMBRE), entidad=entidad
                ),
                "especialidad": fila["especialidad"],
                "email_med": fila["email_med"],
                "telefono_med": reglas.telefono_principal(
                    _contactos(telefonos_medico[fila["id_medico"]]), entidad=entidad
                ),
            }
        )

    telefonos_paciente = _agrupar(origen.telefonos_paciente, "id_paciente")
    embarazos_de_paciente = _agrupar(origen.embarazos, "id_paciente")
    pacientes = []
    for fila in origen.pacientes:
        entidad = f"paciente id_paciente={fila['id_paciente']}"
        pacientes.append(
            {
                "id_paciente": fila["id_paciente"],
                # Her clinic is the clinic of her pregnancies: none registered
                # yet is NULL, one is used, two clinics cannot be expressed by
                # this dimension without choosing -- and choosing is refused.
                "id_clinica": reglas.clinica_contextual(
                    (e["id_clinica"] for e in embarazos_de_paciente[fila["id_paciente"]]),
                    entidad=entidad,
                ),
                "cedula": fila["cedula"],
                "nombre_completo": reglas.nombre_completo(
                    (fila[parte] for parte in PARTES_DEL_NOMBRE), entidad=entidad
                ),
                "telefono_pac": reglas.telefono_principal(
                    _contactos(telefonos_paciente[fila["id_paciente"]]),
                    entidad=entidad,
                ),
                "fecha_nac": fila["fecha_nac"],
            }
        )

    embarazos = [
        {
            "id_embarazo": fila["id_embarazo"],
            "id_paciente": fila["id_paciente"],
            "numero_gestas": fila["numero_gestas"],
            "numero_partos": fila["numero_partos"],
            "estado_embarazo": fila["estado_embarazo"],
            "fecha_inicio": fila["fecha_inicio"],
            "fecha_probable_parto": fila["fecha_probable_parto"],
            "fecha_cierre": fila["fecha_cierre"],
            "duracion_est_semanas": reglas.duracion_estimada_semanas(
                fila["fecha_inicio"],
                fila["fecha_probable_parto"],
                id_embarazo=fila["id_embarazo"],
            ),
            "clasificacion_embarazo": reglas.clasificacion_embarazo(),
        }
        for fila in origen.embarazos
    ]

    return DimensionesTransformadas(
        clinicas=_copiar(
            origen.clinicas, ("id_clinica", "nombre_clinica", "provincia", "distrito")
        ),
        semaforos=_copiar(
            origen.semaforos,
            (
                "id_semaforo",
                "codigo_nivel",
                "etiqueta_visual",
                "color_hex",
                "prioridad",
                "mensaje_app",
                "version_referencia",
            ),
        ),
        tiempos_gestacionales=_copiar(
            origen.tiempos_gestacionales,
            (
                "id_tiempo_gest",
                "semana_gestacion",
                "mes_gestacion",
                "trimestre",
                "descripcion",
            ),
        ),
        factores_riesgo=_copiar(
            origen.factores_riesgo,
            (
                "id_factor_riesgo",
                "clave_factor",
                "nombre_factor",
                "descripcion",
                "activo",
            ),
        ),
        medicos=medicos,
        pacientes=pacientes,
        embarazos=embarazos,
        # The drawing's attributes, exactly: the operational ``fecha_fin`` is
        # not part of the approved bridge.
        bridge=_copiar(
            origen.factores_de_embarazo,
            (
                "id_embarazo",
                "id_factor_riesgo",
                "fecha_diagnostico",
                "activo",
                "observaciones",
            ),
        ),
        id_semaforo_por_codigo=id_semaforo_por_codigo,
    )


def transformar_hechos(
    lecturas: list[Fila],
    seguimientos: list[Fila],
    afiliaciones: list[Fila],
    id_semaforo_por_codigo: dict[str, int],
    *,
    version: str = reglas.VERSION_SIM_1_0,
) -> list[Fila]:
    """Build one fact row per new reading, classified and checked.

    The physician is the PRINCIPAL follow-up that covers the clinical day, and
    that physician must be affiliated, that same day, to the pregnancy's clinic.
    The global level is derived from the per-metric states and compared with
    the one the reading already carries in ``operacional``. Every disagreement
    is collected, and if there is any the whole transformation is refused:
    neither the source nor the fact is adjusted to make the counts match.
    """
    periodos: dict[int, list[reglas.PeriodoDeSeguimiento]] = defaultdict(list)
    for fila in seguimientos:
        periodos[fila["id_embarazo"]].append(
            reglas.PeriodoDeSeguimiento(
                id_seguimiento=fila["id_seguimiento"],
                id_medico=fila["id_medico"],
                fecha_asignacion=fila["fecha_asignacion"],
                fecha_fin=fila["fecha_fin"],
            )
        )
    afiliacion_de = afiliaciones_por_medico(afiliaciones)

    hechos: list[Fila] = []
    discrepancias: list[int] = []
    for lectura in lecturas:
        id_lectura = lectura["id_lectura"]
        try:
            estados = reglas.clasificar_lectura(
                hr_valor=lectura["hr_valor"],
                spo2_valor=lectura["spo2_valor"],
                mov_valor=lectura["mov_valor"],
                semana=lectura["semana_gestacion"],
                trimestre=lectura["trimestre"],
                version=version,
            )
            dia = reglas.fecha_clinica(lectura["fecha_hora_captura"])
        except reglas.ErrorDeDatos as error:
            # The rules do not know which reading they judged; the identifier
            # is added here, and it is the only thing added.
            raise type(error)(f"lectura id_lectura={id_lectura}: {error.detalle}") from error

        id_medico = reglas.medico_responsable(
            periodos[lectura["id_embarazo"]], dia, id_lectura=id_lectura
        )
        reglas.verificar_afiliacion(
            afiliacion_de[id_medico],
            dia,
            id_medico=id_medico,
            id_clinica_embarazo=lectura["id_clinica"],
            id_lectura=id_lectura,
        )
        id_semaforo = id_semaforo_por_codigo[estados.codigo_global]
        if id_semaforo != lectura["id_semaforo"]:
            discrepancias.append(id_lectura)
            continue

        hechos.append(
            {
                "id_lectura": id_lectura,
                "id_sesion": lectura["id_sesion"],
                "id_paciente": lectura["id_paciente"],
                "id_medico": id_medico,
                "id_clinica": lectura["id_clinica"],
                "id_tiempo_gestacional": lectura["id_tiempo_gest"],
                "id_embarazo": lectura["id_embarazo"],
                "id_semaforo": id_semaforo,
                "hr_valor": lectura["hr_valor"],
                "spo2_valor": lectura["spo2_valor"],
                "mov_valor": lectura["mov_valor"],
                "estado_hr": estados.estado_hr,
                "estado_spo2": estados.estado_spo2,
                "estado_mov": estados.estado_mov,
                "fecha_hora": lectura["fecha_hora_captura"],
            }
        )

    if discrepancias:
        raise reglas.DiscrepanciaDeSemaforo(
            f"{len(discrepancias)} lectura(s) tienen un semáforo derivado de sus "
            "métricas distinto del registrado en operacional "
            f"(id_lectura={_listar(discrepancias)}). No se corrige el origen ni el "
            "hecho: la ejecución se revierte."
        )
    return hechos
