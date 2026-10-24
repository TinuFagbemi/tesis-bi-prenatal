"""Carga el escenario de demostracion de SCRUM-109 sobre una base ya preparada.

Uso desde la raiz del repositorio, con el entorno virtual del backend::

    python scripts/cargar_escenario_scrum109.py cargar                 # corte = ahora
    python scripts/cargar_escenario_scrum109.py cargar --corte 2026-11-19
    python scripts/cargar_escenario_scrum109.py cargar --ensayo        # revierte al final
    python scripts/cargar_escenario_scrum109.py verificar              # solo lectura

**Sobre que base.** Una base de demostracion construida con el dataset
canonico (migraciones + ``load_mock_data.py``) **antes** de ejecutar
``provisionar_demo.py``: la protagonista del escenario ocupa el dispositivo
principal de la demo (``DEMO-SCRUM72-01``) y el aprovisionamiento antiguo lo
asignaria a una paciente canonica. Si ese dispositivo ya existe con otro
identificador, la carga se niega y lo explica.

**Que hace.** Lee la definicion (``escenario_scrum109_definicion.py``), resuelve
en la base lo que no se inventa --la clinica de la provincia de la demo, los
roles PACIENTE y MEDICO, la especialidad y los catalogos de semana y de
semaforo--, comprueba que la afiliacion del medico de la demo deriva esa
clinica cada dia del escenario (la regla del ETL), comprueba conflictos
y carga todo en **una transaccion** con :func:`app.loader.postgres.cargar_dataset`,
el mismo motor idempotente del cargador canonico: inserta lo que falta, deja lo
identico y aborta ante una fila con la misma llave y otro contenido. La
validacion canonica (metadatos de 732/1.180) no se usa ni se modifica: este
escenario tiene sus propios totales.

Despues, con la transaccion confirmada, escribe ``provision.json`` (el episodio
que localiza Demo Control), un archivo por dispositivo del escenario y el
manifiesto ``escenario_scrum109/manifiesto.json``, todo bajo la carpeta de
``GESTANTE_PROVISION_PATH``: es la que respalda ``entorno_demo.py``.

**El corte** (``--corte``): por omision, el instante de la ejecucion en
America/Panama. Admite una fecha (``2026-11-19`` = hasta el final de ese dia en
Panama) o un instante ISO (sin zona = Panama). Nunca puede ser posterior al
momento real de la ejecucion ni anterior al ultimo inicio de embarazo del
escenario. Repetirlo no duplica nada; avanzarlo carga solo lo pendiente; un
corte anterior a lo ya cargado se rechaza --no se borra nada en silencio--:
para volver atras se restaura un respaldo con ``entorno_demo.py restaurar``.

**Nunca imprime** una URL de conexion, una contrasena, un hash, ni el correo, el
nombre o la cedula de nadie: solo identificadores tecnicos.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_DEL_REPOSITORIO / "backend"))

from sqlalchemy import UniqueConstraint, and_, create_engine, select, text, tuple_  # noqa: E402
from sqlalchemy.engine import Connection, make_url  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402

from app.config import VARIABLE_URL_ALEMBIC, UrlDeEntornoInvalida, exigir_url_de_entorno  # noqa: E402
from app.etl import reglas  # noqa: E402
from app.gestante.config import ConfiguracionGestanteInvalida, cargar_settings_gestante  # noqa: E402
from app.loader.dataset import (  # noqa: E402
    ORDEN_DE_CARGA,
    ErrorDeCarga,
    _validar_columnas,
    _validar_pk_unicas,
    clave_primaria,
    normalizar_fila,
    tabla_operacional,
)
from app.loader.postgres import (  # noqa: E402
    _leer_existentes,
    cargar_dataset,
    citar_secuencia,
    leer_estado_de_secuencia,
    preflight,
    proximo_valor,
    sanear_mensaje,
    secuencia_de_tabla,
)
from app.models.enums import NombreRol  # noqa: E402
from app.services import passwords  # noqa: E402


def _cargar_script(nombre: str):
    if nombre in sys.modules:
        return sys.modules[nombre]
    ruta = Path(__file__).resolve().parent / f"{nombre}.py"
    especificacion = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[nombre] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


definicion = _cargar_script("escenario_scrum109_definicion")
demo_control = _cargar_script("demo_control")
provisionar_demo = demo_control.provisionar_demo
escenario_demo = _cargar_script("escenario_demo")

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1
CODIGO_DIFERENCIAS = 3

CARPETA_DEL_ESCENARIO = "escenario_scrum109"
ARCHIVO_MANIFIESTO = "manifiesto.json"


class ErrorDeEscenario(RuntimeError):
    """La carga no puede seguir. ``detalle`` no lleva datos personales."""

    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


class ConflictoDeEscenario(ErrorDeEscenario):
    """La base tiene algo que choca con el escenario. No se sobrescribe."""


class ErrorDeCorte(ErrorDeEscenario):
    """El corte pedido no se puede aplicar sin cargar futuro o borrar pasado."""


# ---------------------------------------------------------------------------
# El corte
# ---------------------------------------------------------------------------


def interpretar_corte(texto: str | None, ahora: datetime) -> datetime:
    """El instante de corte, en America/Panama. ``ahora`` debe llevar zona."""
    zona = definicion.ZONA
    if texto is None:
        corte = ahora.astimezone(zona)
    else:
        texto = texto.strip()
        try:
            if len(texto) == 10:
                dia = date.fromisoformat(texto)
                corte = datetime.combine(dia + timedelta(days=1), time(0), zona) - timedelta(microseconds=1)
            else:
                corte = datetime.fromisoformat(texto)
                corte = corte.replace(tzinfo=zona) if corte.tzinfo is None else corte.astimezone(zona)
        except ValueError:
            raise ErrorDeCorte(
                "El corte no es una fecha (AAAA-MM-DD) ni un instante ISO 8601."
            ) from None
    if corte > ahora:
        raise ErrorDeCorte(
            f"El corte {corte.isoformat()} es posterior al momento de la ejecucion "
            f"({ahora.astimezone(zona).isoformat()}): no se cargan mediciones futuras."
        )
    minimo = definicion.corte_minimo()
    if corte < minimo:
        raise ErrorDeCorte(
            f"El corte debe ser posterior a {minimo.isoformat()}, el ultimo inicio de "
            "embarazo del escenario; antes, se cargarian embarazos del futuro."
        )
    return corte


# ---------------------------------------------------------------------------
# Referencias que se resuelven en la base
# ---------------------------------------------------------------------------


def _fechas_que_debe_cubrir_la_afiliacion() -> list[date]:
    fechas = {e.fecha_inicio for e in definicion.EMBARAZOS}
    fechas |= {o.dia for o in definicion.todas_las_observaciones()}
    fechas.add(definicion.FIN_DEL_ESCENARIO)
    return sorted(fechas)


def _id_de_rol(conexion: Connection, nombre: str) -> int:
    rol = conexion.execute(
        text("SELECT id_rol FROM operacional.rol WHERE nombre_rol = :r"), {"r": nombre}
    ).scalar_one_or_none()
    if rol is None:
        raise ErrorDeEscenario(f"El catalogo de roles no tiene {nombre}.")
    return rol


def resolver_referencias(conexion: Connection, clave: str) -> definicion.Referencias:
    """Clinica de la demo, roles, especialidad, catalogos y hashes.

    La clinica es la unica de :data:`definicion.PROVINCIA_DE_LA_DEMO`. El medico
    no se busca entre los existentes: es el del escenario
    (:data:`definicion.MEDICO_DE_LA_DEMO`), con su unica afiliacion a esa
    clinica. Aqui se comprueba, para cada dia del escenario y con la misma
    regla del ETL (``reglas.verificar_afiliacion``), que esa afiliacion --junto
    con cualquier otra que ya tenga en la base-- deriva siempre esta clinica.
    Que otros medicos esten afiliados a la misma clinica no importa: cada
    embarazo tiene su propio seguimiento.
    """
    clinicas = conexion.execute(
        text("SELECT id_clinica FROM operacional.clinica WHERE provincia = :provincia"),
        {"provincia": definicion.PROVINCIA_DE_LA_DEMO},
    ).scalars().all()
    if len(clinicas) != 1:
        raise ErrorDeEscenario(
            f"Se esperaba exactamente una clinica en {definicion.PROVINCIA_DE_LA_DEMO} y hay "
            f"{len(clinicas)}. Carga primero el dataset canonico."
        )
    id_clinica = clinicas[0]

    medico = definicion.MEDICO_DE_LA_DEMO
    afiliaciones = {
        reglas.AfiliacionClinica(id_clinica=id_clinica, fecha_inicio=medico.inicio_afiliacion, fecha_final=None)
    }
    afiliaciones |= {
        reglas.AfiliacionClinica(id_clinica=f.id_clinica, fecha_inicio=f.fecha_inicio, fecha_final=f.fecha_final)
        for f in conexion.execute(
            text("SELECT id_clinica, fecha_inicio, fecha_final FROM operacional.medico_clinica WHERE id_medico = :m"),
            {"m": medico.id_medico},
        )
    }
    try:
        # Falla aqui y no a mitad de la ejecucion del ETL.
        for dia in _fechas_que_debe_cubrir_la_afiliacion():
            reglas.verificar_afiliacion(
                sorted(afiliaciones, key=lambda a: (a.id_clinica, a.fecha_inicio)),
                dia, id_medico=medico.id_medico, id_clinica_embarazo=id_clinica, id_lectura=0,
            )
    except reglas.ErrorDeDatos as error:
        raise ErrorDeEscenario(
            f"La afiliacion del medico de la demo (id_medico={medico.id_medico}) no deriva la clinica "
            f"id_clinica={id_clinica} en todo el escenario ({type(error).__name__})."
        ) from None

    especialidades = conexion.execute(
        text("SELECT id_especialidad FROM operacional.especialidad WHERE nombre_especialidad = :n"),
        {"n": definicion.ESPECIALIDAD_DEL_MEDICO},
    ).scalars().all()
    if len(especialidades) != 1:
        raise ErrorDeEscenario(
            f"El catalogo de especialidades no tiene una unica «{definicion.ESPECIALIDAD_DEL_MEDICO}»."
        )
    catalogos = provisionar_demo.leer_catalogos(conexion)

    cuentas = [p.id_usuario for p in definicion.PACIENTES if p.con_cuenta] + [medico.id_usuario]
    existentes = dict(
        conexion.execute(
            text("SELECT id_usuario, password_hash FROM operacional.usuario WHERE id_usuario = ANY(:ids)"),
            {"ids": cuentas},
        ).all()
    )
    # Argon2id lleva sal aleatoria: un hash nuevo en cada ejecucion convertiria
    # la reejecucion en un conflicto. Se conserva el existente si verifica la
    # clave simulada; si no verifica, la cuenta fue alterada y el cargador
    # informara el conflicto en lugar de sobrescribirla.
    hashes = {
        cuenta: existentes[cuenta]
        if cuenta in existentes and passwords.verificar(existentes[cuenta], clave)
        else passwords.hashear(clave)
        for cuenta in cuentas
    }
    return definicion.Referencias(
        id_clinica=id_clinica,
        id_rol_paciente=_id_de_rol(conexion, NombreRol.PACIENTE.value),
        id_rol_medico=_id_de_rol(conexion, NombreRol.MEDICO.value),
        id_especialidad=especialidades[0],
        semanas={f["semana"]: (f["id_tiempo_gest"], f["trimestre"]) for f in catalogos["tiempo_gestacional"]},
        semaforos=dict(catalogos["semaforo"]),
        password_hash=hashes,
    )


# ---------------------------------------------------------------------------
# Conflictos: se detectan antes de escribir y nunca se resuelven sobrescribiendo
# ---------------------------------------------------------------------------


def _ids(filas_por_tabla: dict, tabla: str) -> set[int]:
    columna = definicion.BLOQUES[tabla][0]
    return {fila[columna] for fila in filas_por_tabla.get(tabla, [])}


def comprobar_conflictos(conexion: Connection, completas: dict, del_corte: dict) -> None:
    motivos: list[str] = []

    # 1. Filas ajenas dentro de los bloques reservados.
    for tabla, (columna, (primero, ultimo)) in definicion.BLOQUES.items():
        presentes = set(
            conexion.execute(
                text(f"SELECT {columna} FROM operacional.{tabla} WHERE {columna} BETWEEN :a AND :b"),
                {"a": primero, "b": ultimo},
            ).scalars()
        )
        ajenas = presentes - _ids(completas, tabla)
        if ajenas:
            motivos.append(
                f"{tabla}: {len(ajenas)} fila(s) en el bloque reservado {primero}-{ultimo} que no son "
                f"del escenario (p. ej. {columna}={min(ajenas)})."
            )
        if tabla in ("sesion_monitoreo", "lectura_biometrica"):
            posteriores = presentes & (_ids(completas, tabla) - _ids(del_corte, tabla))
            if posteriores:
                raise ErrorDeCorte(
                    f"La base ya tiene {len(posteriores)} fila(s) de {tabla} del escenario posteriores "
                    "a este corte: se cargaron con un corte mayor. No se borra nada; para volver a un "
                    "corte anterior, restaura un respaldo tomado antes (entorno_demo.py restaurar)."
                )

    # 2. Valores unicos ya usados por otra fila (correo, cedula, codigo...).
    for tabla, filas in completas.items():
        modelo = tabla_operacional(tabla)
        pk = clave_primaria(modelo)
        grupos = [(c.name,) for c in modelo.columns if c.unique]
        grupos += [
            tuple(c.name for c in r.columns)
            for r in modelo.constraints
            if isinstance(r, UniqueConstraint)
        ]
        for grupo in grupos:
            for fila in filas:
                otra = conexion.execute(
                    select(*(modelo.c[n] for n in pk)).where(
                        and_(*(modelo.c[n] == fila[n] for n in grupo)),
                        tuple_(*(modelo.c[n] for n in pk)) != tuple(fila[n] for n in pk),
                    ).limit(1)
                ).first()
                if otra is not None:
                    clave_fila = ", ".join(f"{n}={fila[n]}" for n in pk)
                    motivos.append(
                        f"{tabla}: el valor de {list(grupo)} de la fila del escenario ({clave_fila}) "
                        f"ya lo usa otra fila ({', '.join(f'{n}={v}' for n, v in zip(pk, otra))})."
                    )
                    if tabla == "dispositivo" and grupo == ("codigo_dispositivo",) and (
                        fila["codigo_dispositivo"] == definicion.CODIGO_DISPOSITIVO_PRINCIPAL
                    ):
                        motivos.append(
                            "El dispositivo principal de la demo ya existe: esta base paso por el "
                            "aprovisionamiento antiguo. Construye la base de demostracion desde el "
                            "dataset canonico y carga el escenario antes de provisionar_demo.py."
                        )

    # 3. Relaciones ajenas sobre filas del escenario (una sola asignacion por
    #    dispositivo, un seguimiento y una asignacion por embarazo, ningun
    #    embarazo mas para las pacientes del escenario).
    embarazos = sorted(_ids(completas, "embarazo"))
    for tabla, columna, valores, propias in (
        ("asignacion_dispositivo", "id_dispositivo", sorted(_ids(completas, "dispositivo")),
         _ids(completas, "asignacion_dispositivo")),
        ("asignacion_dispositivo", "id_embarazo", embarazos, _ids(completas, "asignacion_dispositivo")),
        ("seguimiento_clinico", "id_embarazo", embarazos, _ids(completas, "seguimiento_clinico")),
        # El medico de la demo sigue solo a las pacientes del escenario.
        ("seguimiento_clinico", "id_medico", sorted(_ids(completas, "medico")),
         _ids(completas, "seguimiento_clinico")),
        ("embarazo", "id_paciente", sorted(_ids(completas, "paciente")), set(embarazos)),
    ):
        pk = definicion.BLOQUES[tabla][0]
        ajenas = set(
            conexion.execute(
                text(f"SELECT {pk} FROM operacional.{tabla} WHERE {columna} = ANY(:v)"), {"v": valores}
            ).scalars()
        ) - propias
        if ajenas:
            motivos.append(
                f"{tabla}: {len(ajenas)} fila(s) ajenas ligadas por {columna} a filas del escenario "
                f"(p. ej. {pk}={min(ajenas)})."
            )
    # medico_clinica no tiene identificador propio: sus filas se reconocen por
    # (id_medico, id_clinica). Otra afiliacion del medico de la demo es ajena.
    propias = {(f["id_medico"], f["id_clinica"]) for f in completas.get("medico_clinica", [])}
    ajenas = {
        (f.id_medico, f.id_clinica)
        for f in conexion.execute(
            text("SELECT id_medico, id_clinica FROM operacional.medico_clinica WHERE id_medico = ANY(:v)"),
            {"v": sorted(_ids(completas, "medico"))},
        )
    } - propias
    if ajenas:
        motivos.append(
            f"medico_clinica: {len(ajenas)} afiliacion(es) ajenas del medico de la demo (p. ej. {min(ajenas)})."
        )
    if motivos:
        raise ConflictoDeEscenario(" | ".join(motivos))


# ---------------------------------------------------------------------------
# Secuencias
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reserva:
    tabla: str
    antes: int
    despues: int


def reservar_secuencias(conexion: Connection) -> list[Reserva]:
    """Cada secuencia de una tabla del escenario, detras de su bloque reservado.

    Asi un ``INSERT`` posterior de la API (o del aprovisionamiento) nunca toma
    un identificador que una observacion futura del escenario ya tiene
    asignado. Las secuencias solo avanzan.
    """
    reservas = []
    for tabla, (columna, (_, ultimo)) in definicion.BLOQUES.items():
        modelo = tabla_operacional(tabla)
        ubicacion = secuencia_de_tabla(conexion, modelo, modelo.c[columna])
        if ubicacion is None:
            continue
        secuencia = citar_secuencia(conexion, *ubicacion)
        antes = proximo_valor(*leer_estado_de_secuencia(conexion, secuencia))
        objetivo = ultimo + 1
        if antes < objetivo:
            conexion.execute(text(f"ALTER SEQUENCE {secuencia} RESTART WITH {int(objetivo)}"))
        reservas.append(Reserva(tabla, antes, max(antes, objetivo)))
    return reservas


# ---------------------------------------------------------------------------
# Carga
# ---------------------------------------------------------------------------


@dataclass
class ResultadoEscenario:
    corte: datetime
    revision: str
    insertadas: dict[str, int]
    existentes: dict[str, int]
    esperadas: dict[str, int]
    presentes: dict[str, int]
    reservas: list[Reserva]
    referencias: definicion.Referencias = field(repr=False)
    del_corte: dict = field(repr=False)


def _normalizar(filas_por_tabla: dict) -> dict:
    normalizado = {}
    for tabla in ORDEN_DE_CARGA:
        filas = filas_por_tabla.get(tabla, [])
        modelo = tabla_operacional(tabla)
        _validar_columnas(tabla, modelo, filas)
        _validar_pk_unicas(tabla, modelo, filas)
        normalizado[tabla] = [normalizar_fila(modelo, fila) for fila in filas]
    return normalizado


def contar_presentes(conexion: Connection) -> dict[str, int]:
    conteos = {}
    for tabla, (columna, (primero, ultimo)) in definicion.BLOQUES.items():
        conteos[tabla] = conexion.execute(
            text(f"SELECT count(*) FROM operacional.{tabla} WHERE {columna} BETWEEN :a AND :b"),
            {"a": primero, "b": ultimo},
        ).scalar_one()
    for tabla, columna in (
        ("usuario_paciente", "id_usuario"), ("usuario_medico", "id_usuario"), ("medico_clinica", "id_medico"),
    ):
        conteos[tabla] = conexion.execute(
            text(f"SELECT count(*) FROM operacional.{tabla} WHERE {columna} BETWEEN :a AND :b"),
            {"a": definicion.BLOQUE_ENTIDADES[0], "b": definicion.BLOQUE_ENTIDADES[1]},
        ).scalar_one()
    return conteos


def cargar_escenario(conexion: Connection, *, corte: datetime, revision: str, clave: str) -> ResultadoEscenario:
    """Carga el escenario hasta ``corte`` en la transaccion de quien llama.

    No confirma ni revierte: igual que ``cargar_dataset``, la transaccion es de
    quien llama, de modo que cualquier excepcion la deja entera para revertir.
    """
    referencias = resolver_referencias(conexion, clave)
    completas = definicion.filas(referencias, None)
    del_corte = definicion.filas(referencias, corte)
    comprobar_conflictos(conexion, completas, del_corte)
    resultado = cargar_dataset(conexion, _normalizar(del_corte), revision=revision)
    reservas = reservar_secuencias(conexion)
    por_tabla = {t.tabla: t for t in resultado.tablas if t.tabla in del_corte}
    return ResultadoEscenario(
        corte=corte,
        revision=revision,
        insertadas={t: r.insertados for t, r in por_tabla.items()},
        existentes={t: r.existentes for t, r in por_tabla.items()},
        esperadas={t: len(f) for t, f in del_corte.items()},
        presentes=contar_presentes(conexion),
        reservas=reservas,
        referencias=referencias,
        del_corte=del_corte,
    )


def comparar_con_la_base(conexion: Connection, corte: datetime, clave: str) -> list[str]:
    """Solo lectura: diferencias entre la base y el escenario hasta ``corte``."""
    referencias = resolver_referencias(conexion, clave)
    completas = definicion.filas(referencias, None)
    del_corte = definicion.filas(referencias, corte)
    diferencias = []
    try:
        comprobar_conflictos(conexion, completas, del_corte)
    except ErrorDeEscenario as error:
        diferencias.append(error.detalle)
    normalizado = _normalizar(del_corte)
    for tabla, filas in normalizado.items():
        if not filas:
            continue
        modelo = tabla_operacional(tabla)
        pk = clave_primaria(modelo)
        existentes = _leer_existentes(conexion, modelo, filas)
        faltan = distintas = 0
        for fila in filas:
            actual = existentes.get(tuple(fila[n] for n in pk))
            if actual is None:
                faltan += 1
            elif any(actual[c] != v for c, v in fila.items()):
                distintas += 1
        if faltan or distintas:
            diferencias.append(f"{tabla}: {faltan} fila(s) ausentes y {distintas} distintas.")
    return diferencias


# ---------------------------------------------------------------------------
# Archivos locales: aprovisionamiento y manifiesto
# ---------------------------------------------------------------------------


def _escribir_json_atomico(ruta: Path, contenido: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporal = tempfile.mkstemp(prefix=f".{ruta.name}.", suffix=".tmp", dir=ruta.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as archivo:
            archivo.write(json.dumps(contenido, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
            archivo.flush()
            os.fsync(archivo.fileno())
        os.replace(temporal, ruta)
    except BaseException:
        Path(temporal).unlink(missing_ok=True)
        raise


def escribir_aprovisionamiento(conexion: Connection, ajustes) -> list[str]:
    """``provision.json`` y un archivo por dispositivo en curso, con el formato de siempre.

    El de ``provision.json`` sale de :func:`provisionar_demo.localizar_episodio_demo`,
    la lectura estricta de Demo Control: si no identifica a la protagonista sin
    ambiguedad, no se escribe nada. Un archivo que ya coincide con la base no se
    reescribe (su ``generado_en`` no cambia).
    """
    catalogos = provisionar_demo.leer_catalogos(conexion)
    principal = provisionar_demo.localizar_episodio_demo(conexion)
    esperado = next(e for e in definicion.EMBARAZOS if e.codigo_dispositivo == definicion.CODIGO_DISPOSITIVO_PRINCIPAL)
    if principal["id_embarazo"] != esperado.id_embarazo:
        raise ErrorDeEscenario("Demo Control no localiza el embarazo actual de la protagonista.")
    destinos = [(ajustes.provision_path, principal)]
    for e in definicion.EMBARAZOS:
        if e.cerrado:
            continue
        episodio = {
            "id_usuario": definicion.paciente(e.id_paciente).id_usuario,
            "id_embarazo": e.id_embarazo,
            "fecha_inicio": e.fecha_inicio,
            "id_dispositivo": e.id_embarazo,
        }
        destinos.append((provisionar_demo.ruta_de_dispositivo(ajustes.dispositivos_dir, e.codigo_dispositivo), episodio))
    escritos = []
    for ruta, episodio in destinos:
        estado, _ = demo_control.comparar_provision(ruta, episodio, catalogos)
        if estado != "COHERENTE":
            provisionar_demo.escribir_provision(ruta, episodio, catalogos)
            escritos.append(ruta.name)
    return escritos


def ruta_del_manifiesto(ajustes) -> Path:
    return ajustes.provision_path.parent / CARPETA_DEL_ESCENARIO / ARCHIVO_MANIFIESTO


def construir_manifiesto(resultado: ResultadoEscenario, base: str, ejecutado_en: datetime, anterior: dict | None) -> dict:
    """Sin secretos ni datos personales: version, corte, referencias y cantidades."""
    codigo_por_id = {v: k for k, v in resultado.referencias.semaforos.items()}
    lecturas_por_sesion = {f["id_sesion"]: f for f in resultado.del_corte["lectura_biometrica"]}
    observaciones: dict[str, Counter] = {}
    for sesion in resultado.del_corte["sesion_monitoreo"]:
        conteo = observaciones.setdefault(str(sesion["id_embarazo"]), Counter())
        conteo[sesion["tipo_sesion"]] += 1
        conteo[codigo_por_id[lecturas_por_sesion[sesion["id_sesion"]]["id_semaforo"]]] += 1
    entrada = {
        "corte": resultado.corte.isoformat(),
        "ejecutado_en": ejecutado_en.isoformat(timespec="seconds"),
        "insertadas": sum(resultado.insertadas.values()),
    }
    historial = list((anterior or {}).get("historial", [])) + [entrada]
    return {
        "escenario": definicion.NOMBRE,
        "version": definicion.VERSION,
        "base": base,
        "revision_alembic": resultado.revision,
        "corte_efectivo": resultado.corte.isoformat(),
        "zona_del_corte": str(definicion.ZONA),
        "ejecutado_en": entrada["ejecutado_en"],
        "fin_del_escenario": definicion.FIN_DEL_ESCENARIO.isoformat(),
        "referencias": {
            "id_clinica": resultado.referencias.id_clinica,
            "id_medico": definicion.MEDICO_DE_LA_DEMO.id_medico,
            "bloques": {t: {"columna": c, "desde": a, "hasta": b} for t, (c, (a, b)) in definicion.BLOQUES.items()},
            "pacientes": [
                {"id_paciente": p.id_paciente, "id_usuario": p.id_usuario, "papel": p.papel}
                for p in definicion.PACIENTES
            ],
            "embarazos": [
                {
                    "id_embarazo": e.id_embarazo, "id_paciente": e.id_paciente, "estado_embarazo": e.estado.value,
                    "fecha_inicio": e.fecha_inicio.isoformat(), "id_dispositivo": e.id_embarazo,
                    "codigo_dispositivo": e.codigo_dispositivo, "papel": e.papel,
                }
                for e in definicion.EMBARAZOS
            ],
        },
        "cantidades": {
            tabla: {
                "esperadas": resultado.esperadas.get(tabla, 0),
                "presentes": resultado.presentes.get(tabla, 0),
                "insertadas": resultado.insertadas.get(tabla, 0),
                "existentes": resultado.existentes.get(tabla, 0),
            }
            for tabla in resultado.esperadas
        },
        "observaciones_por_embarazo": {k: dict(sorted(v.items())) for k, v in sorted(observaciones.items())},
        "secuencias": {r.tabla: {"proximo_antes": r.antes, "proximo_despues": r.despues} for r in resultado.reservas},
        "historial": historial,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _url() -> str:
    try:
        return exigir_url_de_entorno(VARIABLE_URL_ALEMBIC)
    except UrlDeEntornoInvalida as error:
        raise ErrorDeEscenario(
            "Falta ALEMBIC_DATABASE_URL o no es utilizable: es la credencial de mantenimiento, "
            "la misma del cargador del dataset y del aprovisionamiento."
        ) from error


def _imprimir_resultado(resultado: ResultadoEscenario, confirmada: bool, escritos: list[str]) -> None:
    print(f"Escenario {definicion.VERSION}, corte {resultado.corte.isoformat()} (America/Panama)")
    print(f"Base en el head de Alembic: {resultado.revision}")
    print(f"{'tabla':<24}{'esperadas':>10}{'insertadas':>11}{'existentes':>11}{'presentes':>10}")
    for tabla in resultado.esperadas:
        print(
            f"{tabla:<24}{resultado.esperadas[tabla]:>10}{resultado.insertadas.get(tabla, 0):>11}"
            f"{resultado.existentes.get(tabla, 0):>11}{resultado.presentes.get(tabla, 0):>10}"
        )
    for r in resultado.reservas:
        if r.antes != r.despues:
            print(f"Secuencia de {r.tabla}: proximo {r.antes} -> {r.despues}")
    if confirmada:
        print("Carga confirmada en una unica transaccion.")
        print("Aprovisionamiento escrito: " + (", ".join(escritos) if escritos else "sin cambios"))
    else:
        print("ENSAYO: la transaccion se revirtio; no se escribio nada.")


def orden_cargar(corte_texto: str | None, ensayo: bool, ahora: datetime | None = None) -> int:
    ahora = ahora or datetime.now(timezone.utc)
    corte = interpretar_corte(corte_texto, ahora)
    ajustes = cargar_settings_gestante()
    clave = escenario_demo.password_simulada()
    url = _url()
    motor = create_engine(url, future=True)
    try:
        with motor.connect() as conexion:
            revision = preflight(conexion)
        with motor.connect() as conexion:
            transaccion = conexion.begin()
            try:
                resultado = cargar_escenario(conexion, corte=corte, revision=revision, clave=clave)
            except BaseException:
                transaccion.rollback()
                raise
            if ensayo:
                transaccion.rollback()
                _imprimir_resultado(resultado, False, [])
                return CODIGO_DE_EXITO
            transaccion.commit()
        with motor.connect() as conexion:
            escritos = escribir_aprovisionamiento(conexion, ajustes)
        ruta = ruta_del_manifiesto(ajustes)
        anterior = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else None
        _escribir_json_atomico(ruta, construir_manifiesto(resultado, make_url(url).database, ahora, anterior))
        escritos.append(f"{CARPETA_DEL_ESCENARIO}/{ARCHIVO_MANIFIESTO}")
    finally:
        motor.dispose()
    _imprimir_resultado(resultado, True, escritos)
    return CODIGO_DE_EXITO


def orden_verificar(corte_texto: str | None, ahora: datetime | None = None) -> int:
    ahora = ahora or datetime.now(timezone.utc)
    ajustes = cargar_settings_gestante()
    ruta = ruta_del_manifiesto(ajustes)
    if corte_texto is None and ruta.exists():
        corte_texto = json.loads(ruta.read_text(encoding="utf-8"))["corte_efectivo"]
    corte = interpretar_corte(corte_texto, ahora)
    clave = escenario_demo.password_simulada()
    motor = create_engine(_url(), future=True)
    try:
        with motor.connect() as conexion:
            conexion.execution_options(postgresql_readonly=True)
            with conexion.begin() as transaccion:
                preflight(conexion)
                diferencias = comparar_con_la_base(conexion, corte, clave)
                presentes = contar_presentes(conexion)
                catalogos = provisionar_demo.leer_catalogos(conexion)
                principal = provisionar_demo.localizar_episodio_demo(conexion)
                transaccion.rollback()
    finally:
        motor.dispose()
    estado, detalle = demo_control.comparar_provision(ajustes.provision_path, principal, catalogos)
    if estado != "COHERENTE":
        diferencias.append(f"provision.json: {estado} {'; '.join(detalle)}")
    print(f"Escenario {definicion.VERSION}, corte {corte.isoformat()} (America/Panama)")
    print("Filas del escenario en la base: " + ", ".join(f"{t}={n}" for t, n in presentes.items()))
    print(f"Episodio de Demo Control: embarazo={principal['id_embarazo']} cuenta={principal['id_usuario']}")
    if diferencias:
        print("DIFERENCIAS:")
        for diferencia in diferencias:
            print(f"  - {diferencia}")
        return CODIGO_DIFERENCIAS
    print("COMPLETO: la base contiene el escenario hasta el corte, sin conflictos.")
    return CODIGO_DE_EXITO


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cargar_escenario_scrum109.py", description=__doc__.splitlines()[0])
    ordenes = parser.add_subparsers(dest="orden", required=True)
    cargar = ordenes.add_parser("cargar", help="carga el escenario hasta el corte, en una transaccion")
    cargar.add_argument("--corte", help="AAAA-MM-DD o instante ISO (America/Panama); por omision, ahora")
    cargar.add_argument("--ensayo", action="store_true", help="ejecuta todo y revierte al final")
    verificar = ordenes.add_parser("verificar", help="compara la base con el escenario; solo lectura")
    verificar.add_argument("--corte", help="por omision, el corte del manifiesto")
    argumentos = parser.parse_args(argv)
    try:
        if argumentos.orden == "cargar":
            return orden_cargar(argumentos.corte, argumentos.ensayo)
        return orden_verificar(argumentos.corte)
    except ErrorDeEscenario as error:
        print(f"Error: {sanear_mensaje(error.detalle)}", file=sys.stderr)
        return CODIGO_DE_ERROR
    except (ErrorDeCarga, definicion.ErrorDeDefinicion, ConfiguracionGestanteInvalida) as error:
        print(f"Error: {sanear_mensaje(str(error))}", file=sys.stderr)
        print("No se conservo ningun cambio: la transaccion completa fue revertida.", file=sys.stderr)
        return CODIGO_DE_ERROR
    except SQLAlchemyError as error:
        print(f"Error de base de datos ({type(error).__name__}): {sanear_mensaje(str(error))}", file=sys.stderr)
        print("No se conservo ningun cambio: la transaccion completa fue revertida.", file=sys.stderr)
        return CODIGO_DE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
