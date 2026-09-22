"""El comando del bloque offline del CI, leido como lo leeria bash (SCRUM-70).

Existe por un defecto concreto: una edicion dejo en ``ci.yml`` los caracteres
barra invertida + ``n`` donde tenia que haber una continuacion de linea. El YAML
seguia siendo valido, asi que nada lo detecto; pero bash convierte ``\\n`` fuera de
comillas en la letra ``n``, pytest recibia un argumento posicional ``n``, no
encontraba ese archivo y terminaba con codigo 4 sin ejecutar una sola prueba.

Estas pruebas no dependen de una biblioteca de YAML, que el proyecto no instala:
localizan el bloque ``run`` del step por su nombre, unen las continuaciones de
linea con la misma regla que bash --barra invertida seguida de salto de linea
desaparece-- y separan los argumentos con ``shlex`` en modo POSIX, que trata una
barra invertida suelta igual que bash. Si el defecto volviera, aparece aqui como
un argumento que no es una opcion.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import re
import shlex
import textwrap
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
RUTA_CI = RAIZ / ".github" / "workflows" / "ci.yml"
DIRECTORIO_PRUEBAS = Path(__file__).resolve().parent

NOMBRE_DEL_STEP = "Pruebas sin servidor PostgreSQL"
CONTINUACION = "\\\n"


def bloque_run(nombre: str) -> str:
    """El texto del ``run: |`` del step llamado ``nombre``, sin su sangria."""
    lineas = RUTA_CI.read_text(encoding="utf-8").splitlines()

    inicio = next(
        indice
        for indice, linea in enumerate(lineas)
        if linea.strip() == f"- name: {nombre}"
    )
    sangria_del_step = len(lineas[inicio]) - len(lineas[inicio].lstrip())

    for indice in range(inicio + 1, len(lineas)):
        linea = lineas[indice]
        if linea.strip().startswith("- name:") and (
            len(linea) - len(linea.lstrip()) <= sangria_del_step
        ):
            raise AssertionError(f"El step {nombre!r} no tiene bloque run.")
        if linea.strip() == "run: |":
            sangria_run = len(linea) - len(linea.lstrip())
            break
    else:  # pragma: no cover -- el step siempre existe
        raise AssertionError(f"No se encontro el step {nombre!r}.")

    cuerpo = []
    for linea in lineas[indice + 1 :]:
        if linea.strip() and len(linea) - len(linea.lstrip()) <= sangria_run:
            break
        cuerpo.append(linea)

    minima = min(len(l) - len(l.lstrip()) for l in cuerpo if l.strip())
    return "\n".join(l[minima:] for l in cuerpo) + "\n"


def argumentos_como_bash(texto: str) -> list[str]:
    """Los argumentos que bash entregaria al comando."""
    return shlex.split(texto.replace(CONTINUACION, " "), posix=True)


def test_el_step_offline_existe_y_ejecuta_pytest():
    argumentos = argumentos_como_bash(bloque_run(NOMBRE_DEL_STEP))

    assert argumentos[:3] == ["python", "-m", "pytest"]


def test_ningun_argumento_suelto_llega_a_pytest():
    """La prueba del defecto: todo lo que sigue a ``pytest`` es una opcion."""
    argumentos = argumentos_como_bash(bloque_run(NOMBRE_DEL_STEP))

    sueltos = [a for a in argumentos[3:] if not a.startswith("-")]
    assert sueltos == [], (
        f"El comando del CI pasaria argumentos posicionales a pytest: {sueltos}. "
        "Una barra invertida que no va seguida de salto de linea se convierte "
        "en un argumento."
    )


def test_el_detector_encuentra_el_defecto_si_vuelve():
    """Control: con el ``\\n`` literal que causo el fallo, el argumento aparece."""
    roto = (
        "python -m pytest -q \\\n"
        "  --ignore=tests/test_etl_postgresql.py \\n"
        "            --ignore=tests/test_autenticacion_postgresql.py\n"
    )

    argumentos = argumentos_como_bash(roto)

    assert [a for a in argumentos[3:] if not a.startswith("-")] == ["n"]


def test_ninguna_linea_del_bloque_termina_en_barra_sin_continuacion():
    """Una barra invertida seguida de espacios tampoco continua la linea."""
    for linea in bloque_run(NOMBRE_DEL_STEP).splitlines():
        cuerpo = linea.rstrip("\r")
        if "\\" in cuerpo:
            assert cuerpo.endswith("\\"), f"Continuacion rota: {cuerpo!r}"


def test_cada_suite_de_postgresql_queda_fuera_del_bloque_offline():
    """Una suite nueva de PostgreSQL no puede colarse en la capa sin servidor."""
    ignorados = {
        a.split("=", 1)[1]
        for a in argumentos_como_bash(bloque_run(NOMBRE_DEL_STEP))
        if a.startswith("--ignore=")
    }
    suites = {
        f"tests/{archivo.name}"
        for archivo in DIRECTORIO_PRUEBAS.glob("test_*_postgresql.py")
    }

    assert suites
    assert suites <= ignorados, f"Sin excluir del bloque offline: {suites - ignorados}"


# ---------------------------------------------------------------------------
# Los heredocs de Python del workflow, compilados de verdad
# ---------------------------------------------------------------------------
#
# Existe por un defecto que ninguna otra comprobacion veia: un step incrusta un
# programa de Python con ``python - <<'PY' ... PY``, y para YAML ese programa es
# un bloque de texto cualquiera. Una edicion dejo dentro un bloque con sangria
# sobrante y una f-string con las comillas mal cerradas. El YAML seguia siendo
# valido, el workflow seguia siendo valido, y el defecto solo aparecia al
# ejecutar el job -- donde el interprete abortaba con ``IndentationError`` antes
# de tocar la base de datos.
#
# Compilar no ejecuta nada: ``compile()`` analiza y genera bytecode sin correr
# una sola linea, asi que esta prueba no conecta a ningun sitio ni necesita
# servicios. Lo que demuestra es lo unico que hacia falta y faltaba: que el
# texto que bash le va a entregar a Python es Python.

HEREDOC = re.compile(r"<<'PY'\n(.*?)\n\s*PY(?:\n|$)", re.S)
NOMBRE_DE_STEP = re.compile(r"- name: (.+)")


def heredocs_de_python() -> list[tuple[str, str]]:
    """``(nombre del step, programa)`` por cada heredoc del workflow."""
    texto = RUTA_CI.read_text(encoding="utf-8")
    nombres = [(m.start(), m.group(1).strip()) for m in NOMBRE_DE_STEP.finditer(texto)]

    encontrados = []
    for bloque in HEREDOC.finditer(texto):
        anteriores = [nombre for pos, nombre in nombres if pos < bloque.start()]
        # ``textwrap.dedent`` retira la sangria del bloque YAML, que es
        # exactamente lo que hace bash: el heredoc sin comillas de cierre
        # conserva el texto tal cual, incluida esa sangria comun.
        encontrados.append(
            (anteriores[-1] if anteriores else "(sin step)",
             textwrap.dedent(bloque.group(1)) + "\n")
        )
    return encontrados


def test_el_workflow_tiene_heredocs_de_python():
    """Si dejaran de existir, las dos pruebas de abajo pasarian sin mirar nada."""
    assert len(heredocs_de_python()) >= 4


@pytest.mark.parametrize(
    "nombre, programa",
    heredocs_de_python(),
    ids=[nombre for nombre, _ in heredocs_de_python()],
)
def test_cada_heredoc_de_python_compila(nombre, programa):
    try:
        compile(programa, f"<{nombre}>", "exec")
    except SyntaxError as error:
        linea = (error.text or "").rstrip()
        pytest.fail(
            f"El programa incrustado en el step {nombre!r} no compila: "
            f"{type(error).__name__}: {error.msg} (linea {error.lineno}: {linea!r}). "
            "El job fallaria al ejecutarlo, no al validarlo."
        )


def test_el_detector_encuentra_un_heredoc_roto():
    """El detector sirve solo si falla cuando tiene que fallar."""
    with pytest.raises(SyntaxError):
        compile("if True:\n    x = 1\n        y = 2\n", "<roto>", "exec")
