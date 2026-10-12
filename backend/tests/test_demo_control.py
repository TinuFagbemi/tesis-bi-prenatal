"""Demo Control (SCRUM-104), offline: menu, verificacion, reset, API y ETL.

Nada de este archivo toca Docker, PostgreSQL ni la red de quien lo ejecuta. El
script se carga como modulo y todo lo que alcanza el exterior --procesos hijos,
HTTP, conexiones, entrada y salida-- se sustituye por dobles que ademas
**vigilan**: un subcomando de Docker fuera de la lista blanca, un proceso hijo
inesperado o una consulta no prevista hacen fallar la prueba.

Lo que si es real: la cola SQLite (creada con el esquema de ``app.edge``), el
archivo de aprovisionamiento (escrito con el helper atomico del repositorio) y
los formateadores del ETL cuya salida Demo Control interpreta.

Las consultas contra PostgreSQL real y la integridad del dataset canonico se
prueban en ``test_provisionar_demo_postgresql.py`` y ``test_etl_postgresql.py``.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import http.server
import importlib.util
import json
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.edge import conectar, inicializar
from app.etl import (
    CandidataDeCarga,
    InformeDeConciliacion,
    Previsualizacion,
    ResultadoEjecucion,
    ResultadoUpsert,
    Verificacion,
    formatear_previsualizacion,
    formatear_resumen,
    reglas,
)
from app.gestante.config import GestanteSettings

RAIZ = Path(__file__).resolve().parents[2]
RUTA_SCRIPT = RAIZ / "scripts" / "demo_control.py"

CLAVE_ETL = "clave-etl-que-no-debe-verse"
CLAVE_MIGRADOR = "clave-migrador-que-no-debe-verse"
CLAVE_API = "clave-api-que-no-debe-verse"
SECRETOS = (CLAVE_ETL, CLAVE_MIGRADOR, CLAVE_API)

BASE = "base_demo_ficticia"
PUERTO_DB = 55432
IP_DB = "172.18.0.2"
URL_ETL = f"postgresql+psycopg://fetalalert_etl:{CLAVE_ETL}@127.0.0.1:{PUERTO_DB}/{BASE}"
URL_MIGRADOR = (
    f"postgresql+psycopg://migrador_ficticio:{CLAVE_MIGRADOR}@127.0.0.1:{PUERTO_DB}/{BASE}"
)
URL_API = f"postgresql+psycopg://fetalalert_api:{CLAVE_API}@db:5432/{BASE}"

AHORA = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)
ID_USUARIO = 107
ID_EMBARAZO = 130


@pytest.fixture(scope="module")
def dc():
    """El script cargado como modulo, sin lanzar un proceso aparte."""
    spec = importlib.util.spec_from_file_location("demo_control_bajo_prueba", RUTA_SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


def _catalogos() -> dict:
    return {
        "semaforo": {"OK": 100, "WARNING": 101, "ERROR": 102},
        "tiempo_gestacional": [
            {"semana": s, "id_tiempo_gest": 100 + s, "trimestre": 1 if s <= 13 else 2 if s <= 27 else 3}
            for s in range(1, 41)
        ],
    }


def _episodio(**cambios) -> dict:
    episodio = {
        "id_dispositivo": 130,
        "estado_dispositivo": "ASIGNADO",
        "id_asignacion": 130,
        "asignacion_inicio": date(2026, 4, 1),
        "asignacion_fin": None,
        "asignacion_activa": True,
        "id_embarazo": ID_EMBARAZO,
        "id_paciente": 100,
        "id_clinica": 100,
        "fecha_inicio": date(2026, 4, 1),
        "fecha_probable_parto": date(2027, 1, 6),
        "estado_embarazo": "ACTIVO",
        "id_usuario": ID_USUARIO,
        "cuenta_activa": True,
    }
    episodio.update(cambios)
    return episodio


def _contexto(**cambios) -> dict:
    contexto = {
        "seguimientos": [
            reglas.PeriodoDeSeguimiento(
                id_seguimiento=130, id_medico=100, fecha_asignacion=date(2026, 4, 1), fecha_fin=None
            )
        ],
        "afiliaciones": {
            100: [reglas.AfiliacionClinica(id_clinica=100, fecha_inicio=date(2025, 1, 1), fecha_final=None)]
        },
        "sin_etl_total": 0,
        "sin_etl_demo": 0,
        "sesiones_demo": 1,
        "lecturas_demo": 1,
        "ultima_captura": datetime(2026, 10, 9, 23, 19, tzinfo=timezone.utc),
        "seudonimo": True,
    }
    contexto.update(cambios)
    return contexto


class Mundo:
    """Docker, HTTP y los procesos del ETL, simulados y vigilados."""

    def __init__(self, raiz: Path) -> None:
        self.raiz = raiz
        self.estados = {"db": "running", "api": "running", "adminer": "exited"}
        self.salud_db = "healthy"
        self.directorio = str(raiz)
        self.hash_actual_api = "hash-api"
        self.hash_del_contenedor_api = "hash-api"
        self.url_en_contenedor_api = URL_API
        self.puerto_publicado_db = PUERTO_DB
        self.puerto_publicado_api = 8000
        self.otra_instancia_responde = False
        self.api_tarda_en_responder = 0
        self.adminer_responde = True
        self.portal_en_marcha = True
        self.docker_disponible = True
        self.falla_control = False
        # ``start api`` devuelve 0 pero el contenedor no llega a quedar en marcha.
        self.el_arranque_no_prospera = False
        # Subcomandos de control ("stop"/"start") durante los que llega un Ctrl+C.
        self.interrumpir_en: set[str] = set()
        self.etl: dict[str, object] = {}
        # Las suites *_postgresql lo fijan para lanzar el CLI real del ETL.
        self.python_real = None
        self.comandos: list[list[str]] = []
        self.peticiones: list[str] = []
        self.dormidas = 0

    # -- procesos -----------------------------------------------------------

    def _contenedor(self, servicio: str) -> dict:
        puertos = {}
        entorno = [f"SECRETO_AJENO={CLAVE_API}"]
        if servicio == "db":
            puertos = {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(self.puerto_publicado_db)}]}
        if servicio == "api":
            puertos = {"8000/tcp": [{"HostIp": "", "HostPort": str(self.puerto_publicado_api)}]}
            entorno.append(f"DATABASE_URL={self.url_en_contenedor_api}")
        if servicio == "adminer":
            puertos = {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8090"}]}
        estado = {"Status": self.estados[servicio]}
        if servicio == "db":
            estado["Health"] = {"Status": self.salud_db}
        return {
            "Id": f"id-{servicio}-000000000000",
            "Config": {
                "Labels": {
                    "com.docker.compose.service": servicio,
                    "com.docker.compose.project": "proyecto-ficticio",
                    "com.docker.compose.project.working_dir": self.directorio,
                    "com.docker.compose.project.config_files": str(
                        Path(self.directorio) / "docker-compose.yml"
                    ),
                    "com.docker.compose.config-hash": (
                        self.hash_del_contenedor_api if servicio == "api" else f"hash-{servicio}"
                    ),
                },
                "Env": entorno,
            },
            "State": estado,
            "HostConfig": {"PortBindings": puertos},
            "NetworkSettings": {
                "Networks": {"proyecto-ficticio_default": {"IPAddress": IP_DB if servicio == "db" else "172.18.0.9"}}
            },
        }

    def ejecutar(self, argumentos: list[str], plazo: float, raiz: Path):
        from collections import namedtuple

        Proceso = namedtuple("Proceso", "codigo salida error")
        self.comandos.append(list(argumentos))
        if argumentos[0] == "docker":
            if not self.docker_disponible:
                raise FileNotFoundError("docker")
            if argumentos[1] == "inspect":
                servicios = [a.split("-")[1] for a in argumentos[2:]]
                return Proceso(0, json.dumps([self._contenedor(s) for s in servicios]), "")
            assert argumentos[1:6] == [
                "compose", "--project-directory", str(self.raiz), "--profile", "demo",
            ], argumentos
            resto = argumentos[6:]
            if resto[:1] == ["ps"]:
                assert resto == ["ps", "-a", "--format", "json"]
                lineas = [
                    json.dumps({"ID": f"id-{servicio}-000000000000", "Service": servicio})
                    for servicio in self.estados
                ]
                return Proceso(0, "\n".join(lineas), "")
            if resto[:2] == ["config", "--hash"]:
                return Proceso(
                    0, f"api {self.hash_actual_api}\ndb hash-db\nadminer hash-adminer\n", ""
                )
            if resto in (["stop", "api"], ["start", "api"]):
                if resto[0] in self.interrumpir_en:
                    raise KeyboardInterrupt
                if self.falla_control:
                    return Proceso(1, "", "fallo simulado")
                if resto[0] == "stop":
                    self.estados["api"] = "exited"
                elif not self.el_arranque_no_prospera:
                    self.estados["api"] = "running"
                return Proceso(0, "", "")
            raise AssertionError(f"Subcomando de Docker no previsto: {resto}")
        assert argumentos[:2] == [sys.executable, str(self.raiz / "scripts" / "etl_analitico.py")]
        assert argumentos[2] in ("previsualizar", "ejecutar") and len(argumentos) == 3
        if self.python_real is not None:
            return self.python_real(argumentos, plazo, raiz)
        assert argumentos[2] in self.etl, f"Orden del ETL no prevista: {argumentos[2:]}"
        if isinstance(self.etl[argumentos[2]], BaseException):
            raise self.etl[argumentos[2]]
        return self.etl[argumentos[2]]

    # -- HTTP ---------------------------------------------------------------

    def http_get(self, url: str, plazo: float):
        self.peticiones.append(url)
        if url.endswith("/health"):
            if self.otra_instancia_responde:
                return 200, '{"status":"ok"}'
            if self.estados["api"] == "running":
                if self.api_tarda_en_responder > 0:
                    self.api_tarda_en_responder -= 1
                    return None, "URLError"
                return 200, '{"status":"ok"}'
            return None, "URLError"
        if url.startswith("http://127.0.0.1:8090"):
            if self.estados["adminer"] == "running" and self.adminer_responde:
                return 200, ""
            return None, "URLError"
        if url.startswith("http://127.0.0.1:8100"):
            return (200, "") if self.portal_en_marcha else (None, "URLError")
        raise AssertionError(f"Petición no prevista: {url}")

    def dormir(self, segundos: float) -> None:
        self.dormidas += 1

    # -- comprobaciones -----------------------------------------------------

    @property
    def de_docker(self) -> list[list[str]]:
        return [c[6:] if c[1] == "compose" else c[1:2] for c in self.comandos if c[0] == "docker"]

    @property
    def de_control(self) -> list[list[str]]:
        return [c for c in self.de_docker if c[:1] in (["stop"], ["start"])]

    @property
    def del_etl(self) -> list[str]:
        return [c[2] for c in self.comandos if c[0] != "docker"]


class ConexionVigilada:
    """Una conexion que no acepta ninguna sentencia: todo lo leen los dobles."""

    def __init__(self, url: str) -> None:
        self.url = url

    def execute(self, *_, **__):
        raise AssertionError("Consulta SQL no simulada.")


class Escenario:
    def __init__(self, dc, tmp_path: Path, monkeypatch) -> None:
        self.dc = dc
        self.raiz = tmp_path / "repo"
        (self.raiz / "scripts").mkdir(parents=True)
        self.datos = tmp_path / "datos"
        self.datos.mkdir()
        self.mundo = Mundo(self.raiz)
        self.salida: list[str] = []
        self.respuestas: list[str] = []
        self.preguntas: list[str] = []
        self.conexiones: list[str] = []
        self.variables = {
            "DATABASE_URL": URL_API,
            "POSTGRES_PORT": str(PUERTO_DB),
            "JWT_EXPIRATION_MINUTES": None,
        }
        self.urls = {"ETL_DATABASE_URL": URL_ETL, "ALEMBIC_DATABASE_URL": URL_MIGRADOR}
        self.identidades = {
            URL_ETL: dc.Identidad(IP_DB, 5432, BASE, "fetalalert_etl", False, False, 131076),
            URL_MIGRADOR: dc.Identidad(IP_DB, 5432, BASE, "migrador_ficticio", True, True, 131076),
        }
        self.episodio = _episodio()
        self.catalogos = _catalogos()
        self.contexto = _contexto()
        self.candado = False
        self.ajustes = GestanteSettings(
            sqlite_path=self.datos / "sesion_local.sqlite3",
            movimientos_dir=self.datos / "movimientos",
            provision_path=self.datos / "provision.json",
            api_base_url="http://127.0.0.1:8000",
            port=8100,
        )

        monkeypatch.setattr(dc, "leer_identidad", lambda conexion: self.identidades[conexion.url])
        monkeypatch.setattr(dc, "preflight", lambda conexion: "9c1d7f2ab4e8")
        monkeypatch.setattr(dc, "candado_ocupado", lambda conexion: self.candado)
        monkeypatch.setattr(dc, "_leer_contexto_demo", lambda conexion, episodio: self.contexto)
        monkeypatch.setattr(dc.provisionar_demo, "localizar_episodio_demo", self._localizar)
        monkeypatch.setattr(dc.provisionar_demo, "leer_catalogos", lambda conexion: self.catalogos)
        for creadora in ("_provisionar", "orden_provisionar", "_crear_dispositivo"):
            monkeypatch.setattr(dc.provisionar_demo, creadora, self._prohibido(creadora))

    def _localizar(self, conexion):
        if isinstance(self.episodio, Exception):
            raise self.episodio
        return self.episodio

    @staticmethod
    def _prohibido(nombre: str):
        def llamado(*_, **__):
            raise AssertionError(f"Demo Control no debe llamar a {nombre}: crea filas.")

        return llamado

    @contextmanager
    def _conectar(self, url: str):
        self.conexiones.append(url)
        yield ConexionVigilada(url)

    def _entrada(self, pregunta: str) -> str:
        self.preguntas.append(pregunta)
        if not self.respuestas:
            raise EOFError
        respuesta = self.respuestas.pop(0)
        if isinstance(respuesta, BaseException):
            raise respuesta
        return respuesta

    @property
    def dep(self):
        return self.dc.Dependencias(
            entrada=self._entrada,
            salida=self.salida.append,
            ejecutar=self.mundo.ejecutar,
            http_get=self.mundo.http_get,
            conectar=self._conectar,
            leer_variable=lambda nombre: self.variables.get(nombre),
            exigir_url=lambda nombre: self.urls[nombre],
            ajustes_gestante=lambda: self.ajustes,
            reloj=lambda: AHORA,
            dormir=self.mundo.dormir,
            raiz=self.raiz,
            python=sys.executable,
        )

    @property
    def texto(self) -> str:
        return "\n".join(self.salida)

    @property
    def ruta_cola(self) -> Path:
        return self.ajustes.movimientos_dir / f"cuenta-{ID_USUARIO}.sqlite3"

    def escribir_provision(self, **cambios) -> None:
        self.dc.provisionar_demo.escribir_provision(
            self.ajustes.provision_path, _episodio(**cambios), self.catalogos
        )

    def sin_secretos(self) -> None:
        for secreto in (*SECRETOS, URL_ETL, URL_MIGRADOR, URL_API):
            assert secreto not in self.texto


@pytest.fixture
def esc(dc, tmp_path, monkeypatch) -> Escenario:
    escenario = Escenario(dc, tmp_path, monkeypatch)
    escenario.escribir_provision()
    return escenario


def control_con_postgresql_real(
    dc, monkeypatch, *, raiz: Path, datos: Path, url_mantenimiento: str, url_etl: str
) -> SimpleNamespace:
    """Demo Control con PostgreSQL **real** y Docker/HTTP simulados.

    Lo usan las suites ``*_postgresql``: las conexiones salen de la fabrica real
    de solo lectura del script y cada consulta se ejecuta de verdad. No lee el
    ``.env`` de nadie: las URLs llegan por argumento.
    """
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from sqlalchemy.pool import NullPool

    url = make_url(url_mantenimiento)
    motor = create_engine(url_mantenimiento, poolclass=NullPool)
    with motor.connect() as conexion:
        ip, puerto = conexion.execute(
            text("SELECT host(inet_server_addr()), inet_server_port()")
        ).one()
    motor.dispose()

    mundo = Mundo(raiz)
    mundo.puerto_publicado_db = url.port
    monkeypatch.setattr(sys.modules[__name__], "IP_DB", ip)

    # El servidor de pruebas no es el contenedor ``db`` de un Compose: se
    # conserva la identidad real que devuelve PostgreSQL y solo se normaliza el
    # puerto interno, que dentro de un contenedor siempre es 5432.
    real = dc.leer_identidad

    def identidad(conexion):
        leida = real(conexion)
        assert leida.puerto == puerto
        return dataclasses.replace(leida, puerto=dc.PUERTO_INTERNO_DB)

    monkeypatch.setattr(dc, "leer_identidad", identidad)

    datos.mkdir(parents=True, exist_ok=True)
    ajustes = GestanteSettings(
        sqlite_path=datos / "sesion_local.sqlite3",
        movimientos_dir=datos / "movimientos",
        provision_path=datos / "provision.json",
        api_base_url="http://127.0.0.1:8000",
        port=8100,
    )
    variables = {
        "DATABASE_URL": (
            f"postgresql+psycopg://fetalalert_api:clave-ficticia@db:5432/{url.database}"
        ),
        "POSTGRES_PORT": str(url.port),
    }
    mundo.url_en_contenedor_api = variables["DATABASE_URL"]
    urls = {"ETL_DATABASE_URL": url_etl, "ALEMBIC_DATABASE_URL": url_mantenimiento}
    salida: list[str] = []
    respuestas: list[str] = []

    def entrada(pregunta: str) -> str:
        salida.append(pregunta)
        return respuestas.pop(0)

    dependencias = dc.Dependencias(
        entrada=entrada,
        salida=salida.append,
        ejecutar=mundo.ejecutar,
        http_get=mundo.http_get,
        conectar=dc._solo_lectura,  # la fabrica real, de solo lectura
        leer_variable=variables.get,
        exigir_url=urls.__getitem__,
        ajustes_gestante=lambda: ajustes,
        dormir=mundo.dormir,
        raiz=raiz,
        python=sys.executable,
    )
    return SimpleNamespace(
        dep=dependencias,
        mundo=mundo,
        salida=salida,
        respuestas=respuestas,
        ajustes=ajustes,
        claves=(url.password, make_url(url_etl).password),
    )


# -- la cola SQLite, con el esquema real de app.edge -------------------------

MARCA = "2026-10-09T23:19:10.000000+00:00"
MAS_TARDE = "2026-10-09T23:49:24.000000+00:00"


def crear_cola(ruta: Path, *eventos: str) -> None:
    """Una cola real de ``app.edge`` con un evento por cada estado pedido."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with conectar(ruta) as conexion:
        inicializar(conexion)
        for numero, evento in enumerate(eventos, start=1):
            conexion.execute(
                "INSERT INTO captura_local (id_captura, payload_json, capturado_en)"
                " VALUES (?, '{}', ?)",
                (numero, MARCA),
            )
            estado = "FALLIDO" if evento.startswith("FALLIDO") else (
                "PENDIENTE" if evento == "INTENTO_ABIERTO" else evento
            )
            conexion.execute(
                "INSERT INTO outbox (id_outbox, id_captura, clave_idempotencia, estado,"
                " reintentable, intentos, creado_en, actualizado_en, enviado_en,"
                " id_sesion_remota, ids_lectura_remotos, motivo_revision)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    numero,
                    numero,
                    f"clave-{numero}",
                    estado,
                    {"FALLIDO_REINTENTABLE": 1, "FALLIDO_REVISION": 0}.get(evento),
                    1 if evento != "PENDIENTE" else 0,
                    MARCA,
                    MARCA,
                    MAS_TARDE if evento == "ENVIADO" else None,
                    832 if evento == "ENVIADO" else None,
                    "[1280]" if evento == "ENVIADO" else None,
                    "RECHAZO_PERMANENTE" if evento == "FALLIDO_REVISION" else None,
                ),
            )
            if evento == "INTENTO_ABIERTO":
                conexion.execute(
                    "INSERT INTO intento_sincronizacion"
                    " (id_outbox, numero, iniciado_en, reconciliable_en)"
                    " VALUES (?, 1, ?, ?)",
                    (numero, MARCA, MAS_TARDE),
                )


def huella_de_carpeta(carpeta: Path) -> dict:
    """Nombre, contenido y fecha de cada archivo: cualquier cambio se nota."""
    if not carpeta.exists():
        return {}
    return {
        str(ruta.relative_to(carpeta)): (
            hashlib.sha256(ruta.read_bytes()).hexdigest(),
            ruta.stat().st_mtime_ns,
        )
        for ruta in sorted(carpeta.rglob("*"))
        if ruta.is_file()
    }


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------


def test_abrir_y_salir_no_ejecuta_nada(dc, esc):
    esc.respuestas = ["7"]

    assert dc.main([], dependencias=esc.dep) == dc.CODIGO_DE_EXITO

    for opcion in (
        "1. Verificar estado",
        "2. Restablecer baseline",
        "3. Simular desconexión",
        "4. Restablecer conexión",
        "5. Previsualizar próxima carga ETL",
        "6. Ejecutar ETL",
        "7. Salir",
    ):
        assert opcion in esc.texto
    # Abrir Demo Control no verifica, no restablece y no toca Docker ni la base.
    assert esc.mundo.comandos == []
    assert esc.mundo.peticiones == []
    assert esc.conexiones == []


def test_el_menu_no_ofrece_auditoria_ni_archivado_ni_pasos_sueltos_del_etl(dc, esc):
    esc.respuestas = ["7"]
    dc.main([], dependencias=esc.dep)

    texto = esc.texto.lower()
    for ausente in ("auditor", "archivar", "extract", "transform", "load", "8."):
        assert ausente not in texto


@pytest.mark.parametrize("entrada", ["", "0", "8", "x", "1 2", "verificar"])
def test_una_opcion_invalida_repite_el_menu_sin_ejecutar_nada(dc, esc, entrada):
    esc.respuestas = [entrada, "7"]

    assert dc.main([], dependencias=esc.dep) == dc.CODIGO_DE_EXITO
    assert "Opción no válida" in esc.texto
    assert esc.mundo.comandos == []
    assert len(esc.preguntas) == 2


PROMESA_RETIRADA = "No quedó ninguna operación a medias"


@pytest.mark.parametrize("interrupcion", [KeyboardInterrupt(), EOFError()])
def test_una_interrupcion_en_el_menu_dice_solo_lo_que_se_sabe(dc, esc, interrupcion):
    esc.respuestas = [interrupcion]

    assert dc.main([], dependencias=esc.dep) == dc.CODIGO_INTERRUMPIDO
    assert "interrumpido en el menú: no había ninguna opción en curso" in esc.texto
    assert "comprueba el estado" not in esc.texto  # nada que comprobar: no corría nada
    assert PROMESA_RETIRADA not in esc.texto
    assert esc.mundo.comandos == []


def _aviso_de_comprobacion(esc, opcion: str) -> None:
    assert f"Demo Control interrumpido durante la opción {opcion}." in esc.texto
    assert "No se puede saber en qué punto quedó." in esc.texto
    assert "comprueba el estado de la API y de los datos" in esc.texto
    assert "usa la opción 1" in esc.texto
    assert PROMESA_RETIRADA not in esc.texto
    assert "a medias" not in esc.texto
    esc.sin_secretos()


def test_una_interrupcion_al_detener_la_api_no_promete_ningun_estado(dc, esc):
    esc.mundo.interrumpir_en = {"stop"}
    esc.respuestas = ["3"]

    assert dc.main([], dependencias=esc.dep) == dc.CODIGO_INTERRUMPIDO

    _aviso_de_comprobacion(esc, "3")
    # No afirma ni que se detuvo ni que sigue en marcha.
    assert "SERVIDOR CENTRAL NO DISPONIBLE" not in esc.texto
    assert "Contenedor api detenido" not in esc.texto


def test_una_interrupcion_al_iniciar_la_api_no_promete_ningun_estado(dc, esc):
    esc.mundo.estados["api"] = "exited"
    esc.mundo.interrumpir_en = {"start"}
    esc.respuestas = ["4"]

    assert dc.main([], dependencias=esc.dep) == dc.CODIGO_INTERRUMPIDO

    _aviso_de_comprobacion(esc, "4")
    assert "CONEXIÓN RESTABLECIDA" not in esc.texto


def test_una_interrupcion_durante_el_etl_pide_conciliar(dc, esc):
    esc.mundo.etl["ejecutar"] = KeyboardInterrupt()
    esc.respuestas = ["6", "EJECUTAR"]

    assert dc.main([], dependencias=esc.dep) == dc.CODIGO_INTERRUMPIDO

    _aviso_de_comprobacion(esc, "6")
    assert "etl_analitico.py conciliar" in esc.texto
    # No dice que el ETL terminó, ni que se revirtió.
    assert "ETL COMPLETADO" not in esc.texto
    assert "RESULTADO:" not in esc.texto
    assert esc.mundo.del_etl == ["ejecutar"]


def test_una_interrupcion_durante_la_previsualizacion(dc, esc):
    esc.mundo.etl["previsualizar"] = KeyboardInterrupt()
    esc.respuestas = ["5"]

    assert dc.main([], dependencias=esc.dep) == dc.CODIGO_INTERRUMPIDO

    _aviso_de_comprobacion(esc, "5")
    assert "NO SE REALIZÓ NINGUNA ESCRITURA" not in esc.texto


@pytest.mark.parametrize("interrupcion", [KeyboardInterrupt(), EOFError()])
def test_una_interrupcion_en_la_confirmacion_del_etl_no_lo_lanza(dc, esc, interrupcion):
    esc.mundo.etl["ejecutar"] = AssertionError("el ETL no debe lanzarse")
    esc.respuestas = ["6", interrupcion]

    assert dc.main([], dependencias=esc.dep) == dc.CODIGO_INTERRUMPIDO

    _aviso_de_comprobacion(esc, "6")
    assert esc.mundo.del_etl == []


def test_una_interrupcion_en_la_confirmacion_del_reset_no_escribe(dc, esc):
    esc.escribir_provision(id_embarazo=999)
    antes = huella_de_carpeta(esc.datos)
    esc.respuestas = ["2", KeyboardInterrupt()]

    assert dc.main([], dependencias=esc.dep) == dc.CODIGO_INTERRUMPIDO

    _aviso_de_comprobacion(esc, "2")
    assert huella_de_carpeta(esc.datos) == antes


def test_tras_una_opcion_terminada_el_menu_vuelve_a_no_tener_nada_en_curso(dc, esc):
    esc.respuestas = ["1", KeyboardInterrupt()]

    assert dc.main([], dependencias=esc.dep) == dc.CODIGO_INTERRUMPIDO

    assert "interrumpido en el menú: no había ninguna opción en curso" in esc.texto
    assert "interrumpido durante la opción" not in esc.texto


def test_el_script_ya_no_garantiza_que_nada_quedo_a_medias():
    fuente = RUTA_SCRIPT.read_text(encoding="utf-8")

    assert PROMESA_RETIRADA not in fuente
    assert "ninguna operación a medias" not in fuente


def test_un_error_inesperado_se_informa_sin_traza_ni_secretos(dc, esc, monkeypatch):
    def revienta(dep):
        raise RuntimeError(f"fallo con {URL_ETL}")

    monkeypatch.setitem(dc.OPCIONES, "1", revienta)
    esc.respuestas = ["1", "7"]

    assert dc.main([], dependencias=esc.dep) == dc.CODIGO_DE_EXITO
    assert "[FAIL] Error inesperado: RuntimeError." in esc.texto
    esc.sin_secretos()


def test_toda_la_salida_pasa_por_el_saneador(dc, esc):
    dc._decir(esc.dep, f"conexion a {URL_MIGRADOR} rechazada")

    esc.sin_secretos()
    assert "127.0.0.1" not in esc.texto


# ---------------------------------------------------------------------------
# Lista blanca de Docker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argumentos", "control"),
    [
        (("down",), False),
        (("up", "-d"), False),
        (("restart", "api"), False),
        (("stop", "api"), False),  # de control, pero pedido como lectura
        (("start", "api"), False),
        (("stop", "db"), True),
        (("stop", "adminer"), True),
        (("stop",), True),
        (("stop", "api", "db"), True),
        (("down",), True),
        (("rm", "-f", "api"), True),
        (("exec", "db", "psql"), False),
        ((), False),
    ],
)
def test_docker_fuera_de_la_lista_blanca_se_rechaza_antes_de_lanzarlo(
    dc, esc, argumentos, control
):
    with pytest.raises(dc.DockerNoPermitido):
        dc._compose(esc.dep, *argumentos, control=control)

    assert esc.mundo.comandos == []


def test_el_unico_control_permitido_es_detener_o_iniciar_api(dc):
    assert dc.CONTROL_PERMITIDO == {("stop", "api"), ("start", "api")}
    assert dc.SUBCOMANDOS_DE_LECTURA == {"ps", "config"}


# ---------------------------------------------------------------------------
# Opcion 1 -- Verificar (solo lectura)
# ---------------------------------------------------------------------------


def _solo_lectura_de_docker(esc) -> None:
    assert esc.mundo.de_control == []
    for comando in esc.mundo.de_docker:
        assert comando[0] in ("ps", "config", "inspect"), comando
    assert esc.mundo.del_etl == []


def test_verificar_con_todo_en_orden_dice_listo(dc, esc):
    crear_cola(esc.ruta_cola, "ENVIADO")

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "LISTO PARA DEMO"
    assert "ESTADO GENERAL: LISTO PARA DEMO" in esc.texto
    assert f"embarazo={ID_EMBARAZO} cuenta={ID_USUARIO}" in esc.texto
    assert "Sin pendientes (1 ENVIADO)" in esc.texto
    assert "docs/demo_adminer.md §4.2" in esc.texto
    assert not [linea for linea in informe.lineas if linea.nivel in ("WARN", "FAIL")]
    _solo_lectura_de_docker(esc)
    esc.sin_secretos()


def test_verificar_no_modifica_ni_crea_ningun_archivo(dc, esc):
    crear_cola(esc.ruta_cola, "ENVIADO", "PENDIENTE")
    antes = huella_de_carpeta(esc.datos)

    dc.opcion_verificar(esc.dep)
    dc.opcion_verificar(esc.dep)

    assert huella_de_carpeta(esc.datos) == antes
    for sufijo in ("-journal", "-wal", "-shm"):
        assert not Path(f"{esc.ruta_cola}{sufijo}").exists()
    _solo_lectura_de_docker(esc)


def test_verificar_solo_abre_conexiones_de_solo_lectura_y_solo_hace_get(dc, esc):
    dc.opcion_verificar(esc.dep)

    # Todas las conexiones salen de la fabrica de solo lectura inyectada; no
    # existe en el script otra forma de llegar a PostgreSQL.
    assert set(esc.conexiones) <= {URL_ETL, URL_MIGRADOR}
    fuente = RUTA_SCRIPT.read_text(encoding="utf-8")
    assert fuente.count("create_engine(") == 1
    assert "default_transaction_read_only=on" in fuente
    assert "postgresql_readonly=True" in fuente
    # Cada sentencia SQL del script es un SELECT: se leen del arbol sintactico,
    # no del texto, para que una docstring no cuente como sentencia.
    sentencias = [
        ast.literal_eval(nodo.args[0])
        for nodo in ast.walk(ast.parse(fuente))
        if isinstance(nodo, ast.Call)
        and isinstance(nodo.func, ast.Name)
        and nodo.func.id == "text"
    ]
    assert len(sentencias) >= 4
    for sentencia in sentencias:
        assert sentencia.lstrip().upper().startswith("SELECT"), sentencia
        for prohibido in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "NEXTVAL", "ADVISORY", " INTO "):
            assert prohibido not in sentencia.upper(), prohibido
    # Y el unico acceso HTTP del script es un GET sin cuerpo.
    assert "urlopen(url, timeout=plazo)" in fuente
    assert "Request(" not in fuente


def test_verificar_con_la_cola_ausente_no_la_crea(dc, esc):
    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "LISTO PARA DEMO"
    assert "Vacía: se crea con la primera captura" in esc.texto
    assert not esc.ruta_cola.exists()
    assert not esc.ajustes.movimientos_dir.exists()


def test_verificar_con_el_aprovisionamiento_ausente_no_lo_crea(dc, esc):
    esc.ajustes.provision_path.unlink()

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "REQUIERE PREPARACIÓN"
    assert "provision.json no existe. La opción 2 lo reconstruye." in esc.texto
    assert not esc.ajustes.provision_path.exists()


def test_verificar_detecta_un_aprovisionamiento_desactualizado_sin_corregirlo(dc, esc):
    esc.escribir_provision(id_embarazo=999)
    antes = esc.ajustes.provision_path.read_bytes()

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "REQUIERE PREPARACIÓN"
    assert f"id_embarazo: archivo=999 base={ID_EMBARAZO}" in esc.texto
    assert esc.ajustes.provision_path.read_bytes() == antes


@pytest.mark.parametrize(
    ("eventos", "esperado"),
    [
        (("PENDIENTE",), "PENDIENTE=1"),
        (("ENVIADO", "PENDIENTE", "PENDIENTE"), "PENDIENTE=2"),
        (("FALLIDO_REINTENTABLE",), "FALLIDO(reintentable)=1"),
        (("FALLIDO_REVISION",), "FALLIDO(revisión)=1"),
        (("INTENTO_ABIERTO",), "intentos abiertos=1"),
    ],
)
def test_una_cola_con_capturas_sin_entregar_requiere_preparacion(dc, esc, eventos, esperado):
    crear_cola(esc.ruta_cola, *eventos)
    antes = huella_de_carpeta(esc.datos)

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "REQUIERE PREPARACIÓN"
    assert esperado in esc.texto
    assert "no se retira ni se archiva" in esc.texto
    # Ni corrige, ni archiva, ni esconde: la cola queda exactamente igual.
    assert huella_de_carpeta(esc.datos) == antes
    assert not (esc.ajustes.movimientos_dir / "archivo").exists()


def test_una_cola_corrupta_no_esta_disponible_y_no_se_toca(dc, esc):
    esc.ruta_cola.parent.mkdir(parents=True)
    esc.ruta_cola.write_bytes(b"esto no es una base SQLite" * 64)
    antes = huella_de_carpeta(esc.datos)

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert "[FAIL] Cola local" in esc.texto
    assert huella_de_carpeta(esc.datos) == antes


def test_una_cola_de_otro_esquema_no_esta_disponible(dc, esc):
    esc.ruta_cola.parent.mkdir(parents=True)
    with sqlite3.connect(esc.ruta_cola) as conexion:
        conexion.execute("CREATE TABLE ajena (x INTEGER)")
    conexion.close()

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert "no tiene el esquema que esta versión espera" in esc.texto


@pytest.mark.parametrize("sufijo", ["-journal", "-wal", "-shm"])
def test_con_un_archivo_auxiliar_la_cola_no_se_abre(dc, esc, sufijo, monkeypatch):
    crear_cola(esc.ruta_cola, "ENVIADO")
    auxiliar = Path(f"{esc.ruta_cola}{sufijo}")
    auxiliar.write_bytes(b"transaccion a medias")
    antes = huella_de_carpeta(esc.datos)

    def no_abrir(*_, **__):
        raise AssertionError("La cola no debe abrirse con un archivo auxiliar al lado.")

    monkeypatch.setattr(dc.sqlite3, "connect", no_abrir)
    estado = dc.inspeccionar_cola(esc.ruta_cola)

    assert estado.existe and not estado.legible
    assert estado.acompanantes == (sufijo,)
    assert huella_de_carpeta(esc.datos) == antes


def test_la_cola_se_abre_en_modo_solo_lectura(dc, esc, monkeypatch):
    crear_cola(esc.ruta_cola, "ENVIADO")
    aperturas = []
    real = sqlite3.connect

    def espiar(destino, **opciones):
        aperturas.append((destino, opciones))
        return real(destino, **opciones)

    monkeypatch.setattr(dc.sqlite3, "connect", espiar)
    dc.inspeccionar_cola(esc.ruta_cola)

    assert len(aperturas) == 1
    destino, opciones = aperturas[0]
    assert destino.endswith("?mode=ro") and opciones == {"uri": True}
    with pytest.raises(sqlite3.OperationalError):
        with real(destino, uri=True) as conexion:
            conexion.execute("DELETE FROM outbox")


def test_verificar_con_la_base_caida_no_esta_disponible(dc, esc):
    esc.mundo.estados["db"] = "exited"
    esc.mundo.salud_db = None

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert "[FAIL] PostgreSQL (db)" in esc.texto
    _solo_lectura_de_docker(esc)  # no intenta arrancarla


def test_sin_base_la_cola_se_revisa_con_la_cuenta_del_aprovisionamiento(dc, esc):
    crear_cola(esc.ruta_cola, "PENDIENTE")
    esc.episodio = dc.provisionar_demo.EpisodioDemoNoIdentificable("sin base")
    antes = huella_de_carpeta(esc.datos)

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert f"Cuenta {ID_USUARIO} tomada de provision.json; la base no la confirmó." in esc.texto
    assert "PENDIENTE=1" in esc.texto
    assert huella_de_carpeta(esc.datos) == antes


def test_sin_base_ni_aprovisionamiento_no_se_adivina_la_cola(dc, esc):
    esc.ajustes.provision_path.unlink()
    esc.episodio = dc.provisionar_demo.EpisodioDemoNoIdentificable("sin base")

    dc.opcion_verificar(esc.dep)

    assert "no se sabe qué cola" in esc.texto
    assert not esc.ajustes.movimientos_dir.exists()


def test_la_conexion_real_se_pide_de_solo_lectura_y_con_plazo(dc, monkeypatch):
    pedido = {}

    class MotorFalso:
        def connect(self):
            raise ConnectionError("sin servidor en esta prueba")

        def dispose(self):
            pedido["liberado"] = True

    def crear(url, **opciones):
        pedido["url"], pedido["opciones"] = url, opciones
        return MotorFalso()

    monkeypatch.setattr(dc, "create_engine", crear)

    with pytest.raises(ConnectionError):
        with dc._solo_lectura(URL_ETL):
            pass

    assert pedido["opciones"]["connect_args"] == {
        "options": "-c default_transaction_read_only=on",
        "connect_timeout": 5,
    }
    assert pedido["opciones"]["poolclass"].__name__ == "NullPool"
    assert pedido["liberado"] is True


def test_verificar_con_docker_ausente_no_esta_disponible(dc, esc):
    esc.mundo.docker_disponible = False

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert "Docker no está disponible" in esc.texto


def test_verificar_con_la_api_detenida_no_la_inicia(dc, esc):
    esc.mundo.estados["api"] = "exited"

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "REQUIERE PREPARACIÓN"
    assert "[WARN] API central: Detenida. La opción 4 la inicia." in esc.texto
    assert esc.mundo.estados["api"] == "exited"
    _solo_lectura_de_docker(esc)


def test_adminer_apagado_no_se_presenta_como_disponible(dc, esc):
    dc.opcion_verificar(esc.dep)

    assert "[INFO] Adminer: Adminer está apagado (es opcional" in esc.texto
    assert "respondiendo en 127.0.0.1:8090" not in esc.texto
    assert esc.mundo.estados["adminer"] == "exited"


def test_adminer_en_marcha_y_respondiendo_es_disponible(dc, esc):
    esc.mundo.estados["adminer"] = "running"

    informe = dc.opcion_verificar(esc.dep)

    assert "Adminer en marcha y respondiendo en 127.0.0.1:8090" in esc.texto
    assert informe.estado_global == "LISTO PARA DEMO"


def test_adminer_en_marcha_sin_respuesta_es_una_advertencia(dc, esc):
    esc.mundo.estados["adminer"] = "running"
    esc.mundo.adminer_responde = False

    informe = dc.opcion_verificar(esc.dep)

    assert "[WARN] Adminer: Adminer está en marcha pero no responde" in esc.texto
    assert informe.estado_global == "REQUIERE PREPARACIÓN"


def test_un_portal_que_apunta_a_otra_api_no_esta_disponible(dc, esc):
    """Bloquearía las opciones 3 y 4: no es una advertencia."""
    esc.ajustes = esc.ajustes.model_copy(update={"api_base_url": "http://127.0.0.1:8010"})
    esc.mundo.otra_instancia_responde = True  # esa otra API contesta /health

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert "[FAIL] Portal → API" in esc.texto
    assert "detenerlo no cortaría la comunicación de la gestante" in esc.texto
    assert "iniciarlo no" in esc.texto
    # No se le pregunta /health a una API que no es la de Compose, ni se da por
    # buena su respuesta.
    assert not [url for url in esc.mundo.peticiones if url.endswith("/health")]
    assert "GET /health responde" not in esc.texto


def test_un_contenedor_api_desfasado_no_esta_disponible(dc, esc):
    esc.mundo.hash_del_contenedor_api = "hash-viejo"

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert "[FAIL] Destino: El contenedor api se creó con otra configuración" in esc.texto
    assert "docker compose up -d api" in esc.texto  # se indica; no se ejecuta
    assert esc.mundo.de_control == []


def test_un_etl_atrasado_se_advierte(dc, esc):
    esc.contexto = _contexto(sin_etl_total=3, sin_etl_demo=1)

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "REQUIERE PREPARACIÓN"
    assert "3 lectura(s) sin incorporar (1 del embarazo demo)" in esc.texto
    assert esc.mundo.del_etl == []  # no lo ejecuta por su cuenta


def test_la_base_acumulada_se_distingue_del_dataset_canonico(dc, esc):
    esc.contexto = _contexto(sesiones_demo=4, lecturas_demo=5)

    dc.opcion_verificar(esc.dep)

    assert "Estado acumulado: 4 sesión(es) y 5 lectura(s) del embarazo demo" in esc.texto
    assert "No es el dataset canónico" in esc.texto


@pytest.mark.parametrize(
    ("inicio", "nivel", "fragmento"),
    [
        (date(2026, 4, 1), "OK", "Semana 28."),
        (date(2026, 1, 14), "WARN", "Semana 39: el episodio sale del catálogo"),
        (date(2026, 1, 5), "WARN", "Semana 40"),
        (date(2025, 12, 29), "FAIL", "Hoy es la semana 41, fuera del catálogo (1-40)"),
        (date(2026, 7, 1), "WARN", "no se admiten capturas de movimiento fetal"),
    ],
)
def test_la_vigencia_del_embarazo_demo_se_calcula_y_se_advierte(
    dc, esc, inicio, nivel, fragmento
):
    esc.episodio = _episodio(fecha_inicio=inicio, asignacion_inicio=inicio)
    esc.contexto = _contexto(
        seguimientos=[
            reglas.PeriodoDeSeguimiento(
                id_seguimiento=1, id_medico=100, fecha_asignacion=inicio, fecha_fin=None
            )
        ]
    )
    esc.escribir_provision(fecha_inicio=inicio)

    informe = dc.opcion_verificar(esc.dep)

    semana = next(l for l in informe.lineas if l.etiqueta == "Semana gestacional")
    assert semana.nivel == nivel
    assert fragmento in semana.detalle
    # La fecha limite se informa siempre, sin suponer cuando es la sustentacion.
    vigencia = next(l for l in informe.lineas if l.etiqueta == "Vigencia del episodio")
    assert "hasta la semana 40" in vigencia.detalle


def test_la_vigencia_se_informa_con_el_ultimo_dia_de_la_ultima_semana(dc, esc):
    """Inicio 2026-04-01: la semana 40 son los dias 273 a 279, hasta el 2027-01-05.

    El dia siguiente ya es la semana 41, que el catalogo no tiene. La misma
    aritmetica que ``semana_gestacional``, comprobada en los dos lados del borde.
    """
    from app.services.ingesta import semana_gestacional

    inicio = date(2026, 4, 1)
    assert semana_gestacional(inicio, datetime(2027, 1, 5, tzinfo=timezone.utc)) == 40
    assert semana_gestacional(inicio, datetime(2027, 1, 6, tzinfo=timezone.utc)) == 41

    informe = dc.opcion_verificar(esc.dep)

    vigencia = next(l for l in informe.lineas if l.etiqueta == "Vigencia del episodio")
    assert "hasta el 2027-01-05" in vigencia.detalle


def test_sin_medico_principal_vigente_no_esta_disponible(dc, esc):
    esc.contexto = _contexto(seguimientos=[])

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert "El ETL rechazaría una captura de hoy (DerivacionImposible)" in esc.texto


def test_un_medico_afiliado_a_otra_clinica_no_esta_disponible(dc, esc):
    esc.contexto = _contexto(
        afiliaciones={
            100: [reglas.AfiliacionClinica(id_clinica=7, fecha_inicio=date(2025, 1, 1), fecha_final=None)]
        }
    )

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"


def test_un_episodio_no_identificable_no_esta_disponible_y_no_se_crea(dc, esc):
    esc.episodio = dc.provisionar_demo.EpisodioDemoNoIdentificable(
        "El episodio de demostracion no se identifica sin ambiguedad."
    )

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert "[FAIL] Destino: El episodio de demostracion no se identifica" in esc.texto
    assert esc.mundo.del_etl == []


@pytest.mark.parametrize(
    ("cambio", "fragmento"),
    [
        ({"cuenta_activa": False}, "[FAIL] Cuenta PACIENTE"),
        ({"estado_embarazo": "FINALIZADO"}, "[FAIL] Embarazo vigente"),
        ({"estado_dispositivo": "DISPONIBLE"}, "[FAIL] Dispositivo"),
        ({"asignacion_activa": False}, "[FAIL] Dispositivo"),
    ],
)
def test_cada_eslabon_roto_del_episodio_se_informa(dc, esc, cambio, fragmento):
    esc.episodio = _episodio(**cambio)

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert fragmento in esc.texto


def test_sin_seudonimo_no_esta_disponible(dc, esc):
    esc.contexto = _contexto(seudonimo=False)

    assert dc.opcion_verificar(esc.dep).estado_global == "NO DISPONIBLE"


def test_el_token_se_advierte_sin_alargarlo(dc, esc):
    dc.opcion_verificar(esc.dep)

    assert "Caduca a los 30 min" in esc.texto


def test_el_get_real_es_un_get_y_no_envia_cuerpo(dc):
    vistos = []

    class Manejador(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- nombre impuesto por http.server
            vistos.append(("GET", self.path, self.headers.get("Content-Length")))
            cuerpo = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *_):
            pass

    servidor = http.server.HTTPServer(("127.0.0.1", 0), Manejador)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        puerto = servidor.server_address[1]
        assert dc._http_get(f"http://127.0.0.1:{puerto}/health", 3.0) == (
            200,
            '{"status":"ok"}',
        )
    finally:
        servidor.shutdown()
        servidor.server_close()
    assert vistos == [("GET", "/health", None)]
    codigo, clase = dc._http_get(f"http://127.0.0.1:{puerto}/health", 0.5)
    assert codigo is None and clase


# ---------------------------------------------------------------------------
# Validacion del entorno (falla cerrada)
# ---------------------------------------------------------------------------


def _romper_urls_otra_base(esc):
    esc.urls["ETL_DATABASE_URL"] = URL_ETL.replace(BASE, "otra_base")


def _romper_urls_puerto(esc):
    esc.urls["ETL_DATABASE_URL"] = URL_ETL.replace(str(PUERTO_DB), "5433")


def _romper_urls_host(esc):
    esc.urls["ALEMBIC_DATABASE_URL"] = URL_MIGRADOR.replace("127.0.0.1", "10.0.0.5")


def _romper_urls_api_fuera_de_compose(esc):
    esc.variables["DATABASE_URL"] = URL_API.replace("@db:5432", "@127.0.0.1:5433")


def _romper_urls_api_con_otro_rol(esc):
    esc.variables["DATABASE_URL"] = URL_API.replace("fetalalert_api", "postgres")


def _romper_urls_etl_con_otro_rol(esc):
    esc.urls["ETL_DATABASE_URL"] = URL_ETL.replace("fetalalert_etl", "postgres")


def _romper_sin_database_url(esc):
    esc.variables["DATABASE_URL"] = None


def _romper_directorio(esc):
    esc.mundo.directorio = str(esc.raiz.parent / "otro-worktree")


def _romper_salud(esc):
    esc.mundo.salud_db = "unhealthy"


def _romper_puerto_publicado(esc):
    esc.mundo.puerto_publicado_db = 5433


def _romper_otra_instancia_postgresql(esc):
    esc.identidades[URL_ETL] = esc.dc.Identidad(
        "127.0.0.1", 5433, BASE, "fetalalert_etl", False, False, 131076
    )


def _romper_migrador_en_otra_base(esc):
    esc.identidades[URL_MIGRADOR] = esc.dc.Identidad(
        IP_DB, 5432, BASE, "migrador_ficticio", True, True, 999
    )


def _romper_etl_superusuario(esc):
    esc.identidades[URL_ETL] = esc.dc.Identidad(
        IP_DB, 5432, BASE, "fetalalert_etl", True, False, 131076
    )


def _romper_etl_bypassrls(esc):
    esc.identidades[URL_ETL] = esc.dc.Identidad(
        IP_DB, 5432, BASE, "fetalalert_etl", False, True, 131076
    )


def _romper_base_conectada(esc):
    esc.identidades[URL_ETL] = esc.dc.Identidad(
        IP_DB, 5432, "otra_base", "fetalalert_etl", False, False, 131076
    )


def _romper_api_desfasada(esc):
    esc.mundo.hash_del_contenedor_api = "hash-de-un-env-anterior"


def _romper_api_con_otra_url(esc):
    esc.mundo.url_en_contenedor_api = URL_API.replace(BASE, "base_anterior")


def _romper_sin_api(esc):
    del esc.mundo.estados["api"]


def _romper_sin_db(esc):
    del esc.mundo.estados["db"]


def _romper_docker(esc):
    esc.mundo.docker_disponible = False


def _romper_episodio(esc):
    esc.episodio = esc.dc.provisionar_demo.EpisodioDemoNoIdentificable("ambiguo")


ROTURAS = [
    _romper_urls_otra_base,
    _romper_urls_puerto,
    _romper_urls_host,
    _romper_urls_api_fuera_de_compose,
    _romper_urls_api_con_otro_rol,
    _romper_urls_etl_con_otro_rol,
    _romper_sin_database_url,
    _romper_directorio,
    _romper_salud,
    _romper_puerto_publicado,
    _romper_otra_instancia_postgresql,
    _romper_migrador_en_otra_base,
    _romper_etl_superusuario,
    _romper_etl_bypassrls,
    _romper_base_conectada,
    _romper_api_desfasada,
    _romper_api_con_otra_url,
    _romper_sin_api,
    _romper_sin_db,
    _romper_docker,
    _romper_episodio,
]
ROTURAS_SIN_API = [r for r in ROTURAS if "api_desfasada" not in r.__name__
                   and "api_con_otra_url" not in r.__name__ and r is not _romper_sin_api]


def test_la_verificacion_valida_el_mismo_destino_que_las_opciones_con_efectos(dc, esc):
    informe = dc.opcion_verificar(esc.dep)

    destino = next(l for l in informe.lineas if l.etiqueta == "Destino")
    assert destino.nivel == "OK"
    assert f"base '{BASE}'" in destino.detalle
    assert f"db en 127.0.0.1:{PUERTO_DB}" in destino.detalle
    assert "rol fetalalert_etl" in destino.detalle
    assert informe.lineas[0] is destino  # es lo primero que se comprueba
    assert informe.estado_global == "LISTO PARA DEMO"


@pytest.mark.parametrize("romper", ROTURAS, ids=lambda r: r.__name__[8:])
def test_verificar_nunca_dice_listo_si_el_destino_no_se_validaria(dc, esc, romper):
    """La verificación no puede ser menos estricta que ``validar_entorno``."""
    crear_cola(esc.ruta_cola, "ENVIADO")
    antes = huella_de_carpeta(esc.datos)
    romper(esc)

    with pytest.raises(dc.EntornoNoValidado) as rechazo:
        dc.validar_entorno(esc.dep)
    esc.mundo.comandos.clear()
    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    assert "LISTO PARA DEMO" not in esc.texto
    # Cada motivo por el que la ejecución real se negaría aparece en el informe.
    fallos = {l.detalle for l in informe.lineas if l.nivel == "FAIL"}
    assert set(rechazo.value.motivos) <= fallos
    assert any(l.etiqueta == "Destino" and l.nivel == "FAIL" for l in informe.lineas)
    # Y sigue siendo solo lectura.
    _solo_lectura_de_docker(esc)
    assert huella_de_carpeta(esc.datos) == antes
    esc.sin_secretos()


@pytest.mark.parametrize(
    ("romper", "fragmento"),
    [
        (_romper_otra_instancia_postgresql, "no llega al contenedor db de este proyecto"),
        (_romper_migrador_en_otra_base, "La credencial de mantenimiento no llega a la misma base"),
        (_romper_puerto_publicado, "db no publica 5432 en 127.0.0.1:55432"),
        (_romper_urls_puerto, "ETL_DATABASE_URL no usa el puerto POSTGRES_PORT=55432"),
        (_romper_directorio, "El contenedor db no pertenece a este directorio de Compose"),
        (_romper_sin_api, "No existe el contenedor del servicio api de este proyecto"),
        (_romper_api_desfasada, "El contenedor api se creó con otra configuración"),
        (_romper_api_con_otra_url, "La DATABASE_URL del contenedor api no coincide con la del .env"),
        (_romper_urls_otra_base, "no nombran la misma base"),
        (_romper_etl_superusuario, "no es fetalalert_etl sin superusuario ni BYPASSRLS"),
    ],
    ids=[
        "instancia-otro-postgresql", "instancia-otra-base", "puerto-publicado",
        "puerto-de-la-url", "contenedor-de-otro-proyecto", "contenedor-api-ausente",
        "configuracion-api-desfasada", "configuracion-url-del-api",
        "configuracion-bases-distintas", "configuracion-rol-del-etl",
    ],
)
def test_cada_discrepancia_del_destino_se_nombra_en_la_verificacion(
    dc, esc, romper, fragmento
):
    romper(esc)

    informe = dc.opcion_verificar(esc.dep)

    assert informe.estado_global == "NO DISPONIBLE"
    destino = [l for l in informe.lineas if l.etiqueta == "Destino"]
    assert destino and all(l.nivel == "FAIL" for l in destino)
    assert any(fragmento in l.detalle for l in destino), [l.detalle for l in destino]


def test_un_mismo_motivo_no_se_informa_dos_veces(dc, esc):
    _romper_urls_otra_base(esc)

    informe = dc.opcion_verificar(esc.dep)

    detalles = [l.detalle for l in informe.lineas if l.nivel == "FAIL"]
    assert len(detalles) == len(set(detalles))
    assert esc.texto.count("no nombran la misma base") == 1


def test_con_el_destino_validado_docker_no_se_consulta_dos_veces(dc, esc):
    dc.opcion_verificar(esc.dep)

    assert [c[0] for c in esc.mundo.de_docker].count("ps") == 1


def test_el_portal_apagado_requiere_preparacion(dc, esc):
    esc.mundo.portal_en_marcha = False

    informe = dc.opcion_verificar(esc.dep)

    portal = next(l for l in informe.lineas if l.etiqueta == "Portal de la gestante")
    assert portal.nivel == "WARN"
    assert "No responde en 127.0.0.1:8100" in portal.detalle
    assert "gestante_web.py" in portal.detalle and "no lo arranca" in portal.detalle
    assert informe.estado_global == "REQUIERE PREPARACIÓN"
    # Todo lo demás está en orden: el portal es lo único que falta.
    assert [l.etiqueta for l in informe.lineas if l.nivel in ("WARN", "FAIL")] == [
        "Portal de la gestante"
    ]
    assert [c for c in esc.mundo.comandos if c[0] != "docker"] == []  # no lo arranca


def test_el_portal_en_marcha_es_ok_y_adminer_apagado_sigue_siendo_opcional(dc, esc):
    assert esc.mundo.estados["adminer"] == "exited"

    informe = dc.opcion_verificar(esc.dep)

    portal = next(l for l in informe.lineas if l.etiqueta == "Portal de la gestante")
    adminer = next(l for l in informe.lineas if l.etiqueta == "Adminer")
    assert (portal.nivel, adminer.nivel) == ("OK", "INFO")
    assert "apagado (es opcional" in adminer.detalle
    assert informe.estado_global == "LISTO PARA DEMO"


def test_un_entorno_coherente_se_valida_y_no_lleva_secretos(dc, esc):
    entorno = dc.validar_entorno(esc.dep)

    assert entorno.base == BASE
    assert entorno.puerto_db == PUERTO_DB
    assert entorno.ip_db == IP_DB
    assert entorno.rol_etl == "fetalalert_etl"
    assert entorno.episodio["id_embarazo"] == ID_EMBARAZO
    for secreto in SECRETOS:
        assert secreto not in repr(entorno)
    _solo_lectura_de_docker(esc)


def test_el_entorno_del_contenedor_no_aparece_en_su_representacion(dc, esc):
    contenedores = dc.leer_contenedores(esc.dep)

    for lista in contenedores.values():
        for contenedor in lista:
            assert CLAVE_API not in repr(contenedor)
            assert CLAVE_API in "".join(contenedor.entorno)  # esta en memoria, y solo ahi


@pytest.mark.parametrize("romper", ROTURAS, ids=lambda r: r.__name__[8:])
def test_cualquier_discrepancia_impide_validar(dc, esc, romper):
    romper(esc)

    with pytest.raises(dc.EntornoNoValidado) as fallo:
        dc.validar_entorno(esc.dep)

    for secreto in (*SECRETOS, URL_ETL, URL_MIGRADOR, URL_API):
        assert secreto not in str(fallo.value)
    _solo_lectura_de_docker(esc)


@pytest.mark.parametrize("romper", ROTURAS, ids=lambda r: r.__name__[8:])
def test_sin_destino_validado_no_se_ejecuta_el_etl_ni_se_pregunta(dc, esc, romper):
    romper(esc)
    esc.respuestas = ["EJECUTAR"]

    dc.opcion_ejecutar_etl(esc.dep)

    assert "[FAIL] No se pudo validar el destino. No se hizo nada." in esc.texto
    assert esc.mundo.del_etl == []
    assert esc.preguntas == []
    assert esc.mundo.de_control == []
    esc.sin_secretos()


@pytest.mark.parametrize("romper", ROTURAS, ids=lambda r: r.__name__[8:])
def test_sin_destino_validado_no_se_previsualiza(dc, esc, romper):
    romper(esc)

    dc.opcion_previsualizar(esc.dep)

    assert "[FAIL] No se pudo validar el destino" in esc.texto
    assert esc.mundo.del_etl == []


@pytest.mark.parametrize("romper", ROTURAS, ids=lambda r: r.__name__[8:])
def test_sin_destino_validado_no_se_detiene_ni_se_inicia_la_api(dc, esc, romper):
    romper(esc)

    dc.opcion_desconectar(esc.dep)
    esc.mundo.estados["api"] = esc.mundo.estados.get("api") and "exited"
    if esc.mundo.estados["api"] is None:
        del esc.mundo.estados["api"]
    dc.opcion_reconectar(esc.dep)

    assert esc.mundo.de_control == []
    assert esc.texto.count("[FAIL] No se pudo validar el destino") == 2


@pytest.mark.parametrize("romper", ROTURAS_SIN_API, ids=lambda r: r.__name__[8:])
def test_sin_destino_validado_el_reset_no_escribe_nada(dc, esc, romper):
    esc.escribir_provision(id_embarazo=999)
    antes = huella_de_carpeta(esc.datos)
    romper(esc)
    esc.respuestas = ["S"]

    dc.opcion_restablecer(esc.dep)

    assert huella_de_carpeta(esc.datos) == antes
    assert esc.preguntas == []


# ---------------------------------------------------------------------------
# Opcion 2 -- Restablecer baseline
# ---------------------------------------------------------------------------


def _respaldos(esc) -> list[Path]:
    return sorted(esc.datos.glob("provision.json.*.bak"))


def test_el_reset_anuncia_lo_que_no_hace(dc, esc):
    dc.opcion_restablecer(esc.dep)

    for frase in (
        "NO modifica PostgreSQL: la base conserva todas las capturas anteriores",
        "NO toca el dataset canónico",
        "NO crea episodios, dispositivos ni cuentas",
        "NO retira ni archiva capturas de la cola local",
    ):
        assert frase in esc.texto


def test_un_reset_sobre_un_entorno_preparado_no_cambia_nada(dc, esc):
    crear_cola(esc.ruta_cola, "ENVIADO")
    antes = huella_de_carpeta(esc.datos)

    dc.opcion_restablecer(esc.dep)
    dc.opcion_restablecer(esc.dep)

    assert huella_de_carpeta(esc.datos) == antes
    assert esc.texto.count("provision.json ya coincide con la base: sin cambios") == 2
    assert esc.preguntas == []  # nada que confirmar
    assert _respaldos(esc) == []
    # Y termina siempre con la verificacion.
    assert esc.texto.count("ESTADO GENERAL: LISTO PARA DEMO") == 2
    _solo_lectura_de_docker(esc)


def test_el_reset_reconstruye_un_aprovisionamiento_ausente(dc, esc):
    esc.ajustes.provision_path.unlink()
    esc.respuestas = ["S"]

    dc.opcion_restablecer(esc.dep)

    assert "provision.json: AUSENTE." in esc.texto
    assert "[OK]   provision.json reconstruido y comprobado." in esc.texto
    assert dc.comparar_provision(esc.ajustes.provision_path, esc.episodio, esc.catalogos) == (
        "COHERENTE",
        [],
    )
    assert _respaldos(esc) == []  # no habia nada que respaldar
    contenido = json.loads(esc.ajustes.provision_path.read_text(encoding="utf-8"))
    assert set(contenido) == {
        "version", "generado_en", "cuenta", "embarazo", "dispositivo", "catalogos",
    }
    assert contenido["cuenta"] == {"id_usuario": ID_USUARIO}  # ningun dato personal


def test_el_reset_reconstruye_uno_desactualizado_con_respaldo(dc, esc):
    esc.escribir_provision(id_embarazo=999, id_dispositivo=998)
    anterior = esc.ajustes.provision_path.read_bytes()
    esc.respuestas = ["s"]

    dc.opcion_restablecer(esc.dep)

    assert "provision.json: DESACTUALIZADO." in esc.texto
    assert f"id_embarazo: archivo=999 base={ID_EMBARAZO}" in esc.texto
    assert "id_dispositivo: archivo=998 base=130" in esc.texto
    respaldos = _respaldos(esc)
    assert len(respaldos) == 1
    assert respaldos[0].read_bytes() == anterior
    assert respaldos[0].name == "provision.json.20261011T150000Z.bak"
    assert dc.comparar_provision(esc.ajustes.provision_path, esc.episodio, esc.catalogos)[0] == "COHERENTE"
    assert "ESTADO GENERAL: LISTO PARA DEMO" in esc.texto


def test_el_reset_reconstruye_uno_ilegible_con_respaldo(dc, esc):
    esc.ajustes.provision_path.write_text("{ esto no es JSON", encoding="utf-8")
    esc.respuestas = ["S"]

    dc.opcion_restablecer(esc.dep)

    assert "provision.json: ILEGIBLE." in esc.texto
    assert len(_respaldos(esc)) == 1
    assert dc.comparar_provision(esc.ajustes.provision_path, esc.episodio, esc.catalogos)[0] == "COHERENTE"


def test_el_segundo_reset_ya_no_cambia_nada(dc, esc):
    esc.escribir_provision(id_embarazo=999)
    esc.respuestas = ["S"]
    dc.opcion_restablecer(esc.dep)
    tras_el_primero = huella_de_carpeta(esc.datos)
    preguntas = len(esc.preguntas)

    dc.opcion_restablecer(esc.dep)

    assert huella_de_carpeta(esc.datos) == tras_el_primero
    assert len(esc.preguntas) == preguntas
    assert len(_respaldos(esc)) == 1


@pytest.mark.parametrize("respuesta", ["N", "n", "", "no", "SI", "si", "x", "EJECUTAR"])
def test_sin_una_s_el_reset_no_modifica_nada(dc, esc, respuesta):
    esc.escribir_provision(id_embarazo=999)
    antes = huella_de_carpeta(esc.datos)
    esc.respuestas = [respuesta]

    dc.opcion_restablecer(esc.dep)

    assert "Cancelado. No se modificó ningún archivo." in esc.texto
    assert huella_de_carpeta(esc.datos) == antes
    assert _respaldos(esc) == []


def test_el_reset_no_crea_un_episodio_que_no_existe(dc, esc):
    esc.ajustes.provision_path.unlink()
    esc.episodio = dc.provisionar_demo.EpisodioDemoNoIdentificable(
        "se esperaba exactamente un(a) dispositivo DEMO-SCRUM72-01 y hay 0."
    )
    esc.respuestas = ["S"]

    dc.opcion_restablecer(esc.dep)

    assert "No se hizo nada" in esc.texto
    assert not esc.ajustes.provision_path.exists()
    assert esc.preguntas == []
    # Ni la ruta que crea filas ni el comando provisionar se invocan (el
    # escenario los sustituye por funciones que fallan si se llaman).
    assert esc.mundo.del_etl == []
    assert [c for c in esc.mundo.comandos if "provisionar_demo.py" in " ".join(c)] == []


def test_el_reset_no_lanza_ningun_proceso_que_escriba(dc, esc):
    esc.escribir_provision(id_embarazo=999)
    esc.respuestas = ["S"]

    dc.opcion_restablecer(esc.dep)

    assert [c for c in esc.mundo.comandos if c[0] != "docker"] == []
    _solo_lectura_de_docker(esc)
    assert set(esc.conexiones) <= {URL_ETL, URL_MIGRADOR}


@pytest.mark.parametrize(
    "eventos",
    [("PENDIENTE",), ("FALLIDO_REINTENTABLE",), ("FALLIDO_REVISION",), ("INTENTO_ABIERTO",)],
)
def test_el_reset_no_toca_una_cola_con_capturas_sin_entregar(dc, esc, eventos):
    crear_cola(esc.ruta_cola, "ENVIADO", *eventos)
    cola_antes = huella_de_carpeta(esc.ajustes.movimientos_dir)

    dc.opcion_restablecer(esc.dep)

    assert "[WARN] Cola local: REQUIERE PREPARACIÓN. No se modifica." in esc.texto
    assert "inicia sesión como la gestante en el" in esc.texto
    assert huella_de_carpeta(esc.ajustes.movimientos_dir) == cola_antes
    assert not (esc.ajustes.movimientos_dir / "archivo").exists()
    assert "ESTADO GENERAL: REQUIERE PREPARACIÓN" in esc.texto


def test_un_fallo_al_escribir_se_informa_como_operacion_incompleta(dc, esc, monkeypatch):
    esc.escribir_provision(id_embarazo=999)
    anterior = esc.ajustes.provision_path.read_bytes()

    def disco_lleno(*_, **__):
        raise PermissionError("acceso denegado")

    monkeypatch.setattr(dc.provisionar_demo, "escribir_provision", disco_lleno)
    esc.respuestas = ["S"]

    dc.opcion_restablecer(esc.dep)

    assert "[FAIL] OPERACIÓN INCOMPLETA: no se pudo escribir provision.json (PermissionError)" in esc.texto
    assert esc.ajustes.provision_path.read_bytes() == anterior  # el activo sigue siendo el anterior
    respaldos = _respaldos(esc)
    assert len(respaldos) == 1 and respaldos[0].read_bytes() == anterior
    assert respaldos[0].name in esc.texto


def test_un_fallo_al_respaldar_impide_escribir(dc, esc, monkeypatch):
    esc.escribir_provision(id_embarazo=999)
    anterior = esc.ajustes.provision_path.read_bytes()

    def sin_permiso(*_, **__):
        raise PermissionError("acceso denegado")

    monkeypatch.setattr(dc.shutil, "copy2", sin_permiso)
    esc.respuestas = ["S"]

    dc.opcion_restablecer(esc.dep)

    assert "OPERACIÓN INCOMPLETA" in esc.texto
    assert esc.ajustes.provision_path.read_bytes() == anterior
    assert _respaldos(esc) == []


def test_la_escritura_atomica_no_deja_un_archivo_a_medias(dc, esc, monkeypatch):
    anterior = esc.ajustes.provision_path.read_bytes()

    def corte(*_, **__):
        raise OSError("corte de energia simulado")

    monkeypatch.setattr(dc.provisionar_demo.os, "replace", corte)

    with pytest.raises(OSError):
        dc.provisionar_demo.escribir_provision(
            esc.ajustes.provision_path, _episodio(id_embarazo=999), esc.catalogos
        )

    assert esc.ajustes.provision_path.read_bytes() == anterior
    assert [p.name for p in esc.datos.iterdir() if p.name.endswith(".tmp")] == []


# ---------------------------------------------------------------------------
# Opciones 3 y 4 -- API central
# ---------------------------------------------------------------------------


def test_desconectar_detiene_solo_la_api_y_comprueba_el_efecto(dc, esc):
    dc.opcion_desconectar(esc.dep)

    assert esc.mundo.de_control == [["stop", "api"]]
    assert esc.mundo.estados == {"db": "running", "api": "exited", "adminer": "exited"}
    for linea in (
        "[OK]   PostgreSQL continúa disponible.",
        "[OK]   Adminer sin cambios (APAGADO).",
        "[OK]   Contenedor api detenido.",
        "[OK]   La API del portal ya no responde.",
        "ESTADO: SERVIDOR CENTRAL NO DISPONIBLE",
    ):
        assert linea in esc.texto
    esc.sin_secretos()


def test_desconectar_deja_adminer_disponible_si_lo_estaba(dc, esc):
    esc.mundo.estados["adminer"] = "running"

    dc.opcion_desconectar(esc.dep)

    assert "[OK]   Adminer sin cambios (DISPONIBLE)." in esc.texto
    assert esc.mundo.estados["adminer"] == "running"
    assert esc.mundo.de_control == [["stop", "api"]]


def test_desconectar_detecta_otra_instancia_que_sigue_respondiendo(dc, esc):
    esc.mundo.otra_instancia_responde = True

    dc.opcion_desconectar(esc.dep)

    assert "SIMULACIÓN NO EFECTIVA: algo sigue respondiendo /health" in esc.texto
    assert "ESTADO: SERVIDOR CENTRAL NO DISPONIBLE" not in esc.texto
    assert "ESTADO: REVISAR" in esc.texto


def test_desconectar_se_niega_si_el_portal_usa_otra_api(dc, esc):
    esc.ajustes = esc.ajustes.model_copy(update={"api_base_url": "http://127.0.0.1:8010"})

    dc.opcion_desconectar(esc.dep)

    assert "GESTANTE_API_BASE_URL no apunta al servicio api de Compose." in esc.texto
    assert "SIMULACIÓN NO EFECTIVA: detenerlo no cortaría al portal." in esc.texto
    assert "No se hizo nada." in esc.texto
    assert esc.mundo.de_control == []
    assert esc.mundo.estados["api"] == "running"


def test_desconectar_con_la_api_ya_detenida_no_hace_nada(dc, esc):
    esc.mundo.estados["api"] = "exited"

    dc.opcion_desconectar(esc.dep)

    assert "La API central ya está detenida" in esc.texto
    assert esc.mundo.de_control == []


def test_un_fallo_de_docker_al_detener_se_informa(dc, esc):
    esc.mundo.falla_control = True

    dc.opcion_desconectar(esc.dep)

    assert "[FAIL] 'docker compose stop api' falló" in esc.texto
    assert "SERVIDOR CENTRAL NO DISPONIBLE" not in esc.texto


def test_reconectar_inicia_solo_la_api_y_espera_a_health(dc, esc):
    esc.mundo.estados["api"] = "exited"
    esc.mundo.api_tarda_en_responder = 3

    dc.opcion_reconectar(esc.dep)

    assert esc.mundo.de_control == [["start", "api"]]
    assert esc.mundo.dormidas == 3
    assert "[OK]   GET /health responde." in esc.texto
    assert "no es una comprobación integral de PostgreSQL" in esc.texto
    assert "ESTADO: CONEXIÓN RESTABLECIDA" in esc.texto
    assert "no se amplía ni se omite la autenticación" in esc.texto
    assert esc.mundo.estados == {"db": "running", "api": "running", "adminer": "exited"}


def test_reconectar_confirma_el_contenedor_ademas_de_health(dc, esc):
    esc.mundo.estados["api"] = "exited"

    dc.opcion_reconectar(esc.dep)

    assert esc.mundo.de_control == [["start", "api"]]
    health = esc.texto.index("[OK]   GET /health responde.")
    contenedor = esc.texto.index("[OK]   Contenedor api en marcha.")
    estado = esc.texto.index("ESTADO: CONEXIÓN RESTABLECIDA")
    assert health < contenedor < estado
    # Tras iniciar se vuelve a leer Docker: el estado no se supone.
    assert [c[0] for c in esc.mundo.de_docker].count("ps") == 2


@pytest.mark.parametrize("api_antes", ["exited", "running"])
def test_reconectar_se_niega_si_el_portal_apunta_a_otra_api(dc, esc, api_antes):
    """El portal en :8010 y esa otra API respondiendo: no es una reconexión."""
    esc.ajustes = esc.ajustes.model_copy(update={"api_base_url": "http://127.0.0.1:8010"})
    esc.mundo.otra_instancia_responde = True
    esc.mundo.estados["api"] = api_antes

    dc.opcion_reconectar(esc.dep)

    assert "CONEXIÓN RESTABLECIDA" not in esc.texto
    assert "GESTANTE_API_BASE_URL no apunta al servicio api de Compose." in esc.texto
    assert "RECONEXIÓN NO COMPROBABLE" in esc.texto
    assert "No se hizo nada." in esc.texto
    assert esc.mundo.de_control == []  # no inicia nada
    assert esc.mundo.estados["api"] == api_antes
    # Ni siquiera consulta /health en esa URL: su respuesta no probaría nada.
    assert not [url for url in esc.mundo.peticiones if url.endswith("/health")]
    assert "GET /health responde" not in esc.texto


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8010", "http://localhost:9000", "http://192.0.2.10:8000", "http://api.example.test"],
)
def test_ni_desconectar_ni_reconectar_actuan_con_el_portal_en_otra_api(dc, esc, url):
    esc.ajustes = esc.ajustes.model_copy(update={"api_base_url": url})
    esc.mundo.otra_instancia_responde = True

    dc.opcion_desconectar(esc.dep)
    dc.opcion_reconectar(esc.dep)

    assert esc.mundo.de_control == []
    assert "CONEXIÓN RESTABLECIDA" not in esc.texto
    assert "SERVIDOR CENTRAL NO DISPONIBLE" not in esc.texto
    assert esc.texto.count("No se hizo nada.") == 2


def test_las_opciones_3_y_4_comparten_la_misma_comprobacion_del_portal(dc):
    """Una sola función decide si el portal usa la API de Compose."""
    fuente = RUTA_SCRIPT.read_text(encoding="utf-8")

    assert fuente.count("def portal_usa_la_api_de_compose(") == 1
    assert fuente.count("puerto_publicado(PUERTO_INTERNO_API)") == 1
    for funcion in (dc.opcion_desconectar, dc.opcion_reconectar):
        assert "_api_del_portal" in funcion.__code__.co_names
    assert "portal_usa_la_api_de_compose" in dc._api_del_portal.__code__.co_names
    assert "portal_usa_la_api_de_compose" in dc._verificar_api.__code__.co_names


def test_reconectar_no_da_por_buena_otra_instancia_si_el_contenedor_no_arranca(dc, esc):
    """``start`` devuelve 0, el contenedor no queda en marcha y algo ajeno responde."""
    esc.mundo.estados["api"] = "exited"
    esc.mundo.el_arranque_no_prospera = True
    esc.mundo.otra_instancia_responde = True

    dc.opcion_reconectar(esc.dep)

    assert esc.mundo.de_control == [["start", "api"]]
    assert "[OK]   GET /health responde." in esc.texto  # respondió… otra cosa
    assert "el contenedor api de este proyecto no está en marcha" in esc.texto
    assert "ESTADO: REVISAR — la reconexión no quedó comprobada" in esc.texto
    assert "CONEXIÓN RESTABLECIDA" not in esc.texto
    assert "Contenedor api en marcha" not in esc.texto


def test_reconectar_no_da_por_bueno_un_contenedor_que_no_responde(dc, esc):
    """El caso inverso: contenedor en marcha, pero /health nunca contesta."""
    esc.mundo.estados["api"] = "exited"
    esc.mundo.api_tarda_en_responder = 10_000

    dc.opcion_reconectar(esc.dep)

    assert esc.mundo.estados["api"] == "running"
    assert "La API no respondió a GET /health" in esc.texto
    assert "CONEXIÓN RESTABLECIDA" not in esc.texto


def test_reconectar_no_da_por_buena_una_api_que_no_responde(dc, esc):
    esc.mundo.estados["api"] = "exited"
    esc.mundo.api_tarda_en_responder = 10_000

    dc.opcion_reconectar(esc.dep)

    assert "[FAIL] La API no respondió a GET /health en 60 s" in esc.texto
    assert "CONEXIÓN RESTABLECIDA" not in esc.texto
    assert esc.mundo.dormidas == 59  # espera simulada: la prueba no duerme de verdad


def test_reconectar_con_la_api_en_marcha_no_la_reinicia(dc, esc):
    dc.opcion_reconectar(esc.dep)

    assert esc.mundo.de_control == []
    assert "ya estaba en marcha" in esc.texto
    assert "ESTADO: CONEXIÓN RESTABLECIDA" in esc.texto


def test_un_fallo_de_docker_al_iniciar_se_informa(dc, esc):
    esc.mundo.estados["api"] = "exited"
    esc.mundo.falla_control = True

    dc.opcion_reconectar(esc.dep)

    assert "[FAIL] 'docker compose start api' falló" in esc.texto
    assert "CONEXIÓN RESTABLECIDA" not in esc.texto


def test_ni_desconectar_ni_reconectar_tocan_la_base_ni_la_cola(dc, esc):
    crear_cola(esc.ruta_cola, "PENDIENTE")
    antes = huella_de_carpeta(esc.datos)

    dc.opcion_desconectar(esc.dep)
    dc.opcion_reconectar(esc.dep)

    assert huella_de_carpeta(esc.datos) == antes
    assert esc.mundo.de_control == [["stop", "api"], ["start", "api"]]
    assert esc.mundo.del_etl == []


# ---------------------------------------------------------------------------
# Opcion 5 -- Previsualizar
# ---------------------------------------------------------------------------


def _candidata(id_lectura=1281, id_embarazo=ID_EMBARAZO, **cambios) -> CandidataDeCarga:
    datos = dict(
        id_lectura=id_lectura,
        id_sesion=833,
        id_embarazo=id_embarazo,
        id_clinica=100,
        id_medico=100,
        semana_gestacion=28,
        trimestre=3,
        fecha_clinica="2026-10-11",
        estado_hr="OK",
        estado_spo2="WARNING",
        estado_mov=None,
        semaforo_derivado="WARNING",
        semaforo_origen="WARNING",
        embarazo_en_dimension=True,
    )
    datos.update(cambios)
    return CandidataDeCarga(**datos)


def _salida_de_previsualizar(*candidatas, candado=False) -> str:
    """La salida real del formateador del ETL, no una copia escrita a mano."""
    return formatear_previsualizacion(
        Previsualizacion(
            revision="9c1d7f2ab4e8",
            version_umbrales="SIM-1.0",
            instantanea="2026-10-11T15:00:00+00:00",
            candado_ocupado=candado,
            hechos_existentes=1181,
            filas_de_dimension=(("dim_clinica", 3), ("dim_embarazo", 31)),
            filas_de_bridge=23,
            candidatas=tuple(candidatas),
        )
    )


def _proceso(dc, codigo=0, salida="", error=""):
    return dc.Proceso(codigo, salida, error)


def test_previsualizar_muestra_contexto_clasificacion_y_destino(dc, esc):
    esc.mundo.etl["previsualizar"] = _proceso(dc, salida=_salida_de_previsualizar(_candidata()))

    dc.opcion_previsualizar(esc.dep)

    for fragmento in (
        "MODO: SOLO LECTURA",
        "Candidatas a hecho nuevo:        1",
        "Lectura #1281 (sesión #833) — DEMO",
        f"Embarazo:                   #{ID_EMBARAZO}",
        "Fecha clínica (Panamá):     2026-10-11",
        "Semana / trimestre:         28 / 3",
        "Médico PRINCIPAL:           #100",
        "Frecuencia cardíaca:        OK",
        "SpO₂:                       WARNING",
        "Movimiento fetal:           no aplica",
        "Semáforo derivado:          WARNING",
        "Semáforo en origen:         WARNING",
        "Validación:                 COINCIDE",
        "Nueva fila candidata en:    fact_lectura_biometrica",
        "Reglas: SIM-1.0",
        "NO SE REALIZÓ NINGUNA ESCRITURA",
    ):
        assert fragmento in esc.texto, fragmento
    assert esc.mundo.del_etl == ["previsualizar"]  # nunca "ejecutar"
    assert esc.mundo.de_control == []
    esc.sin_secretos()


def test_previsualizar_distingue_candidata_de_hecho_confirmado(dc, esc):
    esc.mundo.etl["previsualizar"] = _proceso(dc, salida=_salida_de_previsualizar(_candidata()))

    dc.opcion_previsualizar(esc.dep)

    assert "LIMITACIÓN: Una candidata no es un hecho confirmado" in esc.texto
    assert "la carga, en los seudónimos o en la conciliación" in esc.texto
    assert "Candado del ETL: LIBRE" in esc.texto
    assert "ETL COMPLETADO" not in esc.texto


def test_previsualizar_sin_lecturas_nuevas(dc, esc):
    esc.mundo.etl["previsualizar"] = _proceso(dc, salida=_salida_de_previsualizar())

    dc.opcion_previsualizar(esc.dep)

    assert "Candidatas a hecho nuevo:        0" in esc.texto
    assert "La próxima carga no crearía hechos" in esc.texto
    assert "Lectura #" not in esc.texto


def test_previsualizar_no_filtra_ni_embellece_lecturas_ajenas(dc, esc):
    esc.mundo.etl["previsualizar"] = _proceso(
        dc,
        salida=_salida_de_previsualizar(
            _candidata(900, id_embarazo=12, estado_hr="ERROR", semaforo_derivado="ERROR", semaforo_origen="ERROR"),
            _candidata(1281),
            _candidata(1300, id_embarazo=44),
        ),
        )

    dc.opcion_previsualizar(esc.dep)

    assert "Candidatas a hecho nuevo:        3" in esc.texto
    assert f"[ATENCIÓN] 2 candidata(s) NO pertenecen al embarazo demo {ID_EMBARAZO}" in esc.texto
    assert "Lectura #900 (sesión #833) — NO ESPERADA" in esc.texto
    assert "Lectura #1281 (sesión #833) — DEMO" in esc.texto
    assert "Lectura #1300 (sesión #833) — NO ESPERADA" in esc.texto
    assert "Frecuencia cardíaca:        ERROR" in esc.texto


def test_previsualizar_informa_el_candado_ocupado(dc, esc):
    esc.mundo.etl["previsualizar"] = _proceso(
        dc, salida=_salida_de_previsualizar(_candidata(), candado=True)
    )

    dc.opcion_previsualizar(esc.dep)

    assert "Candado del ETL: OCUPADO" in esc.texto


@pytest.mark.parametrize(
    ("codigo", "mensaje"),
    [
        (1, "Error de configuración, precondición o base de datos. No se escribió nada."),
        (2, "La ejecución real RECHAZARÍA este origen. No se escribió nada."),
        (3, "El ETL detectó una inconsistencia previa a la carga. No se escribió nada."),
        (9, "Código de salida 9."),
    ],
)
def test_previsualizar_conserva_el_significado_de_cada_codigo(dc, esc, codigo, mensaje):
    esc.mundo.etl["previsualizar"] = _proceso(
        dc,
        codigo=codigo,
        salida=f"hr_valor=187.5 conexion {URL_ETL}",
        error=f"resultado=FAILED\nError: lectura id_lectura=7: detalle con {URL_ETL}\n",
    )

    dc.opcion_previsualizar(esc.dep)

    assert f"[FAIL] {mensaje}" in esc.texto
    assert "lectura id_lectura=7: detalle con" in esc.texto
    assert "187.5" not in esc.texto  # la salida cruda no se vuelca
    esc.sin_secretos()


@pytest.mark.parametrize(
    "alterar",
    [
        lambda s: s.replace("estado_hr=OK", "estado_hr=OK hr_valor=187.5"),
        lambda s: s.replace("escrituras=0", "escrituras=1"),
        lambda s: s.replace("modo=SOLO_LECTURA", "modo=ESCRITURA"),
        lambda s: s.replace("resultado=PREVIEW", "resultado=SUCCESS"),
        lambda s: s.replace("candidatas=1", "candidatas=2"),
        lambda s: s.replace(" destino=fact_lectura_biometrica", ""),
        lambda s: "\n".join(l for l in s.splitlines() if not l.startswith("aviso=")),
        lambda s: "Traceback (most recent call last): hr_valor=187.5",
        lambda s: "",
    ],
    ids=[
        "campo-de-valor", "con-escrituras", "otro-modo", "otro-resultado",
        "conteo-incoherente", "campo-ausente", "sin-aviso", "traza", "vacia",
    ],
)
def test_un_formato_inesperado_falla_sin_volcar_la_salida(dc, esc, alterar):
    esc.mundo.etl["previsualizar"] = _proceso(
        dc, salida=alterar(_salida_de_previsualizar(_candidata()))
    )

    dc.opcion_previsualizar(esc.dep)

    assert "formato inesperado. Por seguridad no se muestra su salida" in esc.texto
    assert "187.5" not in esc.texto
    assert "Lectura #" not in esc.texto
    assert "NO SE REALIZÓ NINGUNA ESCRITURA" not in esc.texto


def test_el_interprete_y_el_formateador_del_etl_acuerdan_los_campos(dc):
    datos = dc.interpretar_previsualizacion(_salida_de_previsualizar(_candidata()))

    assert tuple(datos["candidatas"][0]) == dc.CAMPOS_DE_CANDIDATA
    assert not {"hr_valor", "spo2_valor", "mov_valor"} & set(dc.CAMPOS_DE_CANDIDATA)
    assert datos["resumen"]["escrituras"] == "0"
    assert sum(int(f["filas"]) for f in datos["revalidar"]) == 3 + 31 + 23


# ---------------------------------------------------------------------------
# Opcion 6 -- Ejecutar ETL
# ---------------------------------------------------------------------------


def _resumen_del_etl(hechos_nuevos=1) -> str:
    """La salida real de ``formatear_resumen``."""
    return formatear_resumen(
        ResultadoEjecucion(
            revision="9c1d7f2ab4e8",
            version_umbrales="SIM-1.0",
            dimensiones=(ResultadoUpsert("dim_clinica", 3, 0, 0),),
            bridge=ResultadoUpsert("bridge_embarazo_factor_riesgo", 23, 0, 0),
            hechos_nuevos=hechos_nuevos,
            hechos_existentes=1181,
            informe=InformeDeConciliacion(
                verificaciones=(Verificacion("contenido_divergente", "x", 0, True),),
                conteos={"lecturas_origen": 1182, "hechos": 1182},
            ),
            duracion_s=0.5,
            seudonimos_emitidos={"seudonimo_embarazo": 0, "seudonimo_paciente": 0},
        )
    )


@pytest.mark.parametrize(
    "respuesta", ["", "S", "s", "si", "ejecutar", "Ejecutar", "EJECUTAR ETL", "N", "1"]
)
def test_sin_la_palabra_exacta_el_etl_no_se_ejecuta(dc, esc, respuesta):
    esc.mundo.etl["ejecutar"] = _proceso(dc, salida=_resumen_del_etl())
    esc.respuestas = [respuesta]

    dc.opcion_ejecutar_etl(esc.dep)

    assert "Cancelado. No se ejecutó el ETL." in esc.texto
    assert esc.mundo.del_etl == []


def test_con_destino_validado_y_confirmacion_se_ejecuta_el_etl_real_una_vez(dc, esc):
    esc.mundo.etl["ejecutar"] = _proceso(dc, salida=_resumen_del_etl())
    esc.respuestas = ["EJECUTAR"]

    dc.opcion_ejecutar_etl(esc.dep)

    assert esc.preguntas == ["Escribe EJECUTAR para continuar: "]
    # Un unico proceso: el CLI real, con su unica orden transaccional.
    assert [c for c in esc.mundo.comandos if c[0] != "docker"] == [
        [sys.executable, str(esc.raiz / "scripts" / "etl_analitico.py"), "ejecutar"]
    ]
    for fragmento in (
        f"Base:        {BASE}",
        f"proyecto-ficticio (db en 127.0.0.1:{PUERTO_DB})",
        "Rol:         fetalalert_etl",
        "Es una operación CON ESCRITURAS en los esquemas analitico y privado.",
        "única transacción: extracción → transformación →",
        "RESULTADO: ETL COMPLETADO: la transacción se confirmó entera.",
        "Hechos nuevos:        1",
        "Hechos existentes:    1181",
        "Conciliación:         OK (1 verificaciones, 0 fallidas)",
        "Reglas:               SIM-1.0",
    ):
        assert fragmento in esc.texto, fragmento
    # El destino se muestra antes de preguntar.
    assert esc.texto.index("Destino validado") < esc.texto.index("Hechos nuevos")
    esc.sin_secretos()


def test_el_etl_no_se_presenta_como_pasos_independientes(dc, esc):
    esc.mundo.etl["ejecutar"] = _proceso(dc, salida=_resumen_del_etl())
    esc.respuestas = ["EJECUTAR"]

    dc.opcion_ejecutar_etl(esc.dep)

    for falso_progreso in ("[1/4]", "[2/4]", "[3/4]", "[4/4]"):
        assert falso_progreso not in esc.texto
    assert esc.mundo.del_etl == ["ejecutar"]


@pytest.mark.parametrize(
    ("codigo", "fragmento"),
    [
        (1, "Error de configuración, precondición o base de datos."),
        (2, "las reglas SIM-1.0 no permiten cargar"),
        (3, "La conciliación encontró diferencias, u otra inconsistencia del ETL."),
        (4, "Otra ejecución del ETL tiene el candado."),
        (9, "Código de salida inesperado: 9."),
    ],
)
def test_cada_codigo_del_etl_conserva_su_significado(dc, esc, codigo, fragmento):
    esc.mundo.etl["ejecutar"] = _proceso(
        dc,
        codigo=codigo,
        salida=f"nombre_completo=Nombre Ficticio conexion {URL_ETL}",
        error=f"resultado=FAILED\nError: detalle saneable {URL_MIGRADOR} fin\n",
    )
    esc.respuestas = ["EJECUTAR"]

    dc.opcion_ejecutar_etl(esc.dep)

    assert f"RESULTADO: FALLO (código {codigo})" in esc.texto
    assert fragmento in esc.texto
    assert "ETL COMPLETADO" not in esc.texto
    assert "Detalle: detalle saneable" in esc.texto
    assert "Nombre Ficticio" not in esc.texto  # stdout de un fallo no se vuelca
    esc.sin_secretos()


def test_un_resumen_inesperado_con_codigo_cero_no_se_da_por_bueno(dc, esc):
    esc.mundo.etl["ejecutar"] = _proceso(dc, salida="algo distinto hr_valor=187.5")
    esc.respuestas = ["EJECUTAR"]

    dc.opcion_ejecutar_etl(esc.dep)

    assert "el resumen no tiene el formato esperado" in esc.texto
    assert "ETL COMPLETADO" not in esc.texto
    assert "187.5" not in esc.texto


def test_ejecutar_el_etl_no_toca_docker_ni_archivos_locales(dc, esc):
    crear_cola(esc.ruta_cola, "ENVIADO")
    antes = huella_de_carpeta(esc.datos)
    esc.mundo.etl["ejecutar"] = _proceso(dc, salida=_resumen_del_etl())
    esc.respuestas = ["EJECUTAR"]

    dc.opcion_ejecutar_etl(esc.dep)

    assert huella_de_carpeta(esc.datos) == antes
    assert esc.mundo.de_control == []


def test_los_codigos_de_demo_control_son_los_del_cli_del_etl(dc):
    spec = importlib.util.spec_from_file_location(
        "etl_analitico_para_codigos", RAIZ / "scripts" / "etl_analitico.py"
    )
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)

    assert set(dc.MENSAJES_DEL_ETL) == {
        cli.CODIGO_DE_EXITO,
        cli.CODIGO_DE_ERROR,
        cli.CODIGO_ORIGEN_RECHAZADO,
        cli.CODIGO_CONCILIACION_FALLIDA,
        cli.CODIGO_CANDADO_OCUPADO,
    }
    assert "candado" in dc.MENSAJES_DEL_ETL[cli.CODIGO_CANDADO_OCUPADO]
    assert "conciliación" in dc.MENSAJES_DEL_ETL[cli.CODIGO_CONCILIACION_FALLIDA]
    assert "reglas" in dc.MENSAJES_DEL_ETL[cli.CODIGO_ORIGEN_RECHAZADO]


# ---------------------------------------------------------------------------
# El script no reimplementa reglas
# ---------------------------------------------------------------------------


def test_demo_control_no_contiene_umbrales_ni_clasifica():
    fuente = RUTA_SCRIPT.read_text(encoding="utf-8")

    for copia in ("clasificar_", "hr_valor", "spo2_valor", "mov_valor", "UMBRALES", "severidad"):
        assert copia not in fuente, copia
    # Ni reimplementa la sincronizacion ni la carga.
    for ajeno in ("ejecutar_pasada", "upsert_tipo_1", "insertar_hechos", "emitir_seudonimos"):
        assert ajeno not in fuente, ajeno
