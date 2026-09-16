"""The one canonical form of an access email (SCRUM-97).

**One rule, one place.** Provisioning a PACIENTE account, provisioning a MEDICO
account, the login body and ``app.services.principal.autenticar`` all call
:func:`canonizar_email` and nothing else. A rule applied at provisioning and not
at login -- or the other way round -- would store ``paciente31@example.com`` and
then refuse ``Paciente31@Example.com`` at the door, or accept two accounts that
a person reads as the same address. Neither may happen, so there is no second
implementation for either side to drift into.

The rule, in the order it is applied::

    email_canonico = email.strip().lower()

1. ``strip()`` first, so the length limit is measured on what is stored and not
   on the padding a client happened to send;
2. ``lower()``;
3. empty after that is refused;
4. whitespace anywhere inside is refused -- after ``strip()`` any that is left
   is internal, and ``paciente 31@example.com`` is not an address somebody
   meant;
5. longer than the column is refused.

**What it deliberately is not.** It is not an email validator: there is no
``EmailStr`` and no extra dependency. Whether the address has a valid domain is
not something this system can act on -- it sends no email -- and a syntax check
would only add a way to be answered differently for addresses that look wrong,
which the login contract avoids on purpose.

PostgreSQL enforces the same result independently: ``ck_usuario_email_canonico``
rejects a stored value that is empty, has whitespace or is not lowercase, and
``uq_usuario_email`` then makes two equivalent addresses the same key. This
module gives the controlled answer; the database closes the race.

All accounts are fictitious and simulated.
"""

from __future__ import annotations

from app.models.seguridad import Usuario

# Read from the column, never restated: ``operacional.usuario.email`` is the
# authority on how long an identifier may be.
LONGITUD_MAXIMA_EMAIL = Usuario.__table__.c.email.type.length

# Fixed messages. None of them quotes the value: a validation error is echoed
# back to the caller and may end up in a log.
MENSAJE_EMAIL_VACIO = "El correo de acceso no puede estar vacio."
MENSAJE_EMAIL_CON_ESPACIOS = "El correo de acceso no puede contener espacios."
MENSAJE_EMAIL_DEMASIADO_LARGO = (
    f"El correo de acceso no puede superar {LONGITUD_MAXIMA_EMAIL} caracteres."
)


class EmailNoCanonizable(ValueError):
    """The value has no acceptable canonical form.

    A ``ValueError`` so Pydantic turns it into an ordinary 422 when it is raised
    inside a validator. It carries one of the fixed messages above and never the
    value.
    """


def canonizar_email(email: str) -> str:
    """The canonical form of ``email``, or :class:`EmailNoCanonizable`.

    Idempotent: a canonical value comes back unchanged, so calling this again
    further down the same path cannot alter what an earlier layer produced.
    """
    canonico = email.strip().lower()

    if not canonico:
        raise EmailNoCanonizable(MENSAJE_EMAIL_VACIO)
    if any(caracter.isspace() for caracter in canonico):
        raise EmailNoCanonizable(MENSAJE_EMAIL_CON_ESPACIOS)
    if len(canonico) > LONGITUD_MAXIMA_EMAIL:
        raise EmailNoCanonizable(MENSAJE_EMAIL_DEMASIADO_LARGO)

    return canonico
