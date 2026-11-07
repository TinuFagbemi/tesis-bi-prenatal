"""Lee los PDF que Chrome descargó y resume lo que contienen.

Necesita pypdf (y pypdfium2 + pillow para «png») en un entorno virtual
propio, fuera del repositorio: no son dependencias del proyecto.

Uso: <venv>/Scripts/python -I -X utf8 verificar_pdf.py <dir resultado> [png]
Añade a informe.json, por descarga: páginas, título, embarazo, marca de
descarga y filas de lecturas. Con «png» renderiza la primera página de
algunos archivos para comprobar su aspecto.
"""
import json
import re
import sys
from pathlib import Path

import pypdf

DIR = Path(sys.argv[1])
PNG = len(sys.argv) > 2 and sys.argv[2] == "png"
informe = json.loads((DIR / "informe.json").read_text(encoding="utf-8"))


def resumen(archivo):
    lector = pypdf.PdfReader(archivo)
    texto = "\n".join(p.extract_text() or "" for p in lector.pages)
    plano = re.sub(r"\s+", " ", texto)
    titulo = re.search(r"(Resumen|Historial) de mis registros", plano)
    embarazo = re.search(r"Embarazo desde \d+ \w+\.? \d{4} — (En curso|Finalizado)", plano)
    marca = re.search(r"Descargado el [^()]+\(hora de Panamá\)", plano)
    incluidas = re.search(r"Se incluyen (las|la) (\d+)", plano) or re.search(r"(las|la) (\d+) lecturas? más recientes", plano)
    return {
        "cabecera": Path(archivo).read_bytes()[:5].decode("latin-1"),
        "paginas": len(lector.pages),
        "titulo": titulo.group(0) if titulo else None,
        "embarazo": embarazo.group(0) if embarazo else None,
        "marca": marca.group(0).strip() if marca else None,
        "lecturas_incluidas": int(incluidas.group(2)) if incluidas else None,
        "texto_extraible": len(plano) > 200,
    }


for esc in informe["escenarios"]:
    for d in esc["descargas"]:
        if d.get("archivo"):
            d["pdf"] = resumen(d["archivo"])

if PNG:
    import pypdfium2 as pdfium

    for esc in informe["escenarios"][:1]:
        for i, d in enumerate(esc["descargas"]):
            if d.get("archivo"):
                pagina = pdfium.PdfDocument(d["archivo"])[0]
                pagina.render(scale=1.5).to_pil().save(DIR / f"pdf-{i + 1}-pagina1.png")

(DIR / "informe.json").write_text(json.dumps(informe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
for esc in informe["escenarios"]:
    print("==", esc["nombre"], "|", esc["instrumentacion"], "|", esc["resultado"],
          "| errores de página:", len(esc["errores_pagina"]))
    for p in esc["pasos"]:
        extra = {k: v for k, v in p.items() if k not in ("tras", "ui")}
        u = p["ui"]
        print("   -", p["tras"], extra, "| botón deshab.:", u["boton_deshabilitado"], "| estado:", u["estado"],
              "| vista:", "resumen" if u["vista_resumen"] else ("login" if u["vista_login"] else "otra"),
              "|", u["documento"], "|", u["titulo"])
    for d in esc["descargas"]:
        print("   ↓", d["nombre"], d["estado"], d["url_esquema"], d["bytes"], "B", d.get("pdf"))
