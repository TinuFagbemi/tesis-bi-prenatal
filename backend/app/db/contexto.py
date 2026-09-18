"""Handing the caller's identity to PostgreSQL, for the length of one transaction.

Row-level security needs to know, inside the database, who is asking. This
module is the only place that tells it, and the whole design is in three
decisions.

**One value travels, and it is not the role.** Only ``fetalalert.id_usuario``.
The role, the patient id, the physician id and the clinic are all derivable from
it through relations PostgreSQL already holds, and every one of them would be a
second thing that could go stale or be forged. A policy that trusted a role in a
session variable would be a policy that trusts whoever set it; a policy that
resolves the role from ``operacional.usuario`` cannot be told a different one.

**It is transaction-local, not session-local.** ``set_config(..., true)`` is the
third argument doing the work: PostgreSQL discards the value when the
transaction ends, on commit and on rollback alike. That is what makes a
connection safe to hand back to the pool. A plain ``SET`` would survive the
checkin and the next request on that connection would inherit an identity
nobody granted it -- the exact failure this ticket exists to prevent. There is
no ``SET`` anywhere in this module and there must never be one.

**The value is validated before it is sent.** It is an integer that came from a
verified token and a row in ``operacional.usuario``, so it cannot be hostile
today; it is checked anyway, because the cost is one comparison and the
alternative is an invariant maintained by convention.

**What this does not do.** It does not decide anything. Installing the context
grants nothing by itself: without policies there is nothing reading the value,
and with them the policies decide. Nor does it clear anything on the way out --
there is nothing to clear, which is the point.

All accounts and data in this project are simulated and fictitious.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

# The one custom setting this project defines. The prefix is mandatory:
# PostgreSQL only accepts a custom parameter that carries one, and it also keeps
# the name from ever colliding with a built-in.
NOMBRE_DEL_CONTEXTO = "fetalalert.id_usuario"

# ``true`` is the local flag. Written as a literal in the statement and not as a
# bound parameter because it is a property of this project's design, not an
# input: there is no code path that installs a session-wide context.
_INSTALAR = text(f"SELECT set_config('{NOMBRE_DEL_CONTEXTO}', :valor, true)")

# ``true`` in ``current_setting`` is *missing_ok*: an unset parameter comes back
# NULL instead of raising. A connection that never had a context installed is an
# ordinary situation -- the login path, the audit session -- not an error.
_LEER = text(f"SELECT current_setting('{NOMBRE_DEL_CONTEXTO}', true)")


class ContextoInvalido(ValueError):
    """The value offered as an identity is not one, so nothing was installed."""


def _validar(id_usuario: int) -> str:
    """The canonical decimal form of a usable account id.

    ``bool`` is rejected explicitly: it is a subclass of ``int`` in Python, so
    ``True`` would otherwise be installed as the identity of account 1.
    """
    if isinstance(id_usuario, bool) or not isinstance(id_usuario, int):
        raise ContextoInvalido("El contexto solo admite un identificador entero.")
    if id_usuario <= 0:
        raise ContextoInvalido("El contexto solo admite un identificador positivo.")
    return str(id_usuario)


def instalar_contexto(sesion_bd: Session, id_usuario: int) -> None:
    """Make this account the identity of the current transaction.

    It must run on the **same** session -- and therefore the same connection --
    that will issue the protected statements. Installing it on one connection
    and querying on another leaves the second one with no identity, which under
    the policies of the later sub-phases means no rows: a failure that is loud
    rather than silent, but a failure all the same.

    Does not commit. SQLAlchemy begins the transaction on this statement if none
    is open yet, and the value lives exactly as long as that transaction.
    """
    sesion_bd.execute(_INSTALAR, {"valor": _validar(id_usuario)})


def leer_contexto(sesion_bd: Session) -> int | None:
    """The identity installed in this transaction, or ``None``.

    Exists so a test can observe what production installs, instead of asserting
    against a second implementation of the same query. Returns ``None`` both
    when nothing was installed and when the parameter was reset -- the two are
    the same situation and neither grants anything.
    """
    crudo = sesion_bd.execute(_LEER).scalar()
    if crudo is None or crudo == "":
        return None
    return int(crudo)
