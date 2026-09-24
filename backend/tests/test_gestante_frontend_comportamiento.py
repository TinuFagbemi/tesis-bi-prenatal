"""El comportamiento de ``app.js``, ejecutado de verdad.

Las pruebas de ``test_gestante_frontend.py`` leen los archivos como texto. Lo
que no pueden ver --que Inicio e Historial sean contextos independientes, que
una respuesta tardia no pise la seleccion vigente, que los mensajes de envio
digan solo lo que el adaptador confirmo, que cerrar sesion pida
confirmacion-- lo comprueba ``frontend/gestante/pruebas/app.comportamiento.test.js``
con ``node --test``, sobre el ``app.js`` real y un DOM minimo sin dependencias.

Este modulo solo lo lanza. Si ``node`` no esta instalado, se omite con el
motivo a la vista en lugar de darse por superada.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PRUEBA_JS = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "gestante"
    / "pruebas"
    / "app.comportamiento.test.js"
)


def test_el_comportamiento_de_app_js_pasa_en_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no está instalado; no se pueden ejecutar las pruebas de app.js")

    resultado = subprocess.run(
        [node, "--test", str(PRUEBA_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
