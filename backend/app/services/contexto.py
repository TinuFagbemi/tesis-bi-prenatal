"""Which clinical profile the authenticated caller is, resolved in PostgreSQL.

SCRUM-70 answers *who is speaking* -- an account id and a role, revalidated on
every request. That is not enough to decide which rows anybody may see. This
module answers the next question: **which clinical profile that account is**, so
the isolation of SCRUM-98 has something to filter by.

**The client never supplies any of this.** Not the patient id, not the physician
id, not the clinic. Those arrive only as query targets, and a target is not a
proof of authorisation. Everything here is derived from
``PrincipalAutenticado.id_usuario`` and the relations PostgreSQL holds.

**Fail closed, and the shape of the data says when.** SCRUM-97 made the bridges
one-to-one in both directions and added a deferred constraint trigger keeping
role and link in agreement: a PACIENTE account has exactly one
``usuario_paciente`` row and no ``usuario_medico`` row, a MEDICO the opposite,
an ADMIN neither. That invariant can still be violated by a database someone
edited by hand, or by a migration that ran half way, so it is **checked** rather
than assumed. Anything that does not match exactly -- a missing link, a link the
role does not allow, both links at once -- resolves to no context at all, and no
context means no rows.

**What this module does not decide.** Which pregnancies a patient may read, or
which patients a physician follows: those are the policies of the later
sub-phases. Here the answer is only ``id_paciente`` or ``id_medico``, the two
values every one of those policies will start from.

**The account state is already settled upstream.** ``resolver_principal`` refuses
a deleted or deactivated account before this is ever called, so an inactive
account never reaches a context. The check is not repeated here: doing it twice
would suggest either place could be skipped.

All accounts and data in this project are simulated and fictitious.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import NombreRol
from app.models.seguridad import UsuarioMedico, UsuarioPaciente
from app.services.principal import PrincipalAutenticado


class ContextoNoResoluble(Exception):
    """The caller's clinical profile could not be established, so there is none.

    Carries a stable, generic reason. It never names the account, the profile it
    looked for or what it found: the caller of an endpoint has no business
    learning whether a link exists, and a log line is not the place to publish
    the shape of somebody's record.
    """

    def __init__(self, motivo: str) -> None:
        super().__init__(motivo)
        self.motivo = motivo


# The three ways resolution fails. They are separate strings because they mean
# different things to whoever debugs the cluster, and identical in effect: no
# context.
SIN_VINCULO = "la cuenta no tiene un perfil clinico asociado"
VINCULO_AJENO_AL_ROL = "la cuenta tiene un perfil clinico que su rol no admite"
VINCULO_AMBIGUO = "la cuenta tiene mas de un perfil clinico"


@dataclass(frozen=True)
class ContextoClinico:
    """The caller, plus the one profile identifier their role is allowed to have.

    Exactly one of ``id_paciente`` and ``id_medico`` is set for a clinical role,
    and neither for ADMIN. Frozen, so a handler cannot widen the context it was
    handed.
    """

    id_usuario: int
    rol: NombreRol
    id_paciente: int | None = None
    id_medico: int | None = None

    @property
    def es_paciente(self) -> bool:
        return self.id_paciente is not None

    @property
    def es_medico(self) -> bool:
        return self.id_medico is not None


def _perfil_unico(sesion_bd: Session, modelo, columna, id_usuario: int) -> int | None:
    """The single profile id linked to this account, or ``None``.

    Two rows is impossible while the bridge's primary key holds, so the query
    asks for at most two and refuses anything other than one: a corrupted
    database must not resolve to an arbitrary profile because the code took the
    first row it saw.
    """
    filas = sesion_bd.execute(
        select(columna).where(modelo.id_usuario == id_usuario).limit(2)
    ).scalars().all()

    if len(filas) > 1:
        raise ContextoNoResoluble(VINCULO_AMBIGUO)
    return filas[0] if filas else None


def resolver_contexto(
    sesion_bd: Session, principal: PrincipalAutenticado
) -> ContextoClinico:
    """The caller's clinical context, or a refusal.

    Both bridges are read for every role, and that is deliberate: the cheap
    version would look only at the one the role expects and would then accept a
    PACIENTE account that also carries a physician link. What makes the
    invariant worth anything is checking the absence as well as the presence.

    Reads only. Nothing here commits, rolls back or writes; the transaction
    belongs to whoever opened the session, exactly as in
    ``app.services.principal``.
    """
    id_paciente = _perfil_unico(
        sesion_bd, UsuarioPaciente, UsuarioPaciente.id_paciente, principal.id_usuario
    )
    id_medico = _perfil_unico(
        sesion_bd, UsuarioMedico, UsuarioMedico.id_medico, principal.id_usuario
    )

    if id_paciente is not None and id_medico is not None:
        raise ContextoNoResoluble(VINCULO_AMBIGUO)

    if principal.rol is NombreRol.PACIENTE:
        if id_paciente is None:
            raise ContextoNoResoluble(SIN_VINCULO)
        if id_medico is not None:
            raise ContextoNoResoluble(VINCULO_AJENO_AL_ROL)
        return ContextoClinico(
            id_usuario=principal.id_usuario,
            rol=principal.rol,
            id_paciente=id_paciente,
        )

    if principal.rol is NombreRol.MEDICO:
        if id_medico is None:
            raise ContextoNoResoluble(SIN_VINCULO)
        if id_paciente is not None:
            raise ContextoNoResoluble(VINCULO_AJENO_AL_ROL)
        return ContextoClinico(
            id_usuario=principal.id_usuario,
            rol=principal.rol,
            id_medico=id_medico,
        )

    if principal.rol is NombreRol.ADMIN:
        # ADMIN has no clinical reading in SCRUM-98, so it resolves to a context
        # with no profile rather than to a refusal: it is a valid identity with
        # an empty clinical scope, which is exactly what the policies will see.
        if id_paciente is not None or id_medico is not None:
            raise ContextoNoResoluble(VINCULO_AJENO_AL_ROL)
        return ContextoClinico(id_usuario=principal.id_usuario, rol=principal.rol)

    # Unreachable while ``NombreRol`` has three members, and deliberately not an
    # ``else: return``. A role added without deciding its clinical scope must
    # fail closed rather than inherit the last branch.
    raise ContextoNoResoluble(SIN_VINCULO)
