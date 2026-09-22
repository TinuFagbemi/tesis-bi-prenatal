"""Resolucion del contexto clinico y su instalacion, sin servidor (SCRUM-98).

Dos cosas se comprueban aqui:

* **quien es clinicamente el llamante**, derivado solo de ``id_usuario`` y de
  los vinculos que guarda PostgreSQL, nunca de algo que el cliente enviara;
* **que el valor que viaja a PostgreSQL es el correcto y esta acotado**, sin
  abrir una conexion.

El comportamiento real contra un servidor -- que el contexto muera con la
transaccion y no viaje entre conexiones del pool -- vive en
``test_contexto_postgresql.py``.

Todas las cuentas y datos son simulados y ficticios.
"""

from __future__ import annotations

import pytest

from app.api.dependencias import MENSAJE_SIN_CONTEXTO_CLINICO
from app.db.contexto import (
    NOMBRE_DEL_CONTEXTO,
    ContextoInvalido,
    instalar_contexto,
    leer_contexto,
)
from app.models.enums import NombreRol
from app.services.contexto import (
    SIN_VINCULO,
    VINCULO_AJENO_AL_ROL,
    VINCULO_AMBIGUO,
    ContextoClinico,
    ContextoNoResoluble,
    resolver_contexto,
)
from app.services.principal import PrincipalAutenticado

ID_USUARIO = 9_000_001
ID_PACIENTE = 500
ID_MEDICO = 600


class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def scalars(self):
        return self

    def all(self):
        return list(self._filas)

    def scalar(self):
        return self._filas[0] if self._filas else None


class _SesionFalsa:
    """Devuelve el vinculo pedido segun la tabla que la consulta nombre.

    Un doble y no una base: lo que se ejercita es la decision -- que combinacion
    de vinculos admite cada rol --, y esa decision no necesita un servidor.
    """

    def __init__(self, paciente=None, medico=None, pacientes=None, medicos=None):
        self.pacientes = pacientes if pacientes is not None else (
            [paciente] if paciente is not None else []
        )
        self.medicos = medicos if medicos is not None else (
            [medico] if medico is not None else []
        )
        self.sentencias = []

    def execute(self, sentencia, parametros=None):
        self.sentencias.append((str(sentencia), parametros))
        texto = str(sentencia)
        if "usuario_paciente" in texto:
            return _Resultado(self.pacientes)
        if "usuario_medico" in texto:
            return _Resultado(self.medicos)
        return _Resultado([])


def _principal(rol: NombreRol) -> PrincipalAutenticado:
    return PrincipalAutenticado(id_usuario=ID_USUARIO, rol=rol)


# ---------------------------------------------------------------------------
# 1. El camino feliz de cada rol
# ---------------------------------------------------------------------------


def test_una_paciente_resuelve_a_su_perfil():
    contexto = resolver_contexto(
        _SesionFalsa(paciente=ID_PACIENTE), _principal(NombreRol.PACIENTE)
    )

    assert contexto == ContextoClinico(
        id_usuario=ID_USUARIO, rol=NombreRol.PACIENTE, id_paciente=ID_PACIENTE
    )
    assert contexto.es_paciente
    assert not contexto.es_medico


def test_un_medico_resuelve_a_su_perfil():
    contexto = resolver_contexto(
        _SesionFalsa(medico=ID_MEDICO), _principal(NombreRol.MEDICO)
    )

    assert contexto.id_medico == ID_MEDICO
    assert contexto.id_paciente is None
    assert contexto.es_medico


def test_un_admin_resuelve_a_un_contexto_sin_perfil():
    """ADMIN es una identidad valida con alcance clinico vacio, no un rechazo.

    Distinguirlo importa: un rechazo obligaria a cada ruta a tratarlo como un
    error, cuando lo correcto es que las politicas vean sencillamente que no
    alcanza ninguna fila clinica.
    """
    contexto = resolver_contexto(_SesionFalsa(), _principal(NombreRol.ADMIN))

    assert contexto.id_paciente is None
    assert contexto.id_medico is None
    assert not contexto.es_paciente
    assert not contexto.es_medico


# ---------------------------------------------------------------------------
# 2. Fallo cerrado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rol", [NombreRol.PACIENTE, NombreRol.MEDICO])
def test_un_rol_clinico_sin_vinculo_no_obtiene_contexto(rol):
    with pytest.raises(ContextoNoResoluble) as error:
        resolver_contexto(_SesionFalsa(), _principal(rol))

    assert error.value.motivo == SIN_VINCULO


def test_una_paciente_con_vinculo_de_medico_se_rechaza():
    """El rol dice PACIENTE y el vinculo dice MEDICO: no se elige ninguno."""
    with pytest.raises(ContextoNoResoluble) as error:
        resolver_contexto(
            _SesionFalsa(medico=ID_MEDICO), _principal(NombreRol.PACIENTE)
        )

    assert error.value.motivo == SIN_VINCULO


def test_un_medico_con_vinculo_de_paciente_se_rechaza():
    with pytest.raises(ContextoNoResoluble) as error:
        resolver_contexto(
            _SesionFalsa(paciente=ID_PACIENTE), _principal(NombreRol.MEDICO)
        )

    assert error.value.motivo == SIN_VINCULO


@pytest.mark.parametrize("rol", list(NombreRol))
def test_tener_los_dos_vinculos_a_la_vez_se_rechaza(rol):
    """La invariante de SCRUM-97 lo impide, y aun asi se comprueba.

    Una base editada a mano o una migracion a medias pueden dejar ese estado, y
    entonces elegir uno de los dos seria elegir un perfil arbitrario.
    """
    with pytest.raises(ContextoNoResoluble) as error:
        resolver_contexto(
            _SesionFalsa(paciente=ID_PACIENTE, medico=ID_MEDICO), _principal(rol)
        )

    assert error.value.motivo == VINCULO_AMBIGUO


@pytest.mark.parametrize(
    "vinculos", [{"pacientes": [1, 2]}, {"medicos": [1, 2]}]
)
def test_dos_filas_en_el_mismo_puente_se_rechazan(vinculos):
    """Imposible con la PK del puente, y tratado igualmente como ambiguo.

    La consulta pide dos filas a proposito: si tomara la primera, una base
    corrupta resolveria a un perfil cualquiera sin que nada lo dijera.
    """
    rol = NombreRol.PACIENTE if "pacientes" in vinculos else NombreRol.MEDICO

    with pytest.raises(ContextoNoResoluble) as error:
        resolver_contexto(_SesionFalsa(**vinculos), _principal(rol))

    assert error.value.motivo == VINCULO_AMBIGUO


@pytest.mark.parametrize("vinculo", [{"paciente": ID_PACIENTE}, {"medico": ID_MEDICO}])
def test_un_admin_con_cualquier_vinculo_se_rechaza(vinculo):
    """SCRUM-97 garantiza que una cuenta ADMIN no tiene puente."""
    with pytest.raises(ContextoNoResoluble) as error:
        resolver_contexto(_SesionFalsa(**vinculo), _principal(NombreRol.ADMIN))

    assert error.value.motivo in (VINCULO_AJENO_AL_ROL, VINCULO_AMBIGUO)


def test_se_consultan_los_dos_puentes_para_cualquier_rol():
    """Comprobar la ausencia es lo que hace util la invariante.

    Mirar solo el puente que el rol espera aceptaria una cuenta PACIENTE que
    ademas arrastrase un vinculo de medico.
    """
    sesion = _SesionFalsa(paciente=ID_PACIENTE)

    resolver_contexto(sesion, _principal(NombreRol.PACIENTE))
    consultadas = " ".join(texto for texto, _ in sesion.sentencias)

    assert "usuario_paciente" in consultadas
    assert "usuario_medico" in consultadas


def test_el_motivo_no_publica_identificadores():
    """Un rechazo no es sitio para decir que perfil se buscaba ni cual se halló."""
    with pytest.raises(ContextoNoResoluble) as error:
        resolver_contexto(_SesionFalsa(), _principal(NombreRol.PACIENTE))

    assert str(ID_USUARIO) not in error.value.motivo
    assert str(ID_PACIENTE) not in error.value.motivo


def test_el_mensaje_http_es_unico_para_las_tres_causas():
    """Distinguirlas publicaria la forma del registro de alguien."""
    assert "perfil clinico" in MENSAJE_SIN_CONTEXTO_CLINICO
    for motivo in (SIN_VINCULO, VINCULO_AJENO_AL_ROL, VINCULO_AMBIGUO):
        assert motivo != MENSAJE_SIN_CONTEXTO_CLINICO


# ---------------------------------------------------------------------------
# 3. El contexto es inmutable
# ---------------------------------------------------------------------------


def test_un_handler_no_puede_ensanchar_el_contexto_recibido():
    contexto = ContextoClinico(
        id_usuario=ID_USUARIO, rol=NombreRol.PACIENTE, id_paciente=ID_PACIENTE
    )

    with pytest.raises(Exception):
        contexto.id_paciente = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 4. El valor que viaja a PostgreSQL
# ---------------------------------------------------------------------------


class _SesionQueRegistra:
    def __init__(self):
        self.ejecutadas = []

    def execute(self, sentencia, parametros=None):
        self.ejecutadas.append((str(sentencia), parametros))
        return _Resultado([])


def test_se_instala_con_set_config_y_no_con_set():
    """``SET`` sobreviviria al checkin del pool; ``set_config(..., true)`` no."""
    sesion = _SesionQueRegistra()

    instalar_contexto(sesion, ID_USUARIO)
    sentencia, parametros = sesion.ejecutadas[0]

    assert "set_config" in sentencia
    assert ", true)" in sentencia
    assert parametros == {"valor": str(ID_USUARIO)}
    assert not sentencia.lstrip().upper().startswith("SET ")


def test_el_nombre_del_parametro_lleva_prefijo():
    """PostgreSQL solo admite un parametro personalizado con prefijo."""
    assert "." in NOMBRE_DEL_CONTEXTO
    assert NOMBRE_DEL_CONTEXTO.startswith("fetalalert.")


def test_el_rol_no_viaja_en_el_contexto():
    """Un rol en una variable de sesion seria un rol que alguien puede fijar.

    Las politicas lo resuelven desde ``operacional.usuario``, donde nadie puede
    decirles otro.
    """
    sesion = _SesionQueRegistra()

    instalar_contexto(sesion, ID_USUARIO)
    todo = " ".join(f"{s} {p}" for s, p in sesion.ejecutadas)

    for rol in NombreRol:
        assert rol.value not in todo
    assert "rol" not in todo.lower().replace("set_config", "")


@pytest.mark.parametrize("valor", [0, -1, -9_000_001])
def test_un_identificador_no_positivo_se_rechaza(valor):
    sesion = _SesionQueRegistra()

    with pytest.raises(ContextoInvalido):
        instalar_contexto(sesion, valor)

    assert sesion.ejecutadas == [], "no debe enviarse nada al servidor"


@pytest.mark.parametrize("valor", ["1", "1; DROP TABLE x", 1.0, None, b"1"])
def test_un_valor_que_no_es_entero_se_rechaza(valor):
    sesion = _SesionQueRegistra()

    with pytest.raises(ContextoInvalido):
        instalar_contexto(sesion, valor)

    assert sesion.ejecutadas == []


def test_un_booleano_no_se_toma_por_el_usuario_1():
    """``bool`` es subclase de ``int``: ``True`` instalaria la identidad 1."""
    sesion = _SesionQueRegistra()

    with pytest.raises(ContextoInvalido):
        instalar_contexto(sesion, True)

    assert sesion.ejecutadas == []


def test_el_valor_viaja_como_parametro_vinculado():
    """Interpolarlo en el texto haria del identificador parte de la sentencia."""
    sesion = _SesionQueRegistra()

    instalar_contexto(sesion, ID_USUARIO)
    sentencia, parametros = sesion.ejecutadas[0]

    assert str(ID_USUARIO) not in sentencia
    assert parametros["valor"] == str(ID_USUARIO)


@pytest.mark.parametrize("crudo,esperado", [("42", 42), ("", None), (None, None)])
def test_leer_contexto_interpreta_la_ausencia_como_ninguna_identidad(crudo, esperado):
    class _Sesion:
        def execute(self, sentencia, parametros=None):
            return _Resultado([crudo])

    assert leer_contexto(_Sesion()) == esperado


def test_leer_contexto_usa_missing_ok():
    """Una conexion sin contexto es normal, no un error: el login y la auditoria."""
    sesion = _SesionQueRegistra()

    leer_contexto(sesion)
    sentencia, _ = sesion.ejecutadas[0]

    assert "current_setting" in sentencia
    assert ", true)" in sentencia
