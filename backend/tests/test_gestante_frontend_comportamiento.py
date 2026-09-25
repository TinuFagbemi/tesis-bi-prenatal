"""El comportamiento de ``app.js``, ejecutado de verdad.

Las pruebas de ``test_gestante_frontend.py`` leen los archivos como texto. Lo
que no pueden ver --que Inicio e Historial sean contextos independientes, que
una respuesta tardia no pise la seleccion vigente, que los mensajes de envio
digan solo lo que el adaptador confirmo, que cerrar sesion pida
confirmacion-- lo comprueba ``frontend/gestante/pruebas/app.comportamiento.test.js``
con ``node --test``, sobre el ``app.js`` real y un DOM minimo sin dependencias.

Este modulo solo lo lanza. Si ``node`` no esta instalado se omite con el
motivo a la vista --y en CI (``CI`` definido) falla--, en lugar de darse
por superada.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

DIRECTORIO_PRUEBAS_JS = (
    Path(__file__).resolve().parents[2] / "frontend" / "gestante" / "pruebas"
)
# Todas las suites de Node de la interfaz: app.js y graficas.js. Las mismas
# que ejecuta el paso de Node del workflow de CI.
PRUEBAS_JS = sorted(DIRECTORIO_PRUEBAS_JS.glob("*.test.js"))


def test_el_comportamiento_de_app_js_pasa_en_node():
    node = shutil.which("node")
    if node is None:
        # En CI la ausencia de Node es un fallo, no una omisión: una prueba que
        # se omite siempre no protege nada.
        if os.environ.get("CI"):
            pytest.fail("node no está disponible en CI; las pruebas de app.js no se ejecutaron")
        pytest.skip("node no está instalado; no se pueden ejecutar las pruebas de app.js")

    assert len(PRUEBAS_JS) >= 2, PRUEBAS_JS
    resultado = subprocess.run(
        [node, "--test", *map(str, PRUEBAS_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
