"""Who the caller is, resolved against PostgreSQL on every request (SCRUM-70).

**PostgreSQL is the authority on the role, not the token.** The token says only
*which account* is speaking; whether that account still exists, is still active
and still holds the role it held at issuance is read here, every time. The cost
is one indexed primary-key lookup with a join; what it buys is that a
deactivated account, a deleted one or one whose role changed stops having the
old privileges on its **next** request instead of when its token expires. This
MVP implements no revocation, so without this the window would be the full
token lifetime.

**Reads only.** Nothing in this module commits, rolls back or writes. The
transaction belongs to whoever opened the session, exactly as in
``app.services.ingesta`` and ``app.services.idempotencia``.

**The inactive account is not a shortcut.** :func:`autenticar` verifies the
password with Argon2id *before* it looks at ``activo``, and an unknown email
reaches the same verification through ``None``. Unknown account, wrong password
and deactivated account therefore travel the same path and produce the same
answer -- ``None`` -- which the router turns into one indistinguishable 401.

All accounts are fictitious and simulated.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalogos import Rol
from app.models.enums import NombreRol
from app.models.seguridad import Usuario
from app.services.correo import EmailNoCanonizable, canonizar_email
from app.services.passwords import verificar


@dataclass(frozen=True)
class PrincipalAutenticado:
    """The minimum an authorised request needs to know about its caller.

    Typed and frozen rather than a dictionary, so a router cannot reach for a
    field that was never meant to travel and a handler cannot mutate the
    identity it was handed.

    Two fields, and the absence of the rest is the point: no email, no digest,
    no name, no clinical profile, no list of patients or clinics. ``id_usuario``
    is also precisely the key SCRUM-71 will need to resolve ``usuario_paciente``
    and ``usuario_medico``, so row-level isolation can be built on this without
    redesigning the token.
    """

    id_usuario: int
    rol: NombreRol


def _consulta_de_cuenta():
    """Account and its current role, in one statement.

    ``password_hash`` and ``activo`` travel with it because both are needed on
    the login path and reading them separately would mean two round trips to
    decide one answer.
    """
    return select(
        Usuario.id_usuario,
        Usuario.password_hash,
        Usuario.activo,
        Rol.nombre_rol,
    ).join(Rol, Usuario.id_rol == Rol.id_rol)


def resolver_principal(sesion_bd: Session, id_usuario: int) -> PrincipalAutenticado | None:
    """The principal for a validated token's subject, or ``None``.

    ``None`` has three meanings and the caller must not tell them apart: the
    account was deleted, it never existed, or it has been deactivated. All three
    are "this token no longer speaks for anybody", and all three are answered
    401.
    """
    fila = sesion_bd.execute(
        _consulta_de_cuenta().where(Usuario.id_usuario == id_usuario)
    ).one_or_none()

    if fila is None or not fila.activo:
        return None

    return PrincipalAutenticado(id_usuario=fila.id_usuario, rol=fila.nombre_rol)


def autenticar(sesion_bd: Session, email: str, password: str) -> PrincipalAutenticado | None:
    """Exchange credentials for a principal, or ``None``, without saying why.

    The order of the three lines below is the anti-enumeration design, and it
    only works read in that order:

    1. the account is looked up -- it may not exist, and that is not an early
       return;
    2. the password is verified **always**, against the stored digest or, when
       there is no account, against the dummy that ``app.services.passwords``
       built once at import;
    3. only then are the three failures collapsed into one ``None``.

    An early return for a missing account, or for a deactivated one, would make
    that case measurably cheaper than a wrong password and turn this endpoint
    into an account oracle. There is no such return here.

    **The email is looked up in canonical form (SCRUM-97)**, through the same
    ``canonizar_email`` provisioning stores it with. The HTTP contract already
    delivers it canonical, and the helper is idempotent, so this is the same
    value; it is applied again so no internal caller can look an account up
    under a rule the stored addresses do not follow. A value with no canonical
    form cannot match any account, and it still pays the dummy verification
    before answering ``None``.
    """
    try:
        email = canonizar_email(email)
    except EmailNoCanonizable:
        verificar(None, password)
        return None

    fila = sesion_bd.execute(
        _consulta_de_cuenta().where(Usuario.email == email)
    ).one_or_none()

    digest = None if fila is None else fila.password_hash
    coincide = verificar(digest, password)

    if fila is None or not coincide or not fila.activo:
        return None

    return PrincipalAutenticado(id_usuario=fila.id_usuario, rol=fila.nombre_rol)
