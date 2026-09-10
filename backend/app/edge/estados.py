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

There is no ``EN_PROCESO``, no lease and no claim marker. What keeps two
simultaneous senders from corrupting the state is the conditional ``UPDATE`` in
:mod:`app.edge.outbox`, not a fourth state.
"""

from __future__ import annotations

import enum


class EstadoEntrega(str, enum.Enum):
    """Whether a captured package has reached the API yet."""

    PENDIENTE = "PENDIENTE"
    ENVIADO = "ENVIADO"
    FALLIDO = "FALLIDO"


# Sorted so the generated CHECK constraint is byte-stable across runs: the
# schema verification compares the stored definition with the one this package
# would create, and a set iterated in hash order would make that comparison
# depend on the interpreter.
VALORES_DE_ESTADO: tuple[str, ...] = tuple(
    sorted(estado.value for estado in EstadoEntrega)
)
