"""Clinical reads, filtered twice: here and in PostgreSQL (SCRUM-98, sub-phase 4).

This module answers three questions and no others: which pregnancy episodes the
caller may read, which monitoring sessions belong to one of them, and which
readings belong to one of those sessions. It never takes an ``id_paciente`` or an
``id_medico`` from the client -- both come from :class:`ContextoClinico`, which
``app.api.dependencias`` derived from the token and the relations PostgreSQL
holds.

**Two layers, on purpose.** Every query below carries an explicit predicate, and
every table it reads also has a row-level policy that carries the same rule. The
duplication is the design: the policies are what protects the data from a query
this module forgets to filter, and the predicates are what protects it from a
policy that a migration drops, a role that is granted BYPASSRLS by accident, or
an owner that stops being subject to FORCE. Neither layer is a reason to relax
the other, and a test suite connected as ``fetalalert_api`` exercises both at
once.

**The two scopes, as approved.**

* A **PACIENTE** reads every pregnancy of her own profile, including the finished
  ones, and the episodes stay separate: sessions are always asked for within one
  ``id_embarazo`` and never span two.
* A **MEDICO** reads a pregnancy only through a ``SeguimientoClinico`` that is
  direct, active and current today. Belonging to the same clinic grants nothing:
  ``medico_clinica`` is not consulted here, and it is not consulted by the
  policies either. Inside an assigned pregnancy the physician reads its whole
  history, not only the part that overlaps the assignment -- an assignment is
  permission to follow the episode, and half an episode is not a clinical record.

The physician's predicate is ``seguridad.embarazo_en_seguimiento_vigente``, the
same SECURITY DEFINER helper the policy calls. Not because the rule is written
once -- it is written twice, in two places that must agree -- but because the
account that serves requests has no privilege at all on
``operacional.seguimiento_clinico`` and must not be given one: the helper returns
a boolean about a pregnancy the caller already named, and nothing else.

**A foreign resource and a non-existent one answer the same thing.** Not the same
status code only: the same sentence, differing at most in the identifier the
caller itself sent. Telling them apart would turn every route here into an oracle
for which pregnancies and which sessions exist.

Reads only. Nothing in this module commits, rolls back or writes.

All data in this project is simulated and completely fictitious.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.catalogos import Semaforo, TiempoGestacional
from app.models.clinico import Embarazo
from app.models.enums import NombreRol
from app.models.monitoreo import LecturaBiometrica, SesionMonitoreo
from app.services.auditoria import ENTIDAD_EMBARAZO, ENTIDAD_SESION_MONITOREO
from app.services.contexto import ContextoClinico

# El mismo texto que la ingesta usa para un embarazo que no se puede escribir, y
# por la misma razon: ajeno e inexistente no pueden distinguirse desde fuera.
MENSAJE_EMBARAZO = "No existe un embarazo con id_embarazo={id_embarazo}."
MENSAJE_SESION = "No existe una sesion de monitoreo con id_sesion={id_sesion}."


class RecursoClinicoInexistente(LookupError):
    """El recurso no existe, o existe y no esta al alcance de quien pregunta.

    Una sola excepcion para los dos casos, a proposito. Dos excepciones
    invitarian a que alguna capa las tradujera a dos respuestas distintas, y esa
    diferencia es exactamente la que no puede salir del proceso.

    Lleva ``entidad`` y ``id_solicitado`` para que el router pueda auditar la
    denegacion sin volver a adivinar por que recurso se preguntaba. Los dos son
    datos que quien llama envio: la ruta que eligio y el identificador que puso
    en ella. Nada de lo que la base respondio viaja aqui, porque eso es
    justamente lo que la denegacion no puede revelar.
    """

    def __init__(self, mensaje: str, *, entidad: str, id_solicitado: int) -> None:
        super().__init__(mensaje)
        self.entidad = entidad
        self.id_solicitado = id_solicitado


def _alcance_de(contexto: ContextoClinico):
    """El predicado que decide que embarazos ve este contexto.

    Devuelve ``None`` cuando el contexto no alcanza ninguno, que no es lo mismo
    que un predicado falso: quien llama debe poder distinguir «no hay nada que
    consultar» de «consulta esto». Se resuelve por el rol y no por que campo
    venga relleno, para que un contexto con ambos perfiles -- que el resolutor ya
    rechaza -- no pudiera colarse por la rama equivocada si algun dia llegara.
    """
    if contexto.rol is NombreRol.PACIENTE:
        if contexto.id_paciente is None:
            return None
        return Embarazo.id_paciente == contexto.id_paciente

    if contexto.rol is NombreRol.MEDICO:
        if contexto.id_medico is None:
            return None
        # El helper deriva el medico del contexto instalado en la transaccion, no
        # del argumento: no hay forma de preguntar «y los de aquel otro».
        return func.seguridad.embarazo_en_seguimiento_vigente(
            Embarazo.id_embarazo
        ).is_(True)

    # ADMIN y cualquier rol futuro: alcance clinico vacio. Las rutas ni siquiera
    # admiten a ADMIN, asi que esto es la segunda de dos negativas.
    return None


def _embarazos_visibles(contexto: ContextoClinico) -> Select | None:
    alcance = _alcance_de(contexto)
    if alcance is None:
        return None
    return select(Embarazo).where(alcance)


def listar_embarazos(
    sesion_bd: Session, contexto: ContextoClinico
) -> list[Embarazo]:
    """Los episodios que este contexto puede leer, del mas reciente al mas viejo.

    Para una gestante, todos los suyos, cerrados incluidos. Para un medico, los
    que sigue hoy. Una lista vacia es una respuesta legitima y no un error: una
    cuenta recien aprovisionada todavia no tiene nada que mirar.
    """
    consulta = _embarazos_visibles(contexto)
    if consulta is None:
        return []
    return list(
        sesion_bd.execute(
            consulta.order_by(Embarazo.fecha_inicio.desc(), Embarazo.id_embarazo.desc())
        ).scalars()
    )


def exigir_embarazo_visible(
    sesion_bd: Session, contexto: ContextoClinico, id_embarazo: int
) -> None:
    """Confirma que el episodio existe **y** esta al alcance, o levanta el 404.

    Se pregunta por la fila y se cuenta cuantas vuelven, en vez de leerla y
    comparar campos en Python: bajo las politicas una fila ajena sencillamente no
    esta, y contar es lo que hace imposible filtrar la diferencia por descuido.
    """
    consulta = _embarazos_visibles(contexto)
    if consulta is not None:
        visible = sesion_bd.execute(
            consulta.with_only_columns(Embarazo.id_embarazo).where(
                Embarazo.id_embarazo == id_embarazo
            )
        ).first()
        if visible is not None:
            return
    raise RecursoClinicoInexistente(
        MENSAJE_EMBARAZO.format(id_embarazo=id_embarazo),
        entidad=ENTIDAD_EMBARAZO,
        id_solicitado=id_embarazo,
    )


def listar_sesiones(
    sesion_bd: Session, contexto: ContextoClinico, id_embarazo: int
) -> list[SesionMonitoreo]:
    """Las sesiones de **un** episodio, una vez confirmado que es legible.

    El ``id_embarazo`` se comprueba antes de consultar nada, de modo que un
    episodio ajeno no llega siquiera a producir una lista vacia que quien pregunta
    pudiera interpretar como «existe pero no tiene sesiones».
    """
    exigir_embarazo_visible(sesion_bd, contexto, id_embarazo)
    return list(
        sesion_bd.execute(
            select(SesionMonitoreo)
            .where(SesionMonitoreo.id_embarazo == id_embarazo)
            .order_by(
                SesionMonitoreo.fecha_inicio.desc(), SesionMonitoreo.id_sesion.desc()
            )
        ).scalars()
    )


def listar_lecturas(
    sesion_bd: Session, contexto: ContextoClinico, id_sesion: int
) -> list[Mapping[str, object]]:
    """Las lecturas de **una** sesion, si el episodio al que cuelga es legible.

    La sesion se resuelve a su episodio con una consulta que ya esta filtrada por
    la politica de ``sesion_monitoreo``, y el episodio se vuelve a comprobar
    aqui: la segunda comprobacion es la del alcance de este contexto, y no
    depende de que la primera haya filtrado nada.
    """
    id_embarazo = sesion_bd.execute(
        select(SesionMonitoreo.id_embarazo).where(
            SesionMonitoreo.id_sesion == id_sesion
        )
    ).scalar_one_or_none()

    if id_embarazo is None:
        raise RecursoClinicoInexistente(
            MENSAJE_SESION.format(id_sesion=id_sesion),
            entidad=ENTIDAD_SESION_MONITOREO,
            id_solicitado=id_sesion,
        )

    try:
        exigir_embarazo_visible(sesion_bd, contexto, id_embarazo)
    except RecursoClinicoInexistente:
        # El episodio no esta al alcance, pero quien pregunta pidio una sesion:
        # el mensaje tiene que hablar de la sesion, o el cambio de sujeto seria
        # en si mismo la senal de que el embarazo existe.
        raise RecursoClinicoInexistente(
            MENSAJE_SESION.format(id_sesion=id_sesion),
            entidad=ENTIDAD_SESION_MONITOREO,
            id_solicitado=id_sesion,
        ) from None

    # Columnas nombradas y dos joins, en una sola consulta.
    #
    # No se devuelven entidades del ORM: hacerlo obligaria a que la capa HTTP
    # navegara ``lectura.semaforo.codigo_nivel``, y esa navegacion es una
    # consulta por fila -- el N+1 clasico -- sobre una serie que puede tener
    # cientos de lecturas. Tampoco se cargan con ``joinedload``, porque eso
    # traeria las filas de catalogo enteras cuando lo que hace falta son dos
    # columnas.
    #
    # Los dos catalogos son ``INNER JOIN`` a proposito: las dos claves foraneas
    # son ``NOT NULL``, asi que una lectura sin catalogo no existe, y un
    # ``LEFT JOIN`` aqui solo serviria para que un dato roto pasara inadvertido
    # como un nulo en la respuesta.
    #
    # Ninguno de los dos exige privilegio nuevo: la revision de RLS ya concede
    # SELECT sobre ``semaforo`` y ``tiempo_gestacional`` al rol de la API, y
    # ninguna de las dos tablas lleva politicas -- son catalogos, no datos de
    # nadie.
    return list(
        sesion_bd.execute(
            select(
                LecturaBiometrica.id_lectura,
                LecturaBiometrica.fecha_hora_captura,
                Semaforo.codigo_nivel.label("codigo_semaforo"),
                TiempoGestacional.semana_gestacion,
                LecturaBiometrica.hr_valor,
                LecturaBiometrica.spo2_valor,
                LecturaBiometrica.mov_valor,
            )
            .join(Semaforo, Semaforo.id_semaforo == LecturaBiometrica.id_semaforo)
            .join(
                TiempoGestacional,
                TiempoGestacional.id_tiempo_gest == LecturaBiometrica.id_tiempo_gest,
            )
            .where(LecturaBiometrica.id_sesion == id_sesion)
            .order_by(
                LecturaBiometrica.fecha_hora_captura, LecturaBiometrica.id_lectura
            )
        ).mappings()
    )
