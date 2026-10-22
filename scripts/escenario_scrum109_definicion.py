"""Definicion del escenario de demostracion de SCRUM-109: datos fijos, sin base.

**Que es.** El grupo de pacientes del escenario: cinco pacientes ficticias con
cuenta --una protagonista con dos embarazos y cuatro de apoyo-- y un sexto
perfil sin cuenta, sin embarazo y sin mediciones, que SCRUM-110 usara para
habilitar una cuenta. Todo lo que aqui aparece es simulado y completamente
ficticio; las frecuencias son un diseno de datos de prueba, no una
recomendacion clinica.

**Que no es.** No es el dataset canonico de la tesis (732 sesiones, 1.180
lecturas) ni lo modifica: se carga **despues** de el, en filas propias, con
``scripts/cargar_escenario_scrum109.py``. Este modulo no abre conexiones: la
logica de carga vive en ese script.

**El modelo es el de siempre.** Las estructuras auxiliares de este modulo
(:class:`Paciente`, :class:`Embarazo`, :class:`Observacion`) solo describen la
historia. :func:`filas` las traduce, columna por columna, a las tablas
operacionales existentes --``paciente``, ``telefono_paciente``, ``usuario``,
``usuario_paciente``, ``dispositivo``, ``embarazo``, ``seguimiento_clinico``,
``asignacion_dispositivo``, ``sesion_monitoreo`` y ``lectura_biometrica``-- con
sus nombres exactos. No hay tablas, columnas ni catalogos nuevos.

**Las reglas tampoco se reescriben.** La semana gestacional se calcula con
:func:`app.services.ingesta.semana_gestacional`, la misma que aplica la API; el
umbral de la semana 20 es :data:`app.loader.dataset.SEMANA_MINIMA_DE_MOVIMIENTO`;
y el semaforo de cada lectura lo decide :func:`app.etl.reglas.clasificar_lectura`,
la misma que usa el ETL para conciliar. Aqui no se elige ningun nivel.

**Identificadores reservados.** Cada fila del escenario usa un identificador
explicito dentro de un bloque reservado (:data:`BLOQUES`), y ese bloque es la
forma estable de reconocerla. Las sesiones y lecturas tienen identificadores
fijos para toda la historia, tambien para las que aun no se cargaron porque
son posteriores al corte: avanzar el corte nunca renumera nada.

**El corte.** Las fechas de los embarazos y de cada observacion son fijas. El
corte solo decide **hasta donde** se carga: una observacion entra si su
``fecha_hora_captura`` es anterior o igual al corte. Nunca se desplaza la
historia ni se cambia la fecha del sistema.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.etl.reglas import clasificar_lectura
from app.loader.dataset import SEMANA_MINIMA_DE_MOVIMIENTO
from app.models.enums import (
    EstadoDispositivo,
    EstadoEmbarazo,
    EstadoSesion,
    OrigenDato,
    RolSeguimiento,
    TipoContacto,
    TipoSesion,
)
from app.services.ingesta import semana_gestacional

VERSION = "SCRUM-109.1"
NOMBRE = "Escenario de demostracion SCRUM-109"

# Las fechas clinicas del proyecto son de Panama; el ETL ubica cada lectura en
# su dia de America/Panama (``reglas.ZONA_HORARIA_CLINICA``).
ZONA = ZoneInfo("America/Panama")

# Ultimo dia con observaciones programadas. La sustentacion es del 20 al 23 de
# noviembre de 2026; la historia sigue una semana mas para que cualquier corte
# de esos dias encuentre datos hasta el propio dia.
FIN_DEL_ESCENARIO = date(2026, 11, 30)

# El dispositivo simulado que reconocen las herramientas de la demo
# (``provisionar_demo.CODIGO_DISPOSITIVO_DEMO``; Demo Control exige una sola
# asignacion suya). Aqui sirve al embarazo actual de la protagonista. Las
# pruebas comprueban que coincide con el del script, en lugar de importarlo,
# para que este modulo no dependa de ``scripts/``.
CODIGO_DISPOSITIVO_PRINCIPAL = "DEMO-SCRUM72-01"

# El medico autorizado de la demostracion, por su cuenta. El cargador resuelve
# su ``id_medico`` y su clinica en la base; ninguno se escribe aqui.
EMAIL_MEDICO_DE_LA_DEMO = "medico01@example.com"

DOMINIO_DE_CORREO = "example.com"
MODELO_DE_DISPOSITIVO = "Simulado SCRUM-109"
FIRMWARE_DE_DISPOSITIVO = "0.0-demo"
DIAS_POR_SEMANA = 7
DURACION_DE_REFERENCIA_DIAS = 280  # FPP = inicio + 40 semanas exactas

# Semana desde la que se registran signos maternos en cada embarazo.
SEMANA_INICIO_SIGNOS = 4
HORA_SIGNOS = time(9, 30)
HORA_MOVIMIENTOS = time(16, 0)
# Dias dentro de la semana 20 en que cae el primer conteo de movimientos.
DESFASE_MOVIMIENTOS_DIAS = 2

# Bloques reservados: (primero, ultimo), ambos incluidos. Una fila del bloque que
# no figure en esta definicion es ajena y el cargador se niega a seguir.
BLOQUE_ENTIDADES = (10901, 10999)
BLOQUE_MONITOREO = (10_900_001, 10_999_999)
BLOQUES: dict[str, tuple[str, tuple[int, int]]] = {
    "paciente": ("id_paciente", BLOQUE_ENTIDADES),
    "telefono_paciente": ("id_telefono_paciente", BLOQUE_ENTIDADES),
    "usuario": ("id_usuario", BLOQUE_ENTIDADES),
    "dispositivo": ("id_dispositivo", BLOQUE_ENTIDADES),
    "embarazo": ("id_embarazo", BLOQUE_ENTIDADES),
    "seguimiento_clinico": ("id_seguimiento", BLOQUE_ENTIDADES),
    "asignacion_dispositivo": ("id_asignacion", BLOQUE_ENTIDADES),
    "sesion_monitoreo": ("id_sesion", BLOQUE_MONITOREO),
    "lectura_biometrica": ("id_lectura", BLOQUE_MONITOREO),
}
# Sesiones y lecturas: 10_900_000 + (id_embarazo - 10900) * 10_000 + n.
BASE_MONITOREO = 10_900_000
TRAMO_POR_EMBARAZO = 10_000

# Valores biometricos de rutina, en ciclo. Son datos de prueba fijos; su
# clasificacion la decide ``clasificar_lectura``, no este modulo.
HR_DE_RUTINA = (78, 82, 85, 80, 88, 84, 79, 86)
SPO2_DE_RUTINA = (98, 97, 99, 97, 98, 96, 98, 97)
MOV_DE_RUTINA = (12, 14, 11, 15, 13, 16, 12, 14)


class ErrorDeDefinicion(RuntimeError):
    """La definicion se contradice o contradice una regla del dominio."""


# ---------------------------------------------------------------------------
# La historia
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Paciente:
    id_paciente: int
    primer_nombre: str
    apellido_paterno: str
    apellido_materno: str
    fecha_nac: date
    con_cuenta: bool
    papel: str

    @property
    def id_usuario(self) -> int | None:
        """La cuenta PACIENTE comparte el numero del perfil; ``None`` sin cuenta."""
        return self.id_paciente if self.con_cuenta else None

    @property
    def correo(self) -> str:
        return f"escenario109.{self.primer_nombre.lower()}@{DOMINIO_DE_CORREO}"

    @property
    def cedula(self) -> str:
        return f"4-109-{self.id_paciente - 10900:04d}"

    @property
    def celular(self) -> str:
        return f"6109-{self.id_paciente - 10900:04d}"


@dataclass(frozen=True)
class Alteracion:
    """Valores fuera de la rutina para un tipo de sesion en un periodo.

    ``hasta`` incluido; ``None`` = hasta el final. Los valores se recorren en
    ciclo, y los que son ``None`` conservan el de rutina.
    """

    tipo: TipoSesion
    desde: date
    hasta: date | None
    hr: tuple[int, ...] | None = None
    spo2: tuple[int, ...] | None = None
    mov: tuple[int, ...] | None = None

    def aplica(self, tipo: TipoSesion, dia: date) -> bool:
        return tipo is self.tipo and self.desde <= dia and (self.hasta is None or dia <= self.hasta)


@dataclass(frozen=True)
class Embarazo:
    id_embarazo: int
    id_paciente: int
    fecha_inicio: date
    estado: EstadoEmbarazo
    fecha_cierre: date | None
    numero_gestas: int
    numero_partos: int
    codigo_dispositivo: str
    papel: str
    alteraciones: tuple[Alteracion, ...] = ()

    @property
    def fecha_probable_parto(self) -> date:
        return self.fecha_inicio + timedelta(days=DURACION_DE_REFERENCIA_DIAS)

    @property
    def fin_de_observaciones(self) -> date:
        """Primer dia **sin** observaciones (excluido)."""
        if self.fecha_cierre is not None:
            return self.fecha_cierre
        return min(FIN_DEL_ESCENARIO + timedelta(days=1), self.fecha_probable_parto)

    @property
    def cerrado(self) -> bool:
        return self.fecha_cierre is not None


S, M = TipoSesion.SIGNOS_MATERNOS, TipoSesion.MOVIMIENTOS_FETALES

PACIENTES: tuple[Paciente, ...] = (
    Paciente(10901, "Elena", "Ficticia", "Escenario", date(1994, 5, 14), True,
             "protagonista"),
    Paciente(10902, "Marta", "Ficticia", "Apoyo", date(1990, 9, 3), True,
             "apoyo: lecturas OK"),
    Paciente(10903, "Rosa", "Ficticia", "Apoyo", date(1997, 2, 21), True,
             "apoyo: WARNING recientes"),
    Paciente(10904, "Ines", "Ficticia", "Apoyo", date(1988, 11, 30), True,
             "apoyo: WARNING historicos seguidos de OK"),
    Paciente(10905, "Clara", "Ficticia", "Apoyo", date(2001, 7, 9), True,
             "apoyo: embarazo temprano"),
    Paciente(10906, "Paula", "Ficticia", "Pendiente", date(1999, 3, 17), False,
             "perfil sin cuenta para SCRUM-110 (sin embarazo ni mediciones)"),
)

EMBARAZOS: tuple[Embarazo, ...] = (
    Embarazo(
        10901, 10901, date(2025, 1, 13), EstadoEmbarazo.FINALIZADO, date(2025, 10, 13),
        numero_gestas=1, numero_partos=0, codigo_dispositivo="DEMO-SCRUM109-01",
        papel="protagonista: embarazo anterior, finalizado",
        alteraciones=(Alteracion(S, date(2025, 7, 14), date(2025, 7, 27), hr=(103,)),),
    ),
    Embarazo(
        10902, 10901, date(2026, 4, 27), EstadoEmbarazo.ACTIVO, None,
        numero_gestas=2, numero_partos=1, codigo_dispositivo=CODIGO_DISPOSITIVO_PRINCIPAL,
        papel="protagonista: embarazo actual (dispositivo principal de la demo)",
        alteraciones=(Alteracion(S, date(2026, 7, 13), date(2026, 7, 26), spo2=(94,)),),
    ),
    Embarazo(
        10903, 10902, date(2026, 3, 2), EstadoEmbarazo.ACTIVO, None,
        numero_gestas=1, numero_partos=0, codigo_dispositivo="DEMO-SCRUM109-03",
        papel="apoyo: lecturas OK",
    ),
    Embarazo(
        10904, 10903, date(2026, 4, 13), EstadoEmbarazo.ACTIVO, None,
        numero_gestas=2, numero_partos=1, codigo_dispositivo="DEMO-SCRUM109-04",
        papel="apoyo: WARNING recientes (desde el 2026-10-05)",
        alteraciones=(
            Alteracion(S, date(2026, 10, 5), None, hr=(104, 106)),
            Alteracion(M, date(2026, 10, 5), None, mov=(8, 7)),
        ),
    ),
    Embarazo(
        10905, 10904, date(2026, 3, 23), EstadoEmbarazo.ACTIVO, None,
        numero_gestas=1, numero_partos=0, codigo_dispositivo="DEMO-SCRUM109-05",
        papel="apoyo: WARNING historicos (2026-08-17 a 2026-09-06) seguidos de OK",
        alteraciones=(
            Alteracion(S, date(2026, 8, 17), date(2026, 9, 6), spo2=(93,)),
            Alteracion(M, date(2026, 8, 17), date(2026, 9, 6), mov=(7,)),
        ),
    ),
    Embarazo(
        10906, 10905, date(2026, 6, 1), EstadoEmbarazo.ACTIVO, None,
        numero_gestas=1, numero_partos=0, codigo_dispositivo="DEMO-SCRUM109-06",
        papel="apoyo: embarazo temprano, movimientos desde la semana 20",
    ),
)


@dataclass(frozen=True)
class Observacion:
    """Una sesion simulada con su unica lectura (misma forma que el simulador)."""

    id_sesion: int
    id_embarazo: int
    tipo: TipoSesion
    captura: datetime
    hr: Decimal | None
    spo2: Decimal | None
    mov: int | None

    @property
    def id_lectura(self) -> int:
        return self.id_sesion

    @property
    def dia(self) -> date:
        return self.captura.astimezone(ZONA).date()


def _en_panama(dia: date, hora: time) -> datetime:
    return datetime.combine(dia, hora, ZONA)


def _dias(embarazo: Embarazo, primera_semana: int, desfase: int = 0) -> list[date]:
    dia = embarazo.fecha_inicio + timedelta(days=(primera_semana - 1) * DIAS_POR_SEMANA + desfase)
    dias = []
    while dia < embarazo.fin_de_observaciones:
        dias.append(dia)
        dia += timedelta(days=DIAS_POR_SEMANA)
    return dias


def _valor(alteraciones, tipo, dia, campo, rutina, indice):
    for alteracion in alteraciones:
        valores = getattr(alteracion, campo)
        if valores is not None and alteracion.aplica(tipo, dia):
            return valores[indice % len(valores)]
    return rutina[indice % len(rutina)]


def observaciones_del_embarazo(embarazo: Embarazo) -> list[Observacion]:
    """Signos semanales desde la semana 4 y movimientos semanales desde la 20.

    Antes de la semana 20 no hay **ninguna** sesion de movimiento: la ausencia
    es la forma de decir «no registrado», nunca un cero.
    """
    desplazamiento = embarazo.id_embarazo - 10900
    crudas = []
    for indice, dia in enumerate(_dias(embarazo, SEMANA_INICIO_SIGNOS)):
        hr = _valor(embarazo.alteraciones, S, dia, "hr", HR_DE_RUTINA, indice + desplazamiento)
        spo2 = _valor(embarazo.alteraciones, S, dia, "spo2", SPO2_DE_RUTINA, indice + desplazamiento)
        crudas.append((_en_panama(dia, HORA_SIGNOS), S, Decimal(hr), Decimal(spo2), None))
    dias_mov = _dias(embarazo, SEMANA_MINIMA_DE_MOVIMIENTO, DESFASE_MOVIMIENTOS_DIAS)
    for indice, dia in enumerate(dias_mov):
        mov = _valor(embarazo.alteraciones, M, dia, "mov", MOV_DE_RUTINA, indice + desplazamiento)
        crudas.append((_en_panama(dia, HORA_MOVIMIENTOS), M, None, None, mov))
    crudas.sort(key=lambda c: c[0])
    base = BASE_MONITOREO + desplazamiento * TRAMO_POR_EMBARAZO
    return [
        Observacion(base + numero, embarazo.id_embarazo, tipo, captura, hr, spo2, mov)
        for numero, (captura, tipo, hr, spo2, mov) in enumerate(crudas, start=1)
    ]


def todas_las_observaciones() -> list[Observacion]:
    return [o for e in EMBARAZOS for o in observaciones_del_embarazo(e)]


def fecha_estructural_maxima() -> date:
    """La fecha mas reciente de las filas que se cargan siempre, con cualquier corte."""
    fechas = [e.fecha_inicio for e in EMBARAZOS] + [e.fecha_cierre for e in EMBARAZOS if e.fecha_cierre]
    return max(fechas)


def corte_minimo() -> datetime:
    """Un corte anterior dejaria cargados embarazos y asignaciones del futuro."""
    return _en_panama(fecha_estructural_maxima(), time(0))


# ---------------------------------------------------------------------------
# Traduccion explicita al modelo existente
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Referencias:
    """Lo que el cargador resuelve en la base. Nada de esto se inventa aqui."""

    id_clinica: int
    id_medico: int
    id_rol_paciente: int
    # semana_gestacion -> (id_tiempo_gest, trimestre), del catalogo real.
    semanas: dict[int, tuple[int, int]]
    # codigo_nivel -> id_semaforo, del catalogo real.
    semaforos: dict[str, int]
    # id_usuario -> digest Argon2id de la clave simulada del dataset.
    password_hash: dict[int, str]


def paciente(id_paciente: int) -> Paciente:
    return next(p for p in PACIENTES if p.id_paciente == id_paciente)


def clasificar(observacion: Observacion, embarazo: Embarazo, referencias: Referencias):
    """Semana, fila del catalogo y estados, con las funciones del dominio."""
    semana = semana_gestacional(embarazo.fecha_inicio, observacion.captura.astimezone(ZONA))
    if semana not in referencias.semanas:
        raise ErrorDeDefinicion(
            f"sesion {observacion.id_sesion}: la semana {semana} no esta en el catalogo."
        )
    id_tiempo_gest, trimestre = referencias.semanas[semana]
    estados = clasificar_lectura(
        hr_valor=observacion.hr,
        spo2_valor=observacion.spo2,
        mov_valor=observacion.mov,
        semana=semana,
        trimestre=trimestre,
    )
    return semana, id_tiempo_gest, estados


def filas(referencias: Referencias, corte: datetime | None) -> dict[str, list[dict]]:
    """Filas por tabla operacional, con sus columnas exactas.

    ``corte=None`` devuelve la historia completa (la usa el cargador para
    reconocer las filas del bloque reservado); con un corte, solo las
    observaciones capturadas hasta el.
    """
    validar_definicion()
    tablas: dict[str, list[dict]] = {nombre: [] for nombre in (
        "paciente", "telefono_paciente", "usuario", "usuario_paciente", "dispositivo",
        "embarazo", "seguimiento_clinico", "asignacion_dispositivo",
        "sesion_monitoreo", "lectura_biometrica",
    )}
    for p in PACIENTES:
        tablas["paciente"].append({
            "id_paciente": p.id_paciente, "cedula": p.cedula, "primer_nombre": p.primer_nombre,
            "segundo_nombre": None, "apellido_paterno": p.apellido_paterno,
            "apellido_materno": p.apellido_materno, "email_pac": p.correo, "fecha_nac": p.fecha_nac,
        })
        tablas["telefono_paciente"].append({
            "id_telefono_paciente": p.id_paciente, "id_paciente": p.id_paciente,
            "tipo_contacto": TipoContacto.CELULAR.value, "valor_contacto": p.celular, "principal": True,
        })
        if p.con_cuenta:
            tablas["usuario"].append({
                "id_usuario": p.id_usuario, "id_rol": referencias.id_rol_paciente, "email": p.correo,
                "password_hash": referencias.password_hash[p.id_usuario], "activo": True,
            })
            tablas["usuario_paciente"].append({"id_usuario": p.id_usuario, "id_paciente": p.id_paciente})
    for e in EMBARAZOS:
        tablas["dispositivo"].append({
            "id_dispositivo": e.id_embarazo, "id_clinica": referencias.id_clinica,
            "codigo_dispositivo": e.codigo_dispositivo, "modelo": MODELO_DE_DISPOSITIVO,
            "version_firmware": FIRMWARE_DE_DISPOSITIVO,
            # Devuelto al cerrar el embarazo: queda libre, sin otra asignacion.
            "estado": (EstadoDispositivo.DISPONIBLE if e.cerrado else EstadoDispositivo.ASIGNADO).value,
            "fecha_registro": _en_panama(e.fecha_inicio, time(0)),
        })
        tablas["embarazo"].append({
            "id_embarazo": e.id_embarazo, "id_paciente": e.id_paciente,
            "id_clinica": referencias.id_clinica, "numero_gestas": e.numero_gestas,
            "numero_partos": e.numero_partos, "fecha_inicio": e.fecha_inicio,
            "fecha_probable_parto": e.fecha_probable_parto, "estado_embarazo": e.estado.value,
            "fecha_cierre": e.fecha_cierre,
        })
        tablas["seguimiento_clinico"].append({
            "id_seguimiento": e.id_embarazo, "id_embarazo": e.id_embarazo,
            "id_medico": referencias.id_medico, "fecha_asignacion": e.fecha_inicio,
            "fecha_fin": e.fecha_cierre, "rol_seguimiento": RolSeguimiento.PRINCIPAL.value,
            "activo": not e.cerrado,
        })
        tablas["asignacion_dispositivo"].append({
            "id_asignacion": e.id_embarazo, "id_dispositivo": e.id_embarazo,
            "id_embarazo": e.id_embarazo, "fecha_inicio": e.fecha_inicio,
            "fecha_fin": e.fecha_cierre, "activo": not e.cerrado,
        })
        for o in observaciones_del_embarazo(e):
            if corte is not None and o.captura > corte:
                continue
            _, id_tiempo_gest, estados = clasificar(o, e, referencias)
            tablas["sesion_monitoreo"].append({
                "id_sesion": o.id_sesion, "id_embarazo": e.id_embarazo, "id_dispositivo": e.id_embarazo,
                "tipo_sesion": o.tipo.value, "fecha_inicio": o.captura, "fecha_fin": o.captura,
                "estado_sesion": EstadoSesion.COMPLETADA.value, "origen_dato": OrigenDato.DISPOSITIVO.value,
            })
            tablas["lectura_biometrica"].append({
                "id_lectura": o.id_lectura, "id_sesion": o.id_sesion, "id_tiempo_gest": id_tiempo_gest,
                "id_semaforo": referencias.semaforos[estados.codigo_global],
                "fecha_hora_captura": o.captura,
                # Precarga: no hubo sincronizacion que registrar. Es lo mismo que
                # deja la ruta en vivo (el paquete viaja con este campo en NULL).
                "fecha_hora_sincronizacion": None,
                "hr_valor": o.hr, "spo2_valor": o.spo2, "mov_valor": o.mov,
            })
    return tablas


# ---------------------------------------------------------------------------
# Coherencia interna
# ---------------------------------------------------------------------------


def validar_definicion() -> None:
    """Lo que la definicion debe cumplir sin mirar la base."""
    ids_p = [p.id_paciente for p in PACIENTES]
    ids_e = [e.id_embarazo for e in EMBARAZOS]
    codigos = [e.codigo_dispositivo for e in EMBARAZOS]
    for nombre, ids in (("pacientes", ids_p), ("embarazos", ids_e), ("dispositivos", codigos)):
        if len(set(ids)) != len(ids):
            raise ErrorDeDefinicion(f"Identificadores de {nombre} repetidos.")
    primero, ultimo = BLOQUE_ENTIDADES
    if not all(primero <= i <= ultimo for i in ids_p + ids_e):
        raise ErrorDeDefinicion("Un identificador de entidad esta fuera de su bloque.")
    if codigos.count(CODIGO_DISPOSITIVO_PRINCIPAL) != 1:
        raise ErrorDeDefinicion("El dispositivo principal debe servir a un solo embarazo.")
    for e in EMBARAZOS:
        if e.id_paciente not in ids_p or not paciente(e.id_paciente).con_cuenta:
            raise ErrorDeDefinicion(f"embarazo {e.id_embarazo}: su paciente no tiene cuenta.")
        if not 0 <= e.numero_partos < e.numero_gestas:
            raise ErrorDeDefinicion(f"embarazo {e.id_embarazo}: gestas/partos incoherentes.")
        if (e.estado is EstadoEmbarazo.FINALIZADO) != e.cerrado:
            raise ErrorDeDefinicion(f"embarazo {e.id_embarazo}: estado y cierre no concuerdan.")
        activos = [x for x in EMBARAZOS if x.id_paciente == e.id_paciente and not x.cerrado]
        if len(activos) > 1:
            raise ErrorDeDefinicion(f"paciente {e.id_paciente}: mas de un embarazo en curso.")
        for o in observaciones_del_embarazo(e):
            if not BLOQUE_MONITOREO[0] <= o.id_sesion <= BLOQUE_MONITOREO[1]:
                raise ErrorDeDefinicion(f"sesion {o.id_sesion} fuera de su bloque.")
            if not e.fecha_inicio <= o.dia < e.fin_de_observaciones:
                raise ErrorDeDefinicion(f"sesion {o.id_sesion}: fuera del periodo del embarazo.")
            semana = semana_gestacional(e.fecha_inicio, o.captura.astimezone(ZONA))
            if o.tipo is M and semana < SEMANA_MINIMA_DE_MOVIMIENTO:
                raise ErrorDeDefinicion(f"sesion {o.id_sesion}: movimiento antes de la semana 20.")
            if o.tipo is M and (o.mov is None or o.hr is not None or o.spo2 is not None):
                raise ErrorDeDefinicion(f"sesion {o.id_sesion}: forma de movimiento invalida.")
            if o.tipo is S and (o.mov is not None or o.hr is None or o.spo2 is None):
                raise ErrorDeDefinicion(f"sesion {o.id_sesion}: forma de signos invalida.")
