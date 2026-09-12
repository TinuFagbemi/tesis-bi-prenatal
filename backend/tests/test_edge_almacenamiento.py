"""Esquema, garantias e inicializacion del almacenamiento local del nodo edge.

Estas pruebas usan **archivos SQLite reales** bajo ``tmp_path``, nunca
``:memory:``. La diferencia importa: lo que hay que demostrar es que un evento
sobrevive a que el proceso se cierre, y una base en memoria desaparece con la
conexion, asi que probaria justo lo contrario de lo que se afirma.

Cada prueba recibe su propio ``tmp_path``, de modo que ninguna comparte archivo
con otra ni puede tocar una base personal.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.edge import almacenamiento as alm
from app.edge.estados import EstadoEntrega, MotivoRevision


@pytest.fixture
def ruta(tmp_path):
    """Ruta a un archivo que todavia no existe, dentro del temporal de la prueba."""
    return tmp_path / "nodo_edge.sqlite3"


@pytest.fixture
def conexion(ruta):
    """Almacenamiento recien creado y listo para usar."""
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        yield conexion


def _fila_outbox(conexion, **campos):
    """Inserta una captura y una fila de outbox con los campos indicados."""
    conexion.execute(
        "INSERT INTO captura_local (payload_json, capturado_en) VALUES ('{}', 'ahora')"
    )
    id_captura = conexion.execute("SELECT max(id_captura) FROM captura_local").fetchone()[0]
    columnas = {
        "id_captura": id_captura,
        "clave_idempotencia": f"clave-{id_captura:08d}",
        "estado": EstadoEntrega.PENDIENTE.value,
        "creado_en": "ahora",
        "actualizado_en": "ahora",
    }
    columnas.update(campos)
    nombres = ", ".join(columnas)
    marcas = ", ".join("?" for _ in columnas)
    conexion.execute(
        f"INSERT INTO outbox ({nombres}) VALUES ({marcas})", tuple(columnas.values())
    )


# ---------------------------------------------------------------------------
# 1. Inicializacion
# ---------------------------------------------------------------------------


def test_inicializa_desde_una_ruta_inexistente(ruta):
    assert not ruta.exists()
    with alm.conectar(ruta) as conexion:
        assert alm.inicializar(conexion) is True
        assert alm.tablas_presentes(conexion) == {
            alm.TABLA_CAPTURA,
            alm.TABLA_OUTBOX,
            alm.TABLA_INTENTO,
        }
        assert alm.leer_version(conexion) == alm.VERSION_DE_ESQUEMA
    assert ruta.exists()


def test_reinicializar_no_borra_ni_duplica(ruta):
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        with alm.transaccion(conexion):
            _fila_outbox(conexion)

    with alm.conectar(ruta) as conexion:
        assert alm.inicializar(conexion) is False
        assert conexion.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1
        assert (
            conexion.execute("SELECT count(*) FROM captura_local").fetchone()[0] == 1
        )


def test_la_creacion_del_esquema_y_la_version_son_atomicas(ruta, monkeypatch):
    """Un fallo a mitad de la creacion no deja ni tablas ni version estampada.

    ``PRAGMA user_version`` participa de la transaccion como cualquier otra
    escritura, y de eso depende que no exista un archivo que afirme ser de la
    version 1 con medio esquema dentro.
    """
    monkeypatch.setattr(alm, "DDL_OUTBOX", "CREATE TABLE outbox (esto no es SQL valido")

    with alm.conectar(ruta) as conexion:
        with pytest.raises(sqlite3.OperationalError):
            alm.inicializar(conexion)

    with alm.conectar(ruta) as conexion:
        assert alm.tablas_presentes(conexion) == frozenset()
        assert alm.leer_version(conexion) == alm.VERSION_SIN_ESTRENAR


def test_una_version_desconocida_se_rechaza_sin_tocar_el_archivo(ruta):
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        conexion.execute("PRAGMA user_version = 99")

    with alm.conectar(ruta) as conexion:
        with pytest.raises(alm.EsquemaIncompatible):
            alm.inicializar(conexion)
        # Sigue intacto: rechazar no es migrar ni limpiar.
        assert alm.tablas_presentes(conexion) == {
            alm.TABLA_CAPTURA,
            alm.TABLA_OUTBOX,
            alm.TABLA_INTENTO,
        }
        assert alm.leer_version(conexion) == 99


def test_version_cero_con_tablas_propias_se_rechaza(ruta):
    """Un esquema anterior al versionado no se completa en silencio."""
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        conexion.execute("PRAGMA user_version = 0")

    with alm.conectar(ruta) as conexion:
        with pytest.raises(alm.EsquemaIncompatible):
            alm.inicializar(conexion)


def test_version_cero_con_esquema_parcial_se_rechaza(ruta):
    """Media tabla es exactamente el caso que no debe completarse."""
    with alm.conectar(ruta) as conexion:
        conexion.execute(alm.DDL_CAPTURA)

    with alm.conectar(ruta) as conexion:
        with pytest.raises(alm.EsquemaIncompatible):
            alm.inicializar(conexion)
        assert alm.TABLA_OUTBOX not in alm.tablas_presentes(conexion)


def test_version_cero_con_tablas_ajenas_se_rechaza(ruta):
    """A una base de otra persona no se le agregan estas tablas."""
    with alm.conectar(ruta) as conexion:
        conexion.execute("CREATE TABLE agenda (id INTEGER PRIMARY KEY)")

    with alm.conectar(ruta) as conexion:
        with pytest.raises(alm.EsquemaIncompatible):
            alm.inicializar(conexion)
        assert alm.tablas_presentes(conexion) == {"agenda"}


def test_la_unica_sentencia_destructiva_es_la_de_la_migracion():
    """El modulo no ejecuta ``DROP``/``TRUNCATE``/``DELETE`` fuera del upgrade.

    SCRUM-64 podia exigir que no existiera ninguna. SCRUM-65 no: SQLite no sabe
    anadir un ``CHECK`` a una tabla existente, asi que las cinco invariantes
    nuevas obligan a reconstruir ``outbox``, y una reconstruccion termina
    retirando la copia renombrada de la tabla vieja.

    Asi que la prueba no se relaja, se estrecha: la unica sentencia destructiva
    admitida es un ``DROP TABLE`` de la tabla temporal de migracion, y solo puede
    aparecer dentro de ``_migrar_v1_a_v2``. Cualquier otra --incluido un ``DROP``
    de ``outbox`` misma-- sigue prohibida.

    Se mira el arbol sintactico con los docstrings retirados, no el texto fuente:
    la documentacion del modulo menciona ``DROP`` y ``TRUNCATE`` precisamente
    para decir que no los ejecuta, y buscarlos en el texto crudo encontraria esa
    frase en lugar de una sentencia.
    """
    import ast
    import inspect

    arbol = ast.parse(inspect.getsource(alm))
    for nodo in ast.walk(arbol):
        cuerpo = getattr(nodo, "body", None)
        if not isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            continue
        if (
            cuerpo
            and isinstance(cuerpo[0], ast.Expr)
            and isinstance(cuerpo[0].value, ast.Constant)
            and isinstance(cuerpo[0].value.value, str)
        ):
            cuerpo.pop(0)

    migracion = next(
        nodo
        for nodo in ast.walk(arbol)
        if isinstance(nodo, ast.FunctionDef) and nodo.name == "_migrar_v1_a_v2"
    )
    codigo_migracion = ast.unparse(migracion).upper()

    # 1. Dentro de la migracion: exactamente un DROP, y de la tabla temporal.
    #    Las sentencias se componen con f-strings, asi que lo que queda tras
    #    ``unparse`` es el marcador, no el nombre ya sustituido.
    assert codigo_migracion.count("DROP TABLE") == 1
    assert "DROP TABLE {TABLA_MIGRACION_V1}" in codigo_migracion
    assert "DROP TABLE {TABLA_OUTBOX}" not in codigo_migracion
    for prohibida in ("TRUNCATE", "DELETE FROM"):
        assert prohibida not in codigo_migracion

    # 2. Fuera de la migracion: ninguna, como en SCRUM-64.
    migracion.body = [ast.Pass()]
    resto = ast.unparse(arbol).upper()
    for prohibida in ("DROP TABLE", "TRUNCATE", "DELETE FROM"):
        assert prohibida not in resto


def test_la_migracion_copia_las_filas_antes_de_retirar_la_tabla_vieja():
    """El ``DROP`` solo puede venir despues del ``INSERT ... SELECT``.

    Un orden invertido perderia todos los eventos, y la prueba de datos no lo
    detectaria si alguien reordenara las sentencias y ajustara los valores.
    """
    import ast
    import inspect

    fuente = inspect.getsource(alm._migrar_v1_a_v2)
    texto = " ".join(ast.unparse(ast.parse(fuente.strip())).upper().split())
    assert texto.index("INSERT INTO") < texto.index("DROP TABLE")


# ---------------------------------------------------------------------------
# 2. Conexiones
# ---------------------------------------------------------------------------


def test_cada_conexion_activa_las_llaves_foraneas(ruta):
    with alm.conectar(ruta) as conexion:
        assert conexion.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_la_integridad_referencial_se_aplica_de_verdad(conexion):
    """No basta con declarar la FK: tiene que rechazar una referencia rota."""
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            conexion.execute(
                "INSERT INTO outbox (id_captura, clave_idempotencia, estado,"
                " creado_en, actualizado_en) VALUES (9999, 'clave-inexistente',"
                " 'PENDIENTE', 'ahora', 'ahora')"
            )


def test_una_transaccion_fallida_revierte_por_completo(conexion):
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            _fila_outbox(conexion)
            # Segunda fila con la misma clave: viola el UNIQUE.
            conexion.execute(
                "INSERT INTO captura_local (payload_json, capturado_en)"
                " VALUES ('{}', 'ahora')"
            )
            conexion.execute(
                "INSERT INTO outbox (id_captura, clave_idempotencia, estado,"
                " creado_en, actualizado_en) VALUES (2, 'clave-00000001',"
                " 'PENDIENTE', 'ahora', 'ahora')"
            )

    assert conexion.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
    assert conexion.execute("SELECT count(*) FROM captura_local").fetchone()[0] == 0


def test_los_datos_sobreviven_a_cerrar_todas_las_conexiones(ruta):
    with alm.conectar(ruta) as conexion:
        alm.inicializar(conexion)
        with alm.transaccion(conexion):
            _fila_outbox(conexion, clave_idempotencia="clave-que-sobrevive")

    with alm.conectar(ruta) as conexion:
        fila = conexion.execute(
            "SELECT clave_idempotencia, estado FROM outbox"
        ).fetchone()
        assert fila["clave_idempotencia"] == "clave-que-sobrevive"
        assert fila["estado"] == EstadoEntrega.PENDIENTE.value


# ---------------------------------------------------------------------------
# 3. Garantias del esquema
# ---------------------------------------------------------------------------


def test_las_expectativas_declaradas_describen_el_esquema_que_se_crea(conexion):
    """Ata la lista escrita a mano con lo que el DDL produce de verdad.

    ``ESQUEMA_ESPERADO`` esta escrito por separado y no derivado del DDL, asi
    que esta prueba es la que impide que ambos se separen sin que nadie lo note.
    """
    alm.verificar_esquema(conexion)


def test_cada_tabla_tiene_llave_primaria(conexion):
    for tabla in (alm.TABLA_CAPTURA, alm.TABLA_OUTBOX):
        columnas = conexion.execute(f"PRAGMA table_info({tabla})").fetchall()
        assert any(columna["pk"] for columna in columnas), tabla


def test_una_captura_no_puede_tener_dos_filas_de_outbox(conexion):
    with alm.transaccion(conexion):
        _fila_outbox(conexion)
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            conexion.execute(
                "INSERT INTO outbox (id_captura, clave_idempotencia, estado,"
                " creado_en, actualizado_en) VALUES (1, 'otra-clave-distinta',"
                " 'PENDIENTE', 'ahora', 'ahora')"
            )


def test_dos_eventos_no_pueden_compartir_clave(conexion):
    with alm.transaccion(conexion):
        _fila_outbox(conexion, clave_idempotencia="clave-compartida")
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            _fila_outbox(conexion, clave_idempotencia="clave-compartida")


@pytest.mark.parametrize(
    "columna", ["id_captura", "clave_idempotencia", "estado", "creado_en", "actualizado_en"]
)
def test_las_columnas_obligatorias_rechazan_null(conexion, columna):
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            _fila_outbox(conexion, **{columna: None})


def test_el_estado_esta_limitado_al_vocabulario(conexion):
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            _fila_outbox(conexion, estado="EN_PROCESO")


def test_solo_un_fallido_puede_declarar_si_es_reintentable(conexion):
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            _fila_outbox(conexion, estado=EstadoEntrega.PENDIENTE.value, reintentable=1)


def test_un_fallido_tiene_que_declarar_si_es_reintentable(conexion):
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            _fila_outbox(conexion, estado=EstadoEntrega.FALLIDO.value, reintentable=None)


def test_los_intentos_no_pueden_ser_negativos(conexion):
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            _fila_outbox(conexion, intentos=-1)


# ---------------------------------------------------------------------------
# 4. La restriccion de evidencia remota, caso por caso
# ---------------------------------------------------------------------------

EVIDENCIA_COMPLETA = {
    "enviado_en": "ahora",
    "id_sesion_remota": 832,
    "ids_lectura_remotos": "[1280]",
}
EVIDENCIAS_PARCIALES = [
    {"enviado_en": "ahora"},
    {"id_sesion_remota": 832},
    {"ids_lectura_remotos": "[1280]"},
    {"enviado_en": "ahora", "id_sesion_remota": 832},
    {"id_sesion_remota": 832, "ids_lectura_remotos": "[1280]"},
]


@pytest.mark.parametrize("estado", [EstadoEntrega.PENDIENTE, EstadoEntrega.FALLIDO])
@pytest.mark.parametrize("evidencia", EVIDENCIAS_PARCIALES)
def test_un_estado_no_enviado_no_admite_evidencia_parcial(conexion, estado, evidencia):
    """El defecto que corrige ``evidencia_remota_coherente``.

    Escrita como un bicondicional sobre el ``AND`` de las tres columnas, la
    restriccion aceptaba esto: con el estado distinto de ENVIADO bastaba que una
    sola columna fuera NULL para que el lado derecho fuera falso, los dos lados
    coincidieran y una evidencia a medias entrara sin ruido.
    """
    reintentable = 0 if estado is EstadoEntrega.FALLIDO else None
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            _fila_outbox(
                conexion, estado=estado.value, reintentable=reintentable, **evidencia
            )


@pytest.mark.parametrize("estado", [EstadoEntrega.PENDIENTE, EstadoEntrega.FALLIDO])
def test_un_estado_no_enviado_tampoco_admite_evidencia_completa(conexion, estado):
    reintentable = 0 if estado is EstadoEntrega.FALLIDO else None
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            _fila_outbox(
                conexion,
                estado=estado.value,
                reintentable=reintentable,
                **EVIDENCIA_COMPLETA,
            )


@pytest.mark.parametrize("evidencia", EVIDENCIAS_PARCIALES)
def test_un_enviado_exige_la_evidencia_completa(conexion, evidencia):
    with pytest.raises(sqlite3.IntegrityError):
        with alm.transaccion(conexion):
            _fila_outbox(conexion, estado=EstadoEntrega.ENVIADO.value, **evidencia)


def test_un_enviado_con_evidencia_completa_se_acepta(conexion):
    with alm.transaccion(conexion):
        _fila_outbox(
            conexion, estado=EstadoEntrega.ENVIADO.value, **EVIDENCIA_COMPLETA
        )
    assert conexion.execute("SELECT count(*) FROM outbox").fetchone()[0] == 1


# ---------------------------------------------------------------------------
# 5. Deteccion de un esquema debilitado
# ---------------------------------------------------------------------------


def _crear_con_outbox(ruta, ddl_outbox):
    """Una base de la version actual cuya ``outbox`` puede estar manipulada.

    El resto del esquema se crea completo a proposito: si faltara el historial,
    ``verificar_esquema`` fallaria por tabla ausente y la prueba pasaria sin
    haber ejercido nunca la deteccion que dice ejercer.
    """
    with alm.conectar(ruta) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        conexion.execute(alm.DDL_CAPTURA)
        conexion.execute(ddl_outbox)
        conexion.execute(alm.DDL_INTENTO)
        conexion.execute(alm.DDL_INDICE_ABIERTO)
        conexion.execute(alm.DDL_INDICE_CONFIRMO)
        conexion.execute(f"PRAGMA user_version = {alm.VERSION_DE_ESQUEMA}")
        conexion.execute("COMMIT")


DEBILITAMIENTOS = {
    "sin_unique_en_clave": (
        "clave_idempotencia  TEXT    NOT NULL UNIQUE",
        "clave_idempotencia  TEXT    NOT NULL",
    ),
    "sin_not_null_en_estado": (
        "estado              TEXT    NOT NULL",
        "estado              TEXT",
    ),
    "sin_llave_foranea": (
        "INTEGER NOT NULL UNIQUE REFERENCES captura_local(id_captura)",
        "INTEGER NOT NULL UNIQUE",
    ),
    "con_un_cuarto_estado": (
        "CHECK (estado IN ('ENVIADO', 'FALLIDO', 'PENDIENTE'))",
        "CHECK (estado IN ('ENVIADO', 'FALLIDO', 'PENDIENTE', 'EN_PROCESO'))",
    ),
    "con_una_columna_extra": (
        "    ids_lectura_remotos TEXT,",
        "    ids_lectura_remotos TEXT,\n    colada              TEXT,",
    ),
}


@pytest.mark.parametrize("caso", sorted(DEBILITAMIENTOS))
def test_verificar_esquema_detecta_una_garantia_debilitada(ruta, caso):
    original, debilitado = DEBILITAMIENTOS[caso]
    assert original in alm.DDL_OUTBOX, "el fragmento a debilitar dejo de existir"
    _crear_con_outbox(ruta, alm.DDL_OUTBOX.replace(original, debilitado))

    with alm.conectar(ruta) as conexion:
        with pytest.raises(alm.EsquemaIncompatible):
            alm.verificar_esquema(conexion)


def test_verificar_esquema_detecta_un_check_eliminado(ruta):
    """Un CHECK borrado no lo ve ningun PRAGMA: solo el texto almacenado."""
    corte = alm.DDL_OUTBOX.index("    CONSTRAINT evidencia_remota_coherente")
    sin_check = alm.DDL_OUTBOX[:corte].rstrip().rstrip(",") + "\n)\n"
    _crear_con_outbox(ruta, sin_check)

    with alm.conectar(ruta) as conexion:
        with pytest.raises(alm.EsquemaIncompatible, match="evidencia_remota_coherente"):
            alm.verificar_esquema(conexion)


def test_verificar_esquema_detecta_una_tabla_ajena(conexion):
    conexion.execute("CREATE TABLE sobrante (id INTEGER PRIMARY KEY)")
    with pytest.raises(alm.EsquemaIncompatible):
        alm.verificar_esquema(conexion)


def test_verificar_esquema_detecta_una_tabla_faltante(ruta):
    with alm.conectar(ruta) as conexion:
        conexion.execute("BEGIN IMMEDIATE")
        conexion.execute(alm.DDL_CAPTURA)
        conexion.execute(f"PRAGMA user_version = {alm.VERSION_DE_ESQUEMA}")
        conexion.execute("COMMIT")

    with alm.conectar(ruta) as conexion:
        with pytest.raises(alm.EsquemaIncompatible, match=alm.TABLA_OUTBOX):
            alm.inicializar(conexion)


# ---------------------------------------------------------------------------
# 6. El vocabulario de estados y el CHECK no pueden separarse
# ---------------------------------------------------------------------------


def test_el_check_de_estados_cubre_exactamente_el_enum():
    for estado in EstadoEntrega:
        assert f"'{estado.value}'" in alm.DDL_OUTBOX


@pytest.mark.parametrize("estado", list(EstadoEntrega))
def test_cada_estado_del_enum_es_aceptado_por_la_base(conexion, estado):
    extra = {}
    if estado is EstadoEntrega.FALLIDO:
        # ``reintentable = 0`` significa "requiere revision", y desde
        # SCRUM-65 esa combinacion tiene que declarar por que.
        extra["reintentable"] = 0
        extra["motivo_revision"] = MotivoRevision.RECHAZO_PERMANENTE.value
    if estado is EstadoEntrega.ENVIADO:
        extra.update(EVIDENCIA_COMPLETA)
    with alm.transaccion(conexion):
        _fila_outbox(conexion, estado=estado.value, **extra)
