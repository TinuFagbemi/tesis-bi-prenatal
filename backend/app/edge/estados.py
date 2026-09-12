"""The three delivery states of the simulated edge node's outbox (SCRUM-64).

They live in a module of their own for one reason: **there must be exactly one
declaration of what a delivery state is**. The SQL CHECK that limits the column,
the repository that writes transitions, the CLI that prints a summary and the
tests that assert on them all read the vocabulary from here, so a fourth state
cannot be invented in one place and go unnoticed in the others.

**This is not ``EstadoSesion``, and the two must never be mixed.** Both happen to
contain a member called ``PENDIENTE``, and that is the whole of the resemblance.
``EstadoSesion`` describes a *clinical* session moving through the monitoring
pipeline and is persisted in PostgreSQL as part of the package; this enum
describes whether the **edge** has managed to hand that package to the API, and
never leaves the local SQLite file. Importing one where the other belongs would
tie an operational retry to a clinical meaning.

The vocabulary is deliberately small and closed:

* ``PENDIENTE`` -- captured locally, never confirmed by the API, eligible for an
  explicit send attempt.
* ``ENVIADO`` -- the API confirmed the operation, as a first execution or as an
  equivalent replay, and the answer satisfied the contract. **Terminal.**
* ``FALLIDO`` -- the last attempt could not be confirmed or was refused. The row
  and its key are kept; ``reintentable`` says whether another explicit pass may
  try again or a person has to look at it.

There is no ``EN_PROCESO``, no lease and no claim marker **in this enum**. What
keeps two simultaneous senders from corrupting the state is the conditional
``UPDATE`` in :mod:`app.edge.outbox`, not a fourth state. SCRUM-65 adds a local
lease, and it does not change that: the lease is expressed as «there is an open,
unreconciled attempt», a fact the attempt history already records, not as a new
word here.

**Two more closed vocabularies live here (SCRUM-65)**, for the same reason the
first one does: they limit a column with a ``CHECK`` rendered from the enum, and
a value added in Python must not be able to stay unrepresented in the database.
``ResultadoEntrega`` used to live in :mod:`app.edge.cliente`, which imports
httpx and the Pydantic contracts; the storage module cannot depend on either
just to name three strings, so the declaration moved here and the client
re-exports it.
"""

from __future__ import annotations

import enum


class EstadoEntrega(str, enum.Enum):
    """Whether a captured package has reached the API yet."""

    PENDIENTE = "PENDIENTE"
    ENVIADO = "ENVIADO"
    FALLIDO = "FALLIDO"


class ResultadoEntrega(enum.Enum):
    """What one attempt achieved, from the edge's point of view.

    ``REINTENTABLE`` covers two situations that are indistinguishable from here
    and must stay so: the server said it failed on its side, and the outcome is
    genuinely unknown because the answer never arrived. The second is told apart
    by ``codigo_http is None``, not by a fourth member -- inventing one would
    claim a certainty the edge does not have.
    """

    ENTREGADO = "ENTREGADO"
    REINTENTABLE = "REINTENTABLE"
    RECHAZADO = "RECHAZADO"


class MotivoRevision(str, enum.Enum):
    """Why an event stopped being retried automatically and needs a person.

    Kept apart from ``EstadoEntrega`` on purpose. All three mean the same
    *state* -- ``FALLIDO`` with ``reintentable = 0`` -- and differ only in the
    reason, which is what a trace has to be able to say out loud. Deriving the
    reason instead of storing it would make it depend on a configuration that
    can change between runs.
    """

    RECHAZO_PERMANENTE = "RECHAZO_PERMANENTE"
    AGOTAMIENTO = "AGOTAMIENTO"
    AGOTAMIENTO_HEREDADO = "AGOTAMIENTO_HEREDADO"


# Sorted so the generated CHECK constraint is byte-stable across runs: the
# schema verification compares the stored definition with the one this package
# would create, and a set iterated in hash order would make that comparison
# depend on the interpreter.
VALORES_DE_ESTADO: tuple[str, ...] = tuple(
    sorted(estado.value for estado in EstadoEntrega)
)

VALORES_DE_RESULTADO: tuple[str, ...] = tuple(
    sorted(resultado.value for resultado in ResultadoEntrega)
)

VALORES_DE_MOTIVO: tuple[str, ...] = tuple(
    sorted(motivo.value for motivo in MotivoRevision)
)
