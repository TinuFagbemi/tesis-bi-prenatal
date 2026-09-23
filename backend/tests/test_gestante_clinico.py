"""Lectura clinica del portal: reglas de dominio y contrato HTTP (SCRUM-72).

Dos mitades:

* las **reglas puras** de ``app.gestante.clinico`` --cual episodio esta en
  curso, cual es la ultima lectura-- probadas con datos fabricados;
* el **contrato HTTP** del adaptador, caso por caso, con el cliente central
  sustituido por un doble.

No hace falta PostgreSQL ni la API central.

**El caso que da sentido al algoritmo de la ultima lectura** esta en
``test_la_ultima_lectura_no_es_la_de_la_sesion_mas_reciente``: en un sistema
offline-first el orden de las sesiones no garantiza el orden real de captura, y
tomar «la ultima lectura de la sesion mas reciente» devolveria una medicion que
no es la ultima.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.gestante.central import (
    EstadoRespuesta,
    RespuestaClinica,
    RespuestaIdentidad,
    RespuestaToken,
)
from app.gestante.clinico import (
    SesionConLecturas,
    clasificar_episodios,
    en_curso,
    sesion_de_la_lectura,
    ultima_lectura,
)
from app.models.enums import NombreRol
from app.schemas.clinico import EmbarazoResumen, LecturaResumen, SesionResumen
from tests.test_gestante_rutas import (
    ClienteCentralDoble,
    construir_cliente,
    iniciar_sesion,
)

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Constructores de datos de prueba
# ---------------------------------------------------------------------------


def embarazo(
    id_embarazo: int,
    *,
    estado: str = "ACTIVO",
    cierre: date | None = None,
    inicio: date = date(2026, 1, 6),
) -> EmbarazoResumen:
    return EmbarazoResumen(
        id_embarazo=id_embarazo,
        fecha_inicio=inicio,
        fecha_probable_parto=inicio + timedelta(days=280),
        estado_embarazo=estado,
        fecha_cierre=cierre,
    )


def sesion(id_sesion: int, *, tipo: str = "SIGNOS_MATERNOS") -> SesionResumen:
    return SesionResumen(
        id_sesion=id_sesion,
        tipo_sesion=tipo,
        estado_sesion="COMPLETADA",
        fecha_inicio=datetime(2026, 6, 1, 10, 0, tzinfo=UTC),
        fecha_fin=datetime(2026, 6, 1, 11, 0, tzinfo=UTC),
    )


def lectura(
    id_lectura: int,
    captura: datetime,
    *,
    semaforo: str = "OK",
    semana: int = 24,
    hr: str | None = "78.00",
    spo2: str | None = "98.00",
    mov: int | None = None,
) -> LecturaResumen:
    return LecturaResumen(
        id_lectura=id_lectura,
        fecha_hora_captura=captura,
        codigo_semaforo=semaforo,
        semana_gestacion=semana,
        hr_valor=None if hr is None else Decimal(hr),
        spo2_valor=None if spo2 is None else Decimal(spo2),
        mov_valor=mov,
    )


def lectura_de_movimiento(id_lectura: int, captura: datetime, mov: int, **extra):
    return lectura(id_lectura, captura, hr=None, spo2=None, mov=mov, **extra)


# ===========================================================================
# REGLA DE EMBARAZO ACTUAL
# ===========================================================================


def test_un_solo_episodio_en_curso_es_el_actual():
    """Caso A: un ACTIVO sin fecha de cierre."""
    actual = embarazo(101)
    cerrado = embarazo(100, estado="FINALIZADO", cierre=date(2025, 9, 1))

    episodios = clasificar_episodios([actual, cerrado])

    assert episodios.actual is not None
    assert episodios.actual.id_embarazo == 101
    assert [e.id_embarazo for e in episodios.anteriores] == [100]
    assert episodios.ambiguo is False


def test_sin_episodios_en_curso_no_se_inventa_uno():
    """Caso B: cero activos. Que no haya embarazo en curso es un hecho."""
    episodios = clasificar_episodios(
        [
            embarazo(100, estado="FINALIZADO", cierre=date(2025, 9, 1)),
            embarazo(99, estado="SUSPENDIDO", cierre=date(2024, 5, 1)),
        ]
    )

    assert episodios.actual is None
    assert episodios.ambiguo is False
    # Siguen siendo consultables como historial.
    assert len(episodios.anteriores) == 2
    assert len(episodios.todos) == 2


def test_dos_episodios_en_curso_son_ambiguos_y_no_se_elige_ninguno():
    """Caso C: no hay constraint que lo impida, asi que puede ocurrir."""
    episodios = clasificar_episodios([embarazo(101), embarazo(102)])

    assert episodios.ambiguo is True
    assert episodios.actual is None
    # Tampoco se reparte: si no se sabe cual es el actual, no se sabe cual es
    # anterior.
    assert episodios.anteriores == ()
    assert len(episodios.todos) == 2


def test_la_ambiguedad_permite_consultar_cualquier_episodio_propio():
    """Caso D: la paciente no se queda sin acceso a sus datos."""
    episodios = clasificar_episodios([embarazo(101), embarazo(102)])

    assert episodios.contiene(101)
    assert episodios.contiene(102)
    assert episodios.hay_episodios is True


def test_la_ambiguedad_no_marca_ninguno_como_actual():
    """Caso E: ningun episodio queda etiquetado como el actual."""
    episodios = clasificar_episodios([embarazo(101), embarazo(102), embarazo(103)])

    assert episodios.actual is None


def test_un_activo_con_fecha_de_cierre_se_contradice_y_no_cuenta_como_actual():
    """Las dos condiciones de la regla son necesarias."""
    contradictorio = embarazo(101, estado="ACTIVO", cierre=date(2026, 3, 1))

    assert en_curso(contradictorio) is False
    assert clasificar_episodios([contradictorio]).actual is None


def test_sin_ningun_episodio_todo_queda_vacio():
    episodios = clasificar_episodios([])

    assert episodios.actual is None
    assert episodios.todos == ()
    assert episodios.ambiguo is False
    assert episodios.hay_episodios is False


def test_un_episodio_ajeno_no_pertenece_a_la_paciente():
    """Caso F: la comprobacion de pertenencia no mezcla episodios."""
    episodios = clasificar_episodios([embarazo(101)])

    assert episodios.contiene(101) is True
    assert episodios.contiene(999) is False


# ===========================================================================
# ULTIMA LECTURA
# ===========================================================================


def test_la_ultima_lectura_no_es_la_de_la_sesion_mas_reciente():
    """**El caso que la regla anterior habria fallado.**

    La sesion B se lista como mas reciente, pero su lectura se capturo antes
    que la de A. La ultima medicion real es la de las 10:15.
    """
    sesiones = [
        SesionConLecturas(
            sesion=sesion(501),
            lecturas=(lectura(100, datetime(2026, 6, 1, 10, 15, tzinfo=UTC)),),
        ),
        SesionConLecturas(
            sesion=sesion(502),
            lecturas=(lectura(101, datetime(2026, 6, 1, 10, 5, tzinfo=UTC)),),
        ),
    ]

    resultado = ultima_lectura(sesiones)

    assert resultado.id_lectura == 100
    assert resultado.fecha_hora_captura == datetime(2026, 6, 1, 10, 15, tzinfo=UTC)


def test_el_orden_de_la_lista_de_sesiones_no_altera_el_resultado():
    """Determinismo: el maximo es global, no posicional."""
    a = SesionConLecturas(
        sesion=sesion(501),
        lecturas=(lectura(100, datetime(2026, 6, 1, 10, 15, tzinfo=UTC)),),
    )
    b = SesionConLecturas(
        sesion=sesion(502),
        lecturas=(lectura(101, datetime(2026, 6, 1, 10, 5, tzinfo=UTC)),),
    )

    assert ultima_lectura([a, b]).id_lectura == 100
    assert ultima_lectura([b, a]).id_lectura == 100


def test_un_empate_exacto_se_desempata_por_id_lectura():
    """``id_lectura`` solo interviene cuando el instante es identico."""
    momento = datetime(2026, 6, 1, 10, 15, 0, tzinfo=UTC)
    sesiones = [
        SesionConLecturas(sesion=sesion(501), lecturas=(lectura(100, momento),)),
        SesionConLecturas(sesion=sesion(502), lecturas=(lectura(101, momento),)),
    ]

    assert ultima_lectura(sesiones).id_lectura == 101


def test_un_id_mayor_no_gana_si_su_captura_es_anterior():
    """El identificador no sustituye al tiempo."""
    sesiones = [
        SesionConLecturas(
            sesion=sesion(501),
            lecturas=(lectura(100, datetime(2026, 6, 1, 10, 15, tzinfo=UTC)),),
        ),
        SesionConLecturas(
            sesion=sesion(502),
            lecturas=(lectura(9999, datetime(2026, 6, 1, 9, 0, tzinfo=UTC)),),
        ),
    ]

    assert ultima_lectura(sesiones).id_lectura == 100


def test_sin_sesiones_no_hay_ultima_lectura():
    assert ultima_lectura([]) is None


def test_con_sesiones_pero_sin_lecturas_no_hay_ultima_lectura():
    sesiones = [
        SesionConLecturas(sesion=sesion(501), lecturas=()),
        SesionConLecturas(sesion=sesion(502), lecturas=()),
    ]

    assert ultima_lectura(sesiones) is None


def test_se_mezclan_sesiones_con_y_sin_lecturas():
    sesiones = [
        SesionConLecturas(sesion=sesion(501), lecturas=()),
        SesionConLecturas(
            sesion=sesion(502),
            lecturas=(lectura(100, datetime(2026, 6, 1, 10, 15, tzinfo=UTC)),),
        ),
        SesionConLecturas(sesion=sesion(503), lecturas=()),
    ]

    assert ultima_lectura(sesiones).id_lectura == 100


def test_la_ultima_lectura_conserva_sus_propios_nulos():
    """Una lectura de movimiento no adquiere HR de otra lectura."""
    sesiones = [
        SesionConLecturas(
            sesion=sesion(501),
            lecturas=(lectura(100, datetime(2026, 6, 1, 10, 0, tzinfo=UTC)),),
        ),
        SesionConLecturas(
            sesion=sesion(502, tipo="MOVIMIENTOS_FETALES"),
            lecturas=(
                lectura_de_movimiento(
                    101, datetime(2026, 6, 1, 11, 0, tzinfo=UTC), 7
                ),
            ),
        ),
    ]

    resultado = ultima_lectura(sesiones)

    assert resultado.id_lectura == 101
    assert resultado.mov_valor == 7
    # No se rellena con la HR de la lectura de las 10:00.
    assert resultado.hr_valor is None
    assert resultado.spo2_valor is None


def test_se_identifica_de_que_sesion_vino_la_ultima_lectura():
    sesiones = [
        SesionConLecturas(
            sesion=sesion(501),
            lecturas=(lectura(100, datetime(2026, 6, 1, 10, 15, tzinfo=UTC)),),
        ),
        SesionConLecturas(
            sesion=sesion(502),
            lecturas=(lectura(101, datetime(2026, 6, 1, 10, 5, tzinfo=UTC)),),
        ),
    ]
    ultima = ultima_lectura(sesiones)

    assert sesion_de_la_lectura(sesiones, ultima).id_sesion == 501


# ===========================================================================
# CONTRATO HTTP DEL ADAPTADOR
# ===========================================================================


class ClienteClinicoDoble(ClienteCentralDoble):
    """Doble que ademas responde las tres lecturas clinicas."""

    def __init__(self, **kwargs):
        self.respuesta_embarazos = kwargs.pop(
            "respuesta_embarazos", RespuestaClinica(EstadoRespuesta.OK, datos=())
        )
        self.respuesta_sesiones = kwargs.pop(
            "respuesta_sesiones", RespuestaClinica(EstadoRespuesta.OK, datos=())
        )
        self.lecturas_por_sesion = kwargs.pop("lecturas_por_sesion", {})
        self.respuesta_lecturas = kwargs.pop("respuesta_lecturas", None)
        super().__init__(**kwargs)
        self.embarazos_pedidos = 0
        self.sesiones_pedidas: list[int] = []
        self.lecturas_pedidas: list[int] = []

    def embarazos(self, token):
        self.embarazos_pedidos += 1
        return self.respuesta_embarazos

    def sesiones(self, token, id_embarazo):
        self.sesiones_pedidas.append(id_embarazo)
        return self.respuesta_sesiones

    def lecturas(self, token, id_sesion):
        self.lecturas_pedidas.append(id_sesion)
        if self.respuesta_lecturas is not None:
            return self.respuesta_lecturas
        return RespuestaClinica(
            EstadoRespuesta.OK, datos=tuple(self.lecturas_por_sesion.get(id_sesion, ()))
        )


def portal(tmp_path, central):
    """Un cliente con sesion ya iniciada."""
    cliente, _, _, _ = construir_cliente(tmp_path, central=central)
    iniciar_sesion(cliente)
    return cliente


# -- A. Sesion local invalida -> 401 ---------------------------------------


@pytest.mark.parametrize(
    "ruta",
    ["/adaptador/embarazos", "/adaptador/embarazos/101/monitoreo"],
)
def test_sin_sesion_local_las_rutas_clinicas_responden_401(tmp_path, ruta):
    """El unico 401 del adaptador, y lo unico que devuelve al login."""
    cliente, _, _, _ = construir_cliente(tmp_path, central=ClienteClinicoDoble())

    assert cliente.get(ruta).status_code == 401


def test_una_sesion_cerrada_responde_401_en_las_rutas_clinicas(tmp_path):
    cliente = portal(tmp_path, ClienteClinicoDoble())
    cliente.post("/adaptador/cerrar-sesion")

    assert cliente.get("/adaptador/embarazos").status_code == 401


# -- B. Datos obtenidos -> 200 disponible: true ----------------------------


def test_con_datos_responde_200_disponible_true(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(
            EstadoRespuesta.OK,
            datos=(embarazo(101), embarazo(100, estado="FINALIZADO", cierre=date(2025, 9, 1))),
        )
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.get("/adaptador/embarazos")

    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["disponible"] is True
    assert cuerpo["datos"]["actual"]["id_embarazo"] == 101
    assert [e["id_embarazo"] for e in cuerpo["datos"]["anteriores"]] == [100]
    assert cuerpo["datos"]["ambiguo"] is False


def test_la_ambiguedad_viaja_al_navegador(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(
            EstadoRespuesta.OK, datos=(embarazo(101), embarazo(102))
        )
    )
    cliente = portal(tmp_path, central)

    cuerpo = cliente.get("/adaptador/embarazos").json()

    assert cuerpo["datos"]["ambiguo"] is True
    assert cuerpo["datos"]["actual"] is None
    assert cuerpo["datos"]["anteriores"] == []
    assert len(cuerpo["datos"]["todos"]) == 2


def test_una_lista_vacia_de_episodios_es_200_y_no_un_error(tmp_path):
    cliente = portal(tmp_path, ClienteClinicoDoble())

    cuerpo = cliente.get("/adaptador/embarazos").json()

    assert cuerpo["disponible"] is True
    assert cuerpo["datos"]["todos"] == []
    assert cuerpo["datos"]["actual"] is None


# -- C. Sin JWT en memoria -> 200 reautenticacion_requerida ----------------


def test_sin_token_en_memoria_pide_reautenticacion_sin_cerrar_la_sesion(tmp_path):
    """El caso del proceso reiniciado: la cookie sigue valida, el token no esta.

    Es exactamente lo que tiene que pasar, porque el token nunca se persiste.
    """
    central = ClienteClinicoDoble()
    cliente = portal(tmp_path, central)
    # Se vacia el almacen de tokens en memoria, como haria un reinicio.
    cliente.app.state.contexto.tokens = type(cliente.app.state.contexto.tokens)()

    respuesta = cliente.get("/adaptador/embarazos")

    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "disponible": False,
        "motivo": "reautenticacion_requerida",
    }
    # La sesion local sigue en pie.
    assert cliente.get("/adaptador/sesion").json()["autenticada"] is True
    # Y no se pregunto nada al servidor: no habia con que.
    assert central.embarazos_pedidos == 0


def test_sin_token_en_memoria_la_ventana_local_no_se_renueva(tmp_path):
    from tests.test_gestante_rutas import RelojFalso

    reloj = RelojFalso()
    central = ClienteClinicoDoble()
    cliente, _, _, _ = construir_cliente(tmp_path, central=central, reloj=reloj)
    iniciar_sesion(cliente)
    expira = cliente.get("/adaptador/sesion").json()["expira_en"]

    cliente.app.state.contexto.tokens = type(cliente.app.state.contexto.tokens)()
    reloj.avanzar(hours=5)
    cliente.get("/adaptador/embarazos")

    assert cliente.get("/adaptador/sesion").json()["expira_en"] == expira


# -- D. 401 central -> 200 reautenticacion_requerida -----------------------


def test_un_401_central_pide_reautenticacion_sin_cerrar_la_sesion(tmp_path):
    """No se puede distinguir token vencido de cuenta desactivada (SCRUM-70).

    Para la fase de lectura no se destruye la sesion offline. Esto no renueva
    la ventana, no afirma que la cuenta siga autorizada y no da acceso a nada.
    """
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.RECHAZADO)
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.get("/adaptador/embarazos")

    assert respuesta.status_code == 200
    assert respuesta.json()["motivo"] == "reautenticacion_requerida"
    assert cliente.get("/adaptador/sesion").json()["autenticada"] is True


# -- E. Sin conexion -> 200 sin_conexion -----------------------------------


def test_sin_conexion_responde_200_con_motivo_y_deja_el_portal_abierto(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.SIN_CONEXION),
        disponible_=False,
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.get("/adaptador/embarazos")

    assert respuesta.status_code == 200
    assert respuesta.json() == {"disponible": False, "motivo": "sin_conexion"}
    assert cliente.get("/").status_code == 200
    assert cliente.get("/adaptador/sesion").json()["autenticada"] is True


def test_sin_conexion_no_se_confunde_con_lista_vacia(tmp_path):
    """Decir «no tienes embarazos» cuando lo que falta es red seria mentir."""
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.SIN_CONEXION)
    )
    cliente = portal(tmp_path, central)

    cuerpo = cliente.get("/adaptador/embarazos").json()

    assert cuerpo["disponible"] is False
    assert "datos" not in cuerpo


# -- F. 404 central preservado ---------------------------------------------


def test_un_404_central_se_preserva_como_404(tmp_path):
    """SCRUM-98 no distingue ajeno de inexistente, y el adaptador tampoco."""
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(EstadoRespuesta.NO_ENCONTRADO)
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.get("/adaptador/embarazos/999/monitoreo")

    assert respuesta.status_code == 404
    # Ni una pista sobre cual de las dos situaciones fue.
    texto = respuesta.text.lower()
    assert "ajeno" not in texto
    assert "otra paciente" not in texto
    assert "no existe" not in texto


def test_un_404_no_cierra_la_sesion_local(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(EstadoRespuesta.NO_ENCONTRADO)
    )
    cliente = portal(tmp_path, central)

    cliente.get("/adaptador/embarazos/999/monitoreo")

    assert cliente.get("/adaptador/sesion").json()["autenticada"] is True


# -- G. 403 central preservado ---------------------------------------------


def test_un_403_central_se_preserva_como_403(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.PROHIBIDO)
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.get("/adaptador/embarazos")

    assert respuesta.status_code == 403
    assert "reautenticacion" not in respuesta.text.lower()


def test_un_403_no_cierra_la_sesion_local(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.PROHIBIDO)
    )
    cliente = portal(tmp_path, central)

    cliente.get("/adaptador/embarazos")

    assert cliente.get("/adaptador/sesion").json()["autenticada"] is True


# -- H. Error del upstream -> 502 ------------------------------------------


def test_un_fallo_del_servidor_es_502_y_no_un_200(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.ERROR_REMOTO)
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.get("/adaptador/embarazos")

    assert respuesta.status_code == 502
    assert "disponible" not in respuesta.json()


def test_un_502_no_cierra_la_sesion_local(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.ERROR_REMOTO)
    )
    cliente = portal(tmp_path, central)

    cliente.get("/adaptador/embarazos")

    assert cliente.get("/adaptador/sesion").json()["autenticada"] is True


# ===========================================================================
# MONITOREO DE UN EPISODIO
# ===========================================================================


def test_el_monitoreo_devuelve_sesiones_lecturas_y_la_ultima(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(
            EstadoRespuesta.OK, datos=(sesion(501), sesion(502))
        ),
        lecturas_por_sesion={
            501: (lectura(100, datetime(2026, 6, 1, 10, 15, tzinfo=UTC)),),
            502: (lectura(101, datetime(2026, 6, 1, 10, 5, tzinfo=UTC)),),
        },
    )
    cliente = portal(tmp_path, central)

    cuerpo = cliente.get("/adaptador/embarazos/101/monitoreo").json()

    assert cuerpo["disponible"] is True
    assert len(cuerpo["datos"]["sesiones"]) == 2
    # El maximo global, no el de la primera sesion de la lista.
    assert cuerpo["datos"]["ultima_lectura"]["id_lectura"] == 100
    assert cuerpo["datos"]["id_sesion_de_la_ultima"] == 501
    assert central.sesiones_pedidas == [101]
    assert sorted(central.lecturas_pedidas) == [501, 502]


def test_el_monitoreo_no_mezcla_metricas_de_lecturas_distintas(tmp_path):
    """La lectura del panel es una sola, entera.

    La mas reciente es de movimientos, asi que HR y SpO2 salen nulos aunque en
    otra lectura anterior existan.
    """
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(
            EstadoRespuesta.OK, datos=(sesion(501), sesion(502))
        ),
        lecturas_por_sesion={
            501: (lectura(100, datetime(2026, 6, 1, 10, 0, tzinfo=UTC)),),
            502: (
                lectura_de_movimiento(
                    101, datetime(2026, 6, 1, 11, 0, tzinfo=UTC), 7, semaforo="WARNING"
                ),
            ),
        },
    )
    cliente = portal(tmp_path, central)

    ultima = cliente.get("/adaptador/embarazos/101/monitoreo").json()["datos"][
        "ultima_lectura"
    ]

    assert ultima["mov_valor"] == 7
    assert ultima["hr_valor"] is None
    assert ultima["spo2_valor"] is None
    assert ultima["codigo_semaforo"] == "WARNING"


def test_un_episodio_sin_sesiones_no_tiene_ultima_lectura(tmp_path):
    cliente = portal(tmp_path, ClienteClinicoDoble())

    cuerpo = cliente.get("/adaptador/embarazos/101/monitoreo").json()

    assert cuerpo["disponible"] is True
    assert cuerpo["datos"]["sesiones"] == []
    assert cuerpo["datos"]["ultima_lectura"] is None


def test_sesiones_sin_lecturas_no_producen_ultima_lectura(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(EstadoRespuesta.OK, datos=(sesion(501),)),
        lecturas_por_sesion={501: ()},
    )
    cliente = portal(tmp_path, central)

    cuerpo = cliente.get("/adaptador/embarazos/101/monitoreo").json()

    assert cuerpo["datos"]["ultima_lectura"] is None
    assert cuerpo["datos"]["sesiones"][0]["lecturas"] == []


def test_los_nulos_biometricos_viajan_como_nulos_y_nunca_como_cero(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(EstadoRespuesta.OK, datos=(sesion(501),)),
        lecturas_por_sesion={
            501: (
                lectura_de_movimiento(
                    100, datetime(2026, 6, 1, 10, 0, tzinfo=UTC), 0
                ),
            )
        },
    )
    cliente = portal(tmp_path, central)

    ultima = cliente.get("/adaptador/embarazos/101/monitoreo").json()["datos"][
        "ultima_lectura"
    ]

    # El cero medido se conserva como cero...
    assert ultima["mov_valor"] == 0
    # ...y lo no medido sigue siendo nulo, no cero.
    assert ultima["hr_valor"] is None
    assert ultima["spo2_valor"] is None


@pytest.mark.parametrize("codigo", ["OK", "WARNING", "ERROR"])
def test_el_semaforo_viaja_sin_recalcularse(tmp_path, codigo):
    """Llega el codigo de la fuente autorizada, tal cual."""
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(EstadoRespuesta.OK, datos=(sesion(501),)),
        lecturas_por_sesion={
            501: (
                lectura(
                    100, datetime(2026, 6, 1, 10, 0, tzinfo=UTC), semaforo=codigo
                ),
            )
        },
    )
    cliente = portal(tmp_path, central)

    ultima = cliente.get("/adaptador/embarazos/101/monitoreo").json()["datos"][
        "ultima_lectura"
    ]

    assert ultima["codigo_semaforo"] == codigo


def test_un_fallo_a_mitad_de_las_lecturas_no_devuelve_historial_parcial(tmp_path):
    """Un historial incompleto que no se anuncia como incompleto es peor."""
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(EstadoRespuesta.OK, datos=(sesion(501),)),
        respuesta_lecturas=RespuestaClinica(EstadoRespuesta.ERROR_REMOTO),
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.get("/adaptador/embarazos/101/monitoreo")

    assert respuesta.status_code == 502
    assert "sesiones" not in respuesta.text


def test_la_semana_gestacional_viene_de_la_lectura(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(EstadoRespuesta.OK, datos=(sesion(501),)),
        lecturas_por_sesion={
            501: (lectura(100, datetime(2026, 6, 1, 10, 0, tzinfo=UTC), semana=31),)
        },
    )
    cliente = portal(tmp_path, central)

    ultima = cliente.get("/adaptador/embarazos/101/monitoreo").json()["datos"][
        "ultima_lectura"
    ]

    assert ultima["semana_gestacion"] == 31


# ===========================================================================
# LO QUE NO DEBE SALIR
# ===========================================================================


def test_las_rutas_clinicas_no_devuelven_el_token(tmp_path):
    from tests.test_gestante_rutas import TOKEN_DE_PRUEBA

    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(
            EstadoRespuesta.OK, datos=(embarazo(101),)
        )
    )
    cliente = portal(tmp_path, central)

    for ruta in ("/adaptador/embarazos", "/adaptador/embarazos/101/monitoreo"):
        texto = cliente.get(ruta).text
        assert TOKEN_DE_PRUEBA not in texto
        assert "Bearer" not in texto


def test_el_episodio_no_expone_identificadores_que_el_contrato_omite(tmp_path):
    """Ni id_paciente ni id_clinica: SCRUM-98 los deja fuera a proposito."""
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(
            EstadoRespuesta.OK, datos=(embarazo(101),)
        )
    )
    cliente = portal(tmp_path, central)

    cuerpo = cliente.get("/adaptador/embarazos").json()
    campos = set(cuerpo["datos"]["actual"])

    assert campos == {
        "id_embarazo",
        "fecha_inicio",
        "fecha_probable_parto",
        "estado_embarazo",
        "fecha_cierre",
    }


def test_la_lectura_no_expone_claves_de_catalogo(tmp_path):
    """Viaja ``codigo_semaforo``, no ``id_semaforo``."""
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(EstadoRespuesta.OK, datos=(sesion(501),)),
        lecturas_por_sesion={
            501: (lectura(100, datetime(2026, 6, 1, 10, 0, tzinfo=UTC)),)
        },
    )
    cliente = portal(tmp_path, central)

    texto = cliente.get("/adaptador/embarazos/101/monitoreo").text

    assert "id_semaforo" not in texto
    assert "id_tiempo_gest" not in texto
    assert "id_dispositivo" not in texto


# -- G. Dos cuentas, dos sesiones locales: sin mezcla ----------------------


class ClienteClinicoMultiCuenta(ClienteCentralDoble):
    """Doble cuyo token y datos dependen del correo, no de un valor fijo.

    ``ClienteClinicoDoble`` responde igual sin importar el token que reciba,
    lo que basta para el contrato pero no demuestra que el adaptador reenvie
    el token de **esa** sesion local y no uno cacheado o el de otra cuenta.
    Este doble si distingue: cada correo tiene su propio token, y cada token
    su propia respuesta de embarazos, para que una mezcla sea detectable.
    """

    def __init__(self, *, tokens_por_correo, datos_por_token, ids_por_token):
        self.tokens_por_correo = tokens_por_correo
        self.datos_por_token = datos_por_token
        self.ids_por_token = ids_por_token
        super().__init__()
        self.tokens_vistos_en_embarazos: list[str] = []

    def autenticar(self, *, email: str, password: str) -> RespuestaToken:
        self.llamadas_autenticar += 1
        self.ultimo_email = email
        self.ultimo_password = password
        token = self.tokens_por_correo.get(email)
        if token is None:
            return RespuestaToken(EstadoRespuesta.RECHAZADO)
        return RespuestaToken(EstadoRespuesta.OK, token=token)

    def identidad(self, token: str) -> RespuestaIdentidad:
        self.llamadas_identidad += 1
        id_usuario = self.ids_por_token.get(token)
        if id_usuario is None:
            return RespuestaIdentidad(EstadoRespuesta.RECHAZADO)
        return RespuestaIdentidad(
            EstadoRespuesta.OK, id_usuario=id_usuario, rol=NombreRol.PACIENTE
        )

    def embarazos(self, token):
        self.tokens_vistos_en_embarazos.append(token)
        return self.datos_por_token[token]


def test_dos_cuentas_con_sesion_local_propia_nunca_mezclan_token_ni_datos(tmp_path):
    """Cada sesion local reenvia solo el token de la cuenta que la abrio.

    Simula dos pacientes con su propio navegador (dos ``TestClient``, dos
    cookies) contra el mismo proceso del adaptador y el mismo doble central.
    Si el adaptador guardara el token en una variable compartida en lugar de
    leerlo de la sesion local de cada peticion, esta prueba lo mostraria: la
    cuenta A recibiria el embarazo de B, o viceversa.
    """
    correo_a, correo_b = "paciente-a@example.com", "paciente-b@example.com"
    token_a, token_b = "token-cuenta-a", "token-cuenta-b"
    central = ClienteClinicoMultiCuenta(
        tokens_por_correo={correo_a: token_a, correo_b: token_b},
        ids_por_token={token_a: 1, token_b: 2},
        datos_por_token={
            token_a: RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(101),)),
            token_b: RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(202),)),
        },
    )
    cliente_a, _, _, _ = construir_cliente(tmp_path, central=central)
    cliente_b = TestClient(cliente_a.app)

    assert iniciar_sesion(cliente_a, email=correo_a, password="da-igual-a").status_code == 200
    assert iniciar_sesion(cliente_b, email=correo_b, password="da-igual-b").status_code == 200

    respuesta_a = cliente_a.get("/adaptador/embarazos").json()
    respuesta_b = cliente_b.get("/adaptador/embarazos").json()

    ids_a = [e["id_embarazo"] for e in respuesta_a["datos"]["todos"]]
    ids_b = [e["id_embarazo"] for e in respuesta_b["datos"]["todos"]]
    assert ids_a == [101]
    assert ids_b == [202]
    assert central.tokens_vistos_en_embarazos == [token_a, token_b]


def test_una_cuenta_rechazada_no_ve_ni_toca_los_datos_de_la_otra(tmp_path):
    """El rechazo de una cuenta no deja ninguna huella en la sesion de la otra."""
    correo_valido, correo_ajeno = "paciente-valida@example.com", "no-existe@example.com"
    token_valido = "token-cuenta-valida"
    central = ClienteClinicoMultiCuenta(
        tokens_por_correo={correo_valido: token_valido},
        ids_por_token={token_valido: 7},
        datos_por_token={
            token_valido: RespuestaClinica(EstadoRespuesta.OK, datos=(embarazo(303),))
        },
    )
    cliente_valida, _, _, _ = construir_cliente(tmp_path, central=central)
    cliente_ajeno = TestClient(cliente_valida.app)

    assert iniciar_sesion(cliente_valida, email=correo_valido, password="da-igual").status_code == 200
    rechazo = iniciar_sesion(cliente_ajeno, email=correo_ajeno, password="lo-que-sea")
    assert rechazo.status_code == 401
    assert cliente_ajeno.get("/adaptador/embarazos").status_code == 401

    cuerpo = cliente_valida.get("/adaptador/embarazos").json()
    assert [e["id_embarazo"] for e in cuerpo["datos"]["todos"]] == [303]
    assert central.tokens_vistos_en_embarazos == [token_valido]
