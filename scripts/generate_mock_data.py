from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


# ============================================================
# CONFIGURACIÓN DEL DATASET
# ============================================================

SEMILLA = 20260810

TOTAL_CLINICAS = 3
TOTAL_MEDICOS = 5
TOTAL_ADMINISTRADORES = 2
TOTAL_GESTANTES = 30
TOTAL_EMBARAZOS = 30
TOTAL_DISPOSITIVOS = 30

# HR / SpO2
SESIONES_BASE_HR_SPO2_POR_GESTANTE = 3
LECTURAS_POR_SESION_HR_SPO2 = 5

SESIONES_BASE_HR_SPO2 = 90
REGISTROS_BASE_HR_SPO2 = 450

SESIONES_EXTRA_HR_SPO2 = 22
REGISTROS_EXTRA_HR_SPO2 = 110

TOTAL_SESIONES_HR_SPO2 = 112
TOTAL_HR_SPO2 = 560

# Movimientos fetales
REGISTROS_BASE_MOVIMIENTOS = 600
REGISTROS_EXTRA_MOVIMIENTOS = 20
TOTAL_MOVIMIENTOS = 620

# Totales
TOTAL_SESIONES = 732
TOTAL_REGISTROS = 1180
MAXIMO_REGISTROS = 1200

# Distribución técnica del semáforo
ALERTAS_OK = 826
ALERTAS_WARNING = 295
ALERTAS_ERROR = 59

# Las 5 lecturas HR/SpO2 de una misma sesión
# comparten un mismo nivel general de alerta.
SESIONES_HR_OK = 78
SESIONES_HR_WARNING = 28
SESIONES_HR_ERROR = 6

# Distribución de movimientos que completa
# el 70/25/5 global.
MOVIMIENTOS_OK = 436
MOVIMIENTOS_WARNING = 155
MOVIMIENTOS_ERROR = 29

# Factores de riesgo
RIESGO_SIN_FACTOR = 14
RIESGO_UN_FACTOR = 9
RIESGO_DOS_FACTORES = 7

# Cobertura longitudinal base
SEMANAS_HR_BASE = [8, 24, 36]
SEMANAS_MOVIMIENTOS = list(range(20, 40))

# Simulación offline-first
PORCENTAJE_SINCRONIZACION_DIFERIDA = 0.25

# Referencia operativa para la simulación de movimientos
UMBRAL_MOVIMIENTOS_SIMULACION = 10

# Usuarios (registros maestros de acceso)
TOTAL_USUARIOS_ADMIN = TOTAL_ADMINISTRADORES
TOTAL_USUARIOS_MEDICO = TOTAL_MEDICOS
TOTAL_USUARIOS_PACIENTE = TOTAL_GESTANTES
TOTAL_USUARIOS = (
    TOTAL_USUARIOS_ADMIN
    + TOTAL_USUARIOS_MEDICO
    + TOTAL_USUARIOS_PACIENTE
)

# Tipos de contacto permitidos para TelefonoPaciente/TelefonoMedico.
# Provisional: sin modelo SQLAlchemy disponible aún para validar
# el enum físico real (ver informe de discrepancias pendientes).
TIPOS_CONTACTO = ("CELULAR", "FIJO", "CORREO_ALTERNO")


# ============================================================
# RUTAS
# ============================================================

def obtener_raiz_proyecto() -> Path:
    """
    Obtiene la raíz del proyecto.

    Al ejecutar scripts/generate_mock_data.py usa __file__.
    En una consola interactiva usa el directorio actual.
    """
    if "__file__" in globals():
        return Path(__file__).resolve().parents[1]

    return Path.cwd()


RAIZ_PROYECTO = obtener_raiz_proyecto()
CARPETA_SALIDA = RAIZ_PROYECTO / "data" / "generated"


# ============================================================
# FUNCIONES AUXILIARES
# ============================================================

def crear_id(
    prefijo: str,
    numero: int,
    ancho: int = 3,
) -> str:
    return f"{prefijo}-{numero:0{ancho}d}"


def fecha_en_semana_gestacional(
    fecha_inicio: date,
    semana: int,
) -> datetime:
    """
    Genera una fecha y hora dentro de la semana gestacional indicada.
    """
    dias_adicionales = random.randint(0, 6)
    hora = random.randint(8, 19)
    minuto = random.randint(0, 59)

    return (
        datetime.combine(fecha_inicio, datetime.min.time())
        + timedelta(
            weeks=semana - 1,
            days=dias_adicionales,
            hours=hora,
            minutes=minuto,
        )
    )


def calcular_semana_gestacional(
    fecha_inicio: date,
    fecha_evento: datetime,
) -> int:
    dias = (fecha_evento.date() - fecha_inicio).days
    return (dias // 7) + 1


def calcular_trimestre(semana: int) -> int:
    if semana <= 13:
        return 1

    if semana <= 27:
        return 2

    return 3


def calcular_mes_gestacional(semana: int) -> int:
    """
    Aproximación técnica para TiempoGestacional.
    """
    return min(9, ((semana - 1) // 4) + 1)


def fecha_sincronizacion(
    fecha_captura: datetime,
) -> datetime:
    """
    Simula sincronización inmediata o diferida
    para validar el enfoque offline-first.
    """
    if random.random() < PORCENTAJE_SINCRONIZACION_DIFERIDA:
        retraso = timedelta(
            hours=random.randint(6, 72),
            minutes=random.randint(0, 59),
        )
    else:
        retraso = timedelta(
            minutes=random.randint(0, 30)
        )

    return fecha_captura + retraso


def generar_valores_hr_spo2(
    estado: str,
) -> tuple[int, int]:
    """
    Genera valores sintéticos para validación técnica.

    Se evitan los bordes exactos de los rangos mientras
    se termina de estandarizar la tabla de umbrales.
    """
    if estado == "OK":
        return (
            random.randint(65, 98),
            random.randint(95, 99),
        )

    if estado == "WARNING":
        if random.choice([True, False]):
            return (
                random.randint(102, 108),
                random.randint(95, 99),
            )

        return (
            random.randint(65, 98),
            random.randint(92, 94),
        )

    # ERROR
    if random.choice([True, False]):
        hr = random.choice(
            [
                random.randint(45, 54),
                random.randint(115, 125),
            ]
        )

        return (
            hr,
            random.randint(95, 99),
        )

    return (
        random.randint(65, 98),
        random.randint(86, 91),
    )


def generar_movimientos(estado: str) -> int:
    """
    Genera el conteo consolidado de una sesión
    de movimientos fetales.

    La meta de 10 movimientos se utiliza como
    referencia operativa de simulación.
    """
    if estado == "OK":
        return random.randint(
            UMBRAL_MOVIMIENTOS_SIMULACION,
            14,
        )

    if estado == "WARNING":
        return random.randint(
            5,
            UMBRAL_MOVIMIENTOS_SIMULACION - 1,
        )

    return random.randint(0, 4)


def exportar_csv(
    nombre: str,
    datos: list[dict[str, Any]],
) -> None:
    if not datos:
        return

    ruta = CARPETA_SALIDA / f"{nombre}.csv"
    campos = list(datos[0].keys())

    with ruta.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as archivo:
        escritor = csv.DictWriter(
            archivo,
            fieldnames=campos,
        )

        escritor.writeheader()
        escritor.writerows(datos)


# ============================================================
# CATÁLOGOS Y ENTIDADES MAESTRAS
# ============================================================

def generar_clinicas() -> list[dict[str, Any]]:
    """
    Ubicaciones rurales simuladas, coherentes con el alcance
    de FetalAlert (seguimiento prenatal en zonas rurales de
    Panamá). Provincia/distrito/corregimiento/dirección son
    completamente sintéticos.
    """
    ubicaciones = [
        (
            "Chiriquí",
            "Renacimiento",
            "Plaza Caisán",
            "Camino Rural, sector Plaza Caisán",
        ),
        (
            "Veraguas",
            "Santa Fe",
            "Calovébora",
            "Camino Comunitario, sector Calovébora",
        ),
        (
            "Darién",
            "Chepigana",
            "Camogantí",
            "Camino Comunitario, sector Camogantí",
        ),
    ]

    clinicas = []

    for i, (
        provincia,
        distrito,
        corregimiento,
        calle,
    ) in enumerate(ubicaciones, start=1):

        clinicas.append(
            {
                "id_clinica": crear_id("CLI", i),
                "nombre_clinica":
                    f"Clínica Rural Simulada {i:02d}",
                "ruc": f"SIM-RUC-{i:03d}",
                "provincia": provincia,
                "distrito": distrito,
                "corregimiento": corregimiento,
                "calle": calle,
            }
        )

    return clinicas


def generar_especialidades() -> list[dict[str, Any]]:
    return [
        {
            "id_especialidad": "ESP-001",
            "nombre_especialidad":
                "Ginecología y Obstetricia",
        }
    ]


def generar_medicos() -> list[dict[str, Any]]:
    return [
        {
            "id_medico": crear_id("MED", i),
            "id_especialidad": "ESP-001",
            "primer_nombre": f"Médico{i:02d}",
            "segundo_nombre": None,
            "apellido_paterno": "Simulado",
            "apellido_materno": f"{i:02d}",
            "email_med":
                f"medico{i:02d}@example.com",
        }
        for i in range(1, TOTAL_MEDICOS + 1)
    ]


def generar_medico_clinica() -> list[dict[str, Any]]:
    relaciones = []

    for i_medico in range(1, TOTAL_MEDICOS + 1):
        for i_clinica in range(1, TOTAL_CLINICAS + 1):
            relaciones.append(
                {
                    "id_medico":
                        crear_id("MED", i_medico),
                    "id_clinica":
                        crear_id("CLI", i_clinica),
                    "fecha_inicio": "2025-01-01",
                    "fecha_final": None,
                    "activo": True,
                }
            )

    return relaciones


def generar_telefonos_medico(
    medicos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Genera al menos un contacto principal por médico.

    Cada médico recibe un contacto CELULAR principal y un
    contacto FIJO secundario, para ejercitar el escenario de
    múltiples contactos sin ambigüedad sobre cuál es principal.
    """
    telefonos = []
    contador = 1

    for i, medico in enumerate(medicos, start=1):
        telefonos.append(
            {
                "id_telefono_medico":
                    crear_id("TFM", contador),
                "id_medico":
                    medico["id_medico"],
                "tipo_contacto":
                    "CELULAR",
                "valor_contacto":
                    f"6100-{i:04d}",
                "principal":
                    True,
            }
        )
        contador += 1

        telefonos.append(
            {
                "id_telefono_medico":
                    crear_id("TFM", contador),
                "id_medico":
                    medico["id_medico"],
                "tipo_contacto":
                    "FIJO",
                "valor_contacto":
                    f"300-{i:04d}",
                "principal":
                    False,
            }
        )
        contador += 1

    return telefonos


def generar_pacientes() -> list[dict[str, Any]]:
    pacientes = []

    for i in range(1, TOTAL_GESTANTES + 1):
        anio_nacimiento = random.randint(1990, 2005)
        mes = random.randint(1, 12)
        dia = random.randint(1, 28)

        pacientes.append(
            {
                "id_paciente":
                    crear_id("PAC", i),
                "cedula":
                    f"SIM-PAC-{i:03d}",
                "primer_nombre":
                    f"Gestante{i:02d}",
                "segundo_nombre":
                    None,
                "apellido_paterno":
                    "Simulada",
                "apellido_materno":
                    f"{i:02d}",
                "email_pac":
                    f"paciente{i:02d}@example.com",
                "fecha_nac":
                    date(
                        anio_nacimiento,
                        mes,
                        dia,
                    ).isoformat(),
            }
        )

    return pacientes


def generar_telefonos_paciente(
    pacientes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Genera al menos un contacto principal por paciente.

    Toda gestante recibe un contacto CELULAR principal.
    Una de cada tres gestantes recibe además un contacto
    CORREO_ALTERNO secundario (no principal), para validar
    el manejo de múltiples contactos por paciente.
    """
    telefonos = []
    contador = 1

    for i, paciente in enumerate(pacientes, start=1):
        telefonos.append(
            {
                "id_telefono_paciente":
                    crear_id("TFP", contador),
                "id_paciente":
                    paciente["id_paciente"],
                "tipo_contacto":
                    "CELULAR",
                "valor_contacto":
                    f"6000-{i:04d}",
                "principal":
                    True,
            }
        )
        contador += 1

        if i % 3 == 0:
            telefonos.append(
                {
                    "id_telefono_paciente":
                        crear_id("TFP", contador),
                    "id_paciente":
                        paciente["id_paciente"],
                    "tipo_contacto":
                        "CORREO_ALTERNO",
                    "valor_contacto":
                        f"contacto.alterno{i:02d}"
                        "@example.com",
                    "principal":
                        False,
                }
            )
            contador += 1

    return telefonos


def generar_roles() -> list[dict[str, Any]]:
    return [
        {
            "id_rol": "ROL-001",
            "nombre_rol": "ADMIN",
        },
        {
            "id_rol": "ROL-002",
            "nombre_rol": "MEDICO",
        },
        {
            "id_rol": "ROL-003",
            "nombre_rol": "PACIENTE",
        },
    ]


def generar_usuarios_administradores(
) -> list[dict[str, Any]]:
    return [
        {
            "id_usuario":
                crear_id("USR", i),
            "email":
                f"admin{i:02d}@example.com",
            "password_hash":
                "HASH_SIMULADO_NO_USAR_EN_PRODUCCION",
            "id_rol":
                "ROL-001",
            "activo":
                True,
        }
        for i in range(
            1,
            TOTAL_ADMINISTRADORES + 1,
        )
    ]


def generar_usuarios_medicos(
    medicos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Crea una cuenta de acceso MEDICO por cada médico,
    reutilizando su correo institucional simulado.

    La numeración de id_usuario continúa después de los
    administradores para evitar colisiones de identificador.
    """
    indice_inicial = TOTAL_ADMINISTRADORES + 1

    return [
        {
            "id_usuario":
                crear_id(
                    "USR",
                    indice_inicial + offset,
                ),
            "email":
                medico["email_med"],
            "password_hash":
                "HASH_SIMULADO_NO_USAR_EN_PRODUCCION",
            "id_rol":
                "ROL-002",
            "activo":
                True,
        }
        for offset, medico in enumerate(medicos)
    ]


def generar_usuarios_pacientes(
    pacientes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Crea una cuenta de acceso PACIENTE por cada gestante,
    reutilizando su correo simulado.

    La numeración de id_usuario continúa después de
    administradores y médicos.
    """
    indice_inicial = (
        TOTAL_ADMINISTRADORES
        + TOTAL_MEDICOS
        + 1
    )

    return [
        {
            "id_usuario":
                crear_id(
                    "USR",
                    indice_inicial + offset,
                ),
            "email":
                paciente["email_pac"],
            "password_hash":
                "HASH_SIMULADO_NO_USAR_EN_PRODUCCION",
            "id_rol":
                "ROL-003",
            "activo":
                True,
        }
        for offset, paciente in enumerate(pacientes)
    ]


def generar_usuario_medico(
    usuarios_medicos: list[dict[str, Any]],
    medicos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    1 cuenta MEDICO por médico (relación 1:1).
    """
    return [
        {
            "id_usuario":
                usuario["id_usuario"],
            "id_medico":
                medico["id_medico"],
        }
        for usuario, medico in zip(
            usuarios_medicos,
            medicos,
        )
    ]


def generar_usuario_paciente(
    usuarios_pacientes: list[dict[str, Any]],
    pacientes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    1 cuenta PACIENTE por gestante (relación 1:1).
    """
    return [
        {
            "id_usuario":
                usuario["id_usuario"],
            "id_paciente":
                paciente["id_paciente"],
        }
        for usuario, paciente in zip(
            usuarios_pacientes,
            pacientes,
        )
    ]


def generar_semaforos() -> list[dict[str, Any]]:
    return [
        {
            "id_semaforo": "SEM-OK",
            "codigo_nivel": "OK",
            "etiqueta_visual": "Normal",
            "color_hex": "#008000",
            "prioridad": 1,
            "mensaje_app":
                "Lectura dentro del rango esperado.",
            "version_referencia": "SIM-1.0",
        },
        {
            "id_semaforo": "SEM-WARNING",
            "codigo_nivel": "WARNING",
            "etiqueta_visual": "Precaución",
            "color_hex": "#FFC107",
            "prioridad": 2,
            "mensaje_app":
                "Lectura que requiere atención.",
            "version_referencia": "SIM-1.0",
        },
        {
            "id_semaforo": "SEM-ERROR",
            "codigo_nivel": "ERROR",
            "etiqueta_visual": "Alerta",
            "color_hex": "#D32F2F",
            "prioridad": 3,
            "mensaje_app":
                "Lectura fuera del rango esperado.",
            "version_referencia": "SIM-1.0",
        },
    ]


def generar_tiempo_gestacional(
) -> list[dict[str, Any]]:
    filas = []

    for semana in range(1, 41):
        trimestre = calcular_trimestre(semana)

        filas.append(
            {
                "id_tiempo_gest":
                    f"TG-{semana:02d}",
                "semana_gestacion":
                    semana,
                "mes_gestacion":
                    calcular_mes_gestacional(
                        semana
                    ),
                "trimestre":
                    trimestre,
                "grupo_clinico":
                    f"TRIMESTRE_{trimestre}",
                "descripcion":
                    f"Semana gestacional {semana}",
            }
        )

    return filas


def generar_factores_riesgo_catalogo(
) -> list[dict[str, Any]]:
    return [
        {
            "id_factor_riesgo": "FR-001",
            "clave_factor": "HTA",
            "nombre_factor":
                "Hipertensión arterial",
            "descripcion":
                "Factor de riesgo simulado "
                "para validación técnica.",
            "activo": True,
        },
        {
            "id_factor_riesgo": "FR-002",
            "clave_factor": "DMG",
            "nombre_factor":
                "Diabetes gestacional",
            "descripcion":
                "Factor de riesgo simulado "
                "para validación técnica.",
            "activo": True,
        },
        {
            "id_factor_riesgo": "FR-003",
            "clave_factor": "OBS",
            "nombre_factor": "Obesidad",
            "descripcion":
                "Factor de riesgo simulado "
                "para validación técnica.",
            "activo": True,
        },
    ]


# ============================================================
# EMBARAZOS, SEGUIMIENTO Y DISPOSITIVOS
# ============================================================

def generar_embarazos() -> list[dict[str, Any]]:
    embarazos = []
    fecha_base = date(2025, 1, 6)

    for i in range(1, TOTAL_EMBARAZOS + 1):
        fecha_inicio = (
            fecha_base
            + timedelta(days=(i - 1) * 9)
        )

        fecha_probable_parto = (
            fecha_inicio
            + timedelta(days=280)
        )

        numero_gestas = random.randint(1, 3)

        numero_partos = random.randint(
            0,
            numero_gestas - 1,
        )

        id_clinica = crear_id(
            "CLI",
            ((i - 1) // 10) + 1,
        )

        embarazos.append(
            {
                "id_embarazo":
                    crear_id("EMB", i),
                "id_paciente":
                    crear_id("PAC", i),
                "id_clinica":
                    id_clinica,
                "numero_gestas":
                    numero_gestas,
                "numero_partos":
                    numero_partos,
                "fecha_inicio":
                    fecha_inicio.isoformat(),
                "fecha_probable_parto":
                    fecha_probable_parto.isoformat(),
                "estado_embarazo":
                    "ACTIVO_SIMULADO",
                "fecha_cierre":
                    None,
            }
        )

    return embarazos


def medico_para_embarazo(
    indice_embarazo: int,
) -> str:
    return crear_id(
        "MED",
        (
            (indice_embarazo - 1)
            % TOTAL_MEDICOS
        ) + 1,
    )


def generar_seguimiento_clinico(
    embarazos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seguimientos = []

    for i, embarazo in enumerate(
        embarazos,
        start=1,
    ):
        seguimientos.append(
            {
                "id_seguimiento":
                    crear_id("SEG", i),
                "id_embarazo":
                    embarazo["id_embarazo"],
                "id_medico":
                    medico_para_embarazo(i),
                "fecha_asignacion":
                    embarazo["fecha_inicio"],
                "fecha_fin":
                    None,
                "rol_seguimiento":
                    "PRINCIPAL",
                "activo":
                    True,
            }
        )

    return seguimientos


def generar_dispositivos(
    embarazos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    dispositivos = []

    for i, embarazo in enumerate(
        embarazos,
        start=1,
    ):
        fecha_registro = (
            date.fromisoformat(
                embarazo["fecha_inicio"]
            )
            - timedelta(days=7)
        )

        dispositivos.append(
            {
                "id_dispositivo":
                    crear_id("DIS", i),
                "id_clinica":
                    embarazo["id_clinica"],
                "codigo_dispositivo":
                    f"FA-SIM-{i:03d}",
                "modelo":
                    "FetalAlert-SIM",
                "version_firmware":
                    "SIM-1.0",
                "estado":
                    "ACTIVO",
                "fecha_registro":
                    fecha_registro.isoformat(),
            }
        )

    return dispositivos


def generar_asignaciones_dispositivo(
    embarazos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    asignaciones = []

    for i, embarazo in enumerate(
        embarazos,
        start=1,
    ):
        asignaciones.append(
            {
                "id_asignacion":
                    crear_id("ASG", i),
                "id_dispositivo":
                    crear_id("DIS", i),
                "id_embarazo":
                    embarazo["id_embarazo"],
                "fecha_inicio":
                    embarazo["fecha_inicio"],
                "fecha_fin":
                    None,
                "activo":
                    True,
            }
        )

    return asignaciones


def generar_paciente_factor_riesgo(
    embarazos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Distribución:
    - 14 embarazos sin factores
    - 9 con un factor
    - 7 con dos factores
    """
    ids_embarazo = [
        embarazo["id_embarazo"]
        for embarazo in embarazos
    ]

    random.shuffle(ids_embarazo)

    con_un_factor = ids_embarazo[
        RIESGO_SIN_FACTOR:
        RIESGO_SIN_FACTOR + RIESGO_UN_FACTOR
    ]

    con_dos_factores = ids_embarazo[
        RIESGO_SIN_FACTOR + RIESGO_UN_FACTOR:
    ]

    relaciones = []

    ids_factores = [
        "FR-001",
        "FR-002",
        "FR-003",
    ]

    embarazo_por_id = {
        e["id_embarazo"]: e
        for e in embarazos
    }

    for id_embarazo in con_un_factor:
        factor = random.choice(
            ids_factores
        )

        embarazo = embarazo_por_id[
            id_embarazo
        ]

        relaciones.append(
            {
                "id_embarazo":
                    id_embarazo,
                "id_factor_riesgo":
                    factor,
                "fecha_diagnostico":
                    (
                        date.fromisoformat(
                            embarazo["fecha_inicio"]
                        )
                        + timedelta(
                            weeks=random.randint(
                                8,
                                24,
                            )
                        )
                    ).isoformat(),
                "fecha_fin":
                    None,
                "activo":
                    True,
                "observaciones":
                    "Registro sintético "
                    "para validación técnica.",
            }
        )

    for id_embarazo in con_dos_factores:
        embarazo = embarazo_por_id[
            id_embarazo
        ]

        factores = random.sample(
            ids_factores,
            2,
        )

        for factor in factores:
            relaciones.append(
                {
                    "id_embarazo":
                        id_embarazo,
                    "id_factor_riesgo":
                        factor,
                    "fecha_diagnostico":
                        (
                            date.fromisoformat(
                                embarazo["fecha_inicio"]
                            )
                            + timedelta(
                                weeks=random.randint(
                                    8,
                                    24,
                                )
                            )
                        ).isoformat(),
                    "fecha_fin":
                        None,
                    "activo":
                        True,
                    "observaciones":
                        "Registro sintético "
                        "para validación técnica.",
                }
            )

    return relaciones


# ============================================================
# SESIONES Y LECTURAS
# ============================================================

def construir_lectura(
    *,
    contador_lectura: int,
    id_sesion: str,
    fecha_inicio_embarazo: date,
    captura: datetime,
    tipo: str,
) -> dict[str, Any]:
    semana = calcular_semana_gestacional(
        fecha_inicio_embarazo,
        captura,
    )

    return {
        "id_lectura":
            crear_id(
                "LEC",
                contador_lectura,
                4,
            ),
        "id_sesion":
            id_sesion,
        "id_tiempo_gest":
            f"TG-{semana:02d}",
        "id_semaforo":
            None,
        "fecha_hora_captura":
            captura.isoformat(),
        "fecha_hora_sincronizacion":
            fecha_sincronizacion(
                captura
            ).isoformat(),
        "hr_valor":
            None,
        "spo2_valor":
            None,
        "mov_valor":
            None,
        "_tipo":
            tipo,
    }


def generar_sesiones_y_lecturas(
    embarazos: list[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    sesiones: list[dict[str, Any]] = []
    lecturas: list[dict[str, Any]] = []

    contador_sesion = 1
    contador_lectura = 1

    dispositivo_por_embarazo = {
        embarazo["id_embarazo"]:
            crear_id("DIS", i)
        for i, embarazo in enumerate(
            embarazos,
            start=1,
        )
    }

    # --------------------------------------------------------
    # 1. HR / SpO2 BASE
    #
    # 30 gestantes x 3 sesiones x 5 lecturas
    # = 450 registros.
    # --------------------------------------------------------

    for embarazo in embarazos:
        fecha_inicio = date.fromisoformat(
            embarazo["fecha_inicio"]
        )

        for semana in SEMANAS_HR_BASE:
            id_sesion = crear_id(
                "SES",
                contador_sesion,
                4,
            )

            contador_sesion += 1

            inicio = fecha_en_semana_gestacional(
                fecha_inicio,
                semana,
            )

            fin = inicio + timedelta(
                minutes=5
            )

            sesiones.append(
                {
                    "id_sesion":
                        id_sesion,
                    "id_embarazo":
                        embarazo["id_embarazo"],
                    "id_dispositivo":
                        dispositivo_por_embarazo[
                            embarazo["id_embarazo"]
                        ],
                    "fecha_inicio":
                        inicio.isoformat(),
                    "fecha_fin":
                        fin.isoformat(),
                    "estado_sesion":
                        "COMPLETADA",
                    "origen_dato":
                        "API",
                    "_tipo":
                        "HR_SPO2",
                }
            )

            for numero_lectura in range(
                LECTURAS_POR_SESION_HR_SPO2
            ):
                captura = (
                    inicio
                    + timedelta(
                        minutes=numero_lectura
                    )
                )

                lecturas.append(
                    construir_lectura(
                        contador_lectura=
                            contador_lectura,
                        id_sesion=
                            id_sesion,
                        fecha_inicio_embarazo=
                            fecha_inicio,
                        captura=
                            captura,
                        tipo=
                            "HR_SPO2",
                    )
                )

                contador_lectura += 1

    # --------------------------------------------------------
    # 2. HR / SpO2 VOLUNTARIOS
    #
    # 22 sesiones x 5 lecturas = 110 registros.
    # --------------------------------------------------------

    extras_por_embarazo = defaultdict(int)

    while (
        sum(extras_por_embarazo.values())
        < SESIONES_EXTRA_HR_SPO2
    ):
        indice = random.randrange(
            TOTAL_EMBARAZOS
        )

        if extras_por_embarazo[indice] < 3:
            extras_por_embarazo[indice] += 1

    for indice, cantidad in (
        extras_por_embarazo.items()
    ):
        embarazo = embarazos[indice]

        fecha_inicio = date.fromisoformat(
            embarazo["fecha_inicio"]
        )

        for _ in range(cantidad):
            semana = random.randint(
                5,
                39,
            )

            id_sesion = crear_id(
                "SES",
                contador_sesion,
                4,
            )

            contador_sesion += 1

            inicio = fecha_en_semana_gestacional(
                fecha_inicio,
                semana,
            )

            fin = inicio + timedelta(
                minutes=5
            )

            sesiones.append(
                {
                    "id_sesion":
                        id_sesion,
                    "id_embarazo":
                        embarazo["id_embarazo"],
                    "id_dispositivo":
                        dispositivo_por_embarazo[
                            embarazo["id_embarazo"]
                        ],
                    "fecha_inicio":
                        inicio.isoformat(),
                    "fecha_fin":
                        fin.isoformat(),
                    "estado_sesion":
                        "COMPLETADA",
                    "origen_dato":
                        "API",
                    "_tipo":
                        "HR_SPO2",
                }
            )

            for numero_lectura in range(
                LECTURAS_POR_SESION_HR_SPO2
            ):
                captura = (
                    inicio
                    + timedelta(
                        minutes=numero_lectura
                    )
                )

                lecturas.append(
                    construir_lectura(
                        contador_lectura=
                            contador_lectura,
                        id_sesion=
                            id_sesion,
                        fecha_inicio_embarazo=
                            fecha_inicio,
                        captura=
                            captura,
                        tipo=
                            "HR_SPO2",
                    )
                )

                contador_lectura += 1

    # --------------------------------------------------------
    # 3. MOVIMIENTOS FETALES BASE
    #
    # 30 gestantes x 20 semanas = 600.
    # --------------------------------------------------------

    for embarazo in embarazos:
        fecha_inicio = date.fromisoformat(
            embarazo["fecha_inicio"]
        )

        for semana in SEMANAS_MOVIMIENTOS:
            id_sesion = crear_id(
                "SES",
                contador_sesion,
                4,
            )

            contador_sesion += 1

            inicio = fecha_en_semana_gestacional(
                fecha_inicio,
                semana,
            )

            duracion = random.randint(
                60,
                120,
            )

            fin = inicio + timedelta(
                minutes=duracion
            )

            sesiones.append(
                {
                    "id_sesion":
                        id_sesion,
                    "id_embarazo":
                        embarazo["id_embarazo"],
                    "id_dispositivo":
                        dispositivo_por_embarazo[
                            embarazo["id_embarazo"]
                        ],
                    "fecha_inicio":
                        inicio.isoformat(),
                    "fecha_fin":
                        fin.isoformat(),
                    "estado_sesion":
                        "COMPLETADA",
                    "origen_dato":
                        "API",
                    "_tipo":
                        "MOVIMIENTOS",
                }
            )

            lecturas.append(
                construir_lectura(
                    contador_lectura=
                        contador_lectura,
                    id_sesion=
                        id_sesion,
                    fecha_inicio_embarazo=
                        fecha_inicio,
                    captura=
                        fin,
                    tipo=
                        "MOVIMIENTOS",
                )
            )

            contador_lectura += 1

    # --------------------------------------------------------
    # 4. MOVIMIENTOS VOLUNTARIOS
    #
    # 20 sesiones adicionales.
    # --------------------------------------------------------

    indices_extra_mov = random.sample(
        range(TOTAL_EMBARAZOS),
        REGISTROS_EXTRA_MOVIMIENTOS,
    )

    for indice in indices_extra_mov:
        embarazo = embarazos[indice]

        fecha_inicio = date.fromisoformat(
            embarazo["fecha_inicio"]
        )

        semana = random.randint(
            20,
            39,
        )

        id_sesion = crear_id(
            "SES",
            contador_sesion,
            4,
        )

        contador_sesion += 1

        inicio = fecha_en_semana_gestacional(
            fecha_inicio,
            semana,
        )

        duracion = random.randint(
            60,
            120,
        )

        fin = inicio + timedelta(
            minutes=duracion
        )

        sesiones.append(
            {
                "id_sesion":
                    id_sesion,
                "id_embarazo":
                    embarazo["id_embarazo"],
                "id_dispositivo":
                    dispositivo_por_embarazo[
                        embarazo["id_embarazo"]
                    ],
                "fecha_inicio":
                    inicio.isoformat(),
                "fecha_fin":
                    fin.isoformat(),
                "estado_sesion":
                    "COMPLETADA",
                "origen_dato":
                    "API",
                "_tipo":
                    "MOVIMIENTOS",
            }
        )

        lecturas.append(
            construir_lectura(
                contador_lectura=
                    contador_lectura,
                id_sesion=
                    id_sesion,
                fecha_inicio_embarazo=
                    fecha_inicio,
                captura=
                    fin,
                tipo=
                    "MOVIMIENTOS",
            )
        )

        contador_lectura += 1

    return sesiones, lecturas


# ============================================================
# ASIGNACIÓN DE ALERTAS Y VALORES
# ============================================================

def aplicar_valores_y_alertas(
    sesiones: list[dict[str, Any]],
    lecturas: list[dict[str, Any]],
) -> None:
    """
    Mantiene coherencia dentro de cada sesión.

    Las cinco lecturas HR/SpO2 de una misma sesión
    comparten el mismo nivel general de semáforo.

    Las sesiones de movimientos tienen una única
    lectura consolidada.
    """
    lecturas_por_sesion = defaultdict(list)

    for lectura in lecturas:
        lecturas_por_sesion[
            lectura["id_sesion"]
        ].append(lectura)

    sesiones_hr = [
        s["id_sesion"]
        for s in sesiones
        if s["_tipo"] == "HR_SPO2"
    ]

    sesiones_mov = [
        s["id_sesion"]
        for s in sesiones
        if s["_tipo"] == "MOVIMIENTOS"
    ]

    assert (
        len(sesiones_hr)
        == TOTAL_SESIONES_HR_SPO2
    )

    assert (
        len(sesiones_mov)
        == TOTAL_MOVIMIENTOS
    )

    estados_hr = (
        ["OK"] * SESIONES_HR_OK
        + ["WARNING"] * SESIONES_HR_WARNING
        + ["ERROR"] * SESIONES_HR_ERROR
    )

    estados_mov = (
        ["OK"] * MOVIMIENTOS_OK
        + ["WARNING"] * MOVIMIENTOS_WARNING
        + ["ERROR"] * MOVIMIENTOS_ERROR
    )

    random.shuffle(estados_hr)
    random.shuffle(estados_mov)

    id_semaforo_por_estado = {
        "OK": "SEM-OK",
        "WARNING": "SEM-WARNING",
        "ERROR": "SEM-ERROR",
    }

    for id_sesion, estado in zip(
        sesiones_hr,
        estados_hr,
    ):
        for lectura in lecturas_por_sesion[
            id_sesion
        ]:
            hr, spo2 = generar_valores_hr_spo2(
                estado
            )

            lectura["id_semaforo"] = (
                id_semaforo_por_estado[estado]
            )

            lectura["hr_valor"] = hr
            lectura["spo2_valor"] = spo2
            lectura["mov_valor"] = None

            del lectura["_tipo"]

    for id_sesion, estado in zip(
        sesiones_mov,
        estados_mov,
    ):
        lectura = lecturas_por_sesion[
            id_sesion
        ][0]

        lectura["id_semaforo"] = (
            id_semaforo_por_estado[estado]
        )

        lectura["hr_valor"] = None
        lectura["spo2_valor"] = None

        lectura["mov_valor"] = (
            generar_movimientos(estado)
        )

        del lectura["_tipo"]

    for sesion in sesiones:
        del sesion["_tipo"]


# ============================================================
# VALIDACIONES INTERNAS
# ============================================================

def validar_dataset(
    *,
    clinicas,
    medicos,
    usuarios_admin,
    dispositivos,
    pacientes,
    embarazos,
    seguimientos,
    asignaciones,
    paciente_factor_riesgo,
    sesiones,
    lecturas,
    tiempo_gestacional,
    telefonos_paciente,
    telefonos_medico,
    usuarios_medicos,
    usuarios_pacientes,
    usuario_medico,
    usuario_paciente,
) -> None:

    assert len(clinicas) == TOTAL_CLINICAS
    assert len(medicos) == TOTAL_MEDICOS

    assert (
        len(usuarios_admin)
        == TOTAL_ADMINISTRADORES
    )

    assert (
        len(dispositivos)
        == TOTAL_DISPOSITIVOS
    )

    assert len(pacientes) == TOTAL_GESTANTES
    assert len(embarazos) == TOTAL_EMBARAZOS
    assert len(seguimientos) == TOTAL_EMBARAZOS
    assert len(asignaciones) == TOTAL_EMBARAZOS

    assert len(sesiones) == TOTAL_SESIONES
    assert len(lecturas) == TOTAL_REGISTROS
    assert len(lecturas) <= MAXIMO_REGISTROS

    lecturas_hr = [
        x
        for x in lecturas
        if x["hr_valor"] is not None
    ]

    lecturas_mov = [
        x
        for x in lecturas
        if x["mov_valor"] is not None
    ]

    assert len(lecturas_hr) == TOTAL_HR_SPO2

    assert (
        len(lecturas_mov)
        == TOTAL_MOVIMIENTOS
    )

    assert all(
        x["mov_valor"] is None
        for x in lecturas_hr
    )

    assert all(
        x["hr_valor"] is None
        and x["spo2_valor"] is None
        for x in lecturas_mov
    )

    semanas_por_id_tiempo = {
        fila["id_tiempo_gest"]:
            fila["semana_gestacion"]
        for fila in tiempo_gestacional
    }

    assert all(
        semanas_por_id_tiempo[
            x["id_tiempo_gest"]
        ] >= 20
        for x in lecturas_mov
    )

    # Identificadores únicos
    for coleccion, campo in [
        (pacientes, "id_paciente"),
        (embarazos, "id_embarazo"),
        (dispositivos, "id_dispositivo"),
        (seguimientos, "id_seguimiento"),
        (asignaciones, "id_asignacion"),
        (sesiones, "id_sesion"),
        (lecturas, "id_lectura"),
    ]:
        valores = [
            x[campo]
            for x in coleccion
        ]

        assert (
            len(valores)
            == len(set(valores))
        )

    # Dispositivos únicos
    codigos = [
        x["codigo_dispositivo"]
        for x in dispositivos
    ]

    assert (
        len(codigos)
        == len(set(codigos))
    )

    # Distribución global de alertas
    codigo_por_id = {
        "SEM-OK": "OK",
        "SEM-WARNING": "WARNING",
        "SEM-ERROR": "ERROR",
    }

    alertas = Counter(
        codigo_por_id[
            x["id_semaforo"]
        ]
        for x in lecturas
    )

    assert alertas["OK"] == ALERTAS_OK

    assert (
        alertas["WARNING"]
        == ALERTAS_WARNING
    )

    assert (
        alertas["ERROR"]
        == ALERTAS_ERROR
    )

    # Factores de riesgo 14 / 9 / 7
    factores_por_embarazo = defaultdict(int)

    for relacion in paciente_factor_riesgo:
        factores_por_embarazo[
            relacion["id_embarazo"]
        ] += 1

    sin_factor = sum(
        1
        for embarazo in embarazos
        if factores_por_embarazo[
            embarazo["id_embarazo"]
        ] == 0
    )

    un_factor = sum(
        1
        for embarazo in embarazos
        if factores_por_embarazo[
            embarazo["id_embarazo"]
        ] == 1
    )

    dos_factores = sum(
        1
        for embarazo in embarazos
        if factores_por_embarazo[
            embarazo["id_embarazo"]
        ] == 2
    )

    assert sin_factor == RIESGO_SIN_FACTOR
    assert un_factor == RIESGO_UN_FACTOR
    assert dos_factores == RIESGO_DOS_FACTORES

    # 10 gestantes por clínica
    por_clinica = Counter(
        x["id_clinica"]
        for x in embarazos
    )

    assert set(
        por_clinica.values()
    ) == {10}

    # 6 embarazos por médico
    por_medico = Counter(
        x["id_medico"]
        for x in seguimientos
    )

    assert set(
        por_medico.values()
    ) == {6}

    # Un dispositivo por embarazo
    asignaciones_por_embarazo = Counter(
        x["id_embarazo"]
        for x in asignaciones
    )

    asignaciones_por_dispositivo = Counter(
        x["id_dispositivo"]
        for x in asignaciones
    )

    assert set(
        asignaciones_por_embarazo.values()
    ) == {1}

    assert set(
        asignaciones_por_dispositivo.values()
    ) == {1}

    # Sincronización diferida
    diferidas = 0

    for lectura in lecturas:
        captura = datetime.fromisoformat(
            lectura["fecha_hora_captura"]
        )

        sincronizacion = datetime.fromisoformat(
            lectura[
                "fecha_hora_sincronizacion"
            ]
        )

        assert sincronizacion >= captura

        if (
            sincronizacion - captura
            >= timedelta(hours=6)
        ):
            diferidas += 1

    assert diferidas > 0

    # Datos en los tres trimestres
    sesion_por_id = {
        x["id_sesion"]: x
        for x in sesiones
    }

    embarazo_por_id = {
        x["id_embarazo"]: x
        for x in embarazos
    }

    trimestres_por_paciente = defaultdict(set)

    for lectura in lecturas:
        sesion = sesion_por_id[
            lectura["id_sesion"]
        ]

        embarazo = embarazo_por_id[
            sesion["id_embarazo"]
        ]

        semana = semanas_por_id_tiempo[
            lectura["id_tiempo_gest"]
        ]

        trimestres_por_paciente[
            embarazo["id_paciente"]
        ].add(
            calcular_trimestre(
                semana
            )
        )

    assert all(
        trimestres_por_paciente[
            crear_id("PAC", i)
        ] == {1, 2, 3}
        for i in range(
            1,
            TOTAL_GESTANTES + 1,
        )
    )

    # Toda lectura debe pertenecer
    # al periodo del embarazo
    for lectura in lecturas:
        sesion = sesion_por_id[
            lectura["id_sesion"]
        ]

        embarazo = embarazo_por_id[
            sesion["id_embarazo"]
        ]

        inicio = date.fromisoformat(
            embarazo["fecha_inicio"]
        )

        fpp = date.fromisoformat(
            embarazo["fecha_probable_parto"]
        )

        captura = datetime.fromisoformat(
            lectura["fecha_hora_captura"]
        ).date()

        assert inicio <= captura <= fpp

    # Número de lecturas por sesión
    conteo_por_sesion = Counter(
        x["id_sesion"]
        for x in lecturas
    )

    sesiones_hr = [
        id_sesion
        for id_sesion, cantidad
        in conteo_por_sesion.items()
        if cantidad == LECTURAS_POR_SESION_HR_SPO2
    ]

    sesiones_mov = [
        id_sesion
        for id_sesion, cantidad
        in conteo_por_sesion.items()
        if cantidad == 1
    ]

    assert (
        len(sesiones_hr)
        == TOTAL_SESIONES_HR_SPO2
    )

    assert (
        len(sesiones_mov)
        == TOTAL_MOVIMIENTOS
    )

    # Las cinco lecturas de una sesión HR/SpO2
    # deben compartir el mismo nivel de alerta
    for id_sesion in sesiones_hr:
        estados = {
            x["id_semaforo"]
            for x in lecturas
            if x["id_sesion"] == id_sesion
        }

        assert len(estados) == 1

    # Duración de movimientos: 60-120 minutos
    ids_sesiones_mov = set(sesiones_mov)

    for sesion in sesiones:
        if sesion["id_sesion"] in ids_sesiones_mov:
            inicio = datetime.fromisoformat(
                sesion["fecha_inicio"]
            )

            fin = datetime.fromisoformat(
                sesion["fecha_fin"]
            )

            minutos = int(
                (
                    fin - inicio
                ).total_seconds()
                / 60
            )

            assert 60 <= minutos <= 120

    # --------------------------------------------------------
    # Teléfonos: FK válida, enum permitido, un único
    # contacto principal por persona.
    # --------------------------------------------------------

    ids_paciente_validos = {
        x["id_paciente"] for x in pacientes
    }

    ids_medico_validos = {
        x["id_medico"] for x in medicos
    }

    for telefono in telefonos_paciente:
        assert (
            telefono["id_paciente"]
            in ids_paciente_validos
        )

        assert (
            telefono["tipo_contacto"]
            in TIPOS_CONTACTO
        )

    for telefono in telefonos_medico:
        assert (
            telefono["id_medico"]
            in ids_medico_validos
        )

        assert (
            telefono["tipo_contacto"]
            in TIPOS_CONTACTO
        )

    principales_por_paciente = defaultdict(int)

    for telefono in telefonos_paciente:
        if telefono["principal"]:
            principales_por_paciente[
                telefono["id_paciente"]
            ] += 1

    assert all(
        principales_por_paciente[
            paciente["id_paciente"]
        ] == 1
        for paciente in pacientes
    )

    principales_por_medico = defaultdict(int)

    for telefono in telefonos_medico:
        if telefono["principal"]:
            principales_por_medico[
                telefono["id_medico"]
            ] += 1

    assert all(
        principales_por_medico[
            medico["id_medico"]
        ] == 1
        for medico in medicos
    )

    ids_telefono_paciente = [
        x["id_telefono_paciente"]
        for x in telefonos_paciente
    ]

    assert (
        len(ids_telefono_paciente)
        == len(set(ids_telefono_paciente))
    )

    ids_telefono_medico = [
        x["id_telefono_medico"]
        for x in telefonos_medico
    ]

    assert (
        len(ids_telefono_medico)
        == len(set(ids_telefono_medico))
    )

    # --------------------------------------------------------
    # Usuarios: cantidades, FK, roles y ausencia de huérfanos.
    # --------------------------------------------------------

    assert (
        len(usuarios_medicos)
        == TOTAL_USUARIOS_MEDICO
    )

    assert (
        len(usuarios_pacientes)
        == TOTAL_USUARIOS_PACIENTE
    )

    todos_usuarios = (
        usuarios_admin
        + usuarios_medicos
        + usuarios_pacientes
    )

    assert len(todos_usuarios) == TOTAL_USUARIOS

    ids_usuario = [
        x["id_usuario"] for x in todos_usuarios
    ]

    assert (
        len(ids_usuario)
        == len(set(ids_usuario))
    )

    ids_rol_validos = {"ROL-001", "ROL-002", "ROL-003"}

    assert all(
        x["id_rol"] in ids_rol_validos
        for x in todos_usuarios
    )

    assert (
        sum(
            1
            for x in todos_usuarios
            if x["id_rol"] == "ROL-001"
        )
        == TOTAL_ADMINISTRADORES
    )

    assert (
        sum(
            1
            for x in todos_usuarios
            if x["id_rol"] == "ROL-002"
        )
        == TOTAL_MEDICOS
    )

    assert (
        sum(
            1
            for x in todos_usuarios
            if x["id_rol"] == "ROL-003"
        )
        == TOTAL_GESTANTES
    )

    ids_usuario_validos = set(ids_usuario)

    for relacion in usuario_medico:
        assert (
            relacion["id_usuario"]
            in ids_usuario_validos
        )

        assert (
            relacion["id_medico"]
            in ids_medico_validos
        )

    for relacion in usuario_paciente:
        assert (
            relacion["id_usuario"]
            in ids_usuario_validos
        )

        assert (
            relacion["id_paciente"]
            in ids_paciente_validos
        )

    assert len(usuario_medico) == TOTAL_MEDICOS

    assert (
        len(usuario_paciente)
        == TOTAL_GESTANTES
    )

    medicos_con_cuenta = [
        x["id_medico"] for x in usuario_medico
    ]

    assert (
        len(medicos_con_cuenta)
        == len(set(medicos_con_cuenta))
    )

    pacientes_con_cuenta = [
        x["id_paciente"] for x in usuario_paciente
    ]

    assert (
        len(pacientes_con_cuenta)
        == len(set(pacientes_con_cuenta))
    )


# ============================================================
# EXPORTACIÓN
# ============================================================

def exportar_dataset(
    *,
    clinicas,
    especialidades,
    medicos,
    medico_clinica,
    roles,
    usuarios_admin,
    semaforos,
    tiempo_gestacional,
    pacientes,
    embarazos,
    seguimientos,
    factores_riesgo,
    paciente_factor_riesgo,
    dispositivos,
    asignaciones,
    sesiones,
    lecturas,
    telefonos_paciente,
    telefonos_medico,
    usuarios_medicos,
    usuarios_pacientes,
    usuario_medico,
    usuario_paciente,
) -> None:

    CARPETA_SALIDA.mkdir(
        parents=True,
        exist_ok=True,
    )

    dataset = {
        "metadata": {
            "nombre":
                "Dataset simulado FetalAlert",
            "semilla":
                SEMILLA,
            "total_sesiones":
                len(sesiones),
            "total_registros_biometricos":
                len(lecturas),
            "uso":
                "Validación técnica y funcional",
        },
        "clinicas":
            clinicas,
        "especialidades":
            especialidades,
        "medicos":
            medicos,
        "medico_clinica":
            medico_clinica,
        "roles":
            roles,
        "usuarios_administradores":
            usuarios_admin,
        "semaforos":
            semaforos,
        "tiempo_gestacional":
            tiempo_gestacional,
        "pacientes":
            pacientes,
        "embarazos":
            embarazos,
        "seguimiento_clinico":
            seguimientos,
        "factores_riesgo":
            factores_riesgo,
        "paciente_factor_riesgo":
            paciente_factor_riesgo,
        "dispositivos":
            dispositivos,
        "asignacion_dispositivo":
            asignaciones,
        "sesiones_monitoreo":
            sesiones,
        "lecturas_biometricas":
            lecturas,
        "telefonos_paciente":
            telefonos_paciente,
        "telefonos_medico":
            telefonos_medico,
        "usuarios":
            usuarios_admin
            + usuarios_medicos
            + usuarios_pacientes,
        "usuario_medico":
            usuario_medico,
        "usuario_paciente":
            usuario_paciente,
    }

    ruta_json = (
        CARPETA_SALIDA
        / "dataset_fetalalert.json"
    )

    with ruta_json.open(
        "w",
        encoding="utf-8",
    ) as archivo:
        json.dump(
            dataset,
            archivo,
            ensure_ascii=False,
            indent=2,
        )

    exportar_csv("clinicas", clinicas)
    exportar_csv(
        "especialidades",
        especialidades,
    )
    exportar_csv("medicos", medicos)
    exportar_csv(
        "medico_clinica",
        medico_clinica,
    )
    exportar_csv("roles", roles)
    exportar_csv(
        "usuarios_administradores",
        usuarios_admin,
    )
    exportar_csv("semaforos", semaforos)
    exportar_csv(
        "tiempo_gestacional",
        tiempo_gestacional,
    )
    exportar_csv("pacientes", pacientes)
    exportar_csv("embarazos", embarazos)
    exportar_csv(
        "seguimiento_clinico",
        seguimientos,
    )
    exportar_csv(
        "factores_riesgo",
        factores_riesgo,
    )
    exportar_csv(
        "paciente_factor_riesgo",
        paciente_factor_riesgo,
    )
    exportar_csv(
        "dispositivos",
        dispositivos,
    )
    exportar_csv(
        "asignacion_dispositivo",
        asignaciones,
    )
    exportar_csv(
        "sesiones_monitoreo",
        sesiones,
    )
    exportar_csv(
        "lecturas_biometricas",
        lecturas,
    )
    exportar_csv(
        "telefonos_paciente",
        telefonos_paciente,
    )
    exportar_csv(
        "telefonos_medico",
        telefonos_medico,
    )
    exportar_csv(
        "usuarios",
        usuarios_admin
        + usuarios_medicos
        + usuarios_pacientes,
    )
    exportar_csv(
        "usuario_medico",
        usuario_medico,
    )
    exportar_csv(
        "usuario_paciente",
        usuario_paciente,
    )


# ============================================================
# EJECUCIÓN PRINCIPAL
# ============================================================

def main() -> None:

    random.seed(SEMILLA)

    clinicas = generar_clinicas()
    especialidades = generar_especialidades()
    medicos = generar_medicos()
    medico_clinica = generar_medico_clinica()
    roles = generar_roles()

    usuarios_admin = (
        generar_usuarios_administradores()
    )

    semaforos = generar_semaforos()

    tiempo_gestacional = (
        generar_tiempo_gestacional()
    )

    pacientes = generar_pacientes()

    telefonos_paciente = (
        generar_telefonos_paciente(pacientes)
    )

    telefonos_medico = (
        generar_telefonos_medico(medicos)
    )

    usuarios_medicos = (
        generar_usuarios_medicos(medicos)
    )

    usuarios_pacientes = (
        generar_usuarios_pacientes(pacientes)
    )

    usuario_medico = generar_usuario_medico(
        usuarios_medicos,
        medicos,
    )

    usuario_paciente = generar_usuario_paciente(
        usuarios_pacientes,
        pacientes,
    )

    embarazos = generar_embarazos()

    seguimientos = (
        generar_seguimiento_clinico(
            embarazos
        )
    )

    factores_riesgo = (
        generar_factores_riesgo_catalogo()
    )

    paciente_factor_riesgo = (
        generar_paciente_factor_riesgo(
            embarazos
        )
    )

    dispositivos = generar_dispositivos(
        embarazos
    )

    asignaciones = (
        generar_asignaciones_dispositivo(
            embarazos
        )
    )

    sesiones, lecturas = (
        generar_sesiones_y_lecturas(
            embarazos
        )
    )

    aplicar_valores_y_alertas(
        sesiones,
        lecturas,
    )

    validar_dataset(
        clinicas=clinicas,
        medicos=medicos,
        usuarios_admin=usuarios_admin,
        dispositivos=dispositivos,
        pacientes=pacientes,
        embarazos=embarazos,
        seguimientos=seguimientos,
        asignaciones=asignaciones,
        paciente_factor_riesgo=
            paciente_factor_riesgo,
        sesiones=sesiones,
        lecturas=lecturas,
        tiempo_gestacional=
            tiempo_gestacional,
        telefonos_paciente=telefonos_paciente,
        telefonos_medico=telefonos_medico,
        usuarios_medicos=usuarios_medicos,
        usuarios_pacientes=usuarios_pacientes,
        usuario_medico=usuario_medico,
        usuario_paciente=usuario_paciente,
    )

    exportar_dataset(
        clinicas=clinicas,
        especialidades=especialidades,
        medicos=medicos,
        medico_clinica=medico_clinica,
        roles=roles,
        usuarios_admin=usuarios_admin,
        semaforos=semaforos,
        tiempo_gestacional=
            tiempo_gestacional,
        pacientes=pacientes,
        embarazos=embarazos,
        seguimientos=seguimientos,
        factores_riesgo=factores_riesgo,
        paciente_factor_riesgo=
            paciente_factor_riesgo,
        dispositivos=dispositivos,
        asignaciones=asignaciones,
        sesiones=sesiones,
        lecturas=lecturas,
        telefonos_paciente=telefonos_paciente,
        telefonos_medico=telefonos_medico,
        usuarios_medicos=usuarios_medicos,
        usuarios_pacientes=usuarios_pacientes,
        usuario_medico=usuario_medico,
        usuario_paciente=usuario_paciente,
    )

    codigo_por_id = {
        "SEM-OK": "OK",
        "SEM-WARNING": "WARNING",
        "SEM-ERROR": "ERROR",
    }

    alertas = Counter(
        codigo_por_id[
            x["id_semaforo"]
        ]
        for x in lecturas
    )

    total_hr = sum(
        1
        for x in lecturas
        if x["hr_valor"] is not None
    )

    total_mov = sum(
        1
        for x in lecturas
        if x["mov_valor"] is not None
    )

    print()
    print(
        "Dataset simulado FetalAlert "
        "generado correctamente."
    )
    print()
    print(f"Clínicas: {len(clinicas)}")
    print(f"Médicos: {len(medicos)}")
    print(
        f"Administradores: "
        f"{len(usuarios_admin)}"
    )
    print(f"Gestantes: {len(pacientes)}")
    print(f"Embarazos: {len(embarazos)}")
    print(
        f"Dispositivos: "
        f"{len(dispositivos)}"
    )
    print(f"Sesiones: {len(sesiones)}")
    print(
        f"Lecturas biométricas: "
        f"{len(lecturas)}"
    )
    print(f"  HR/SpO₂: {total_hr}")
    print(
        f"  Movimientos fetales: "
        f"{total_mov}"
    )
    print()
    print(
        "Alertas: "
        f"{alertas['OK']} OK / "
        f"{alertas['WARNING']} WARNING / "
        f"{alertas['ERROR']} ERROR"
    )
    print()
    total_usuarios = (
        len(usuarios_admin)
        + len(usuarios_medicos)
        + len(usuarios_pacientes)
    )
    print(
        f"Usuarios: {total_usuarios} "
        f"(ADMIN {len(usuarios_admin)} / "
        f"MEDICO {len(usuarios_medicos)} / "
        f"PACIENTE {len(usuarios_pacientes)})"
    )
    print(
        f"Teléfonos paciente: "
        f"{len(telefonos_paciente)}"
    )
    print(
        f"Teléfonos médico: "
        f"{len(telefonos_medico)}"
    )
    print()
    print(
        "Archivos generados en: "
        f"{CARPETA_SALIDA}"
    )


if __name__ == "__main__":
    main()