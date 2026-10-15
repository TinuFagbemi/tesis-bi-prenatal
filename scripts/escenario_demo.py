"""Escenarios de demostracion con una o varias gestantes (entorno de pruebas restaurable).

Uso desde la raiz del repositorio, con el entorno virtual del backend::

    python scripts/escenario_demo.py capturar   <escenario.json>   # sin red
    python scripts/escenario_demo.py sincronizar <registro.json>   # inicia sesion y envia
    python scripts/escenario_demo.py verificar   <registro.json>   # solo lectura
    python scripts/escenario_demo.py ejecutar    <escenario.json>  # los tres seguidos

**Que es.** Una herramienta de pruebas para repetir el recorrido de la
demostracion con varias gestantes simuladas: captura sin red, cola PENDIENTE,
sincronizacion, llegada unica a PostgreSQL y, despues del ETL, incorporacion al
modelo analitico y a la publicacion. No es un componente de FetalAlert.

**No reimplementa nada.** Captura con
:func:`app.gestante.movimientos.registrar_sesion_simulada` --el mismo camino que
``dispositivo_gestante.py``--, sincroniza con
:func:`app.gestante.movimientos.enviar_pendientes_de_la_cuenta` --el mismo que el
envio automatico del portal-- y lee la cola y la base con las funciones de solo
lectura de Demo Control. El ETL no lo ejecuta: se previsualiza y se ejecuta con
Demo Control (opciones 5 y 6) o con ``etl_analitico.py``.

**Sin atajos de seguridad.** Cada gestante inicia sesion de verdad contra
``POST /api/v1/autenticacion/token`` con su cuenta simulada; la API vuelve a
comprobar el rol, la propiedad del embarazo, la idempotencia y la auditoria. La
contrasena ficticia del dataset se lee del generador sin ejecutarlo y nunca se
imprime ni se guarda; el token vive en memoria.

**Un escenario** es un JSON con las gestantes, por su dispositivo de
demostracion, y cuantas sesiones de que tipo captura cada una (una lectura por
sesion)::

    {"nombre": "C-varias", "concurrente": true,
     "gestantes": [
       {"dispositivo": "DEMO-SCRUM72-01", "sesiones": 2, "tipo": "SIGNOS_MATERNOS"},
       {"dispositivo": "DEMO-GESTANTE-02", "sesiones": 3,
        "tipo": ["SIGNOS_MATERNOS", "MOVIMIENTOS_FETALES"]}]}

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ_DEL_REPOSITORIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_DEL_REPOSITORIO / "backend"))

import httpx  # noqa: E402  -- tras ajustar sys.path
from sqlalchemy import text  # noqa: E402

from app.gestante import movimientos, provision  # noqa: E402
from app.gestante.central import ClienteCentralHTTP, EstadoRespuesta  # noqa: E402
from app.gestante.config import GestanteSettings, cargar_settings_gestante  # noqa: E402
from app.models.enums import TipoSesion  # noqa: E402


def _cargar_script(nombre: str):
    ruta = Path(__file__).resolve().parent / f"{nombre}.py"
    especificacion = importlib.util.spec_from_file_location(nombre, ruta)
    modulo = importlib.util.module_from_spec(especificacion)
    sys.modules.setdefault(nombre, modulo)
    especificacion.loader.exec_module(modulo)
    return modulo


demo_control = _cargar_script("demo_control")
dispositivo_gestante = _cargar_script("dispositivo_gestante")

CODIGO_DE_EXITO = 0
CODIGO_DE_ERROR = 1
CODIGO_VERIFICACION_FALLIDA = 3

MAXIMO_DE_SESIONES = 50
RONDAS_DE_SINCRONIZACION = 10
PAUSA_ENTRE_RONDAS = 1.0


class ErrorDeEscenario(RuntimeError):
    """El escenario no se puede ejecutar tal como esta. Nada se escribio."""


# ---------------------------------------------------------------------------
# El escenario
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GestanteDelEscenario:
    dispositivo: str
    sesiones: int
    tipos: tuple[TipoSesion, ...]

    def tipo_de_la_sesion(self, indice: int) -> TipoSesion:
        return self.tipos[indice % len(self.tipos)]


@dataclass(frozen=True)
class Escenario:
    nombre: str
    concurrente: bool
    gestantes: tuple[GestanteDelEscenario, ...]


def cargar_escenario(ruta: Path) -> Escenario:
    """Lee y valida el escenario. Cualquier forma inesperada es un error."""
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ErrorDeEscenario(f"No se pudo leer el escenario ({type(error).__name__}).") from None
    if not isinstance(datos, dict) or not isinstance(datos.get("gestantes"), list):
        raise ErrorDeEscenario("El escenario debe tener una lista 'gestantes'.")
    nombre = str(datos.get("nombre") or ruta.stem)
    if not nombre.replace("-", "").replace("_", "").isalnum():
        raise ErrorDeEscenario("'nombre' solo admite letras, numeros, '-' y '_'.")
    gestantes = []
    for numero, entrada in enumerate(datos["gestantes"], start=1):
        if not isinstance(entrada, dict):
            raise ErrorDeEscenario(f"La gestante {numero} no tiene la forma esperada.")
        dispositivo = entrada.get("dispositivo")
        if not isinstance(dispositivo, str) or not dispositivo_gestante.PATRON_DE_DISPOSITIVO.fullmatch(dispositivo):
            raise ErrorDeEscenario(f"La gestante {numero} no nombra un dispositivo de demostracion valido.")
        sesiones = entrada.get("sesiones")
        if isinstance(sesiones, bool) or not isinstance(sesiones, int) or not 1 <= sesiones <= MAXIMO_DE_SESIONES:
            raise ErrorDeEscenario(f"La gestante {numero}: 'sesiones' debe ser un entero entre 1 y {MAXIMO_DE_SESIONES}.")
        tipos = entrada.get("tipo", TipoSesion.SIGNOS_MATERNOS.value)
        tipos = [tipos] if isinstance(tipos, str) else tipos
        try:
            tipos = tuple(TipoSesion(tipo) for tipo in tipos)
        except (TypeError, ValueError):
            raise ErrorDeEscenario(f"La gestante {numero}: tipo de sesion no reconocido.") from None
        if not tipos:
            raise ErrorDeEscenario(f"La gestante {numero}: falta el tipo de sesion.")
        gestantes.append(GestanteDelEscenario(dispositivo, sesiones, tipos))
    if not gestantes:
        raise ErrorDeEscenario("El escenario no tiene gestantes.")
    if len({g.dispositivo for g in gestantes}) != len(gestantes):
        raise ErrorDeEscenario("Un dispositivo aparece dos veces en el escenario.")
    return Escenario(nombre, bool(datos.get("concurrente", False)), tuple(gestantes))


# ---------------------------------------------------------------------------
# Lecturas de solo lectura
# ---------------------------------------------------------------------------


def _dependencias() -> "demo_control.Dependencias":
    return demo_control.Dependencias()


def _conexion_de_lectura(dep):
    return dep.conectar(dep.exigir_url(demo_control.VARIABLE_URL_ALEMBIC))


def _referencias_del_embarazo(conexion, id_embarazo: int) -> dict:
    fila = conexion.execute(
        text(
            "SELECT"
            " (SELECT count(*) FROM operacional.sesion_monitoreo WHERE id_embarazo = :e) AS sesiones,"
            " (SELECT count(*) FROM operacional.lectura_biometrica l"
            "   JOIN operacional.sesion_monitoreo s USING (id_sesion) WHERE s.id_embarazo = :e) AS lecturas,"
            " (SELECT count(*) FROM analitico.fact_lectura_biometrica WHERE id_embarazo = :e) AS hechos,"
            " (SELECT count(*) FROM publicacion.v_lectura v"
            "   JOIN privado.seudonimo_embarazo se ON se.seudonimo = v.seudonimo_embarazo"
            "  WHERE se.id_embarazo = :e) AS publicadas"
        ),
        {"e": id_embarazo},
    ).mappings().one()
    return dict(fila)


def _estados_de_la_cola(settings: GestanteSettings, id_usuario: int, claves: list[str]) -> dict:
    ruta = movimientos.ruta_para_la_cuenta(settings, id_usuario)
    if not ruta.exists():
        return {}
    with closing(sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True)) as conexion:
        marcadores = ",".join("?" for _ in claves)
        return {
            clave: (estado, id_sesion)
            for clave, estado, id_sesion in conexion.execute(
                "SELECT clave_idempotencia, estado, id_sesion_remota FROM outbox"
                f" WHERE clave_idempotencia IN ({marcadores})",
                claves,
            )
        }


# ---------------------------------------------------------------------------
# capturar
# ---------------------------------------------------------------------------


def capturar(escenario: Escenario, settings: GestanteSettings, reloj=None) -> dict:
    """Captura todas las sesiones sin usar la red y devuelve el registro."""
    dep = _dependencias()
    preparadas = []
    for gestante in escenario.gestantes:
        ruta = dispositivo_gestante.ruta_de_aprovisionamiento(settings, gestante.dispositivo)
        try:
            aprovisionamiento = provision.cargar(ruta)
        except provision.ProvisionInvalida as error:
            raise ErrorDeEscenario(f"{gestante.dispositivo}: {error.detalle}") from None
        cola = demo_control.inspeccionar_cola(
            movimientos.ruta_para_la_cuenta(settings, aprovisionamiento.id_usuario)
        )
        if cola.existe and (not cola.legible or cola.con_residuos):
            raise ErrorDeEscenario(
                f"{gestante.dispositivo}: la cola de la cuenta {aprovisionamiento.id_usuario} "
                "tiene capturas sin entregar o no se puede leer. Resuelvela antes del escenario."
            )
        preparadas.append((gestante, aprovisionamiento))
    cuentas = [a.id_usuario for _, a in preparadas]
    if len(set(cuentas)) != len(cuentas):
        raise ErrorDeEscenario("Dos dispositivos del escenario sirven a la misma cuenta.")

    with _conexion_de_lectura(dep) as conexion:
        antes = {a.id_embarazo: _referencias_del_embarazo(conexion, a.id_embarazo) for _, a in preparadas}

    base = (reloj or (lambda: datetime.now(timezone.utc)))()

    def capturar_gestante(par):
        gestante, aprovisionamiento = par
        claves = []
        for indice in range(gestante.sesiones):
            # Una sesion por minuto hacia atras: todas anteriores a "ahora",
            # en orden, y dentro de la misma semana gestacional.
            instante = base - timedelta(minutes=gestante.sesiones - 1 - indice)
            capturada = movimientos.registrar_sesion_simulada(
                settings,
                id_usuario=aprovisionamiento.id_usuario,
                provision=aprovisionamiento,
                tipo_sesion=gestante.tipo_de_la_sesion(indice),
                ahora=instante,
            )
            claves.append(capturada.clave)
        return claves

    if escenario.concurrente:
        with ThreadPoolExecutor(max_workers=len(preparadas)) as hilos:
            todas = list(hilos.map(capturar_gestante, preparadas))
    else:
        todas = [capturar_gestante(par) for par in preparadas]

    registro = {
        "escenario": escenario.nombre,
        "concurrente": escenario.concurrente,
        "capturado_en": base.isoformat(),
        "gestantes": [
            {
                "dispositivo": gestante.dispositivo,
                "id_usuario": a.id_usuario,
                "id_embarazo": a.id_embarazo,
                "id_dispositivo": a.id_dispositivo,
                "tipos": [gestante.tipo_de_la_sesion(i).value for i in range(gestante.sesiones)],
                "claves": claves,
                "antes": antes[a.id_embarazo],
            }
            for (gestante, a), claves in zip(preparadas, todas)
        ],
    }
    for entrada in registro["gestantes"]:
        estados = _estados_de_la_cola(settings, entrada["id_usuario"], entrada["claves"])
        if sorted(e for e, _ in estados.values()) != ["PENDIENTE"] * len(entrada["claves"]):
            raise ErrorDeEscenario(f"{entrada['dispositivo']}: no todas las capturas quedaron PENDIENTE.")
    return registro


def guardar_registro(settings: GestanteSettings, registro: dict) -> Path:
    carpeta = settings.movimientos_dir.parent / "escenarios"
    carpeta.mkdir(parents=True, exist_ok=True)
    marca = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ruta = carpeta / f"{registro['escenario']}-{marca}.json"
    ruta.write_text(json.dumps(registro, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ruta


# ---------------------------------------------------------------------------
# sincronizar
# ---------------------------------------------------------------------------


def password_simulada() -> str:
    """La contrasena ficticia de las cuentas del dataset, leida sin ejecutar el generador."""
    fuente = (RAIZ_DEL_REPOSITORIO / "scripts" / "generate_mock_data.py").read_text(encoding="utf-8")
    for nodo in ast.parse(fuente).body:
        if (
            isinstance(nodo, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "PASSWORD_SIMULADA" for t in nodo.targets)
            and isinstance(nodo.value, ast.Constant)
        ):
            return nodo.value.value
    raise ErrorDeEscenario("No se encontro la contrasena simulada del dataset.")


def _correos(dep, cuentas: list[int]) -> dict[int, str]:
    with _conexion_de_lectura(dep) as conexion:
        filas = conexion.execute(
            text("SELECT id_usuario, email FROM operacional.usuario WHERE id_usuario = ANY(:c)"),
            {"c": cuentas},
        ).all()
    return {fila.id_usuario: fila.email for fila in filas}


def sincronizar(registro: dict, settings: GestanteSettings, constructor_http=None) -> dict:
    """Cada gestante inicia sesion y entrega su cola. Devuelve el resultado por cuenta."""
    dep = _dependencias()
    correos = _correos(dep, [g["id_usuario"] for g in registro["gestantes"]])
    clave_simulada = password_simulada()
    construir = constructor_http or (
        lambda: httpx.Client(base_url=settings.api_base_url, timeout=settings.http_timeout)
    )

    def sincronizar_gestante(entrada):
        with construir() as http:
            respuesta = ClienteCentralHTTP(http).autenticar(
                email=correos[entrada["id_usuario"]], password=clave_simulada
            )
        if respuesta.estado is not EstadoRespuesta.OK or not respuesta.token:
            return {"dispositivo": entrada["dispositivo"], "sesion": respuesta.estado.value, "enviadas": 0}
        for ronda in range(1, RONDAS_DE_SINCRONIZACION + 1):
            movimientos.enviar_pendientes_de_la_cuenta(
                settings, id_usuario=entrada["id_usuario"], token=respuesta.token
            )
            estados = _estados_de_la_cola(settings, entrada["id_usuario"], entrada["claves"])
            enviadas = sum(1 for e, _ in estados.values() if e == "ENVIADO")
            if enviadas == len(entrada["claves"]):
                break
            time.sleep(PAUSA_ENTRE_RONDAS)
        return {"dispositivo": entrada["dispositivo"], "sesion": "OK", "enviadas": enviadas, "rondas": ronda}

    if registro.get("concurrente"):
        with ThreadPoolExecutor(max_workers=len(registro["gestantes"])) as hilos:
            return {r["dispositivo"]: r for r in hilos.map(sincronizar_gestante, registro["gestantes"])}
    return {r["dispositivo"]: r for r in map(sincronizar_gestante, registro["gestantes"])}


# ---------------------------------------------------------------------------
# verificar
# ---------------------------------------------------------------------------


def verificar(registro: dict, settings: GestanteSettings) -> list[dict]:
    """Por gestante: cola, llegada unica, pertenencia, y estado del ETL. Solo lee."""
    dep = _dependencias()
    resultados = []
    with _conexion_de_lectura(dep) as conexion:
        for entrada in registro["gestantes"]:
            claves = entrada["claves"]
            esperadas = len(claves)
            estados = _estados_de_la_cola(settings, entrada["id_usuario"], claves)
            filas = conexion.execute(
                text(
                    "SELECT i.clave, i.id_sesion, s.id_embarazo, s.id_dispositivo,"
                    "       (SELECT count(*) FROM operacional.lectura_biometrica l"
                    "         WHERE l.id_sesion = s.id_sesion) AS lecturas"
                    "  FROM operacional.idempotencia_solicitud i"
                    "  JOIN operacional.sesion_monitoreo s ON s.id_sesion = i.id_sesion"
                    " WHERE i.clave = ANY(:claves)"
                ),
                {"claves": claves},
            ).all()
            # Se cuenta lo de **estas** claves, no la diferencia del embarazo:
            # otro escenario puede haber usado el mismo embarazo despues.
            hechos = conexion.execute(
                text("SELECT count(*) FROM analitico.fact_lectura_biometrica WHERE id_sesion = ANY(:s)"),
                {"s": [f.id_sesion for f in filas]},
            ).scalar_one()
            # publicacion.v_lectura no expone id_lectura; publica cada hecho del
            # embarazo, asi que se comprueba que publica todos los del embarazo.
            del_embarazo = _referencias_del_embarazo(conexion, entrada["id_embarazo"])
            por_clave = {}
            for fila in filas:
                por_clave.setdefault(fila.clave, []).append(fila)
            llegadas_unicas = sum(1 for c in claves if len(por_clave.get(c, [])) == 1)
            pertenecen = all(
                f.id_embarazo == entrada["id_embarazo"] and f.id_dispositivo == entrada["id_dispositivo"]
                for f in filas
            )
            coinciden_con_la_cola = all(
                estados.get(f.clave, (None, None))[1] == f.id_sesion for f in filas
            )
            resultado = {
                "dispositivo": entrada["dispositivo"],
                "id_embarazo": entrada["id_embarazo"],
                "esperadas": esperadas,
                "enviadas_en_cola": sum(1 for e, _ in estados.values() if e == "ENVIADO"),
                "llegadas_unicas": llegadas_unicas,
                "una_lectura_por_sesion": all(f.lecturas == 1 for f in filas),
                "pertenecen_al_embarazo": pertenecen,
                "coinciden_con_la_cola": coinciden_con_la_cola,
                "sesiones": len({f.id_sesion for f in filas}),
                "lecturas": sum(f.lecturas for f in filas),
                "hechos": hechos,
                "embarazo_publicado_completo": del_embarazo["publicadas"] == del_embarazo["hechos"],
            }
            resultado["llegada_correcta"] = (
                resultado["enviadas_en_cola"] == esperadas
                and llegadas_unicas == esperadas
                and len(filas) == esperadas
                and pertenecen
                and coinciden_con_la_cola
                and resultado["una_lectura_por_sesion"]
                and resultado["sesiones"] == esperadas
                and resultado["lecturas"] == esperadas
            )
            resultado["etl_completo"] = hechos == esperadas and resultado["embarazo_publicado_completo"]
            resultados.append(resultado)
    return resultados


def _imprimir_verificacion(resultados: list[dict]) -> bool:
    print("dispositivo        embarazo  esperadas  enviadas  unicas  pertenencia  sesiones  lecturas  hechos  llegada  ETL")
    for r in resultados:
        print(
            f"{r['dispositivo']:<18} {r['id_embarazo']:>8}  {r['esperadas']:>9}  {r['enviadas_en_cola']:>8}"
            f"  {r['llegadas_unicas']:>6}  {'OK' if r['pertenecen_al_embarazo'] and r['coinciden_con_la_cola'] else 'FALLA':>11}"
            f"  {r['sesiones']:>8}  {r['lecturas']:>8}  {r['hechos']:>6}"
            f"  {'OK' if r['llegada_correcta'] else 'FALLA':>7}  {'OK' if r['etl_completo'] else 'pendiente'}"
        )
    return all(r["llegada_correcta"] for r in resultados)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="escenario_demo.py", description=__doc__.splitlines()[0])
    ordenes = parser.add_subparsers(dest="orden", required=True)
    for nombre, ayuda in (
        ("capturar", "captura las sesiones del escenario sin red"),
        ("sincronizar", "inicia sesion por cada gestante y entrega su cola"),
        ("verificar", "comprueba llegada unica, pertenencia y ETL; solo lectura"),
        ("ejecutar", "capturar, sincronizar y verificar seguidos"),
    ):
        sub = ordenes.add_parser(nombre, help=ayuda)
        sub.add_argument("archivo", type=Path)
    argumentos = parser.parse_args(argv)
    settings = cargar_settings_gestante()

    try:
        if argumentos.orden in ("capturar", "ejecutar"):
            escenario = cargar_escenario(argumentos.archivo)
            registro = capturar(escenario, settings)
            ruta = guardar_registro(settings, registro)
            total = sum(len(g["claves"]) for g in registro["gestantes"])
            print(f"Escenario {escenario.nombre}: {total} sesiones capturadas sin red, todas PENDIENTE.")
            for g in registro["gestantes"]:
                print(f"  {g['dispositivo']}: cuenta {g['id_usuario']}, embarazo {g['id_embarazo']}, {len(g['claves'])} sesiones {sorted(set(g['tipos']))}")
            print(f"Registro: {ruta}")
            if argumentos.orden == "capturar":
                return CODIGO_DE_EXITO
        else:
            ruta = argumentos.archivo
            registro = json.loads(ruta.read_text(encoding="utf-8"))

        if argumentos.orden in ("sincronizar", "ejecutar"):
            resultado = sincronizar(registro, settings)
            for dispositivo, r in resultado.items():
                print(f"  {dispositivo}: sesion {r['sesion']}, {r['enviadas']} enviadas")

        resultados = verificar(registro, settings)
        correcto = _imprimir_verificacion(resultados)
        print("RESULTADO:", "LLEGADA CORRECTA" if correcto else "VERIFICACION FALLIDA")
        return CODIGO_DE_EXITO if correcto else CODIGO_VERIFICACION_FALLIDA
    except ErrorDeEscenario as error:
        print(f"Error: {error}", file=sys.stderr)
        return CODIGO_DE_ERROR
    except demo_control.EntornoNoValidado as error:
        print("Error: destino no validado:", "; ".join(error.motivos), file=sys.stderr)
        return CODIGO_DE_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
