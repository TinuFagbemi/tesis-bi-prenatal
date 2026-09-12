"""Evolucion del almacenamiento local de v1 a v2, sin perder una sola fila.

Con **archivos reales** bajo ``tmp_path``, nunca ``:memory:``: lo que hay que
demostrar es que una base de SCRUM-64 guardada en disco puede actualizarse y
seguir sirviendo, y una base en memoria desapareceria con la conexion.

La prueba central de este archivo no es que las filas sobrevivan --eso se ve a
simple vista-- sino que **una base migrada y una base creada de cero produzcan la
misma definicion almacenada**. SQLite reescribe el DDL de todo lo que se
renombra: ``ALTER TABLE outbox_v2 RENAME TO outbox`` deja
``CREATE TABLE "outbox" (...)``, con comillas, y ``verificar_esquema`` compara
contra el texto que este modulo escribe. Una migracion construida asi produciria
una base que la siguiente inicializacion rechaza -- un fallo que solo aparece con
datos reales. Por eso se renombra la tabla **vieja** y la nueva nace con su
nombre definitivo, y por eso hay una prueba que compara los dos textos.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.edge import almacenamiento as alm
from app.edge import outbox
from app.edge.estados import EstadoEntrega, MotivoRevision

EVIDENCIA = ("2026-03-01T12:00:00+00:00", 832, "[1280, 1281]")


def crear_v1(ruta, filas=()):
    """Una base exactamente como la dejaba SCRUM-64, con las filas indicadas.

    ``filas`` son tuplas ``(estado, reintentable, intentos, ultimo_http,
    ultimo_error)``.
    """
    with alm.conectar(ruta) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        conexion.execute(alm.DDL_CAPTURA)
        conexion.execute(alm.DDL_OUTBOX_V1)
        conexion.execute(f"PRAGMA user_version = {alm.VERSION_ANTERIOR}")
        for indice, (estado, reintentable, intentos, http, error) in enumerate(
            filas, start=1
        ):
            conexion.execute(
                "INSERT INTO captura_local (payload_json, capturado_en)"
                " VALUES (?, ?)",
                ('{"lecturas": [{"hr_valor": 88}]}', f"2026-03-01T10:0{indice}:00+00:00"),
            )
            extra = EVIDENCIA if estado == EstadoEntrega.ENVIADO.value else (None, None, None)
            conexion.execute(
                "INSERT INTO outbox (id_captura, clave_idempotencia, estado,"
                " reintentable, intentos, creado_en, actualizado_en, ultimo_http,"
                " ultimo_error, enviado_en, id_sesion_remota, ids_lectura_remotos)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    indice,
                    f"clave-heredada-{indice:04d}",
                    estado,
                    reintentable,
                    intentos,
                    "2026-03-01T10:00:00+00:00",
                    "2026-03-01T10:00:00+00:00",
                    http,
                    error,
                    *extra,
                ),
            )
        conexion.execute("COMMIT")


def definiciones(ruta) -> dict[str, str]:
    with alm.conectar(ruta) as conexion:
        return {
            fila["name"]: " ".join((fila["sql"] or "").split())
            for fila in conexion.execute(
                "SELECT name, sql FROM sqlite_master WHERE sql IS NOT NULL"
            ).fetchall()
        }


@pytest.fixture
def ruta(tmp_path):
    return tmp_path / "nodo_edge.sqlite3"


# ---------------------------------------------------------------------------
# 1. Equivalencia entre una base nueva y una migrada
# ---------------------------------------------------------------------------


def test_una_base_creada_en_v2_pasa_la_verificacion(ruta):
    with alm.conectar(ruta) as conexion:
        assert alm.inicializar(conexion) is True
        alm.verificar_esquema(conexion)
        assert alm.leer_version(conexion) == alm.VERSION_DE_ESQUEMA


def test_una_base_v1_migrada_pasa_la_verificacion(ruta):
    crear_v1(ruta, [(EstadoEntrega.PENDIENTE.value, None, 0, None, None)])
    with alm.conectar(ruta) as conexion:
        assert alm.inicializar(conexion) is True
        alm.verificar_esquema(conexion)
        assert alm.leer_version(conexion) == alm.VERSION_DE_ESQUEMA


def test_el_ddl_de_una_base_migrada_es_identico_al_de_una_nueva(ruta, tmp_path):
    """La regresion del defecto de las comillas del ``RENAME``.

    Si alguien invirtiera el orden --crear ``outbox_v2`` y renombrarla-- esta
    prueba fallaria de inmediato, en vez de dejar una migracion que produce bases
    que la ejecucion siguiente rechaza.
    """
    nueva = tmp_path / "nueva.sqlite3"
    with alm.conectar(nueva) as conexion:
        alm.inicializar(conexion)

    crear_v1(ruta, [(EstadoEntrega.PENDIENTE.value, None, 0, None, None)])
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)

    esperadas = definiciones(nueva)
    obtenidas = definiciones(ruta)
    assert set(esperadas) == set(obtenidas)
    for nombre, definicion in esperadas.items():
        assert obtenidas[nombre] == definicion, nombre
    # Y explicitamente, el detalle que provocaba el fallo.
    assert obtenidas[alm.TABLA_OUTBOX].startswith("CREATE TABLE outbox (")
    assert '"outbox"' not in obtenidas[alm.TABLA_OUTBOX]


def test_los_indices_parciales_existen_por_ambos_caminos(ruta, tmp_path):
    nueva = tmp_path / "nueva.sqlite3"
    with alm.conectar(nueva) as conexion:
        alm.inicializar(conexion)
    crear_v1(ruta, [(EstadoEntrega.PENDIENTE.value, None, 0, None, None)])
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)

    for base in (nueva, ruta):
        with alm.conectar(base) as conexion:
            for indice in alm.INDICES_PARCIALES_ESPERADOS:
                real = alm._indice_real(conexion, indice.tabla, indice.nombre)
                assert real is not None, (base, indice.nombre)
                assert real["unique"] and real["partial"]


# ---------------------------------------------------------------------------
# 2. Las filas sobreviven, con la semantica correcta
# ---------------------------------------------------------------------------


CASOS = [
    ("pendiente", EstadoEntrega.PENDIENTE.value, None, 0, None, None, None),
    ("enviado", EstadoEntrega.ENVIADO.value, None, 1, 201, None, None),
    (
        "reintentable",
        EstadoEntrega.FALLIDO.value,
        1,
        2,
        503,
        "error del servidor 503",
        None,
    ),
    (
        "en_revision",
        EstadoEntrega.FALLIDO.value,
        0,
        3,
        409,
        "conflicto 409",
        MotivoRevision.RECHAZO_PERMANENTE.value,
    ),
]


@pytest.mark.parametrize(
    "nombre, estado, reintentable, intentos, http, error, motivo", CASOS
)
def test_la_migracion_conserva_cada_estado(
    ruta, nombre, estado, reintentable, intentos, http, error, motivo
):
    crear_v1(ruta, [(estado, reintentable, intentos, http, error)])
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        fila = outbox.leer_evento(conexion, 1)

    assert fila["estado"] == estado
    assert fila["reintentable"] == reintentable
    assert fila["intentos"] == intentos
    assert fila["clave_idempotencia"] == "clave-heredada-0001"
    assert fila["ultimo_http"] == http
    assert fila["ultimo_error"] == error
    assert fila["motivo_revision"] == motivo
    # Todo lo consumido bajo v1 es heredado, y el contador no se reinicia.
    assert fila["intentos_heredados"] == intentos
    # Nadie ha adoptado una politica todavia.
    assert fila["max_intentos_aplicado"] is None
    assert fila["proximo_intento_en"] is None


def test_un_enviado_heredado_conserva_su_evidencia_remota(ruta):
    """El caso que habria abortado la migracion con el CHECK condicionado.

    Si la confirmacion se hubiera duplicado en ``outbox`` y el ``CHECK`` la
    hubiera exigido, este ``INSERT ... SELECT`` habria fallado y la migracion
    entera se habria revertido en cualquier base que ya tuviera una entrega.
    """
    crear_v1(ruta, [(EstadoEntrega.ENVIADO.value, None, 1, 201, None)])
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        fila = outbox.leer_evento(conexion, 1)

    assert fila["enviado_en"] == EVIDENCIA[0]
    assert fila["id_sesion_remota"] == EVIDENCIA[1]
    assert fila["ids_lectura_remotos"] == EVIDENCIA[2]


def test_la_migracion_conserva_todas_las_filas_y_sus_identificadores(ruta):
    crear_v1(ruta, [(caso[1], caso[2], caso[3], caso[4], caso[5]) for caso in CASOS])
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        filas = conexion.execute(
            "SELECT id_outbox, id_captura, clave_idempotencia FROM outbox"
            " ORDER BY id_outbox"
        ).fetchall()

    assert [tuple(f) for f in filas] == [
        (1, 1, "clave-heredada-0001"),
        (2, 2, "clave-heredada-0002"),
        (3, 3, "clave-heredada-0003"),
        (4, 4, "clave-heredada-0004"),
    ]


def test_el_payload_heredado_no_se_toca(ruta):
    crear_v1(ruta, [(EstadoEntrega.PENDIENTE.value, None, 0, None, None)])
    with alm.conectar(ruta) as conexion:
        antes = outbox.leer_payload(conexion, 1)
        alm.inicializar(conexion)
        despues = outbox.leer_payload(conexion, 1)
    assert antes == despues == '{"lecturas": [{"hr_valor": 88}]}'


def test_la_migracion_es_idempotente(ruta):
    crear_v1(ruta, [(EstadoEntrega.PENDIENTE.value, None, 0, None, None)])
    with alm.conectar(ruta) as conexion:
        assert alm.inicializar(conexion) is True
    with alm.conectar(ruta) as conexion:
        assert alm.inicializar(conexion) is False
        assert outbox.leer_evento(conexion, 1) is not None


# ---------------------------------------------------------------------------
# 3. Coherencia entre el contador y el historial
# ---------------------------------------------------------------------------


def test_la_invariante_de_coherencia_cubre_migrados_y_nativos(ruta):
    """``count(historial) == intentos - intentos_heredados``, en ambos origenes.

    Para un evento nativo de v2 el termino heredado es 0 y la regla se reduce a
    la original. Para uno migrado es exacta, en vez de exigir un historial que
    nunca se guardo.
    """
    crear_v1(ruta, [(EstadoEntrega.FALLIDO.value, 1, 3, 503, "error del servidor 503")])
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)

        # Migrado: tres intentos, cero historial.
        fila = outbox.leer_evento(conexion, 1)
        registrados = conexion.execute(
            "SELECT count(*) FROM intento_sincronizacion WHERE id_outbox = 1"
        ).fetchone()[0]
        assert registrados == fila["intentos"] - fila["intentos_heredados"] == 0

        # Nativo: se captura y se reclama un intento.
        from app.edge.captura import capturar

        registro = capturar(conexion, _paquete())
        with alm.transaccion(conexion):
            outbox.reclamar_intento(
                conexion,
                registro.id_outbox,
                max_attempts=5,
                duracion_lease=70.0,
                momento=outbox.ahora_utc(),
            )
        fila = outbox.leer_evento(conexion, registro.id_outbox)
        registrados = conexion.execute(
            "SELECT count(*) FROM intento_sincronizacion WHERE id_outbox = ?",
            (registro.id_outbox,),
        ).fetchone()[0]
        assert fila["intentos_heredados"] == 0
        assert registrados == fila["intentos"] - fila["intentos_heredados"] == 1


def test_el_primer_intento_v2_de_un_migrado_continua_la_numeracion(ruta):
    """Tres heredados -> el siguiente es el numero 4, y el hueco 1-3 es la senal."""
    crear_v1(ruta, [(EstadoEntrega.FALLIDO.value, 1, 3, 503, "error del servidor 503")])
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        with alm.transaccion(conexion):
            reclamacion = outbox.reclamar_intento(
                conexion, 1, max_attempts=5, duracion_lease=70.0,
                momento=outbox.ahora_utc(),
            )
        assert reclamacion.numero == 4
        assert outbox.leer_evento(conexion, 1)["intentos"] == 4


# ---------------------------------------------------------------------------
# 4. Lo que la migracion se niega a hacer
# ---------------------------------------------------------------------------


def test_una_base_v1_divergente_no_se_migra(ruta):
    """No se reescribe un archivo cuya forma no es la que v1 producia."""
    with alm.conectar(ruta) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        conexion.execute(alm.DDL_CAPTURA)
        conexion.execute(
            alm.DDL_OUTBOX_V1.replace(
                "clave_idempotencia  TEXT    NOT NULL UNIQUE",
                "clave_idempotencia  TEXT    NOT NULL",
            )
        )
        conexion.execute(f"PRAGMA user_version = {alm.VERSION_ANTERIOR}")
        conexion.execute("COMMIT")

    with alm.conectar(ruta) as conexion:
        with pytest.raises(alm.EsquemaIncompatible):
            alm.inicializar(conexion)
        # Y no la ha tocado: sigue siendo v1.
        assert alm.leer_version(conexion) == alm.VERSION_ANTERIOR
        assert alm.TABLA_INTENTO not in alm.tablas_presentes(conexion)


@pytest.mark.parametrize("version", [3, 99])
def test_una_version_futura_se_rechaza(ruta, version):
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        conexion.execute(f"PRAGMA user_version = {version}")
    with alm.conectar(ruta) as conexion:
        with pytest.raises(alm.EsquemaIncompatible):
            alm.inicializar(conexion)
        assert alm.leer_version(conexion) == version


def test_la_migracion_no_deja_la_tabla_temporal(ruta):
    crear_v1(ruta, [(EstadoEntrega.PENDIENTE.value, None, 0, None, None)])
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        assert alm.TABLA_MIGRACION_V1 not in alm.tablas_presentes(conexion)


def test_la_migracion_conserva_la_llave_foranea_hacia_la_captura(ruta):
    """Renombrar la tabla vieja con ``foreign_keys = ON`` no rompe la FK."""
    crear_v1(ruta, [(EstadoEntrega.PENDIENTE.value, None, 0, None, None)])
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        foraneas = alm._foraneas_reales(conexion, alm.TABLA_OUTBOX)
        assert foraneas == (
            alm.ForaneaEsperada("id_captura", alm.TABLA_CAPTURA, "id_captura"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            with alm.transaccion(conexion):
                conexion.execute(
                    "INSERT INTO outbox (id_captura, clave_idempotencia, estado,"
                    " creado_en, actualizado_en) VALUES (9999, 'clave-huerfana',"
                    " 'PENDIENTE', 't', 't')"
                )


def test_la_migracion_es_atomica(ruta, monkeypatch):
    """Si el upgrade falla a mitad, el archivo sigue siendo una v1 usable."""
    crear_v1(ruta, [(EstadoEntrega.PENDIENTE.value, None, 0, None, None)])

    original = alm.DDL_INTENTO
    monkeypatch.setattr(alm, "DDL_INTENTO", "CREATE TABLE esto no es sql valido (")
    with alm.conectar(ruta) as conexion:
        with pytest.raises(sqlite3.OperationalError):
            alm.inicializar(conexion)
    monkeypatch.setattr(alm, "DDL_INTENTO", original)

    with alm.conectar(ruta) as conexion:
        assert alm.leer_version(conexion) == alm.VERSION_ANTERIOR
        assert alm.tablas_presentes(conexion) == {alm.TABLA_CAPTURA, alm.TABLA_OUTBOX}
        assert conexion.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# 5. El soporte minimo de SQLite
# ---------------------------------------------------------------------------


def test_se_exige_una_version_de_sqlite_con_returning(monkeypatch):
    """Un SQLite viejo falla con una frase, no con un error de sintaxis."""
    monkeypatch.setattr(alm.sqlite3, "sqlite_version_info", (3, 34, 0))
    monkeypatch.setattr(alm.sqlite3, "sqlite_version", "3.34.0")
    with pytest.raises(alm.SoporteInsuficiente, match="3.35"):
        alm.exigir_soporte_de_returning()


def test_la_instalacion_actual_soporta_returning():
    alm.exigir_soporte_de_returning()


def _paquete() -> dict:
    from tests.test_edge_captura import paquete_de_varias_lecturas

    return paquete_de_varias_lecturas(1)
