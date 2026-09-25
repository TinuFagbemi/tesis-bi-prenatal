"""«Tus últimos registros», series, estado de conexión y reautenticación (SCRUM-72).

Tres bloques, sin PostgreSQL ni API central (el servidor es un doble):

* **La regla por variable** de ``app.gestante.clinico``: el último valor no nulo
  de cada variable dentro del episodio, ordenado por ``fecha_hora_captura`` y,
  solo para desempatar, ``id_lectura``. Cero es un valor; ``None`` no. Las
  series son las mismas lecturas en orden, sin puntos inventados.
* **El contrato HTTP** que la expone: trazabilidad, unidades, clasificación de
  la lectura de origen (rotulada como tal), todo o nada ante un fallo a mitad y
  la semana **actual** separada de la semana de cada lectura.
* **Conexión y sesión central**: ``/adaptador/estado-conexion`` distingue API
  caída, token vencido (401), acceso denegado (403) y error del servidor sin
  renovar nada, y ``/adaptador/reautenticar`` recupera el token de la **misma**
  cuenta sin cerrar la sesión local. El vencimiento se reproduce con un reloj
  falso y un doble que empieza a responder 401, no esperando treinta minutos.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient

from app.gestante.central import EstadoRespuesta, RespuestaClinica, RespuestaToken
from app.gestante.clinico import (
    VARIABLES,
    SesionConLecturas,
    serie,
    ultima_lectura,
    ultimo_registro,
)
from app.models.enums import NombreRol
from app.schemas.clinico import EmbarazoResumen
from tests.test_gestante_clinico import (
    ClienteClinicoDoble,
    embarazo,
    lectura,
    lectura_de_movimiento,
    portal,
    sesion,
)
from tests.test_gestante_movimientos import (
    ENTREGA_ACEPTADA,
    ID_EMBARAZO,
    _constructor_que_responde,
    cliente_con_provision,
)
from tests.test_gestante_rutas import (
    EMAIL_DE_PRUEBA,
    ID_USUARIO_DE_PRUEBA,
    PASSWORD_DE_PRUEBA,
    RelojFalso,
    construir_cliente,
    iniciar_sesion,
)

UTC = timezone.utc
FC, SPO2, MOV = VARIABLES


def t(dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 6, dia, hora, minuto, tzinfo=UTC)


def episodio(*grupos: tuple[int, tuple]) -> list[SesionConLecturas]:
    return [SesionConLecturas(sesion=sesion(i), lecturas=l) for i, l in grupos]


# ===========================================================================
# LA REGLA POR VARIABLE
# ===========================================================================


def test_cada_variable_toma_su_propio_ultimo_registro_con_su_fecha():
    """El caso de paciente30: FC/SpO2 de una lectura, movimientos de otra posterior."""
    sesiones = episodio(
        (1, (lectura(549, t(1, 8), hr="83.00", spo2="96.00"),)),
        (2, (lectura_de_movimiento(1259, t(21, 14), 7, semaforo="WARNING"),)),
    )

    fc = ultimo_registro(sesiones, FC)
    mov = ultimo_registro(sesiones, MOV)

    assert (fc.lectura.id_lectura, fc.valor, fc.id_sesion) == (549, Decimal("83.00"), 1)
    assert ultimo_registro(sesiones, SPO2).lectura.id_lectura == 549
    assert (mov.lectura.id_lectura, mov.valor, mov.id_sesion) == (1259, 7, 2)
    assert fc.lectura.fecha_hora_captura != mov.lectura.fecha_hora_captura
    # La «última lectura» sigue siendo una sola, la global.
    assert ultima_lectura(sesiones).id_lectura == 1259


def test_una_lectura_posterior_sin_la_variable_no_la_borra():
    sesiones = episodio(
        (1, (lectura(10, t(1, 8), hr="80.00", spo2="97.00"),)),
        (2, (lectura_de_movimiento(11, t(2, 8), 5),)),
    )

    assert ultimo_registro(sesiones, FC).lectura.id_lectura == 10


def test_el_cero_es_un_valor_registrado():
    sesiones = episodio(
        (1, (lectura_de_movimiento(10, t(1, 8), 4),)),
        (2, (lectura_de_movimiento(11, t(2, 8), 0),)),
    )

    ultimo = ultimo_registro(sesiones, MOV)

    assert ultimo.lectura.id_lectura == 11
    assert ultimo.valor == 0


def test_una_variable_nunca_registrada_es_none_y_no_cero():
    sesiones = episodio((1, (lectura_de_movimiento(10, t(1, 8), 4),)))

    assert ultimo_registro(sesiones, FC) is None
    assert ultimo_registro(sesiones, SPO2) is None


def test_un_episodio_sin_lecturas_no_tiene_ningun_registro():
    for variable in VARIABLES:
        assert ultimo_registro([], variable) is None
        assert ultimo_registro(episodio((1, ())), variable) is None
        assert serie([], variable) == ()


def test_el_empate_temporal_se_resuelve_por_id_lectura():
    mismo_instante = t(3, 9)
    sesiones = episodio(
        (1, (lectura(21, mismo_instante, hr="70.00", spo2="95.00"),)),
        (2, (lectura(20, mismo_instante, hr="90.00", spo2="99.00"),)),
    )

    assert ultimo_registro(sesiones, FC).lectura.id_lectura == 21
    assert ultimo_registro(sesiones, FC).valor == Decimal("70.00")


def test_el_orden_es_el_de_captura_y_no_el_de_las_sesiones():
    """Offline-first: la sesión listada al final puede traer lo más antiguo."""
    sesiones = episodio(
        (9, (lectura(30, t(5, 8), hr="88.00", spo2="98.00"),)),
        (1, (lectura(31, t(1, 8), hr="70.00", spo2="95.00"),)),
    )

    assert ultimo_registro(sesiones, FC).lectura.id_lectura == 30


def test_la_serie_solo_tiene_lecturas_existentes_en_orden_y_termina_en_la_tarjeta():
    sesiones = episodio(
        (2, (lectura_de_movimiento(3, t(9, 8), 6), lectura(4, t(9, 9)))),
        (1, (lectura_de_movimiento(1, t(1, 8), 0), lectura_de_movimiento(2, t(5, 8), 9))),
    )

    puntos = serie(sesiones, MOV)

    assert [p.lectura.id_lectura for p in puntos] == [1, 2, 3]
    assert [p.valor for p in puntos] == [0, 9, 6]
    assert puntos[-1] == ultimo_registro(sesiones, MOV)
    # Sin puntos para lo que la lectura 4 no midió.
    assert all(p.lectura.id_lectura != 4 for p in puntos)


# ===========================================================================
# EL CONTRATO HTTP
# ===========================================================================


def central_de_paciente30(**extra) -> ClienteClinicoDoble:
    return ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(
            EstadoRespuesta.OK, datos=(sesion(1), sesion(811, tipo="MOVIMIENTOS_FETALES"))
        ),
        lecturas_por_sesion={
            1: (lectura(549, t(1, 8, 27), semana=36, hr="83.00", spo2="96.00"),),
            811: (lectura_de_movimiento(1259, t(21, 14, 56), 7, semaforo="WARNING", semana=39),),
        },
        **extra,
    )


def test_los_ultimos_registros_viajan_con_su_origen_y_su_unidad(tmp_path):
    cliente = portal(tmp_path, central_de_paciente30())

    datos = cliente.get("/adaptador/embarazos/129/monitoreo").json()["datos"]
    ultimos = datos["ultimos_registros"]

    assert ultimos["frecuencia_cardiaca"] == {
        "valor": "83.00",
        "unidad": "BPM",
        "fecha_hora_captura": "2026-06-01T08:27:00+00:00",
        "semana_gestacion_lectura": 36,
        "id_lectura": 549,
        "id_sesion": 1,
    }
    assert ultimos["saturacion_oxigeno"]["valor"] == "96.00"
    assert ultimos["saturacion_oxigeno"]["unidad"] == "%"
    assert ultimos["movimientos_fetales"]["valor"] == 7
    assert ultimos["movimientos_fetales"]["id_lectura"] == 1259
    # Ninguna clasificación por variable: la API solo clasifica lecturas
    # completas, y esa clasificación sigue en su sitio.
    for registro in ultimos.values():
        assert not any("semaforo" in clave for clave in registro)
    for serie_ in datos["series"].values():
        assert all(not any("semaforo" in c for c in p) for p in serie_["puntos"])
    assert datos["ultima_lectura"]["codigo_semaforo"] == "WARNING"
    assert datos["sesiones"][0]["lecturas"][0]["codigo_semaforo"] == "OK"
    # El contrato anterior sigue intacto.
    assert datos["ultima_lectura"]["id_lectura"] == 1259
    assert len(datos["sesiones"]) == 2


def test_las_series_van_en_orden_con_unidad_y_sin_puntos_inventados(tmp_path):
    cliente = portal(tmp_path, central_de_paciente30())

    series = cliente.get("/adaptador/embarazos/129/monitoreo").json()["datos"]["series"]

    assert series["frecuencia_cardiaca"]["unidad"] == "BPM"
    assert [p["id_lectura"] for p in series["frecuencia_cardiaca"]["puntos"]] == [549]
    assert [p["valor"] for p in series["movimientos_fetales"]["puntos"]] == [7]
    assert series["saturacion_oxigeno"]["puntos"][-1] == (
        cliente.get("/adaptador/embarazos/129/monitoreo").json()["datos"]
        ["ultimos_registros"]["saturacion_oxigeno"]
    )


def test_el_cero_y_la_ausencia_llegan_distintos_al_navegador(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(EstadoRespuesta.OK, datos=(sesion(1),)),
        lecturas_por_sesion={1: (lectura_de_movimiento(5, t(1, 8), 0),)},
    )
    cliente = portal(tmp_path, central)

    ultimos = cliente.get("/adaptador/embarazos/101/monitoreo").json()["datos"][
        "ultimos_registros"
    ]

    assert ultimos["movimientos_fetales"]["valor"] == 0
    assert ultimos["frecuencia_cardiaca"] is None
    assert ultimos["saturacion_oxigeno"] is None


def test_sin_lecturas_los_tres_registros_son_nulos_y_las_series_vacias(tmp_path):
    cliente = portal(tmp_path, ClienteClinicoDoble())

    datos = cliente.get("/adaptador/embarazos/101/monitoreo").json()["datos"]

    assert datos["ultimos_registros"] == {
        "frecuencia_cardiaca": None,
        "saturacion_oxigeno": None,
        "movimientos_fetales": None,
    }
    assert all(s["puntos"] == [] for s in datos["series"].values())


def test_un_fallo_a_mitad_no_entrega_un_resumen_parcial(tmp_path):
    """Si una sesión no se pudo leer, no hay «último registro» que afirmar."""

    class FallaEnLaSegunda(ClienteClinicoDoble):
        def lecturas(self, token, id_sesion):
            if id_sesion == 811:
                return RespuestaClinica(EstadoRespuesta.SIN_CONEXION)
            return super().lecturas(token, id_sesion)

    base = central_de_paciente30()
    central = FallaEnLaSegunda(
        respuesta_sesiones=base.respuesta_sesiones,
        lecturas_por_sesion=base.lecturas_por_sesion,
    )
    cliente = portal(tmp_path, central)

    cuerpo = cliente.get("/adaptador/embarazos/129/monitoreo").json()

    assert cuerpo == {"disponible": False, "motivo": "sin_conexion"}


def test_un_embarazo_ajeno_no_entrega_nada(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_sesiones=RespuestaClinica(EstadoRespuesta.NO_ENCONTRADO)
    )
    cliente = portal(tmp_path, central)

    respuesta = cliente.get("/adaptador/embarazos/100/monitoreo")

    assert respuesta.status_code == 404
    assert "ultimos_registros" not in respuesta.text
    assert central.lecturas_pedidas == []


def test_cada_cuenta_consulta_con_su_propio_token(tmp_path):
    """El adaptador no mezcla sesiones: la autorización la decide el servidor con el token de cada una."""

    class RegistraTokens(ClienteClinicoDoble):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.tokens_usados: list[str] = []
            self.emitidos = iter(["token-cuenta-a", "token-cuenta-b"])

        def autenticar(self, *, email, password):
            super().autenticar(email=email, password=password)
            return RespuestaToken(EstadoRespuesta.OK, token=next(self.emitidos))

        def sesiones(self, token, id_embarazo):
            self.tokens_usados.append(token)
            return super().sesiones(token, id_embarazo)

    central = RegistraTokens()
    cliente_a, _, _, _ = construir_cliente(tmp_path, central=central)
    iniciar_sesion(cliente_a)
    cliente_b = TestClient(cliente_a.app)
    iniciar_sesion(cliente_b)

    cliente_a.get("/adaptador/embarazos/101/monitoreo")
    cliente_b.get("/adaptador/embarazos/102/monitoreo")

    assert central.tokens_usados == ["token-cuenta-a", "token-cuenta-b"]


# -- Semana actual frente a semana de la lectura -----------------------------


def test_la_semana_actual_se_calcula_en_el_dia_de_panama(tmp_path):
    """03:00Z del 25 es el 24 a las 22:00 en Panamá: el día clínico es el 24."""
    reloj = RelojFalso(datetime(2026, 9, 25, 3, 0, tzinfo=UTC))
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(
            EstadoRespuesta.OK, datos=(embarazo(130, inicio=date(2026, 9, 18)),)
        )
    )
    cliente, _, _, _ = construir_cliente(tmp_path, central=central, reloj=reloj)
    iniciar_sesion(cliente)

    datos = cliente.get("/adaptador/embarazos").json()["datos"]

    assert datos["hoy"] == "2026-09-24"
    # 6 días desde el inicio: semana 1. En UTC serían 7 días y semana 2.
    assert datos["semana_actual"] == 1


def test_pasada_la_fecha_probable_de_parto_no_se_publica_semana_actual(tmp_path):
    """El episodio 129 de paciente30: ACTIVO, con su FPP (1/7/2026) ya pasada.

    Un «semana actual: 53» se leería como seguimiento vigente. No se publica, y
    las fechas y el estado del episodio llegan intactos.
    """
    reloj = RelojFalso(datetime(2026, 9, 25, 15, 0, tzinfo=UTC))
    episodio_129 = EmbarazoResumen(
        id_embarazo=129,
        fecha_inicio=date(2025, 9, 24),
        fecha_probable_parto=date(2026, 7, 1),
        estado_embarazo="ACTIVO",
        fecha_cierre=None,
    )
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=(episodio_129,))
    )
    cliente, _, _, _ = construir_cliente(tmp_path, central=central, reloj=reloj)
    iniciar_sesion(cliente)

    datos = cliente.get("/adaptador/embarazos").json()["datos"]

    assert datos["semana_actual"] is None
    assert datos["actual"] == {
        "id_embarazo": 129,
        "fecha_inicio": "2025-09-24",
        "fecha_probable_parto": "2026-07-01",
        "estado_embarazo": "ACTIVO",
        "fecha_cierre": None,
    }


def test_el_dia_de_la_fecha_probable_de_parto_aun_hay_semana_actual(tmp_path):
    reloj = RelojFalso(datetime(2026, 7, 1, 20, 0, tzinfo=UTC))  # 1/7 en Panamá
    episodio = EmbarazoResumen(
        id_embarazo=129,
        fecha_inicio=date(2025, 9, 24),
        fecha_probable_parto=date(2026, 7, 1),
        estado_embarazo="ACTIVO",
        fecha_cierre=None,
    )
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=(episodio,))
    )
    cliente, _, _, _ = construir_cliente(tmp_path, central=central, reloj=reloj)
    iniciar_sesion(cliente)

    assert cliente.get("/adaptador/embarazos").json()["datos"]["semana_actual"] == 41


def test_la_semana_actual_no_lleva_topes_numericos(tmp_path):
    """Si la FPP registrada está lejos, la semana se dice tal cual, aunque pase de 42."""
    reloj = RelojFalso(datetime(2026, 9, 25, 15, 0, tzinfo=UTC))
    episodio = EmbarazoResumen(
        id_embarazo=129,
        fecha_inicio=date(2025, 9, 24),
        fecha_probable_parto=date(2026, 12, 31),
        estado_embarazo="ACTIVO",
        fecha_cierre=None,
    )
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=(episodio,))
    )
    cliente, _, _, _ = construir_cliente(tmp_path, central=central, reloj=reloj)
    iniciar_sesion(cliente)

    assert cliente.get("/adaptador/embarazos").json()["datos"]["semana_actual"] == 53


def test_sin_embarazo_en_curso_no_hay_semana_actual(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(
            EstadoRespuesta.OK,
            datos=(embarazo(100, estado="FINALIZADO", cierre=date(2025, 10, 13)),),
        )
    )
    cliente = portal(tmp_path, central)

    assert cliente.get("/adaptador/embarazos").json()["datos"]["semana_actual"] is None


# ===========================================================================
# ESTADO DE LA CONEXIÓN
# ===========================================================================


def estado(cliente):
    return cliente.get("/adaptador/estado-conexion")


def test_sin_sesion_local_el_estado_de_conexion_es_401(tmp_path):
    cliente, _, _, _ = construir_cliente(tmp_path)

    assert estado(cliente).status_code == 401


def test_con_token_aceptado_el_estado_es_vigente(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)

    cuerpo = estado(cliente).json()

    assert cuerpo["api_central"] == "disponible"
    assert cuerpo["autenticacion_central"] == "vigente"


def test_el_token_vencido_no_es_falta_de_conexion(tmp_path):
    """Treinta y un minutos después, ``/yo`` responde 401: se pide reautenticar."""
    reloj = RelojFalso()
    cliente, central, _, _ = construir_cliente(tmp_path, reloj=reloj)
    iniciar_sesion(cliente)

    reloj.avanzar(minutes=31)
    central.estado_identidad = EstadoRespuesta.RECHAZADO

    cuerpo = estado(cliente).json()

    assert cuerpo["api_central"] == "disponible"
    assert cuerpo["autenticacion_central"] == "reautenticacion_requerida"
    # El token se olvidó: la siguiente consulta clínica ni siquiera va a la red.
    antes = central.llamadas_identidad
    segundo = estado(cliente).json()
    assert segundo["autenticacion_central"] == "reautenticacion_requerida"
    assert central.llamadas_identidad == antes
    # Y la sesión local sigue abierta.
    assert cliente.get("/adaptador/sesion").json()["autenticada"] is True


def test_un_403_no_se_trata_como_token_vencido(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)
    central.estado_identidad = EstadoRespuesta.PROHIBIDO

    primero = estado(cliente).json()
    segundo = estado(cliente).json()

    assert primero["autenticacion_central"] == "acceso_denegado"
    assert segundo["autenticacion_central"] == "acceso_denegado"
    # El token se conservó: se volvió a preguntar con él.
    assert central.llamadas_identidad >= 3


def test_con_la_api_caida_se_dice_sin_conexion_y_se_conserva_el_token(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)
    central.estado_identidad = EstadoRespuesta.SIN_CONEXION

    cuerpo = estado(cliente).json()

    assert cuerpo["api_central"] == "no_disponible"
    assert cuerpo["autenticacion_central"] == "no_comprobada"

    central.estado_identidad = EstadoRespuesta.OK
    assert estado(cliente).json()["autenticacion_central"] == "vigente"


def test_un_error_del_servidor_no_se_confunde_con_falta_de_red(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)
    central.estado_identidad = EstadoRespuesta.ERROR_REMOTO

    assert estado(cliente).json()["api_central"] == "con_errores"


def test_sin_token_en_memoria_se_pregunta_solo_por_la_salud(tmp_path):
    """El proceso del portal se reinició: el token se perdió, como debe."""
    cliente, central, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)
    central.disponible_ = False
    # Lo que deja un reinicio: la sesión local en SQLite, sin token en memoria.
    cliente.app.state.contexto.tokens = type(cliente.app.state.contexto.tokens)()

    cuerpo = estado(cliente).json()

    assert cuerpo == {
        "api_central": "no_disponible",
        "autenticacion_central": "reautenticacion_requerida",
        "sesion_local_expira_en": cuerpo["sesion_local_expira_en"],
    }


def test_consultar_el_estado_no_alarga_la_ventana_local(tmp_path):
    reloj = RelojFalso()
    cliente, _, _, _ = construir_cliente(tmp_path, reloj=reloj)
    iniciar_sesion(cliente)

    primero = estado(cliente).json()["sesion_local_expira_en"]
    reloj.avanzar(hours=5)
    segundo = estado(cliente).json()["sesion_local_expira_en"]

    assert primero == segundo


def test_si_el_token_habla_por_otra_cuenta_la_sesion_se_cierra(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)
    central.id_usuario = ID_USUARIO_DE_PRUEBA + 1

    assert estado(cliente).status_code == 401
    assert cliente.get("/adaptador/sesion").json()["autenticada"] is False


# ===========================================================================
# REAUTENTICACIÓN
# ===========================================================================


def reautenticar(cliente, **cuerpo):
    datos = {"email": EMAIL_DE_PRUEBA, "password": PASSWORD_DE_PRUEBA}
    datos.update(cuerpo)
    return cliente.post("/adaptador/reautenticar", json=datos)


def vencer_token(cliente, central):
    central.estado_identidad = EstadoRespuesta.RECHAZADO
    assert estado(cliente).json()["autenticacion_central"] == "reautenticacion_requerida"
    central.estado_identidad = EstadoRespuesta.OK


def test_reautenticar_recupera_la_lectura_clinica_sin_cerrar_la_sesion(tmp_path):
    central = ClienteClinicoDoble()
    cliente, _, _, _ = construir_cliente(tmp_path, central=central)
    iniciar_sesion(cliente)
    vencer_token(cliente, central)
    assert cliente.get("/adaptador/embarazos").json()["motivo"] == "reautenticacion_requerida"

    respuesta = reautenticar(cliente)

    assert respuesta.status_code == 200
    assert respuesta.json()["autenticacion_central"] == "vigente"
    assert cliente.get("/adaptador/embarazos").json()["disponible"] is True


def test_reautenticar_renueva_la_ventana_local_como_una_validacion_en_linea(tmp_path):
    reloj = RelojFalso()
    cliente, central, _, _ = construir_cliente(tmp_path, reloj=reloj)
    iniciar_sesion(cliente)
    antes = estado(cliente).json()["sesion_local_expira_en"]
    reloj.avanzar(hours=1)

    despues = reautenticar(cliente).json()["sesion_local_expira_en"]

    assert despues > antes


def test_reautenticar_con_credenciales_que_no_sirven_es_400_y_no_cierra_nada(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)
    central.estado_token = EstadoRespuesta.RECHAZADO

    respuesta = reautenticar(cliente, password="otra-cosa")

    assert respuesta.status_code == 400
    assert respuesta.json()["detail"] == "Correo o contraseña incorrectos."
    assert cliente.get("/adaptador/sesion").json()["autenticada"] is True


def test_otra_cuenta_no_puede_heredar_la_sesion_local(tmp_path):
    central = ClienteClinicoDoble()
    cliente, _, _, _ = construir_cliente(tmp_path, central=central)
    iniciar_sesion(cliente)
    vencer_token(cliente, central)
    central.id_usuario = ID_USUARIO_DE_PRUEBA + 7

    respuesta = reautenticar(cliente, email="otra@example.com")

    assert respuesta.status_code == 403
    # No se guardó ese token: la lectura clínica sigue pidiendo reautenticar.
    assert cliente.get("/adaptador/embarazos").json()["motivo"] == "reautenticacion_requerida"


def test_un_rol_que_no_es_paciente_no_reautentica(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)
    central.rol = NombreRol.MEDICO

    assert reautenticar(cliente).status_code == 403


def test_reautenticar_sin_conexion_es_503_y_conserva_la_sesion(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)
    central.estado_token = EstadoRespuesta.SIN_CONEXION

    respuesta = reautenticar(cliente)

    assert respuesta.status_code == 503
    assert "registros siguen guardados" in respuesta.json()["detail"]
    assert cliente.get("/adaptador/sesion").json()["autenticada"] is True


def test_reautenticar_exige_sesion_local(tmp_path):
    cliente, _, _, _ = construir_cliente(tmp_path)

    assert reautenticar(cliente).status_code == 401


def test_la_contrasena_de_la_reautenticacion_no_llega_al_disco(tmp_path):
    cliente, _, _, settings = construir_cliente(tmp_path)
    iniciar_sesion(cliente)
    reautenticar(cliente)

    for archivo in tmp_path.rglob("*"):
        if archivo.is_file():
            assert PASSWORD_DE_PRUEBA.encode() not in archivo.read_bytes()


def test_iniciar_sesion_con_un_403_de_identidad_es_403_y_no_502(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)
    central.estado_identidad = EstadoRespuesta.PROHIBIDO

    assert iniciar_sesion(cliente).status_code == 403


def test_revalidar_con_un_403_conserva_el_token(tmp_path):
    cliente, central, _, _ = construir_cliente(tmp_path)
    iniciar_sesion(cliente)
    central.estado_identidad = EstadoRespuesta.PROHIBIDO

    cliente.get("/adaptador/sesion")
    central.estado_identidad = EstadoRespuesta.OK

    assert estado(cliente).json()["autenticacion_central"] == "vigente"


# ===========================================================================
# REGISTROS LOCALES DURANTE LA INDISPONIBILIDAD
# ===========================================================================


def test_con_el_token_vencido_los_registros_se_guardan_y_nada_se_da_por_enviado(tmp_path):
    cliente, central, _, _ = cliente_con_provision(tmp_path)
    vencer_token(cliente, central)

    registro = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "MOVIMIENTOS_FETALES"},
    )
    envio = cliente.post("/adaptador/movimientos/sincronizar").json()
    cola = cliente.get("/adaptador/movimientos/estado").json()

    assert registro.status_code == 201
    assert envio == {"disponible": False, "motivo": "reautenticacion_requerida"}
    assert cola["pendientes"] == 1
    assert cola["enviados"] == 0
    assert cola["ultimo_envio_confirmado"] is None


def test_el_ultimo_envio_confirmado_sale_de_la_outbox(tmp_path):
    cliente, _, reloj, _ = cliente_con_provision(
        tmp_path, constructor_cliente_edge=_constructor_que_responde(*ENTREGA_ACEPTADA)
    )
    cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "MOVIMIENTOS_FETALES"},
    )
    assert cliente.get("/adaptador/movimientos/estado").json()["ultimo_envio_confirmado"] is None

    cliente.post("/adaptador/movimientos/sincronizar")

    ultimo = cliente.get("/adaptador/movimientos/estado").json()["ultimo_envio_confirmado"]
    assert ultimo is not None
    assert datetime.fromisoformat(ultimo).tzinfo is not None
