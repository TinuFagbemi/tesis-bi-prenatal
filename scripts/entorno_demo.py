"""Respaldo, verificacion y restauracion del entorno de demostracion de FetalAlert.

Uso desde la raiz del repositorio, con el entorno virtual del backend::

    python scripts/entorno_demo.py respaldar --carpeta C:\\FetalAlert_respaldos\\baseline
    python scripts/entorno_demo.py verificar --carpeta C:\\FetalAlert_respaldos\\baseline
    python scripts/entorno_demo.py restaurar --carpeta C:\\FetalAlert_respaldos\\baseline

**Que guarda un respaldo.** La base de la demostracion con ``pg_dump`` del propio
contenedor ``db`` (la misma version de PostgreSQL que la restaurara), los roles
y sus membresias sin contrasenas, una copia coherente de ``data/gestante`` --las
SQLite con la API de respaldo de SQLite--, un manifiesto con la huella de cada
tabla, de cada secuencia, de las politicas RLS, las vistas, los permisos y los
roles, y ``SHA256SUMS`` de todo lo anterior.

**La huella.** Se calcula con ``psql`` dentro del contenedor, como el superusuario
del cluster, en una transaccion de solo lectura: el superusuario no esta sujeto
a RLS, de modo que ve todas las filas de las tablas con FORCE ROW LEVEL SECURITY.
Cada tabla se resume con ``md5`` de sus filas ordenadas con ``COLLATE "C"``; con
la colacion por omision dos imagenes de PostgreSQL pueden ordenar distinto el
mismo contenido. Las SQLite se comparan por contenido (``iterdump``), no por
bytes.

**Restaurar no borra nada.** Restaura el dump en una base temporal y compara su
huella con el manifiesto; solo si coincide, renombra la base activa a
``<base>__previa_<marca>`` (sin conexiones permitidas, como archivo historico) y
pone la restaurada en su lugar. Los archivos locales actuales se apartan a
``<carpeta>__previa_<marca>`` antes de copiar los del respaldo. Al final se
vuelve a comparar todo con el manifiesto. Si un paso falla, se detiene y dice en
que estado quedo.

**Lo que exige antes de restaurar.** Que nadie mas este conectado a la base --la
API detenida (Demo Control, opcion 3), Adminer sin sesiones-- y que el portal de
la gestante este apagado, porque sus archivos se van a reemplazar.

**Nunca imprime** una URL de conexion, una contrasena ni el contenido de un
archivo; las contrasenas de los roles no forman parte del respaldo.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_DEL_REPOSITORIO / "backend"))

from sqlalchemy.engine import make_url  # noqa: E402  -- tras ajustar sys.path

from app.config import VARIABLE_URL_ALEMBIC, _leer_del_entorno, exigir_url_de_entorno  # noqa: E402
from app.gestante.config import cargar_settings_gestante  # noqa: E402
from app.loader.postgres import sanear_mensaje  # noqa: E402


def _cargar_script(nombre: str):
    if nombre in sys.modules:
        return sys.modules[nombre]
    ruta = Path(__file__).resolve().parent / f"{nombre}.py"
    especificacion = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules[nombre] = modulo
    especificacion.loader.exec_module(modulo)
    return modulo


demo_control = _cargar_script("demo_control")

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1
CODIGO_DIFERENCIAS = 3

ARCHIVO_DUMP = "base.dump"
ARCHIVO_GLOBALES = "roles_y_membresias_sin_claves.sql"
ARCHIVO_MANIFIESTO = "manifiesto.json"
ARCHIVO_SUMAS = "SHA256SUMS"
CARPETA_LOCAL = "data_gestante"
ESQUEMAS = ("operacional", "analitico", "privado", "publicacion", "public")
# Archivos auxiliares de SQLite: la API de respaldo ya incorpora su contenido.
SUFIJOS_AUXILIARES = ("-wal", "-shm", "-journal")
PATRON_DE_NOMBRE = re.compile(r"[a-z][a-z0-9_]{0,62}")
PLAZO = 600
# Solo lectura y UTC: ``timestamptz::text`` no depende de la zona de la sesion.
OPCIONES_DE_LECTURA = "-c default_transaction_read_only=on -c timezone=UTC"


class ErrorDeEntorno(RuntimeError):
    """Una precondicion no se cumple o un paso fallo. Se explica y se detiene."""


def _nombre_seguro(nombre: str) -> str:
    if not PATRON_DE_NOMBRE.fullmatch(nombre or ""):
        raise ErrorDeEntorno("Nombre de base o de rol no admitido.")
    return nombre


# ---------------------------------------------------------------------------
# Destino: base, contenedor y administrador
# ---------------------------------------------------------------------------


class Destino:
    """La base que se trata, el contenedor que la sirve y su superusuario.

    Por omision, la base de ``ALEMBIC_DATABASE_URL`` en el contenedor ``db`` del
    proyecto Compose de este repositorio, con ``POSTGRES_USER``. Las pruebas
    pasan otro contenedor, otra base y otro usuario.
    """

    def __init__(self, base: str | None = None, contenedor: str | None = None, usuario: str | None = None):
        if base is None:
            base = make_url(exigir_url_de_entorno(VARIABLE_URL_ALEMBIC)).database
        if usuario is None:
            usuario = _leer_del_entorno("POSTGRES_USER")
        self.base = _nombre_seguro(base)
        self.usuario = _nombre_seguro(usuario)
        self.contenedor = contenedor or self._contenedor_del_proyecto()

    @staticmethod
    def _contenedor_del_proyecto() -> str:
        dep = demo_control.Dependencias()
        contenedores = demo_control.leer_contenedores(dep)
        db = demo_control._uno_del_servicio(contenedores, demo_control.SERVICIO_DB)
        if db is None or not demo_control._del_proyecto(dep, db) or not db.en_marcha:
            raise ErrorDeEntorno("El contenedor db de este proyecto no esta en marcha.")
        return db.identificador

    def docker(self, *argumentos: str, entrada: Path | None = None, salida: Path | None = None,
               solo_lectura: bool = False) -> str:
        """Un programa de PostgreSQL dentro del contenedor, sin shell."""
        comando = ["docker", "exec"]
        if entrada:
            comando.append("-i")
        if solo_lectura:
            comando += ["-e", f"PGOPTIONS={OPCIONES_DE_LECTURA}"]
        comando += [self.contenedor, *argumentos]
        fuente = open(entrada, "rb") if entrada else None
        destino = open(salida, "wb") if salida else subprocess.PIPE
        try:
            resultado = subprocess.run(  # noqa: S603 -- argumentos fijos, sin shell
                comando, stdin=fuente, stdout=destino, stderr=subprocess.PIPE, timeout=PLAZO, check=False
            )
        finally:
            if fuente:
                fuente.close()
            if salida:
                destino.close()
        if resultado.returncode != 0:
            lineas = resultado.stderr.decode("utf-8", "replace").strip().splitlines()
            errores = [linea for linea in lineas if "ERROR" in linea or "error:" in linea]
            detalle = sanear_mensaje((errores or lineas or [f"codigo {resultado.returncode}"])[0])
            raise ErrorDeEntorno(f"Fallo {argumentos[0]} en el contenedor: {detalle}")
        return "" if salida else resultado.stdout.decode("utf-8", "replace")

    def psql(self, sql: str, base: str = "postgres", solo_lectura: bool = False) -> list[list[str]]:
        """Filas de ``sql`` separadas por tabulador. ``base`` es la de conexion."""
        salida = self.docker(
            "psql", "-X", "-U", self.usuario, "-d", base, "-v", "ON_ERROR_STOP=1",
            "-At", "-F", "\t", "-c", sql, solo_lectura=solo_lectura,
        )
        return [linea.split("\t") for linea in salida.splitlines() if linea]

    def existe(self, base: str) -> bool:
        return self.psql(f"SELECT count(*) FROM pg_database WHERE datname = '{_nombre_seguro(base)}'")[0][0] == "1"


# ---------------------------------------------------------------------------
# Huella de la base y de los archivos locales
# ---------------------------------------------------------------------------

_SQL_TABLAS = (
    "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
    " WHERE c.relkind IN ('r', 'p') AND n.nspname IN ({}) ORDER BY n.nspname COLLATE \"C\", c.relname COLLATE \"C\""
)
_SQL_GENERAL = """
SELECT 'alembic', (SELECT string_agg(version_num, ',' ORDER BY version_num) FROM public.alembic_version)
UNION ALL SELECT 'propietario', (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database())
UNION ALL SELECT 'politicas_rls', (SELECT md5(string_agg(schemaname || '.' || tablename || ':' || policyname || ':'
    || cmd || ':' || array_to_string(roles, ',') || ':' || coalesce(qual, '') || ':' || coalesce(with_check, ''),
    '|' ORDER BY schemaname || tablename || policyname COLLATE "C")) FROM pg_policies)
UNION ALL SELECT 'rls_activa', (SELECT string_agg(n.nspname || '.' || c.relname || ':' || c.relrowsecurity::text
    || ':' || c.relforcerowsecurity::text, '|' ORDER BY n.nspname || '.' || c.relname COLLATE "C")
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relrowsecurity)
UNION ALL SELECT 'vistas', (SELECT md5(string_agg(schemaname || '.' || viewname || ':' || definition,
    '|' ORDER BY schemaname || viewname COLLATE "C")) FROM pg_views WHERE schemaname IN ({esquemas}))
UNION ALL SELECT 'permisos', (SELECT md5(string_agg(n.nspname || '.' || c.relname || ':' || coalesce(c.relacl::text, ''),
    '|' ORDER BY n.nspname || '.' || c.relname COLLATE "C")) FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname IN ({esquemas}))
UNION ALL SELECT 'permisos_esquemas', (SELECT string_agg(nspname || ':' || coalesce(nspacl::text, ''),
    '|' ORDER BY nspname COLLATE "C") FROM pg_namespace WHERE nspname IN ({esquemas}))
UNION ALL SELECT 'funciones', (SELECT md5(string_agg(n.nspname || '.' || p.proname || ':' || md5(p.prosrc) || ':'
    || p.prosecdef::text || ':' || coalesce(p.proacl::text, ''), '|' ORDER BY n.nspname || p.proname || md5(p.prosrc) COLLATE "C"))
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname IN ({esquemas}))
UNION ALL SELECT 'roles', (SELECT md5(string_agg(rolname || ':' || rolsuper::text || rolinherit::text || rolcreaterole::text
    || rolcreatedb::text || rolcanlogin::text || rolbypassrls::text, '|' ORDER BY rolname COLLATE "C"))
    FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%')
UNION ALL SELECT 'membresias', (SELECT md5(string_agg(pg_get_userbyid(roleid) || '>' || pg_get_userbyid(member)
    || ':' || admin_option::text, '|' ORDER BY pg_get_userbyid(roleid) || pg_get_userbyid(member) COLLATE "C"))
    FROM pg_auth_members)
"""


def huella_de_la_base(destino: Destino, base: str | None = None) -> dict:
    """Recuentos y huella de cada tabla, secuencias y objetos de seguridad."""
    base = _nombre_seguro(base or destino.base)
    lista = ", ".join(f"'{e}'" for e in ESQUEMAS)
    tablas = destino.psql(_SQL_TABLAS.format(lista), base, solo_lectura=True)
    consultas = [
        f"SELECT '{e}.{t}', count(*), md5(coalesce(string_agg(x::text, '|' ORDER BY x::text COLLATE \"C\"), ''))"
        f' FROM "{e}"."{t}" x'
        for e, t in tablas
    ]
    huella: dict = {"tablas": {}}
    if consultas:
        for nombre, filas, resumen in destino.psql(" UNION ALL ".join(consultas), base, solo_lectura=True):
            huella["tablas"][nombre] = [int(filas), resumen]
    huella["secuencias"] = {
        nombre: valor
        for nombre, valor in destino.psql(
            "SELECT schemaname || '.' || sequencename, coalesce(last_value::text, 'sin_usar')"
            " FROM pg_sequences ORDER BY schemaname || '.' || sequencename COLLATE \"C\"", base, solo_lectura=True,
        )
    }
    for clave, valor in destino.psql(_SQL_GENERAL.format(esquemas=lista), base, solo_lectura=True):
        huella[clave] = valor
    return huella


def _sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _contenido_sqlite(ruta: Path) -> str:
    """Huella del contenido de una SQLite, independiente de sus paginas."""
    with closing(sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True)) as conexion:
        if conexion.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ErrorDeEntorno(f"{ruta.name} no pasa integrity_check.")
        resumen = hashlib.sha256()
        for sentencia in conexion.iterdump():
            resumen.update(sentencia.encode("utf-8") + b"\n")
        return resumen.hexdigest()


def _archivos(carpeta: Path) -> list[Path]:
    if not carpeta.exists():
        return []
    return sorted(
        p for p in carpeta.rglob("*") if p.is_file() and not p.name.endswith(SUFIJOS_AUXILIARES)
    )


def huella_de_archivos(carpeta: Path) -> dict[str, str]:
    """Cada archivo de ``carpeta``: contenido logico si es SQLite, bytes si no."""
    return {
        p.relative_to(carpeta).as_posix(): _contenido_sqlite(p) if p.suffix == ".sqlite3" else _sha256(p)
        for p in _archivos(carpeta)
    }


def copiar_archivos_locales(origen: Path, destino: Path) -> None:
    """Copia coherente de ``origen``: las SQLite con la API de respaldo."""
    for archivo in _archivos(origen):
        salida = destino / archivo.relative_to(origen)
        salida.parent.mkdir(parents=True, exist_ok=True)
        if archivo.suffix == ".sqlite3":
            with closing(sqlite3.connect(f"{archivo.resolve().as_uri()}?mode=ro", uri=True)) as o, \
                    closing(sqlite3.connect(salida)) as d:
                o.backup(d)
        else:
            shutil.copy2(archivo, salida)


def _escribir_sumas(carpeta: Path) -> None:
    lineas = [
        f"{_sha256(p)}  {p.relative_to(carpeta).as_posix()}"
        for p in sorted(p for p in carpeta.rglob("*") if p.is_file() and p.name != ARCHIVO_SUMAS)
    ]
    (carpeta / ARCHIVO_SUMAS).write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")


def comprobar_sumas(carpeta: Path) -> dict:
    """Las sumas del respaldo cuadran y no sobra ni falta nada. Devuelve el manifiesto."""
    sumas = carpeta / ARCHIVO_SUMAS
    if not sumas.is_file():
        raise ErrorDeEntorno("La carpeta no contiene un respaldo (falta SHA256SUMS).")
    listados = set()
    for linea in sumas.read_text(encoding="utf-8").splitlines():
        suma, _, relativo = linea.partition("  ")
        ruta = carpeta / relativo
        if not ruta.is_file() or _sha256(ruta) != suma:
            raise ErrorDeEntorno(f"El respaldo esta alterado o incompleto: {relativo}.")
        listados.add(relativo)
    presentes = {p.relative_to(carpeta).as_posix() for p in carpeta.rglob("*") if p.is_file()} - {ARCHIVO_SUMAS}
    if presentes - listados:
        raise ErrorDeEntorno("El respaldo contiene archivos que no estan en SHA256SUMS.")
    return json.loads((carpeta / ARCHIVO_MANIFIESTO).read_text(encoding="utf-8"))


def comparar_huellas(esperada: dict, real: dict) -> list[str]:
    diferencias = []
    for clave in sorted((set(esperada) | set(real)) - {"tablas", "secuencias"}):
        if esperada.get(clave) != real.get(clave):
            diferencias.append(f"{clave} distinto")
    for grupo in ("tablas", "secuencias"):
        a, b = esperada.get(grupo, {}), real.get(grupo, {})
        for clave in sorted(set(a) | set(b)):
            antes, ahora = a.get(clave), b.get(clave)
            if antes != ahora:
                if grupo == "tablas" and antes and ahora:
                    detalle = f"{antes[0]} -> {ahora[0]} filas" if antes[0] != ahora[0] else "mismas filas, contenido distinto"
                else:
                    detalle = f"{antes if antes is not None else 'ausente'} -> {ahora if ahora is not None else 'ausente'}"
                diferencias.append(f"{clave}: {detalle}")
    return diferencias


def comparar_archivos(esperados: dict[str, str], carpeta: Path) -> list[str]:
    actuales = huella_de_archivos(carpeta)
    diferencias = [f"archivo distinto o ausente: {n}" for n, h in sorted(esperados.items()) if actuales.get(n) != h]
    diferencias += [f"archivo sobrante: {n}" for n in sorted(set(actuales) - set(esperados))]
    return diferencias


# ---------------------------------------------------------------------------
# Ordenes
# ---------------------------------------------------------------------------


def respaldar(destino: Destino, carpeta: Path, datos: Path) -> dict:
    if carpeta.exists() and any(carpeta.iterdir()):
        raise ErrorDeEntorno("La carpeta de destino no esta vacia; un respaldo no se sobrescribe.")
    if not destino.existe(destino.base):
        raise ErrorDeEntorno(f"No existe la base '{destino.base}'.")
    carpeta.mkdir(parents=True, exist_ok=True)
    huella_previa = huella_de_la_base(destino)
    destino.docker("pg_dump", "-U", destino.usuario, "-d", destino.base, "--format=custom",
                   salida=carpeta / ARCHIVO_DUMP)
    destino.docker("pg_restore", "--list", entrada=carpeta / ARCHIVO_DUMP)
    destino.docker("pg_dumpall", "-U", destino.usuario, "--globals-only", "--no-role-passwords",
                   salida=carpeta / ARCHIVO_GLOBALES)
    copiar_archivos_locales(datos, carpeta / CARPETA_LOCAL)
    huella = huella_de_la_base(destino)
    # Si la base cambio mientras se volcaba, el dump y la huella no se corresponden.
    if comparar_huellas(huella_previa, huella):
        raise ErrorDeEntorno("La base cambio durante el respaldo; repitelo con la API detenida.")
    manifiesto = {
        "base": destino.base,
        "creado_en": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "huella": huella,
        "archivos_locales": huella_de_archivos(carpeta / CARPETA_LOCAL),
    }
    if manifiesto["archivos_locales"] != huella_de_archivos(datos):
        raise ErrorDeEntorno("Los archivos locales cambiaron durante el respaldo; deten el portal y repitelo.")
    (carpeta / ARCHIVO_MANIFIESTO).write_text(
        json.dumps(manifiesto, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    _escribir_sumas(carpeta)
    return manifiesto


def _exigir_misma_base(manifiesto: dict, destino: Destino, respaldo_de: str | None) -> None:
    """El respaldo es de esta base, o de la que se nombro expresamente.

    Poner en uso la base limpia construida aparte (``fetalalert_demo_baseline``)
    es restaurar su respaldo **con el nombre de la activa**, para que la API y
    Power BI no cambien de configuracion. Eso solo se acepta si se declara con
    ``--respaldo-de``; un respaldo de otra base nunca se aplica por descuido.
    """
    if manifiesto["base"] not in (destino.base, respaldo_de):
        raise ErrorDeEntorno(
            f"El respaldo es de la base '{manifiesto['base']}', no de '{destino.base}'"
            " (si es intencional, indicalo con --respaldo-de)."
        )


def verificar(destino: Destino, carpeta: Path, datos: Path, respaldo_de: str | None = None) -> list[str]:
    manifiesto = comprobar_sumas(carpeta)
    _exigir_misma_base(manifiesto, destino, respaldo_de)
    diferencias = comparar_huellas(manifiesto["huella"], huella_de_la_base(destino))
    return diferencias + comparar_archivos(manifiesto["archivos_locales"], datos)


def otras_sesiones(destino: Destino, base: str) -> list[str]:
    filas = destino.psql(
        "SELECT coalesce(usename, '?') || ' (' || coalesce(nullif(application_name, ''), 'sin nombre') || ')'"
        f" FROM pg_stat_activity WHERE datname = '{_nombre_seguro(base)}' AND pid <> pg_backend_pid()"
        " AND backend_type = 'client backend'"
    )
    return sorted({fila[0] for fila in filas})


def restaurar(destino: Destino, carpeta: Path, datos: Path, portal_en_marcha: bool,
              marca: str | None = None, respaldo_de: str | None = None) -> dict:
    manifiesto = comprobar_sumas(carpeta)
    _exigir_misma_base(manifiesto, destino, respaldo_de)
    if portal_en_marcha:
        raise ErrorDeEntorno("El portal de la gestante esta en marcha: detenlo antes de restaurar sus archivos.")
    sesiones = otras_sesiones(destino, destino.base)
    if sesiones:
        raise ErrorDeEntorno(
            "Hay sesiones abiertas en la base: " + ", ".join(sesiones)
            + ". Deten la API (opcion 3 de Demo Control) y cierra Adminer antes de restaurar."
        )

    marca = marca or datetime.now(timezone.utc).strftime("%Y%m%dt%H%M%Sz")
    temporal = _nombre_seguro(f"{destino.base}__restaurando")
    previa = _nombre_seguro(f"{destino.base}__previa_{marca}")
    apartados = datos.with_name(f"{datos.name}__previa_{marca}")
    if destino.existe(temporal):
        raise ErrorDeEntorno(f"Ya existe '{temporal}' de un intento anterior; revisala antes de seguir.")
    if destino.existe(previa) or apartados.exists():
        raise ErrorDeEntorno("Ya existe un estado previo con esta marca de tiempo.")

    # 1. Restaurar en una base temporal y comprobarla antes de tocar la activa.
    propietario = _nombre_seguro(manifiesto["huella"]["propietario"])
    destino.psql(f'CREATE DATABASE "{temporal}" OWNER "{propietario}"')
    try:
        destino.docker("pg_restore", "-U", destino.usuario, "-d", temporal, "--exit-on-error",
                       "--single-transaction", entrada=carpeta / ARCHIVO_DUMP)
        diferencias = comparar_huellas(manifiesto["huella"], huella_de_la_base(destino, temporal))
        if diferencias:
            raise ErrorDeEntorno("La base restaurada no coincide con el manifiesto: " + "; ".join(diferencias[:5]))
    except BaseException:
        destino.psql(f'DROP DATABASE IF EXISTS "{temporal}"')
        raise

    # 2. Intercambio. La activa pasa a archivo historico sin conexiones.
    if otras_sesiones(destino, destino.base):
        destino.psql(f'DROP DATABASE IF EXISTS "{temporal}"')
        raise ErrorDeEntorno("Alguien se conecto a la base durante la restauracion; no se cambio nada.")
    destino.psql(f'ALTER DATABASE "{destino.base}" RENAME TO "{previa}"')
    destino.psql(f'ALTER DATABASE "{temporal}" RENAME TO "{destino.base}"')
    destino.psql(f'ALTER DATABASE "{previa}" ALLOW_CONNECTIONS false')

    # 3. Archivos locales: los actuales se apartan, no se sobrescriben ni se borran.
    if datos.exists():
        datos.rename(apartados)
    else:
        apartados = None
    origen = carpeta / CARPETA_LOCAL
    if origen.exists():
        shutil.copytree(origen, datos)
    else:
        datos.mkdir(parents=True)

    # 4. Comprobacion final contra el manifiesto.
    return {
        "previa": previa,
        "archivos_apartados": apartados,
        "diferencias": verificar(destino, carpeta, datos, respaldo_de),
    }


def _resumen(huella: dict) -> list[str]:
    tablas = huella["tablas"]
    claves = (
        "operacional.sesion_monitoreo", "operacional.lectura_biometrica",
        "analitico.fact_lectura_biometrica", "operacional.auditoria_log",
    )
    return [f"  {clave}: {tablas[clave][0]} filas" for clave in claves if clave in tablas]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="entorno_demo.py", description=__doc__.splitlines()[0])
    ordenes = parser.add_subparsers(dest="orden", required=True)
    for nombre in ("respaldar", "verificar", "restaurar"):
        sub = ordenes.add_parser(nombre)
        sub.add_argument("--carpeta", type=Path, required=True, help="carpeta del respaldo")
        sub.add_argument("--base", help="base a tratar (por omision, la de ALEMBIC_DATABASE_URL)")
        sub.add_argument("--contenedor", help="contenedor PostgreSQL (por omision, el db del proyecto)")
        sub.add_argument("--usuario-admin", help="superusuario del contenedor (por omision, POSTGRES_USER)")
        sub.add_argument("--datos", type=Path, help="carpeta local (por omision, la de GESTANTE_PROVISION_PATH)")
        if nombre != "respaldar":
            sub.add_argument("--respaldo-de", metavar="BASE",
                             help="acepta un respaldo tomado de otra base (p. ej. la limpia construida aparte)")
    argumentos = parser.parse_args(argv)

    try:
        destino = Destino(argumentos.base, argumentos.contenedor, argumentos.usuario_admin)
        ajustes = cargar_settings_gestante()
        datos = argumentos.datos or ajustes.provision_path.parent
        if argumentos.orden == "respaldar":
            manifiesto = respaldar(destino, argumentos.carpeta, datos)
            huella = manifiesto["huella"]
            print(f"Respaldo de '{destino.base}' en {argumentos.carpeta}")
            print(f"  {len(huella['tablas'])} tablas, {len(huella['secuencias'])} secuencias, "
                  f"{len(manifiesto['archivos_locales'])} archivos locales")
            print("\n".join(_resumen(huella)))
            return CODIGO_DE_EXITO
        if argumentos.orden == "verificar":
            diferencias = verificar(destino, argumentos.carpeta, datos, argumentos.respaldo_de)
        else:
            # El portal solo importa si sus archivos son los que se reemplazan.
            portal = datos.resolve() == ajustes.provision_path.parent.resolve() and (
                demo_control.Dependencias().http_get(f"http://127.0.0.1:{ajustes.port}/", 3)[0] is not None
            )
            resultado = restaurar(destino, argumentos.carpeta, datos, portal_en_marcha=portal,
                                  respaldo_de=argumentos.respaldo_de)
            print(f"Restaurada '{destino.base}' desde {argumentos.carpeta}")
            print(f"  el estado anterior se conserva como la base '{resultado['previa']}' (sin conexiones)")
            if resultado["archivos_apartados"]:
                print(f"  los archivos locales anteriores estan en {resultado['archivos_apartados']}")
            diferencias = resultado["diferencias"]
        if diferencias:
            print("DIFERENCIAS con el manifiesto:")
            for diferencia in diferencias:
                print(f"  - {diferencia}")
            return CODIGO_DIFERENCIAS
        print("IDENTICO al manifiesto: tablas, secuencias, RLS, vistas, funciones, permisos, roles y archivos locales.")
        return CODIGO_DE_EXITO
    except ErrorDeEntorno as error:
        print(f"Error: {error}", file=sys.stderr)
        return CODIGO_DE_ERROR
    except demo_control.EntornoNoValidado as error:
        print("Error: " + "; ".join(error.motivos), file=sys.stderr)
        return CODIGO_DE_ERROR
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        print(f"Error: Docker no responde ({type(error).__name__}).", file=sys.stderr)
        return CODIGO_DE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
