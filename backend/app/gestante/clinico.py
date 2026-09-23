"""Las dos reglas de lectura clinica del portal, y nada mas (SCRUM-72).

Este modulo **no clasifica nada clinico**. Recibe lo que SCRUM-98 ya decidio y
responde dos preguntas de presentacion:

1. cual de los episodios es el embarazo en curso, si es que hay uno;
2. cual es la ultima lectura de un episodio.

Ambas son puras: no abren conexiones, no llaman a la red y no leen
configuracion, asi que cada rama es alcanzable desde una prueba con datos
fabricados.

**El semaforo no se toca.** ``codigo_semaforo`` viaja tal cual desde
``LecturaResumen`` hasta la pantalla. Aqui no hay umbrales, ni comparaciones de
frecuencia cardiaca, ni nada que se parezca a SIM-1.0: esa autoridad es del ETL
y del servidor, y duplicarla seria crear una segunda version libre de separarse
de la primera.

Todos los datos son ficticios y simulados.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.models.enums import EstadoEmbarazo
from app.schemas.clinico import EmbarazoResumen, LecturaResumen, SesionResumen

# El unico estado que un episodio en curso puede declarar. Se compara contra el
# valor del enum del dominio y no contra una cadena escrita a mano.
ESTADO_EN_CURSO = EstadoEmbarazo.ACTIVO.value


@dataclass(frozen=True)
class Episodios:
    """Los episodios de una paciente, clasificados sin inventar nada.

    ``actual`` es ``None`` en **dos** situaciones distintas que la interfaz debe
    poder separar, y por eso existe ``ambiguo``:

    * no hay ningun episodio en curso -- ``ambiguo`` es ``False``;
    * hay mas de uno que dice estarlo -- ``ambiguo`` es ``True``.

    En el segundo caso ``anteriores`` queda vacio y ``todos`` conserva la lista
    completa: cuando no se puede decir cual es el actual, tampoco se puede decir
    cual es anterior, y repartirlos seria inventar la respuesta que falta.
    """

    todos: tuple[EmbarazoResumen, ...]
    actual: EmbarazoResumen | None
    anteriores: tuple[EmbarazoResumen, ...]
    ambiguo: bool

    @property
    def hay_episodios(self) -> bool:
        return bool(self.todos)

    def contiene(self, id_embarazo: int) -> bool:
        """Si ese identificador es uno de los episodios de esta paciente."""
        return any(e.id_embarazo == id_embarazo for e in self.todos)


def en_curso(embarazo: EmbarazoResumen) -> bool:
    """Si un episodio declara estar en curso.

    La regla aprobada, y las dos condiciones son necesarias: un episodio
    ``ACTIVO`` que ademas trae ``fecha_cierre`` se contradice a si mismo, y ante
    una contradiccion el lado seguro es no contarlo como el actual.
    """
    return embarazo.estado_embarazo == ESTADO_EN_CURSO and embarazo.fecha_cierre is None


def clasificar_episodios(embarazos: Iterable[EmbarazoResumen]) -> Episodios:
    """Reparte los episodios en actual y anteriores, o declara la ambiguedad.

    Tres desenlaces, y ninguno elige en silencio:

    * **exactamente uno en curso** -- ese es el actual; el resto, anteriores;
    * **ninguno** -- no hay actual, y todos los episodios son historial. No se
      promueve el mas reciente a «actual»: que no haya embarazo en curso es un
      hecho, no un hueco que rellenar;
    * **dos o mas** -- ambiguo. No se elige ninguno. La paciente puede consultar
      cualquiera de sus episodios, pero la interfaz no llamara «actual» a lo que
      no pudo determinar.

    PostgreSQL no impide hoy dos episodios ``ACTIVO`` de la misma paciente, asi
    que el tercer caso es alcanzable con datos reales y se trata en tiempo de
    ejecucion, no como un imposible.
    """
    todos = tuple(embarazos)
    activos = tuple(e for e in todos if en_curso(e))

    if len(activos) == 1:
        actual = activos[0]
        anteriores = tuple(e for e in todos if e.id_embarazo != actual.id_embarazo)
        return Episodios(todos=todos, actual=actual, anteriores=anteriores, ambiguo=False)

    if not activos:
        return Episodios(todos=todos, actual=None, anteriores=todos, ambiguo=False)

    return Episodios(todos=todos, actual=None, anteriores=(), ambiguo=True)


@dataclass(frozen=True)
class SesionConLecturas:
    """Una sesion de monitoreo junto con las lecturas que se pudieron leer."""

    sesion: SesionResumen
    lecturas: tuple[LecturaResumen, ...]


def _clave_de_captura(lectura: LecturaResumen) -> tuple:
    """El orden que define cual lectura es la ultima.

    ``fecha_hora_captura`` primero, e ``id_lectura`` **solo** como desempate
    determinista cuando dos capturas comparten el instante exacto. El
    identificador no es un sustituto del tiempo: es lo que evita que dos
    ejecuciones sobre los mismos datos devuelvan cosas distintas.
    """
    return (lectura.fecha_hora_captura, lectura.id_lectura)


def ultima_lectura(
    sesiones: Sequence[SesionConLecturas],
) -> LecturaResumen | None:
    """La lectura mas reciente del episodio, sobre **todas** sus lecturas.

    **El maximo es global, no por sesion, y esa es toda la decision.** En un
    sistema pensado para conectividad intermitente, el orden en que las sesiones
    llegan, se sincronizan o se listan no coincide necesariamente con el orden
    en que se capturaron las mediciones. Una sesion mas reciente puede contener
    una lectura anterior a la de otra sesion, y tomar «la ultima lectura de la
    sesion mas reciente» devolveria entonces una medicion que no es la ultima.

    Por eso no se usa ``id_sesion``, ni el orden de la lista, ni la fecha de
    sincronizacion: la unica fuente del orden cronologico es
    ``fecha_hora_captura``.

    Devuelve ``None`` cuando el episodio no tiene ni una sola lectura --sin
    sesiones, o con sesiones que no trajeron ninguna--, que es distinto de tener
    una lectura con valores nulos.
    """
    todas = [lectura for entrada in sesiones for lectura in entrada.lecturas]
    if not todas:
        return None
    return max(todas, key=_clave_de_captura)


def sesion_de_la_lectura(
    sesiones: Sequence[SesionConLecturas], lectura: LecturaResumen
) -> SesionResumen | None:
    """A que sesion pertenece una lectura concreta.

    Se busca por identidad de ``id_lectura`` en lugar de recordar el indice
    mientras se calcula el maximo: separa el «cual es la ultima» del «de donde
    salio», y deja :func:`ultima_lectura` como una funcion de una sola idea.
    """
    for entrada in sesiones:
        if any(l.id_lectura == lectura.id_lectura for l in entrada.lecturas):
            return entrada.sesion
    return None
