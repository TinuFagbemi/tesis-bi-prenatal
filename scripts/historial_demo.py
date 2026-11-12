"""Historial de mediciones del episodio de demostración de paciente01.

Uso desde la raíz del repositorio::

    python scripts/historial_demo.py previsualizar   # qué se cargaría; no escribe
    python scripts/historial_demo.py cargar          # envía las sesiones a la API
    python scripts/historial_demo.py verificar       # compara la base con el plan

**Qué hace.** El episodio de demostración ``DEMO-SCRUM72-01`` (embarazo 130 de
paciente01, creado por ``provisionar_demo.py``) empieza sin mediciones. Este
comando le da un historial de las semanas gestacionales ya **completas**, para
que la interfaz de la gestante y Power BI muestren varios meses de seguimiento.
La semana en curso queda libre para las capturas en vivo de la demostración
(escenarios A y C).

**Qué no hace.** No toca el dataset canónico (las 1180 lecturas oficiales ni
los embarazos anteriores de nadie), no cambia fechas, reglas, umbrales ni
esquema, y no escribe en la base por su cuenta: entra por la API de ingesta,
como la aplicación.

**Por el mismo camino que la aplicación.** Inicia sesión como la paciente del
episodio (la contraseña simulada del dataset) y envía cada sesión a
``POST /api/v1/sesiones-monitoreo`` con el cliente del nodo edge
(:class:`app.edge.cliente.ClienteEdge`). La API vuelve a comprobar el rol, la
propiedad del embarazo, la asignación del dispositivo, la semana gestacional de
cada lectura y la idempotencia, y audita el acceso.

**Las reglas son las del sistema, no unas propias.**

- La semana se calcula con :func:`app.services.ingesta.semana_gestacional`,
  la misma función que usa el servidor para comprobarla.
- El semáforo se deriva con :func:`app.etl.reglas.clasificar_lectura` (SIM-1.0),
  la misma regla con la que el ETL vuelve a clasificar.
- Los movimientos fetales empiezan en ``SEMANA_MINIMA_DE_MOVIMIENTO`` (20),
  más restrictiva que el fin del primer trimestre. Antes de esa semana no hay
  sesiones de movimiento, y no se representan con ceros.

**Forma de las sesiones, la del modelo.** Una sesión de signos maternos lleva
``LECTURAS_POR_SESION_DE_SIGNOS`` lecturas, cada una con frecuencia cardíaca y
SpO₂, como el dataset (5). Una sesión de movimientos fetales lleva **una**
lectura con el conteo consolidado de la sesión: ese es el contrato del sistema,
y repartir el conteo en cinco lecturas inventaría una medición que no existe.

**Reproducible.** El plan depende solo de la fecha de inicio del embarazo, de la
semana en curso y de ``--semilla``. Cada sesión tiene una clave de idempotencia
derivada de esos mismos datos, así que repetir ``cargar`` no duplica nada: la API
reconoce cada sesión ya confirmada y devuelve el mismo resultado. Si el
embarazo ya tiene, en las semanas del plan, sesiones que no son de este plan
(otra semilla, otra herramienta), ``cargar`` se niega a mezclar.

La base se lee con ``ALEMBIC_DATABASE_URL``, en una transacción de solo lectura,
y la API se alcanza con ``GESTANTE_API_BASE_URL``. No se imprime ningún nombre,
cédula, correo, contraseña, token ni valor biométrico individual: solo
identificadores técnicos, fechas, conteos y códigos.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_DEL_REPOSITORIO / "backend"))

import httpx  # noqa: E402  -- tras ajustar sys.path
from sqlalchemy import create_engine, text  # noqa: E402

from app.config import VARIABLE_URL_ALEMBIC, UrlDeEntornoInvalida, exigir_url_de_entorno  # noqa: E402
from app.edge.cliente import ClienteEdge  # noqa: E402
from app.edge.estados import ResultadoEntrega  # noqa: E402
from app.etl.reglas import ZONA_HORARIA_CLINICA, clasificar_lectura  # noqa: E402
from app.gestante import provision as modulo_provision  # noqa: E402
from app.gestante.central import ClienteCentralHTTP, EstadoRespuesta  # noqa: E402
from app.gestante.config import cargar_settings_gestante  # noqa: E402
from app.loader.dataset import SEMANA_MINIMA_DE_MOVIMIENTO  # noqa: E402
from app.models.enums import EstadoSesion, TipoSesion  # noqa: E402
from app.services.ingesta import semana_gestacional  # noqa: E402

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1
CODIGO_DE_DIFERENCIAS = 3

# Parámetros del escenario. Cambiarlos cambia el plan y sus claves.
DISPOSITIVO_POR_OMISION = "DEMO-SCRUM72-01"  # episodio de paciente01 (embarazo 130)
SEMILLA_POR_OMISION = 20261107
LECTURAS_POR_SESION_DE_SIGNOS = 5  # como el dataset canónico
# Dos sesiones de cada tipo por semana gestacional: días 1 y 4 de la semana
# para signos maternos, 2 y 5 para movimientos fetales. Así cualquier tramo de
# mes de una semana o más tiene al menos dos sesiones de cada tipo.
DIAS_DE_SIGNOS = (1, 4)
DIAS_DE_MOVIMIENTO = (2, 5)
HORA_DE_SIGNOS = (time(8, 30), time(19, 0))
HORA_DE_MOVIMIENTO = time(20, 15)
MINUTOS_ENTRE_LECTURAS = 1
PREFIJO_DE_CLAVE = "hist"


class ErrorDeHistorial(RuntimeError):
    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


@dataclass(frozen=True)
class SesionPlaneada:
    clave: str
    semana: int
    tipo: TipoSesion
    inicio: datetime
    paquete: dict


# ---------------------------------------------------------------------------
# El plan: puro, sin red ni base
# ---------------------------------------------------------------------------


def _generador(semilla: int, *partes: object) -> random.Random:
    """Un generador por sesión: añadir semanas no cambia las anteriores."""
    material = "|".join(str(p) for p in (semilla, *partes)).encode("utf-8")
    return random.Random(int.from_bytes(hashlib.sha256(material).digest()[:8], "big"))


def clave_de_sesion(id_embarazo: int, fecha_inicio: date, semilla: int, semana: int, tipo: TipoSesion, numero: int) -> str:
    material = f"historial-demo|{id_embarazo}|{fecha_inicio.isoformat()}|{semilla}|{semana}|{tipo.value}|{numero}"
    return f"{PREFIJO_DE_CLAVE}-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:40]}"


def _valores_de_signos(gen: random.Random, semana: int) -> list[tuple[Decimal, Decimal]]:
    """Frecuencia cardíaca con la subida fisiológica del embarazo; SpO₂ estable.

    Valores plausibles y variados; casi siempre dentro de lo normal según
    SIM-1.0, con alguna sesión de taquicardia leve o SpO₂ algo baja para que
    las tendencias y el semáforo se vean. La clasificación no se decide aquí.
    """
    base_hr = 72 + 12 * min(semana, 40) / 40 + gen.gauss(0, 3)
    if gen.random() < 0.08:
        base_hr += gen.uniform(18, 24)  # episodio de taquicardia leve
    base_spo2 = 97.6 + gen.gauss(0, 0.5)
    if gen.random() < 0.06:
        base_spo2 -= gen.uniform(2.5, 3.2)  # saturación algo baja
    lecturas = []
    for _ in range(LECTURAS_POR_SESION_DE_SIGNOS):
        hr = round(min(max(base_hr + gen.gauss(0, 1.8), 58), 112))
        spo2 = round(min(max(base_spo2 + gen.gauss(0, 0.4), 93), 99))
        lecturas.append((Decimal(hr), Decimal(spo2)))
    return lecturas


def _conteo_de_movimientos(gen: random.Random, semana: int) -> int:
    """Conteo consolidado de la sesión: crece algo con la semana; a veces bajo."""
    base = 10 + (semana - SEMANA_MINIMA_DE_MOVIMIENTO) * 0.25 + gen.gauss(0, 1.6)
    if gen.random() < 0.1:
        base -= gen.uniform(3, 5)  # sesión con menos movimientos de lo habitual
    return int(min(max(round(base), 5), 16))


def _lectura(prov, semana: int, instante: datetime, hr, spo2, mov) -> dict:
    fila = prov.semana(semana)
    if fila is None:
        raise ErrorDeHistorial(f"La semana {semana} no está en el catálogo gestacional del aprovisionamiento.")
    estados = clasificar_lectura(hr_valor=hr, spo2_valor=spo2, mov_valor=mov, semana=semana, trimestre=fila.trimestre)
    id_semaforo = prov.id_semaforo_de(estados.codigo_global)
    if id_semaforo is None:
        raise ErrorDeHistorial("El aprovisionamiento no incluye el nivel de semáforo de una lectura.")
    return {
        "id_tiempo_gest": fila.id_tiempo_gest,
        "id_semaforo": id_semaforo,
        "fecha_hora_captura": instante.isoformat(),
        "fecha_hora_sincronizacion": None,
        "hr_valor": None if hr is None else str(hr),
        "spo2_valor": None if spo2 is None else str(spo2),
        "mov_valor": mov,
    }


def semana_en_curso(fecha_inicio: date, hasta: datetime) -> int:
    return semana_gestacional(fecha_inicio, hasta)


def construir_plan(prov, semilla: int, hasta: datetime) -> list[SesionPlaneada]:
    """Las sesiones de las semanas completas, de la 1 a la anterior a la actual."""
    inicio = prov.fecha_inicio_embarazo
    actual = semana_en_curso(inicio, hasta)
    plan: list[SesionPlaneada] = []
    for semana in range(1, actual):
        primer_dia = inicio + timedelta(days=7 * (semana - 1))
        tipos = [(TipoSesion.SIGNOS_MATERNOS, DIAS_DE_SIGNOS)]
        if semana >= SEMANA_MINIMA_DE_MOVIMIENTO:
            tipos.append((TipoSesion.MOVIMIENTOS_FETALES, DIAS_DE_MOVIMIENTO))
        for tipo, dias in tipos:
            for numero, dia in enumerate(dias, start=1):
                hora = HORA_DE_SIGNOS[numero - 1] if tipo is TipoSesion.SIGNOS_MATERNOS else HORA_DE_MOVIMIENTO
                comienzo = datetime.combine(primer_dia + timedelta(days=dia), hora, tzinfo=ZONA_HORARIA_CLINICA)
                gen = _generador(semilla, prov.id_embarazo, semana, tipo.value, numero)
                if tipo is TipoSesion.SIGNOS_MATERNOS:
                    lecturas = [
                        _lectura(prov, semana, comienzo + timedelta(minutes=MINUTOS_ENTRE_LECTURAS * i), hr, spo2, None)
                        for i, (hr, spo2) in enumerate(_valores_de_signos(gen, semana))
                    ]
                else:
                    lecturas = [_lectura(prov, semana, comienzo, None, None, _conteo_de_movimientos(gen, semana))]
                fin = datetime.fromisoformat(lecturas[-1]["fecha_hora_captura"])
                if fin >= hasta:
                    raise ErrorDeHistorial("El plan produciría una lectura no anterior al corte; revisa --hasta.")
                if semana_gestacional(inicio, fin) != semana:
                    raise ErrorDeHistorial(f"Una sesión de la semana {semana} cruzaría a otra semana.")
                paquete = {
                    "id_embarazo": prov.id_embarazo,
                    "id_dispositivo": prov.id_dispositivo,
                    "tipo_sesion": tipo.value,
                    "fecha_inicio": comienzo.isoformat(),
                    "fecha_fin": fin.isoformat(),
                    "estado_sesion": EstadoSesion.COMPLETADA.value,
                    "lecturas": lecturas,
                }
                plan.append(
                    SesionPlaneada(
                        clave=clave_de_sesion(prov.id_embarazo, inicio, semilla, semana, tipo, numero),
                        semana=semana,
                        tipo=tipo,
                        inicio=comienzo,
                        paquete=paquete,
                    )
                )
    return plan


def resumen_del_plan(prov, plan: list[SesionPlaneada], hasta: datetime) -> dict:
    por_mes: dict[str, Counter] = {}
    semaforos: Counter = Counter()
    lecturas: Counter = Counter()
    codigos = {v: k for k, v in prov.semaforos.items()}
    for s in plan:
        mes = s.inicio.strftime("%Y-%m")
        por_mes.setdefault(mes, Counter())[s.tipo.value] += 1
        lecturas[s.tipo.value] += len(s.paquete["lecturas"])
        for lectura in s.paquete["lecturas"]:
            semaforos[codigos.get(lectura["id_semaforo"], "?")] += 1
    return {
        "id_usuario": prov.id_usuario,
        "id_embarazo": prov.id_embarazo,
        "id_dispositivo": prov.id_dispositivo,
        "fecha_inicio_embarazo": prov.fecha_inicio_embarazo.isoformat(),
        "corte": hasta.isoformat(timespec="minutes"),
        "semana_en_curso": semana_en_curso(prov.fecha_inicio_embarazo, hasta),
        "semanas_del_plan": [plan[0].semana, plan[-1].semana] if plan else [],
        "primera_sesion": plan[0].inicio.isoformat(timespec="minutes") if plan else None,
        "ultima_sesion": plan[-1].inicio.isoformat(timespec="minutes") if plan else None,
        "sesiones_por_mes": {mes: dict(c) for mes, c in sorted(por_mes.items())},
        "sesiones": dict(Counter(s.tipo.value for s in plan)),
        "lecturas": dict(lecturas),
        "semaforo": dict(sorted(semaforos.items())),
        "primera_semana_de_movimiento": min((s.semana for s in plan if s.tipo is TipoSesion.MOVIMIENTOS_FETALES), default=None),
    }


# ---------------------------------------------------------------------------
# Base (solo lectura) y API
# ---------------------------------------------------------------------------


def _motor():
    try:
        return create_engine(exigir_url_de_entorno(VARIABLE_URL_ALEMBIC))
    except UrlDeEntornoInvalida as error:
        raise ErrorDeHistorial("Falta ALEMBIC_DATABASE_URL o no es utilizable.") from error


def leer_episodio(prov) -> dict:
    """Comprueba en la base que el episodio es el que dice el aprovisionamiento."""
    motor = _motor()
    try:
        with motor.connect() as conexion:
            conexion.execute(text("SET TRANSACTION READ ONLY"))
            fila = conexion.execute(
                text(
                    "SELECT e.id_paciente, e.fecha_inicio, e.estado_embarazo, u.email,"
                    "       (SELECT count(*) FROM operacional.asignacion_dispositivo a"
                    "         WHERE a.id_embarazo = e.id_embarazo AND a.id_dispositivo = :d"
                    "           AND a.activo AND a.fecha_inicio <= e.fecha_inicio) AS asignaciones,"
                    "       (SELECT count(*) FROM operacional.seguimiento_clinico s"
                    "         WHERE s.id_embarazo = e.id_embarazo AND s.activo) AS seguimientos"
                    "  FROM operacional.embarazo e"
                    "  JOIN operacional.usuario_paciente up ON up.id_paciente = e.id_paciente"
                    "  JOIN operacional.usuario u ON u.id_usuario = up.id_usuario"
                    " WHERE e.id_embarazo = :e AND up.id_usuario = :u"
                ),
                {"e": prov.id_embarazo, "u": prov.id_usuario, "d": prov.id_dispositivo},
            ).one_or_none()
            conexion.rollback()
    finally:
        motor.dispose()
    if fila is None:
        raise ErrorDeHistorial("El embarazo del aprovisionamiento no pertenece a su cuenta.")
    if fila.estado_embarazo != "ACTIVO" or fila.fecha_inicio != prov.fecha_inicio_embarazo:
        raise ErrorDeHistorial("El embarazo no está ACTIVO o su fecha de inicio no coincide con el aprovisionamiento.")
    if fila.asignaciones != 1 or fila.seguimientos < 1:
        raise ErrorDeHistorial("El embarazo no tiene la asignación del dispositivo o el seguimiento médico esperados.")
    return {"id_paciente": fila.id_paciente, "email": fila.email}


def sesiones_en_la_ventana(prov, hasta: datetime) -> list[tuple[int, str | None]]:
    """(id_sesion, clave) de las sesiones del embarazo anteriores a la semana en curso."""
    fin = prov.fecha_inicio_embarazo + timedelta(days=7 * (semana_en_curso(prov.fecha_inicio_embarazo, hasta) - 1))
    motor = _motor()
    try:
        with motor.connect() as conexion:
            conexion.execute(text("SET TRANSACTION READ ONLY"))
            filas = conexion.execute(
                text(
                    "SELECT s.id_sesion, i.clave FROM operacional.sesion_monitoreo s"
                    "  LEFT JOIN operacional.idempotencia_solicitud i ON i.id_sesion = s.id_sesion"
                    " WHERE s.id_embarazo = :e"
                    "   AND s.fecha_inicio < (CAST(:fin AS date)::timestamp AT TIME ZONE 'America/Panama')"
                    " ORDER BY s.id_sesion"
                ),
                {"e": prov.id_embarazo, "fin": fin.isoformat()},
            ).all()
            conexion.rollback()
    finally:
        motor.dispose()
    return [(f.id_sesion, f.clave) for f in filas]


def lecturas_por_sesion(ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    motor = _motor()
    try:
        with motor.connect() as conexion:
            conexion.execute(text("SET TRANSACTION READ ONLY"))
            filas = conexion.execute(
                text(
                    "SELECT id_sesion, count(*) AS n FROM operacional.lectura_biometrica"
                    " WHERE id_sesion = ANY(:ids) GROUP BY id_sesion"
                ),
                {"ids": ids},
            ).all()
            conexion.rollback()
    finally:
        motor.dispose()
    return {f.id_sesion: f.n for f in filas}


def password_simulada() -> str:
    """La contraseña ficticia de las cuentas del dataset, leída sin ejecutar el generador."""
    fuente = (RAIZ_DEL_REPOSITORIO / "scripts" / "generate_mock_data.py").read_text(encoding="utf-8")
    for nodo in ast.parse(fuente).body:
        if (
            isinstance(nodo, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "PASSWORD_SIMULADA" for t in nodo.targets)
            and isinstance(nodo.value, ast.Constant)
        ):
            return nodo.value.value
    raise ErrorDeHistorial("No se encontró la contraseña simulada del dataset.")


def cargar(prov, plan: list[SesionPlaneada], settings, email: str, hasta: datetime) -> dict:
    claves_del_plan = {s.clave for s in plan}
    ajenas = [i for i, clave in sesiones_en_la_ventana(prov, hasta) if clave not in claves_del_plan]
    if ajenas:
        raise ErrorDeHistorial(
            f"El embarazo ya tiene {len(ajenas)} sesiones en las semanas del plan que no son de este plan "
            "(otra semilla u otra herramienta). No se mezclan historiales."
        )
    with httpx.Client(base_url=settings.api_base_url, timeout=settings.http_timeout) as http:
        respuesta = ClienteCentralHTTP(http).autenticar(email=email, password=password_simulada())
    if respuesta.estado is not EstadoRespuesta.OK or not respuesta.token:
        raise ErrorDeHistorial(f"No se pudo iniciar sesión como la paciente del episodio ({respuesta.estado.value}).")
    resultado = Counter()
    cabeceras = {"Authorization": f"Bearer {respuesta.token}"}
    with httpx.Client(base_url=settings.api_base_url, timeout=settings.http_timeout, headers=cabeceras) as http:
        cliente = ClienteEdge(http)
        for sesion in plan:
            entrega = cliente.enviar(clave=sesion.clave, payload_json=json.dumps(sesion.paquete, ensure_ascii=False))
            if entrega.resultado is not ResultadoEntrega.ENTREGADO:
                raise ErrorDeHistorial(
                    f"La sesión de la semana {sesion.semana} ({sesion.tipo.value}) no se entregó: "
                    f"{entrega.resultado.value} {entrega.codigo_http or ''} {entrega.error}".strip()
                )
            resultado["reproducidas" if entrega.reproducido else "creadas"] += 1
    return dict(resultado)


def verificar(prov, plan: list[SesionPlaneada], hasta: datetime) -> list[str]:
    claves = {s.clave: s for s in plan}
    en_base = sesiones_en_la_ventana(prov, hasta)
    diferencias = []
    encontradas = {clave: i for i, clave in en_base if clave in claves}
    faltan = sorted(set(claves) - set(encontradas))
    ajenas = [i for i, clave in en_base if clave not in claves]
    if faltan:
        diferencias.append(f"faltan {len(faltan)} sesiones del plan")
    if ajenas:
        diferencias.append(f"hay {len(ajenas)} sesiones en las semanas del plan que no son de este plan")
    n = lecturas_por_sesion(list(encontradas.values()))
    mal = [c for c, i in encontradas.items() if n.get(i, 0) != len(claves[c].paquete["lecturas"])]
    if mal:
        diferencias.append(f"{len(mal)} sesiones con un número de lecturas distinto del plan")
    return diferencias


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="historial_demo.py", description=__doc__.splitlines()[0])
    parser.add_argument("orden", choices=("previsualizar", "cargar", "verificar"))
    parser.add_argument("--dispositivo", default=DISPOSITIVO_POR_OMISION,
                        help="episodio de demostración (por omisión, el de paciente01)")
    parser.add_argument("--semilla", type=int, default=SEMILLA_POR_OMISION)
    parser.add_argument("--hasta", help="corte AAAA-MM-DD (por omisión, ahora); solo semanas completas anteriores")
    argumentos = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    try:
        settings = cargar_settings_gestante()
        ruta = (settings.provision_path if argumentos.dispositivo == DISPOSITIVO_POR_OMISION
                else settings.dispositivos_dir / f"{argumentos.dispositivo}.json")
        try:
            prov = modulo_provision.cargar(ruta)
        except modulo_provision.ProvisionInvalida as error:
            raise ErrorDeHistorial(f"Aprovisionamiento no utilizable: {error.detalle}") from None
        ahora = datetime.now(ZONA_HORARIA_CLINICA)
        hasta = (datetime.combine(date.fromisoformat(argumentos.hasta), time(0), tzinfo=ZONA_HORARIA_CLINICA)
                 if argumentos.hasta else ahora)
        if hasta > ahora:
            raise ErrorDeHistorial("El corte no puede ser posterior a ahora: no se generan fechas futuras.")
        plan = construir_plan(prov, argumentos.semilla, hasta)
        if not plan:
            raise ErrorDeHistorial("El embarazo todavía no tiene semanas completas.")
        episodio = leer_episodio(prov)
        resumen = resumen_del_plan(prov, plan, hasta)
        resumen["id_paciente"] = episodio["id_paciente"]
        resumen["semilla"] = argumentos.semilla
        if argumentos.orden == "previsualizar":
            print(json.dumps(resumen, ensure_ascii=False, indent=1))
            return CODIGO_DE_EXITO
        if argumentos.orden == "cargar":
            print(json.dumps(resumen, ensure_ascii=False, indent=1))
            entregas = cargar(prov, plan, settings, episodio["email"], hasta)
            print(f"Entregadas: {entregas.get('creadas', 0)} creadas, {entregas.get('reproducidas', 0)} ya existentes.")
        diferencias = verificar(prov, plan, hasta)
        if diferencias:
            print("DIFERENCIAS con el plan:")
            for diferencia in diferencias:
                print(f"  - {diferencia}")
            return CODIGO_DE_DIFERENCIAS
        print(f"COINCIDE con el plan: {len(plan)} sesiones y "
              f"{sum(len(s.paquete['lecturas']) for s in plan)} lecturas, sin sesiones ajenas en esas semanas.")
        return CODIGO_DE_EXITO
    except ErrorDeHistorial as error:
        print(f"Error: {error.detalle}", file=sys.stderr)
        return CODIGO_DE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
