"""Reproduce en local los pasos ``run`` del job ``pruebas-backend`` de CI.

Uso (desde la raiz del repositorio, con el venv del backend)::

    python scripts/reproducir_ci_local.py CONTENEDOR [--desde N]

``CONTENEDOR`` es un PostgreSQL 16 desechable publicado en 127.0.0.1:5432 con
las credenciales ficticias del propio ``ci.yml`` (el equivalente local del
servicio ``postgres`` del job). Nunca apuntes a la base de la demostracion.

Garantias, para que el resultado sea evidencia de *esta* ejecucion:

- se detiene en el primer paso que falla y sale con codigo distinto de cero;
- antes de empezar borra los reportes JUnit (``backend/pytest-*.xml``) que
  hayan quedado de ejecuciones anteriores, de modo que el guardian de pruebas
  omitidas solo puede leer reportes nuevos (con ``--desde`` tambien se borran:
  el guardian fallara si faltan los de pasos no repetidos, como debe);
- cada paso que declara ``--junitxml=...`` tiene que haber escrito ese reporte
  durante su propia ejecucion; si no, cuenta como fallo.

Requiere PyYAML. Solo en local: sustituye ``localhost`` por ``127.0.0.1``,
porque en Windows la resolucion IPv6 de ``localhost`` tarda minutos.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[1]
PASO_INICIAL = "Pruebas de comportamiento de la interfaz"
PATRON_JUNIT = re.compile(r"--junitxml=(\S+)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("contenedor", help="contenedor PostgreSQL desechable del CI local")
    parser.add_argument("--desde", type=int, help="indice del paso por el que empezar")
    args = parser.parse_args()

    flujo = yaml.safe_load((RAIZ / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    job = flujo["jobs"]["pruebas-backend"]
    entorno_job = {k: str(v) for k, v in job["env"].items()}

    def sustituir(valor: object) -> str:
        valor = str(valor).replace("${{ job.services.postgres.id }}", args.contenedor)
        valor = valor.replace("@localhost:", "@127.0.0.1:")
        return re.sub(r"\$\{\{\s*env\.(\w+)\s*\}\}", lambda m: entorno_job[m.group(1)], valor)

    entorno_job = {k: sustituir(v) for k, v in entorno_job.items()}
    venv = RAIZ / "backend" / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    base = dict(os.environ, **entorno_job, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    base["PATH"] = f"{venv}{os.pathsep}{base['PATH']}"
    # Nada del .env del entorno de demostracion debe colarse en las pruebas.
    for clave in [k for k in base if k.startswith(("ALEMBIC_", "ETL_DATABASE", "DATABASE_URL", "GESTANTE_"))]:
        base.pop(clave)

    pasos = job["steps"]
    inicio = args.desde if args.desde is not None else next(
        i for i, p in enumerate(pasos) if p.get("name", "").startswith(PASO_INICIAL)
    )

    antiguos = sorted((RAIZ / "backend").glob("pytest-*.xml"))
    for reporte in antiguos:
        reporte.unlink()
    print(f"Reportes JUnit previos borrados: {len(antiguos)}", flush=True)

    ejecutados = 0
    for paso in pasos[inicio:]:
        if "run" not in paso:
            continue
        nombre = paso.get("name", "(sin nombre)")
        entorno = dict(base, **{k: sustituir(v) for k, v in (paso.get("env") or {}).items()})
        cwd = RAIZ / paso.get("working-directory", ".")
        orden = paso["run"].replace("@localhost:", "@127.0.0.1:")
        t0 = time.time()
        r = subprocess.run(
            ["bash", "-e", "-c", orden], cwd=cwd, env=entorno, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=2400,
        )
        segundos = round(time.time() - t0)
        salida = (r.stdout + r.stderr).strip().splitlines()
        faltantes = [
            nombre_xml for nombre_xml in PATRON_JUNIT.findall(orden)
            if not (cwd / nombre_xml).exists() or (cwd / nombre_xml).stat().st_mtime < t0
        ]
        ok = r.returncode == 0 and not faltantes
        ejecutados += 1
        print(f"[{'OK' if ok else 'FALLO'}] {nombre} ({segundos} s)", flush=True)
        for linea in [l for l in salida if re.search(r"passed|failed|error|skipped|OK:|conforme", l, re.I)][-3:]:
            print("      ", linea[:220], flush=True)
        if not ok:
            if faltantes:
                print(f"   | reporte JUnit no generado en esta ejecucion: {', '.join(faltantes)}", flush=True)
            print("\n".join("   | " + l[:240] for l in salida[-25:]), flush=True)
            print(f"DETENIDO en el paso {pasos.index(paso)} ({nombre}); {ejecutados - 1} pasos en verde antes.")
            return 1
    print(f"RESUMEN: {ejecutados} de {ejecutados} pasos en verde")
    return 0


if __name__ == "__main__":
    sys.exit(main())
