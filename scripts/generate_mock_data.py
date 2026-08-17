from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ============================================================
# CONFIGURACIÓN DEL DATASET
# ============================================================

SEMILLA = 20260810

# PK enteras determinísticas: cada tabla reinicia su numeración en 100
# (alineado con Integer autoincrement en SQLAlchemy real; aquí se fija
# de forma determinística por reproducibilidad e inspección humana).
ID_BASE = 100

ZONA_HORARIA = timezone.utc

TOTAL_CLINICAS = 3
TOTAL_MEDICOS = 5
TOTAL_ADMINISTRADORES = 2
TOTAL_GESTANTES = 30
TOTAL_EMBARAZOS = 30
TOTAL_DISPOSITIVOS = 30

# HR / SpO2
LECTURAS_POR_SESION_HR_SPO2 = 5
SESIONES_EXTRA_HR_SPO2 = 22
TOTAL_SESIONES_HR_SPO2 = 112
TOTAL_HR_SPO2 = 560

# Movimientos fetales
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
TOTAL_USUARIOS_MEDICO = TOTAL_MEDICOS
TOTAL_USUARIOS_PACIENTE = TOTAL_GESTANTES
TOTAL_USUARIOS = (
    TOTAL_ADMINISTRADORES
    + TOTAL_USUARIOS_MEDICO
    + TOTAL_USUARIOS_PACIENTE
)

# Tipos de contacto: alineados con el enum TipoContacto real de SQLAlchemy
# (backend/app/models/enums.py en origin/feature/sprint-4-sqlalchemy-models).
# "FIJO" fue eliminado deliberadamente del enum real y no debe usarse.
TIPOS_CONTACTO = ("CELULAR", "TELEFONO_DOMICILIO", "CORREO_ALTERNO")

# Estados de embarazo: alineados con el enum EstadoEmbarazo real
# (ACTIVO/FINALIZADO/SUSPENDIDO). La distribución 20/8/2 es una regla
# técnica de simulación y no representa prevalencia clínica.
EMBARAZOS_ACTIVOS = 20
EMBARAZOS_FINALIZADOS = 8
EMBARAZOS_SUSPENDIDOS = 2

# Estados de dispositivo: alineados con el enum EstadoDispositivo real.
# Un dispositivo queda ASIGNADO mientras el embarazo sigue ACTIVO, y
# vuelve a DISPONIBLE cuando el embarazo se cierra (FINALIZADO/SUSPENDIDO).
DISPOSITIVOS_ASIGNADOS = EMBARAZOS_ACTIVOS
DISPOSITIVOS_DISPONIBLES = EMBARAZOS_FINALIZADOS + EMBARAZOS_SUSPENDIDOS


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

def id_secuencial(indice_uno_basado: int) -> int:
    """
    PK entera determinística dentro de una entidad: 100, 101, 102...

    Cada tabla reinicia su propia numeración porque las PK
    pertenecen a tablas distintas.
    """
    return ID_BASE + indice_uno_basado - 1


def fecha_en_semana_gestacional(
    fecha_inicio: date,
    semana: int,
) -> datetime:
    """
    Genera una fecha y hora (UTC, offset-aware) dentro de la
    semana gestacional indicada.
    """
    dias_adicionales = random.randint(0, 6)
    hora = random.randint(8, 19)
    minuto = random.randint(0, 59)

    return (
        datetime.combine(
            fecha_inicio,
            datetime.min.time(),
            tzinfo=ZONA_HORARIA,
        )
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

    ``direccion_fisica`` es el nombre físico real de la columna
    en SQLAlchemy (antes se generaba como ``calle``).
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
        direccion_fisica,
    ) in enumerate(ubicaciones, start=1):

        clinicas.append(
            {
                "id_clinica": id_secuencial(i),
                "nombre_clinica":
                    f"Clínica Rural Simulada {i:02d}",
                "ruc": f"SIM-RUC-{i:03d}",
                "provincia": provincia,
                "distrito": distrito,
                "corregimiento": corregimiento,
                "direccion_fisica": direccion_fisica,
            }
        )

    return clinicas


def generar_especialidades() -> list[dict[str, Any]]:
    return [
        {
            "id_especialidad": id_secuencial(1),
            "nombre_especialidad":
                "Ginecología y Obstetricia",
        }
    ]


def generar_medicos(id_especialidad: int) -> list[dict[str, Any]]:
    return [
        {
            "id_medico": id_secuencial(i),
            "id_especialidad": id_especialidad,
            "primer_nombre": f"Médico{i:02d}",
            "segundo_nombre": None,
            "apellido_paterno": "Simulado",
            "apellido_materno": f"{i:02d}",
            "email_med":
                f"medico{i:02d}@example.com",
        }
        for i in range(1, TOTAL_MEDICOS + 1)
    ]


def generar_medico_clinica(
    medicos: list[dict[str, Any]],
    clinicas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "id_medico": medico["id_medico"],
            "id_clinica": clinica["id_clinica"],
            "fecha_inicio": "2025-01-01",
            "fecha_final": None,
            "activo": True,
        }
        for medico in medicos
        for clinica in clinicas
    ]


def generar_telefonos_medico(
    medicos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Genera al menos un contacto principal por médico.

    Cada médico recibe un contacto CELULAR principal y un
    contacto TELEFONO_DOMICILIO secundario (el enum real ya
    no admite "FIJO"), para ejercitar el escenario de múltiples
    contactos sin ambigüedad sobre cuál es principal.
    """
    telefonos = []
    contador = 1

    for i, medico in enumerate(medicos, start=1):
        telefonos.append(
            {
                "id_telefono_medico":
                    id_secuencial(contador),
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
                    id_secuencial(contador),
                "id_medico":
                    medico["id_medico"],
                "tipo_contacto":
                    "TELEFONO_DOMICILIO",
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
                    id_secuencial(i),
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
                    id_secuencial(contador),
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
                        id_secuencial(contador),
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
            "id_rol": id_secuencial(1),
            "nombre_rol": "ADMIN",
        },
        {
            "id_rol": id_secuencial(2),
            "nombre_rol": "MEDICO",
        },
        {
            "id_rol": id_secuencial(3),
            "nombre_rol": "PACIENTE",
        },
    ]


def generar_usuarios_administradores(
    id_rol_admin: int,
) -> list[dict[str, Any]]:
    return [
        {
            "id_usuario":
                id_secuencial(i),
            "email":
                f"admin{i:02d}@example.com",
            "password_hash":
                "HASH_SIMULADO_NO_USAR_EN_PRODUCCION",
            "id_rol":
                id_rol_admin,
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
    id_rol_medico: int,
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
                id_secuencial(
                    indice_inicial + offset,
                ),
            "email":
                medico["email_med"],
            "password_hash":
                "HASH_SIMULADO_NO_USAR_EN_PRODUCCION",
            "id_rol":
                id_rol_medico,
            "activo":
                True,
        }
        for offset, medico in enumerate(medicos)
    ]


def generar_usuarios_pacientes(
    pacientes: list[dict[str, Any]],
    id_rol_paciente: int,
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
                id_secuencial(
                    indice_inicial + offset,
                ),
            "email":
                paciente["email_pac"],
            "password_hash":
                "HASH_SIMULADO_NO_USAR_EN_PRODUCCION",
            "id_rol":
                id_rol_paciente,
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
    definiciones = [
        (
            "OK",
            "Normal",
            "#008000",
            1,
            "Lectura dentro del rango esperado.",
        ),
        (
            "WARNING",
            "Precaución",
            "#FFC107",
            2,
            "Lectura que requiere atención.",
        ),
        (
            "ERROR",
            "Alerta",
            "#D32F2F",
            3,
            "Lectura fuera del rango esperado.",
        ),
    ]

    return [
        {
            "id_semaforo": id_secuencial(i),
            "codigo_nivel": codigo,
            "etiqueta_visual": etiqueta,
            "color_hex": color,
            "prioridad": prioridad,
            "mensaje_app": mensaje,
            "version_referencia": "SIM-1.0",
        }
        for i, (
            codigo,
            etiqueta,
            color,
            prioridad,
            mensaje,
        ) in enumerate(definiciones, start=1)
    ]


def generar_tiempo_gestacional(
) -> list[dict[str, Any]]:
    filas = []

    for semana in range(1, 41):
        trimestre = calcular_trimestre(semana)

        filas.append(
            {
                "id_tiempo_gest":
                    id_secuencial(semana),
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
    definiciones = [
        ("HTA", "Hipertensión arterial"),
        ("DMG", "Diabetes gestacional"),
        ("OBS", "Obesidad"),
    ]

    return [
        {
            "id_factor_riesgo": id_secuencial(i),
            "clave_factor": clave,
            "nombre_factor": nombre,
            "descripcion":
                "Factor de riesgo simulado "
                "para validación técnica.",
            "activo": True,
        }
        for i, (clave, nombre) in enumerate(
            definiciones,
            start=1,
        )
    ]


# ============================================================
# EMBARAZOS, SEGUIMIENTO Y DISPOSITIVOS
# ============================================================

def estado_para_embarazo(indice_uno_basado: int) -> str:
    """
    Distribución determinística 20 ACTIVO / 8 FINALIZADO / 2 SUSPENDIDO.

    Regla técnica de simulación aprobada para SCRUM-54; no
    representa prevalencia clínica ni epidemiológica.
    """
    if indice_uno_basado <= EMBARAZOS_ACTIVOS:
        return "ACTIVO"

    if indice_uno_basado <= EMBARAZOS_ACTIVOS + EMBARAZOS_FINALIZADOS:
        return "FINALIZADO"

    return "SUSPENDIDO"


def generar_embarazos(
    pacientes: list[dict[str, Any]],
    clinicas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    ``fecha_cierre`` se deja en None aquí para todos los embarazos.

    Para los que terminan FINALIZADO/SUSPENDIDO se calcula después,
    en ``finalizar_fecha_cierre_embarazos``, una vez que existen las
    sesiones/lecturas reales de cada embarazo (para que el cierre sea
    coherente con el último evento registrado).
    """
    ids_clinica = [c["id_clinica"] for c in clinicas]

    embarazos = []
    fecha_base = date(2025, 1, 6)

    for i, paciente in enumerate(pacientes, start=1):
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

        id_clinica = ids_clinica[(i - 1) // 10]

        embarazos.append(
            {
                "id_embarazo":
                    id_secuencial(i),
                "id_paciente":
                    paciente["id_paciente"],
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
                    estado_para_embarazo(i),
                "fecha_cierre":
                    None,
            }
        )

    return embarazos


def generar_seguimiento_clinico(
    embarazos: list[dict[str, Any]],
    medicos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ids_medico = [m["id_medico"] for m in medicos]

    seguimientos = []

    for i, embarazo in enumerate(
        embarazos,
        start=1,
    ):
        seguimientos.append(
            {
                "id_seguimiento":
                    id_secuencial(i),
                "id_embarazo":
                    embarazo["id_embarazo"],
                "id_medico":
                    ids_medico[(i - 1) % TOTAL_MEDICOS],
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
    """
    El estado del dispositivo depende del estado final del
    embarazo (debe llamarse después de
    ``finalizar_fecha_cierre_embarazos``):

    - Embarazo ACTIVO   -> Dispositivo ASIGNADO
    - Embarazo FINALIZADO/SUSPENDIDO -> Dispositivo DISPONIBLE
    """
    dispositivos = []

    for i, embarazo in enumerate(
        embarazos,
        start=1,
    ):
        fecha_registro_dia = (
            date.fromisoformat(
                embarazo["fecha_inicio"]
            )
            - timedelta(days=7)
        )

        fecha_registro = datetime.combine(
            fecha_registro_dia,
            datetime.min.time(),
            tzinfo=ZONA_HORARIA,
        )

        estado = (
            "ASIGNADO"
            if embarazo["estado_embarazo"] == "ACTIVO"
            else "DISPONIBLE"
        )

        dispositivos.append(
            {
                "id_dispositivo":
                    id_secuencial(i),
                "id_clinica":
                    embarazo["id_clinica"],
                "codigo_dispositivo":
                    f"FA-SIM-{i:03d}",
                "modelo":
                    "FetalAlert-SIM",
                "version_firmware":
                    "SIM-1.0",
                "estado":
                    estado,
                "fecha_registro":
                    fecha_registro.isoformat(),
            }
        )

    return dispositivos


def generar_asignaciones_dispositivo(
    embarazos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Debe llamarse después de ``finalizar_fecha_cierre_embarazos``:

    - Embarazo ACTIVO -> asignación sigue vigente
      (activo=True, fecha_fin=None).
    - Embarazo FINALIZADO/SUSPENDIDO -> asignación finalizada
      (activo=False, fecha_fin=Embarazo.fecha_cierre).
    """
    asignaciones = []

    for i, embarazo in enumerate(
        embarazos,
        start=1,
    ):
        activo = embarazo["estado_embarazo"] == "ACTIVO"

        asignaciones.append(
            {
                "id_asignacion":
                    id_secuencial(i),
                "id_dispositivo":
                    id_secuencial(i),
                "id_embarazo":
                    embarazo["id_embarazo"],
                "fecha_inicio":
                    embarazo["fecha_inicio"],
                "fecha_fin":
                    None if activo else embarazo["fecha_cierre"],
                "activo":
                    activo,
            }
        )

    return asignaciones


def generar_embarazo_factor_riesgo(
    embarazos: list[dict[str, Any]],
    factores_riesgo: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Distribución:
    - 14 embarazos sin factores
    - 9 con un factor
    - 7 con dos factores

    Entidad real: ``embarazo_factor_riesgo`` (PK compuesta
    id_embarazo + id_factor_riesgo).
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

    ids_factores = [
        f["id_factor_riesgo"] for f in factores_riesgo
    ]

    embarazo_por_id = {
        e["id_embarazo"]: e
        for e in embarazos
    }

    def fecha_diagnostico_para(
        embarazo: dict[str, Any],
    ) -> str:
        return (
            date.fromisoformat(
                embarazo["fecha_inicio"]
            )
            + timedelta(
                weeks=random.randint(8, 24)
            )
        ).isoformat()

    relaciones = []

    for id_embarazo in con_un_factor:
        embarazo = embarazo_por_id[id_embarazo]
        factor = random.choice(ids_factores)

        relaciones.append(
            {
                "id_embarazo":
                    id_embarazo,
                "id_factor_riesgo":
                    factor,
                "fecha_diagnostico":
                    fecha_diagnostico_para(embarazo),
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
        embarazo = embarazo_por_id[id_embarazo]

        for factor in random.sample(ids_factores, 2):
            relaciones.append(
                {
                    "id_embarazo":
                        id_embarazo,
                    "id_factor_riesgo":
                        factor,
                    "fecha_diagnostico":
                        fecha_diagnostico_para(embarazo),
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

# SesionMonitoreo.tipo_sesion es un campo NOT NULL real en
# SQLAlchemy (backend/app/models/enums.py: TipoSesion). Se deriva
# de la modalidad interna ya usada por el generador.
TIPO_SESION_POR_MODALIDAD = {
    "HR_SPO2": "SIGNOS_MATERNOS",
    "MOVIMIENTOS": "MOVIMIENTOS_FETALES",
}


def construir_lectura(
    *,
    contador_lectura: int,
    id_sesion: int,
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
            id_secuencial(contador_lectura),
        "id_sesion":
            id_sesion,
        "id_tiempo_gest":
            id_secuencial(semana),
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
            id_secuencial(i)
        for i, embarazo in enumerate(
            embarazos,
            start=1,
        )
    }

    def nueva_sesion(
        embarazo: dict[str, Any],
        inicio: datetime,
        fin: datetime,
        tipo: str,
    ) -> int:
        nonlocal contador_sesion

        id_sesion = id_secuencial(contador_sesion)
        contador_sesion += 1

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
                "tipo_sesion":
                    TIPO_SESION_POR_MODALIDAD[tipo],
                "fecha_inicio":
                    inicio.isoformat(),
                "fecha_fin":
                    fin.isoformat(),
                "estado_sesion":
                    "COMPLETADA",
                "origen_dato":
                    "DISPOSITIVO",
                "_tipo":
                    tipo,
            }
        )

        return id_sesion

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
            inicio = fecha_en_semana_gestacional(
                fecha_inicio,
                semana,
            )

            fin = inicio + timedelta(
                minutes=5
            )

            id_sesion = nueva_sesion(
                embarazo, inicio, fin, "HR_SPO2"
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

            inicio = fecha_en_semana_gestacional(
                fecha_inicio,
                semana,
            )

            fin = inicio + timedelta(
                minutes=5
            )

            id_sesion = nueva_sesion(
                embarazo, inicio, fin, "HR_SPO2"
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

            id_sesion = nueva_sesion(
                embarazo, inicio, fin, "MOVIMIENTOS"
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

        id_sesion = nueva_sesion(
            embarazo, inicio, fin, "MOVIMIENTOS"
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
    id_semaforo_por_estado: dict[str, int],
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


def finalizar_fecha_cierre_embarazos(
    embarazos: list[dict[str, Any]],
    sesiones: list[dict[str, Any]],
    lecturas: list[dict[str, Any]],
) -> None:
    """
    Calcula ``fecha_cierre`` para los embarazos FINALIZADO/SUSPENDIDO
    a partir de la última captura biométrica real de cada embarazo,
    sin eliminar ni truncar sesiones o lecturas.

    - FINALIZADO: cierre coherente con el fin del seguimiento y con
      ``fecha_probable_parto`` (se usa la fecha posterior entre ambas).
    - SUSPENDIDO: cierre inmediatamente posterior al último evento
      registrado.

    Debe llamarse después de generar y poblar sesiones/lecturas, y
    antes de generar Dispositivo/AsignacionDispositivo (su estado
    depende de este resultado).
    """
    sesion_por_id = {
        s["id_sesion"]: s for s in sesiones
    }

    ultima_captura_por_embarazo: dict[int, date] = {}

    for lectura in lecturas:
        sesion = sesion_por_id[lectura["id_sesion"]]
        id_embarazo = sesion["id_embarazo"]

        captura = datetime.fromisoformat(
            lectura["fecha_hora_captura"]
        ).date()

        actual = ultima_captura_por_embarazo.get(
            id_embarazo
        )

        if actual is None or captura > actual:
            ultima_captura_por_embarazo[id_embarazo] = captura

    for embarazo in embarazos:
        estado = embarazo["estado_embarazo"]

        if estado == "ACTIVO":
            embarazo["fecha_cierre"] = None
            continue

        ultima_captura = ultima_captura_por_embarazo[
            embarazo["id_embarazo"]
        ]

        if estado == "FINALIZADO":
            fecha_probable_parto = date.fromisoformat(
                embarazo["fecha_probable_parto"]
            )
            fecha_cierre = max(
                ultima_captura,
                fecha_probable_parto,
            )
        else:  # SUSPENDIDO
            fecha_cierre = ultima_captura + timedelta(days=1)

        embarazo["fecha_cierre"] = fecha_cierre.isoformat()


# ============================================================
# VALIDACIONES INTERNAS
# ============================================================

def validar_dataset(
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
    embarazo_factor_riesgo,
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

    # --------------------------------------------------------
    # Cantidades básicas
    # --------------------------------------------------------

    assert len(clinicas) == TOTAL_CLINICAS
    assert len(medicos) == TOTAL_MEDICOS
    assert len(usuarios_admin) == TOTAL_ADMINISTRADORES
    assert len(dispositivos) == TOTAL_DISPOSITIVOS
    assert len(pacientes) == TOTAL_GESTANTES
    assert len(embarazos) == TOTAL_EMBARAZOS
    assert len(seguimientos) == TOTAL_EMBARAZOS
    assert len(asignaciones) == TOTAL_EMBARAZOS
    assert len(sesiones) == TOTAL_SESIONES
    assert len(lecturas) == TOTAL_REGISTROS
    assert len(lecturas) <= MAXIMO_REGISTROS

    # --------------------------------------------------------
    # PK enteras, únicas, desde ID_BASE
    # --------------------------------------------------------

    entidades_pk_simple = [
        (clinicas, "id_clinica"),
        (especialidades, "id_especialidad"),
        (medicos, "id_medico"),
        (pacientes, "id_paciente"),
        (embarazos, "id_embarazo"),
        (seguimientos, "id_seguimiento"),
        (dispositivos, "id_dispositivo"),
        (asignaciones, "id_asignacion"),
        (sesiones, "id_sesion"),
        (lecturas, "id_lectura"),
        (roles, "id_rol"),
        (semaforos, "id_semaforo"),
        (tiempo_gestacional, "id_tiempo_gest"),
        (factores_riesgo, "id_factor_riesgo"),
        (telefonos_paciente, "id_telefono_paciente"),
        (telefonos_medico, "id_telefono_medico"),
    ]

    for coleccion, campo in entidades_pk_simple:
        valores = [x[campo] for x in coleccion]

        assert all(isinstance(v, int) for v in valores), campo
        assert len(valores) == len(set(valores)), campo
        assert min(valores) == ID_BASE, campo

    todos_usuarios = (
        usuarios_admin
        + usuarios_medicos
        + usuarios_pacientes
    )

    ids_usuario_todos = [
        x["id_usuario"] for x in todos_usuarios
    ]

    assert all(isinstance(v, int) for v in ids_usuario_todos)
    assert len(ids_usuario_todos) == len(set(ids_usuario_todos))
    assert min(ids_usuario_todos) == ID_BASE
    assert len(todos_usuarios) == TOTAL_USUARIOS

    # --------------------------------------------------------
    # FK enteras, sin huérfanas
    # --------------------------------------------------------

    ids_clinica_validos = {x["id_clinica"] for x in clinicas}
    ids_medico_validos = {x["id_medico"] for x in medicos}
    ids_paciente_validos = {x["id_paciente"] for x in pacientes}
    ids_embarazo_validos = {x["id_embarazo"] for x in embarazos}
    ids_dispositivo_validos = {x["id_dispositivo"] for x in dispositivos}
    ids_sesion_validos = {x["id_sesion"] for x in sesiones}
    ids_tiempo_gest_validos = {x["id_tiempo_gest"] for x in tiempo_gestacional}
    ids_semaforo_validos = {x["id_semaforo"] for x in semaforos}
    ids_factor_riesgo_validos = {x["id_factor_riesgo"] for x in factores_riesgo}
    ids_especialidad_validos = {x["id_especialidad"] for x in especialidades}
    ids_rol_validos = {x["id_rol"] for x in roles}
    ids_usuario_validos = set(ids_usuario_todos)

    for medico in medicos:
        assert isinstance(medico["id_especialidad"], int)
        assert medico["id_especialidad"] in ids_especialidad_validos

    for relacion in medico_clinica:
        assert relacion["id_medico"] in ids_medico_validos
        assert relacion["id_clinica"] in ids_clinica_validos

    for embarazo in embarazos:
        assert embarazo["id_paciente"] in ids_paciente_validos
        assert embarazo["id_clinica"] in ids_clinica_validos

    for seguimiento in seguimientos:
        assert seguimiento["id_embarazo"] in ids_embarazo_validos
        assert seguimiento["id_medico"] in ids_medico_validos

    for relacion in embarazo_factor_riesgo:
        assert relacion["id_embarazo"] in ids_embarazo_validos
        assert relacion["id_factor_riesgo"] in ids_factor_riesgo_validos

    for dispositivo in dispositivos:
        assert dispositivo["id_clinica"] in ids_clinica_validos

    for asignacion in asignaciones:
        assert asignacion["id_dispositivo"] in ids_dispositivo_validos
        assert asignacion["id_embarazo"] in ids_embarazo_validos

    for sesion in sesiones:
        assert sesion["id_embarazo"] in ids_embarazo_validos
        assert sesion["id_dispositivo"] in ids_dispositivo_validos

    for lectura in lecturas:
        assert lectura["id_sesion"] in ids_sesion_validos
        assert lectura["id_tiempo_gest"] in ids_tiempo_gest_validos
        assert lectura["id_semaforo"] in ids_semaforo_validos

    for usuario in todos_usuarios:
        assert usuario["id_rol"] in ids_rol_validos

    for telefono in telefonos_paciente:
        assert telefono["id_paciente"] in ids_paciente_validos
        assert telefono["tipo_contacto"] in TIPOS_CONTACTO

    for telefono in telefonos_medico:
        assert telefono["id_medico"] in ids_medico_validos
        assert telefono["tipo_contacto"] in TIPOS_CONTACTO

    tipos_contacto_usados = {
        t["tipo_contacto"] for t in telefonos_paciente
    } | {
        t["tipo_contacto"] for t in telefonos_medico
    }

    assert "FIJO" not in tipos_contacto_usados

    for relacion in usuario_medico:
        assert relacion["id_usuario"] in ids_usuario_validos
        assert relacion["id_medico"] in ids_medico_validos

    for relacion in usuario_paciente:
        assert relacion["id_usuario"] in ids_usuario_validos
        assert relacion["id_paciente"] in ids_paciente_validos

    # --------------------------------------------------------
    # Contactos: un único principal por persona
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Usuarios: distribución de roles y relaciones 1:1
    # --------------------------------------------------------

    assert len(usuarios_medicos) == TOTAL_USUARIOS_MEDICO
    assert len(usuarios_pacientes) == TOTAL_USUARIOS_PACIENTE

    id_rol_por_nombre = {
        r["nombre_rol"]: r["id_rol"] for r in roles
    }

    assert (
        sum(
            1 for u in todos_usuarios
            if u["id_rol"] == id_rol_por_nombre["ADMIN"]
        )
        == TOTAL_ADMINISTRADORES
    )

    assert (
        sum(
            1 for u in todos_usuarios
            if u["id_rol"] == id_rol_por_nombre["MEDICO"]
        )
        == TOTAL_MEDICOS
    )

    assert (
        sum(
            1 for u in todos_usuarios
            if u["id_rol"] == id_rol_por_nombre["PACIENTE"]
        )
        == TOTAL_GESTANTES
    )

    assert len(usuario_medico) == TOTAL_MEDICOS
    assert len(usuario_paciente) == TOTAL_GESTANTES

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

    # --------------------------------------------------------
    # Geografía de Clinica
    # --------------------------------------------------------

    for clinica in clinicas:
        assert "direccion_fisica" in clinica
        assert "calle" not in clinica

    # --------------------------------------------------------
    # Estados de embarazo 20/8/2 y coherencia de fecha_cierre
    # --------------------------------------------------------

    conteo_estados = Counter(
        e["estado_embarazo"] for e in embarazos
    )

    assert conteo_estados["ACTIVO"] == EMBARAZOS_ACTIVOS
    assert conteo_estados["FINALIZADO"] == EMBARAZOS_FINALIZADOS
    assert conteo_estados["SUSPENDIDO"] == EMBARAZOS_SUSPENDIDOS
    assert set(conteo_estados) == {"ACTIVO", "FINALIZADO", "SUSPENDIDO"}

    for embarazo in embarazos:
        inicio = date.fromisoformat(embarazo["fecha_inicio"])

        if embarazo["estado_embarazo"] == "ACTIVO":
            assert embarazo["fecha_cierre"] is None
        else:
            assert embarazo["fecha_cierre"] is not None
            cierre = date.fromisoformat(embarazo["fecha_cierre"])
            assert cierre >= inicio

    # --------------------------------------------------------
    # Dispositivo / AsignacionDispositivo coherentes con el
    # estado final del embarazo
    # --------------------------------------------------------

    embarazo_por_id = {
        e["id_embarazo"]: e for e in embarazos
    }

    dispositivo_por_id = {
        d["id_dispositivo"]: d for d in dispositivos
    }

    conteo_estado_dispositivo = Counter(
        d["estado"] for d in dispositivos
    )

    assert conteo_estado_dispositivo["ASIGNADO"] == DISPOSITIVOS_ASIGNADOS
    assert conteo_estado_dispositivo["DISPONIBLE"] == DISPOSITIVOS_DISPONIBLES
    assert conteo_estado_dispositivo["MANTENIMIENTO"] == 0
    assert conteo_estado_dispositivo["INACTIVO"] == 0

    asignacion_por_embarazo = {
        a["id_embarazo"]: a for a in asignaciones
    }

    for embarazo in embarazos:
        asignacion = asignacion_por_embarazo[embarazo["id_embarazo"]]
        dispositivo = dispositivo_por_id[asignacion["id_dispositivo"]]

        if embarazo["estado_embarazo"] == "ACTIVO":
            assert asignacion["activo"] is True
            assert asignacion["fecha_fin"] is None
            assert dispositivo["estado"] == "ASIGNADO"
        else:
            assert asignacion["activo"] is False
            assert asignacion["fecha_fin"] is not None
            assert asignacion["fecha_fin"] == embarazo["fecha_cierre"]
            assert dispositivo["estado"] == "DISPONIBLE"

            fecha_inicio_asig = date.fromisoformat(
                asignacion["fecha_inicio"]
            )
            fecha_fin_asig = date.fromisoformat(
                asignacion["fecha_fin"]
            )
            assert fecha_fin_asig >= fecha_inicio_asig

    # --------------------------------------------------------
    # Ninguna sesión/lectura posterior a fecha_cierre
    # --------------------------------------------------------

    sesion_por_id = {
        x["id_sesion"]: x
        for x in sesiones
    }

    for lectura in lecturas:
        sesion = sesion_por_id[lectura["id_sesion"]]
        embarazo = embarazo_por_id[sesion["id_embarazo"]]

        if embarazo["fecha_cierre"] is not None:
            cierre = date.fromisoformat(embarazo["fecha_cierre"])
            captura = datetime.fromisoformat(
                lectura["fecha_hora_captura"]
            ).date()
            assert captura <= cierre

    # --------------------------------------------------------
    # tipo_sesion y origen_dato
    # --------------------------------------------------------

    assert all(s["origen_dato"] == "DISPOSITIVO" for s in sesiones)
    assert all("tipo_sesion" in s for s in sesiones)

    conteo_tipo_sesion = Counter(
        s["tipo_sesion"] for s in sesiones
    )

    assert conteo_tipo_sesion["SIGNOS_MATERNOS"] == TOTAL_SESIONES_HR_SPO2
    assert conteo_tipo_sesion["MOVIMIENTOS_FETALES"] == TOTAL_MOVIMIENTOS

    # --------------------------------------------------------
    # DateTime offset-aware (SesionMonitoreo, LecturaBiometrica,
    # Dispositivo.fecha_registro)
    # --------------------------------------------------------

    for sesion in sesiones:
        assert datetime.fromisoformat(sesion["fecha_inicio"]).tzinfo is not None
        assert datetime.fromisoformat(sesion["fecha_fin"]).tzinfo is not None

    for lectura in lecturas:
        assert datetime.fromisoformat(
            lectura["fecha_hora_captura"]
        ).tzinfo is not None
        assert datetime.fromisoformat(
            lectura["fecha_hora_sincronizacion"]
        ).tzinfo is not None

    for dispositivo in dispositivos:
        assert datetime.fromisoformat(
            dispositivo["fecha_registro"]
        ).tzinfo is not None

    # --------------------------------------------------------
    # Semántica biométrica de NULL
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Dispositivos únicos (código de negocio)
    # --------------------------------------------------------

    codigos = [
        x["codigo_dispositivo"]
        for x in dispositivos
    ]

    assert (
        len(codigos)
        == len(set(codigos))
    )

    # --------------------------------------------------------
    # Distribución global de alertas
    # --------------------------------------------------------

    codigo_por_id_semaforo = {
        s["id_semaforo"]: s["codigo_nivel"]
        for s in semaforos
    }

    alertas = Counter(
        codigo_por_id_semaforo[x["id_semaforo"]]
        for x in lecturas
    )

    assert alertas["OK"] == ALERTAS_OK
    assert alertas["WARNING"] == ALERTAS_WARNING
    assert alertas["ERROR"] == ALERTAS_ERROR

    # --------------------------------------------------------
    # Factores de riesgo 14 / 9 / 7
    # --------------------------------------------------------

    factores_por_embarazo = defaultdict(int)

    for relacion in embarazo_factor_riesgo:
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

    # --------------------------------------------------------
    # 10 gestantes por clínica / 6 embarazos por médico
    # --------------------------------------------------------

    por_clinica = Counter(
        x["id_clinica"]
        for x in embarazos
    )

    assert set(
        por_clinica.values()
    ) == {10}

    por_medico = Counter(
        x["id_medico"]
        for x in seguimientos
    )

    assert set(
        por_medico.values()
    ) == {6}

    # --------------------------------------------------------
    # Un dispositivo por embarazo (histórico)
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Sincronización diferida
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Datos en los tres trimestres
    # --------------------------------------------------------

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
            paciente["id_paciente"]
        ] == {1, 2, 3}
        for paciente in pacientes
    )

    # --------------------------------------------------------
    # Toda lectura debe pertenecer al periodo del embarazo
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Número de lecturas por sesión
    # --------------------------------------------------------

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
    # deben compartir el mismo nivel de alerta.
    #
    # NOTA: esta prueba funcional se mantiene deliberadamente.
    # SQLAlchemy fuerza hoy 1:1 entre SesionMonitoreo y
    # LecturaBiometrica; hasta que Tinuola ajuste el modelo a
    # 1:N, esta es la única incompatibilidad estructural
    # pendiente entre el dataset y el esquema físico real.
    for id_sesion in sesiones_hr:
        estados = {
            x["id_semaforo"]
            for x in lecturas
            if x["id_sesion"] == id_sesion
        }

        assert len(estados) == 1

    # --------------------------------------------------------
    # Duración de movimientos: 60-120 minutos
    # --------------------------------------------------------

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
    embarazo_factor_riesgo,
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

    usuarios = (
        usuarios_admin
        + usuarios_medicos
        + usuarios_pacientes
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
        "embarazo_factor_riesgo":
            embarazo_factor_riesgo,
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
            usuarios,
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
        "embarazo_factor_riesgo",
        embarazo_factor_riesgo,
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
        usuarios,
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
    id_especialidad_unica = especialidades[0]["id_especialidad"]
    medicos = generar_medicos(id_especialidad_unica)

    medico_clinica = generar_medico_clinica(medicos, clinicas)

    roles = generar_roles()
    id_rol_por_nombre = {
        r["nombre_rol"]: r["id_rol"] for r in roles
    }

    usuarios_admin = generar_usuarios_administradores(
        id_rol_por_nombre["ADMIN"]
    )

    semaforos = generar_semaforos()
    id_semaforo_por_estado = {
        s["codigo_nivel"]: s["id_semaforo"] for s in semaforos
    }

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

    usuarios_medicos = generar_usuarios_medicos(
        medicos,
        id_rol_por_nombre["MEDICO"],
    )

    usuarios_pacientes = generar_usuarios_pacientes(
        pacientes,
        id_rol_por_nombre["PACIENTE"],
    )

    usuario_medico = generar_usuario_medico(
        usuarios_medicos,
        medicos,
    )

    usuario_paciente = generar_usuario_paciente(
        usuarios_pacientes,
        pacientes,
    )

    embarazos = generar_embarazos(pacientes, clinicas)

    seguimientos = (
        generar_seguimiento_clinico(
            embarazos,
            medicos,
        )
    )

    factores_riesgo = (
        generar_factores_riesgo_catalogo()
    )

    embarazo_factor_riesgo = (
        generar_embarazo_factor_riesgo(
            embarazos,
            factores_riesgo,
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
        id_semaforo_por_estado,
    )

    # Requiere sesiones/lecturas ya generadas: el cierre de los
    # embarazos FINALIZADO/SUSPENDIDO se calcula a partir de su
    # última captura biométrica real.
    finalizar_fecha_cierre_embarazos(
        embarazos,
        sesiones,
        lecturas,
    )

    # Dispositivo/AsignacionDispositivo dependen del estado final
    # del embarazo (y de fecha_cierre), por eso se generan después.
    dispositivos = generar_dispositivos(
        embarazos
    )

    asignaciones = (
        generar_asignaciones_dispositivo(
            embarazos
        )
    )

    validar_dataset(
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
        embarazo_factor_riesgo=
            embarazo_factor_riesgo,
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
        embarazo_factor_riesgo=
            embarazo_factor_riesgo,
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

    codigo_por_id_semaforo = {
        s["id_semaforo"]: s["codigo_nivel"]
        for s in semaforos
    }

    alertas = Counter(
        codigo_por_id_semaforo[
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

    total_usuarios = (
        len(usuarios_admin)
        + len(usuarios_medicos)
        + len(usuarios_pacientes)
    )

    estados_embarazo = Counter(
        e["estado_embarazo"] for e in embarazos
    )

    tipos_sesion = Counter(
        s["tipo_sesion"] for s in sesiones
    )

    estados_dispositivo = Counter(
        d["estado"] for d in dispositivos
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
        "Estados embarazo: "
        f"{estados_embarazo['ACTIVO']} ACTIVO / "
        f"{estados_embarazo['FINALIZADO']} FINALIZADO / "
        f"{estados_embarazo['SUSPENDIDO']} SUSPENDIDO"
    )
    print(
        "Tipo de sesión: "
        f"{tipos_sesion['SIGNOS_MATERNOS']} SIGNOS_MATERNOS / "
        f"{tipos_sesion['MOVIMIENTOS_FETALES']} MOVIMIENTOS_FETALES"
    )
    print(
        "Estado dispositivos: "
        f"{estados_dispositivo['ASIGNADO']} ASIGNADO / "
        f"{estados_dispositivo['DISPONIBLE']} DISPONIBLE"
    )
    print()
    print(
        "Archivos generados en: "
        f"{CARPETA_SALIDA}"
    )


if __name__ == "__main__":
    main()
