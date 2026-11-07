"""The two legal texts a patient accepts when she registers herself (SCRUM-110).

**One source for what is shown and what is recorded.** The texts live next to
this module, as plain files, and the API serves them from here
(``GET /api/v1/registro/documentos``). The interface displays exactly what it
receives, and the acceptance stored in PostgreSQL carries the version declared
below and the SHA-256 of that very text. A text shown from one place and hashed
from another could silently drift apart; this way they cannot.

**Plain ``.txt`` and not ``.md`` on purpose.** ``backend/.dockerignore`` excludes
``*.md`` from the API image, so a Markdown file would exist in the repository
and be missing in the container. Loading happens at import, so a missing or
unreadable file stops the API from starting instead of letting it accept
registrations it cannot document.

**The digest does not depend on the checkout.** Git on Windows may convert line
endings on checkout. The text is normalised to ``\\n`` before hashing, so the
same version yields the same digest on every machine and in the container.

**Changing a text means a new version.** Editing a file without changing its
``version`` keeps the old label on a different text; a test pins each digest so
that cannot happen unnoticed. A new version makes every later registration
record the new one; acceptances already stored keep the version they accepted.

All data handled by this prototype is fictitious and simulated.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from app.models.enums import DocumentoLegal

DIRECTORIO_DE_TEXTOS = Path(__file__).resolve().parent


@dataclass(frozen=True)
class DocumentoVigente:
    """A legal text in force: what it is, which version, its words and digest."""

    documento: DocumentoLegal
    version: str
    titulo: str
    texto: str
    sha256: str


def normalizar(texto: str) -> str:
    """The same words with ``\\n`` line endings, whatever the checkout produced."""
    return texto.replace("\r\n", "\n").replace("\r", "\n")


def _leer(nombre: str) -> str:
    return normalizar((DIRECTORIO_DE_TEXTOS / nombre).read_bytes().decode("utf-8"))


def huella(texto: str) -> str:
    """SHA-256, in lowercase hex, of a text already normalised to ``\\n``."""
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def _documento(documento: DocumentoLegal, version: str, titulo: str, archivo: str) -> DocumentoVigente:
    texto = _leer(archivo)
    return DocumentoVigente(
        documento=documento,
        version=version,
        titulo=titulo,
        texto=texto,
        sha256=huella(texto),
    )


# Version labels. They fit ``aceptacion_documento.version`` (40 characters).
VERSION_POLITICA_PRIVACIDAD = "2026-11-v3"
VERSION_TERMINOS_CONDICIONES = "2026-11-v3"

DOCUMENTOS_VIGENTES: MappingProxyType[DocumentoLegal, DocumentoVigente] = MappingProxyType(
    {
        DocumentoLegal.POLITICA_PRIVACIDAD: _documento(
            DocumentoLegal.POLITICA_PRIVACIDAD,
            VERSION_POLITICA_PRIVACIDAD,
            "Política de privacidad",
            "politica_privacidad.txt",
        ),
        DocumentoLegal.TERMINOS_CONDICIONES: _documento(
            DocumentoLegal.TERMINOS_CONDICIONES,
            VERSION_TERMINOS_CONDICIONES,
            "Términos y condiciones",
            "terminos_condiciones.txt",
        ),
    }
)


def vigente(documento: DocumentoLegal) -> DocumentoVigente:
    return DOCUMENTOS_VIGENTES[documento]
