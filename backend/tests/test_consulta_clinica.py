"""Las decisiones de la lectura clinica, sin servidor (SCRUM-98, subfase 4).

Lo que se comprueba aquí es la **forma** de las decisiones: qué predicado
produce cada rol, qué campos salen en las respuestas, y quién puede llegar a las
rutas. El comportamiento contra políticas reales se demuestra en
``test_http_como_api_postgresql.py``, conectado como ``fetalalert_api``; estas
pruebas cubren lo que allí no se vería si las políticas hicieran el trabajo por
la aplicación.

Esa distinción es el punto de tener las dos capas: si la consulta de un rol
dejara de filtrar, una prueba que solo mire el resultado final seguiría en verde
porque PostgreSQL la habría salvado. Aquí se mira el predicado.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import pytest

from app.api.v1 import clinico as router_clinico
from app.models.enums import NombreRol
from app.schemas.clinico import EmbarazoResumen, LecturaResumen, SesionResumen
from app.services.consulta_clinica import (
    MENSAJE_EMBARAZO,
    MENSAJE_SESION,
    RecursoClinicoInexistente,
    _alcance_de,
    listar_embarazos,
)
from app.services.contexto import ContextoClinico

ID_USUARIO = 9_000_701
ID_PACIENTE = 9_000_301
ID_MEDICO = 9_000_401


def contexto(rol: NombreRol, **perfil) -> ContextoClinico:
    return ContextoClinico(id_usuario=ID_USUARIO, rol=rol, **perfil)


# ---------------------------------------------------------------------------
# 1. Qué produce cada rol
# ---------------------------------------------------------------------------


def test_una_paciente_filtra_por_su_propio_perfil():
    alcance = _alcance_de(contexto(NombreRol.PACIENTE, id_paciente=ID_PACIENTE))

    assert alcance is not None
    assert str(ID_PACIENTE) in str(alcance.compile(compile_kwargs={"literal_binds": True}))


def test_un_medico_filtra_por_el_helper_de_seguimiento_vigente():
    """Y no por ``medico_clinica``, que no aparece en el predicado.

    El helper deriva el médico del contexto instalado en la transacción, así que
    el predicado no lleva el ``id_medico``: no hay forma de preguntar por el
    alcance de otro.
    """
    alcance = _alcance_de(contexto(NombreRol.MEDICO, id_medico=ID_MEDICO))

    texto = str(alcance)
    assert "seguridad.embarazo_en_seguimiento_vigente" in texto
    assert "medico_clinica" not in texto
    assert str(ID_MEDICO) not in texto


@pytest.mark.parametrize(
    "sujeto",
    [
        contexto(NombreRol.ADMIN),
        contexto(NombreRol.PACIENTE),  # rol de gestante sin id_paciente
        contexto(NombreRol.MEDICO),  # rol de médico sin id_medico
    ],
    ids=["admin", "paciente-sin-perfil", "medico-sin-perfil"],
)
def test_un_contexto_sin_alcance_no_produce_predicado(sujeto):
    """``None`` y no un predicado falso, y esa diferencia importa.

    Un perfil ausente que se colara en la consulta como ``id_paciente IS NULL``
    no filtraría: devolvería las filas cuyo paciente es nulo, que no existen hoy,
    pero el día que una columna admitiera nulos el fallo sería abierto. Devolver
    ``None`` obliga a quien llama a decidir explícitamente, y lo que decide es no
    consultar.
    """
    assert _alcance_de(sujeto) is None


def test_sin_alcance_el_listado_es_una_lista_vacia_sin_tocar_la_base():
    """No hace falta una sesión: si hubiera consulta, esto reventaría."""
    assert listar_embarazos(None, contexto(NombreRol.ADMIN)) == []


# ---------------------------------------------------------------------------
# 2. Ajeno e inexistente dicen lo mismo
# ---------------------------------------------------------------------------


def test_los_dos_mensajes_solo_varian_en_el_identificador_recibido():
    primero = MENSAJE_EMBARAZO.format(id_embarazo=101)
    segundo = MENSAJE_EMBARAZO.format(id_embarazo=999_999)

    assert primero.replace("101", "N") == segundo.replace("999999", "N")


def test_el_mensaje_de_sesion_habla_de_la_sesion_y_no_del_embarazo():
    """Cambiar de sujeto sería en sí mismo la señal de que el embarazo existe."""
    mensaje = MENSAJE_SESION.format(id_sesion=7)

    assert "sesion" in mensaje.lower()
    assert "embarazo" not in mensaje.lower()


def test_la_excepcion_es_una_sola_para_los_dos_casos():
    """Dos excepciones invitarían a traducirlas a dos respuestas distintas."""
    assert issubclass(RecursoClinicoInexistente, LookupError)


# ---------------------------------------------------------------------------
# 3. Los esquemas de respuesta, y lo que omiten
# ---------------------------------------------------------------------------


def test_el_resumen_del_embarazo_no_expone_a_la_persona():
    campos = set(EmbarazoResumen.model_fields)

    assert campos == {
        "id_embarazo",
        "fecha_inicio",
        "fecha_probable_parto",
        "estado_embarazo",
        "fecha_cierre",
    }
    assert not campos & {"id_paciente", "id_clinica", "numero_gestas", "numero_partos"}


def test_el_resumen_de_sesion_no_expone_el_dispositivo_ni_el_canal():
    campos = set(SesionResumen.model_fields)

    assert campos == {
        "id_sesion",
        "tipo_sesion",
        "estado_sesion",
        "fecha_inicio",
        "fecha_fin",
    }
    assert not campos & {"id_dispositivo", "origen_dato"}
    # ``id_embarazo`` tampoco: quien pregunta lo puso en la ruta para llegar
    # aqui, asi que devolverlo no anade nada e invita a leer el campo en vez de
    # confiar en la ruta que se pidio.
    assert "id_embarazo" not in campos


def test_el_resumen_de_lectura_lleva_valores_de_catalogo_y_no_sus_claves():
    """Un ``id_semaforo`` no significa nada fuera de esta base.

    Enviar la clave sustituta obligaba a quien lee a traerse el catalogo o a
    fijar la correspondencia a mano, y es asi como un identificador interno
    acaba copiado en el sistema de otro. Viajan el codigo del semaforo y la
    semana gestacional, que son los dos valores que hacen legible la serie.

    ``fecha_hora_sincronizacion`` sigue fuera: cuando llego dice algo de la red;
    cuando se capturo, de la paciente.
    """
    campos = set(LecturaResumen.model_fields)

    assert campos == {
        "id_lectura",
        "fecha_hora_captura",
        "codigo_semaforo",
        "semana_gestacion",
        "hr_valor",
        "spo2_valor",
        "mov_valor",
    }
    assert "id_semaforo" not in campos and "id_tiempo_gest" not in campos
    assert "fecha_hora_sincronizacion" not in campos


# ---------------------------------------------------------------------------
# 4. Quién puede llegar al router
# ---------------------------------------------------------------------------


def rutas_clinicas():
    """Las rutas del router clinico, aplanadas como las lee ``test_rbac``."""
    from app.main import app
    from tests.test_rbac import aplanar

    return [
        (ruta, camino)
        for ruta, camino in aplanar(app.routes)
        if camino.startswith("/api/v1/clinico")
    ]


def test_la_superficie_clinica_es_de_tres_rutas_y_no_de_cuatro():
    """La reducción de superficie, fijada donde se puede comprobar.

    Un borrador anterior proponía una cuarta ruta, ``GET
    /clinico/embarazos/{id}``, con el detalle de un episodio. Se descartó en
    revisión: el listado ya devuelve todos los campos que ese detalle habría
    tenido, así que lo único que añadía era una segunda forma de llegar a la
    misma fila — un segundo sitio donde acertar con el alcance, un segundo 404
    que mantener indistinguible del ajeno, y una segunda entrada que auditar.

    Esta prueba existe para que volver a añadirla sea una decisión explícita y
    no un descuido: quien la añada tendrá que cambiar este número y leer por qué
    se había quitado.
    """
    caminos = {camino for _, camino in rutas_clinicas()}

    assert caminos == {
        "/api/v1/clinico/embarazos",
        "/api/v1/clinico/embarazos/{id_embarazo}/sesiones",
        "/api/v1/clinico/sesiones/{id_sesion}/lecturas",
    }


def test_las_tres_rutas_comparten_la_misma_guardia():
    """Una guardia por ruta se separaría; una compartida no puede."""
    from tests.test_rbac import dependencias_de

    rutas = rutas_clinicas()

    assert len(rutas) == 3
    for ruta, _ in rutas:
        assert "verificar_rol" in dependencias_de(ruta)


def test_el_admin_no_esta_en_la_lista_de_la_guardia():
    """Se lee del código que produce la guardia, no de una constante aparte."""
    import inspect

    fuente = inspect.getsource(router_clinico)
    declaracion = fuente[fuente.index("EXIGIR_LECTURA_CLINICA = ") :]
    declaracion = declaracion[: declaracion.index(")")]

    assert "NombreRol.PACIENTE" in declaracion
    assert "NombreRol.MEDICO" in declaracion
    assert "NombreRol.ADMIN" not in declaracion


def test_las_rutas_clinicas_son_solo_de_lectura():
    """Esta subfase no escribe nada. Un POST aquí sería otro ticket."""
    metodos = set()
    for ruta, _ in rutas_clinicas():
        metodos |= set(ruta.methods)

    assert metodos == {"GET"}
