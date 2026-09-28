"""Aprovisiona un dispositivo simulado de gestante para la demostracion (SCRUM-72).

Uso desde la raiz del repositorio::

    python scripts/provisionar_demo.py provisionar   # prepara y escribe el archivo
    python scripts/provisionar_demo.py verificar     # solo informa, sin escribir

**Por que hace falta.** El dataset canonico simula un periodo que ya paso: sus
embarazos ACTIVO empezaron entre febrero y septiembre de 2025, asi que hoy
estan en la semana 53 o mas. El contrato de ingesta solo admite semanas 1 a 42
--y el catalogo gestacional solo llega a 40--, de modo que **ninguna sesion
capturada hoy podria ingresarse contra un embarazo del dataset**. No es un
problema de identificadores: es que el reloj del dataset y el reloj real no
coinciden.

La solucion es aprovisionar un episodio de demostracion anclado al reloj real,
**sin alterar el dataset canonico**: no se modifica ni se borra una sola fila
suya. Se agregan filas nuevas y se reutiliza lo que ya existe --una cuenta
PACIENTE sin embarazo en curso, un dispositivo DISPONIBLE de su clinica y un
medico ya afiliado a esa clinica--, de modo que todas las reglas del dominio se
cumplan de verdad en vez de esquivarse.

**Idempotente.** Ejecutarlo dos veces no crea dos episodios: reconoce el que ya
creo por su codigo de dispositivo y lo reutiliza.

**Este script es el unico que habla con PostgreSQL.** Usa
``ALEMBIC_DATABASE_URL``, la credencial de mantenimiento, igual que el cargador
del dataset: crear filas clinicas no es trabajo del runtime restringido. El
adaptador de la gestante no abre ninguna conexion: solo lee el archivo JSON que
este comando escribe.

**Lo que escribe el archivo, y lo que no.** Lleva a que cuenta --por
``id_usuario``-- y a que embarazo sirve el dispositivo, su ``id_dispositivo`` y
los dos catalogos que hacen falta para formar un paquete sin red. No lleva el
correo, el nombre ni el telefono de nadie: esos se imprimen en pantalla para
quien opera, y no se guardan.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_DEL_REPOSITORIO / "backend"))

from sqlalchemy import create_engine, text  # noqa: E402  -- tras ajustar sys.path

from app.config import (  # noqa: E402
    VARIABLE_URL_ALEMBIC,
    UrlDeEntornoInvalida,
    exigir_url_de_entorno,
)
from app.gestante.config import cargar_settings_gestante  # noqa: E402
from app.gestante.provision import VERSION_SOPORTADA  # noqa: E402

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1

# Semana gestacional en la que queda el episodio de demostracion el dia que se
# aprovisiona. 28 esta comodamente dentro del catalogo (1-40), por encima de la
# semana 20 que el dominio exige para registrar movimiento fetal, y deja margen
# de meses antes de salirse del rango: el dispositivo sigue sirviendo despues.
SEMANA_INICIAL = 28

# Marca del dispositivo creado por este comando. Es lo que hace idempotente al
# script y lo que distingue sus filas de las del dataset canonico.
CODIGO_DISPOSITIVO_DEMO = "DEMO-SCRUM72-01"

DIAS_POR_SEMANA = 7


class ErrorDeAprovisionamiento(RuntimeError):
    def __init__(self, detalle: str) -> None:
        self.detalle = detalle
        super().__init__(detalle)


def _url_de_mantenimiento() -> str:
    """La credencial del migrador, la misma que usa el cargador del dataset.

    Se pide con ``exigir_url_de_entorno`` --no con ``Settings``-- porque esa
    funcion existe justamente para que un proceso solo tenga a la vista la
    credencial que le toca.
    """
    try:
        return exigir_url_de_entorno(VARIABLE_URL_ALEMBIC)
    except UrlDeEntornoInvalida as error:
        raise ErrorDeAprovisionamiento(
            "Falta ALEMBIC_DATABASE_URL o no es utilizable. Es la credencial de "
            "mantenimiento, la misma con la que se cargan las migraciones y el "
            "dataset simulado; este comando escribe filas clinicas y no usa la "
            "de la API."
        ) from error


def _fecha_de_inicio(hoy: date) -> date:
    """Inicio del embarazo para que hoy caiga en :data:`SEMANA_INICIAL`.

    ``semana_gestacional`` cuenta la primera semana como 1, asi que la semana N
    empieza ``(N-1)*7`` dias despues del inicio.
    """
    return hoy - timedelta(days=(SEMANA_INICIAL - 1) * DIAS_POR_SEMANA)


def _leer_catalogos(conexion) -> dict:
    semaforos = {
        fila.codigo_nivel: fila.id_semaforo
        for fila in conexion.execute(
            text("SELECT id_semaforo, codigo_nivel FROM operacional.semaforo")
        )
    }
    semanas = [
        {
            "semana": fila.semana_gestacion,
            "id_tiempo_gest": fila.id_tiempo_gest,
            "trimestre": fila.trimestre,
        }
        for fila in conexion.execute(
            text(
                "SELECT id_tiempo_gest, semana_gestacion, trimestre"
                "  FROM operacional.tiempo_gestacional"
                " ORDER BY semana_gestacion"
            )
        )
    ]
    if not semaforos or not semanas:
        raise ErrorDeAprovisionamiento(
            "Los catalogos de semaforo o de tiempo gestacional estan vacios. "
            "Carga el dataset simulado antes de aprovisionar."
        )
    return {"semaforo": semaforos, "tiempo_gestacional": semanas}


def _buscar_demo_existente(conexion) -> dict | None:
    """El episodio que este comando creo antes, si sigue ahi."""
    fila = conexion.execute(
        text(
            "SELECT d.id_dispositivo, ad.id_embarazo, e.fecha_inicio,"
            "       up.id_usuario, u.email"
            "  FROM operacional.dispositivo d"
            "  JOIN operacional.asignacion_dispositivo ad"
            "    ON ad.id_dispositivo = d.id_dispositivo"
            "  JOIN operacional.embarazo e ON e.id_embarazo = ad.id_embarazo"
            "  JOIN operacional.usuario_paciente up ON up.id_paciente = e.id_paciente"
            "  JOIN operacional.usuario u ON u.id_usuario = up.id_usuario"
            " WHERE d.codigo_dispositivo = :codigo"
            " ORDER BY ad.id_asignacion DESC"
            " LIMIT 1"
        ),
        {"codigo": CODIGO_DISPOSITIVO_DEMO},
    ).one_or_none()
    if fila is None:
        return None
    return {
        "id_dispositivo": fila.id_dispositivo,
        "id_embarazo": fila.id_embarazo,
        "fecha_inicio": fila.fecha_inicio,
        "id_usuario": fila.id_usuario,
        "email": fila.email,
    }


def _elegir_paciente(conexion) -> dict:
    """Una cuenta PACIENTE sin embarazo en curso, para no crear ambiguedad.

    Si se le agregara un episodio ACTIVO a una gestante que ya tiene otro, la
    interfaz tendria dos episodios en curso y --con toda razon-- se negaria a
    llamar «actual» a ninguno. Elegir a quien no tiene ninguno evita fabricar
    esa ambiguedad.
    """
    fila = conexion.execute(
        text(
            "SELECT p.id_paciente, e.id_clinica, up.id_usuario, u.email"
            "  FROM operacional.paciente p"
            "  JOIN operacional.embarazo e ON e.id_paciente = p.id_paciente"
            "  JOIN operacional.usuario_paciente up ON up.id_paciente = p.id_paciente"
            "  JOIN operacional.usuario u ON u.id_usuario = up.id_usuario"
            " WHERE p.id_paciente NOT IN ("
            "        SELECT id_paciente FROM operacional.embarazo"
            "         WHERE estado_embarazo = 'ACTIVO')"
            " ORDER BY p.id_paciente"
            " LIMIT 1"
        )
    ).one_or_none()
    if fila is None:
        raise ErrorDeAprovisionamiento(
            "No hay ninguna cuenta PACIENTE sin embarazo en curso. Agregar un "
            "episodio a una gestante que ya tiene uno crearia una ambiguedad "
            "que la interfaz no debe inventar."
        )
    return {
        "id_paciente": fila.id_paciente,
        "id_clinica": fila.id_clinica,
        "id_usuario": fila.id_usuario,
        "email": fila.email,
    }


def _elegir_medico(conexion, id_clinica: int, dia: date) -> int:
    """Un medico afiliado a esa clinica ese dia, y solo uno.

    El ETL exige exactamente una afiliacion vigente para el medico responsable
    (``verificar_afiliacion``). Se elige por identificador para que dos
    ejecuciones sobre los mismos datos escojan al mismo.
    """
    fila = conexion.execute(
        text(
            "SELECT mc.id_medico FROM operacional.medico_clinica mc"
            " WHERE mc.id_clinica = :id_clinica"
            "   AND mc.fecha_inicio <= :dia"
            "   AND (mc.fecha_final IS NULL OR mc.fecha_final >= :dia)"
            " ORDER BY mc.id_medico"
            " LIMIT 1"
        ),
        {"id_clinica": id_clinica, "dia": dia},
    ).one_or_none()
    if fila is None:
        raise ErrorDeAprovisionamiento(
            f"Ningun medico esta afiliado hoy a la clinica id_clinica={id_clinica}."
        )
    return fila.id_medico


def _crear_dispositivo(conexion, id_clinica: int) -> int:
    """El dispositivo de demostracion, creado una vez y reutilizado despues.

    No se toma uno DISPONIBLE del dataset: cambiarle el estado a una fila
    canonica seria alterar el dataset, y el objetivo es exactamente no hacerlo.
    """
    fila = conexion.execute(
        text(
            "SELECT id_dispositivo FROM operacional.dispositivo"
            " WHERE codigo_dispositivo = :codigo"
        ),
        {"codigo": CODIGO_DISPOSITIVO_DEMO},
    ).one_or_none()
    if fila is not None:
        return fila.id_dispositivo

    return conexion.execute(
        text(
            "INSERT INTO operacional.dispositivo"
            " (id_clinica, codigo_dispositivo, modelo, version_firmware, estado)"
            " VALUES (:id_clinica, :codigo, 'Simulado SCRUM-72', '0.0-demo', 'ASIGNADO')"
            " RETURNING id_dispositivo"
        ),
        {"id_clinica": id_clinica, "codigo": CODIGO_DISPOSITIVO_DEMO},
    ).scalar_one()


def _provisionar(conexion, hoy: date) -> dict:
    """Crea --o reconoce-- el episodio de demostracion. Una sola transaccion."""
    existente = _buscar_demo_existente(conexion)
    if existente is not None:
        return existente

    paciente = _elegir_paciente(conexion)
    fecha_inicio = _fecha_de_inicio(hoy)
    id_medico = _elegir_medico(conexion, paciente["id_clinica"], fecha_inicio)
    id_dispositivo = _crear_dispositivo(conexion, paciente["id_clinica"])

    id_embarazo = conexion.execute(
        text(
            "INSERT INTO operacional.embarazo"
            " (id_paciente, id_clinica, numero_gestas, numero_partos,"
            "  fecha_inicio, fecha_probable_parto, estado_embarazo)"
            " VALUES (:id_paciente, :id_clinica, 1, 0,"
            "         :fecha_inicio, :fpp, 'ACTIVO')"
            " RETURNING id_embarazo"
        ),
        {
            "id_paciente": paciente["id_paciente"],
            "id_clinica": paciente["id_clinica"],
            "fecha_inicio": fecha_inicio,
            "fpp": fecha_inicio + timedelta(days=280),
        },
    ).scalar_one()

    conexion.execute(
        text(
            "INSERT INTO operacional.asignacion_dispositivo"
            " (id_dispositivo, id_embarazo, fecha_inicio, activo)"
            " VALUES (:id_dispositivo, :id_embarazo, :fecha_inicio, true)"
        ),
        {
            "id_dispositivo": id_dispositivo,
            "id_embarazo": id_embarazo,
            "fecha_inicio": fecha_inicio,
        },
    )

    conexion.execute(
        text(
            "INSERT INTO operacional.seguimiento_clinico"
            " (id_embarazo, id_medico, fecha_asignacion, rol_seguimiento, activo)"
            " VALUES (:id_embarazo, :id_medico, :fecha_inicio, 'PRINCIPAL', true)"
        ),
        {
            "id_embarazo": id_embarazo,
            "id_medico": id_medico,
            "fecha_inicio": fecha_inicio,
        },
    )

    return {
        "id_dispositivo": id_dispositivo,
        "id_embarazo": id_embarazo,
        "fecha_inicio": fecha_inicio,
        "id_usuario": paciente["id_usuario"],
        "email": paciente["email"],
    }


def _escribir_archivo(ruta: Path, demo: dict, catalogos: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    contenido = {
        "version": VERSION_SOPORTADA,
        "generado_en": datetime.now(timezone.utc).isoformat(),
        "cuenta": {"id_usuario": demo["id_usuario"]},
        "embarazo": {
            "id_embarazo": demo["id_embarazo"],
            "fecha_inicio": demo["fecha_inicio"].isoformat(),
        },
        "dispositivo": {"id_dispositivo": demo["id_dispositivo"]},
        "catalogos": catalogos,
    }
    ruta.write_text(
        json.dumps(contenido, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _informar(demo: dict, ruta: Path, escrito: bool) -> None:
    print("Aprovisionamiento del dispositivo simulado de la gestante:")
    print(f"  cuenta PACIENTE   : {demo['email']} (id_usuario={demo['id_usuario']})")
    print(f"  embarazo de demo  : id_embarazo={demo['id_embarazo']}")
    print(f"  inicio del episodio: {demo['fecha_inicio'].isoformat()}")
    print(f"  dispositivo       : id_dispositivo={demo['id_dispositivo']}")
    print(f"  archivo           : {ruta}" + ("" if escrito else "  (no se escribio)"))
    print(
        "La contrasena de las cuentas simuladas es la del dataset generado; el "
        "README y las pruebas la documentan. Este comando no la imprime ni la "
        "guarda."
    )


def orden_provisionar(ruta: Path) -> int:
    motor = create_engine(_url_de_mantenimiento(), future=True)
    with motor.begin() as conexion:
        catalogos = _leer_catalogos(conexion)
        demo = _provisionar(conexion, date.today())
    _escribir_archivo(ruta, demo, catalogos)
    _informar(demo, ruta, escrito=True)
    return CODIGO_DE_EXITO


def orden_verificar(ruta: Path) -> int:
    motor = create_engine(_url_de_mantenimiento(), future=True)
    with motor.connect() as conexion:
        demo = _buscar_demo_existente(conexion)
    if demo is None:
        print(
            "Todavia no hay un episodio de demostracion. Ejecuta "
            "'python scripts/provisionar_demo.py provisionar'.",
            file=sys.stderr,
        )
        return CODIGO_DE_ERROR
    _informar(demo, ruta, escrito=ruta.exists())
    return CODIGO_DE_EXITO


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="provisionar_demo.py",
        description=(
            "Aprovisiona un dispositivo simulado de gestante: un episodio "
            "anclado al reloj real, su dispositivo y los catalogos que el "
            "portal necesita para capturar sin red. No altera el dataset "
            "canonico."
        ),
    )
    ordenes = parser.add_subparsers(dest="orden", required=True)
    ordenes.add_parser("provisionar", help="prepara el episodio y escribe el archivo")
    ordenes.add_parser("verificar", help="informa del estado, sin escribir en la base")

    argumentos = parser.parse_args(argv)
    ruta = cargar_settings_gestante().provision_path

    try:
        if argumentos.orden == "provisionar":
            return orden_provisionar(ruta)
        return orden_verificar(ruta)
    except ErrorDeAprovisionamiento as error:
        print(f"Error: {error.detalle}", file=sys.stderr)
        return CODIGO_DE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
