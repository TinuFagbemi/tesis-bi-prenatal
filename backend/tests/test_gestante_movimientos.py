"""Sesiones de movimiento simuladas: aprovisionamiento, captura y envio automatico.

Cuatro capas, como en el resto de la interfaz de la gestante:

* :mod:`app.gestante.provision` lee y valida el aprovisionamiento del
  dispositivo -- a que cuenta y a que embarazo sirve, con que catalogos --;
* :mod:`app.gestante.simulacion` es puro: resuelve las referencias de ese
  aprovisionamiento y **deriva** el semaforo con la autoridad SIM-1.0, en vez
  de escogerlo. El paquete se valida contra el contrato real
  ``SesionMonitoreoEntrada``;
* :mod:`app.gestante.movimientos` abre SQLite de verdad, en ``tmp_path``, y
  ejercita :mod:`app.edge` sin ningun doble;
* las rutas se prueban por HTTP, con el cliente central sustituido, y el
  envio automatico llamando a su ciclo con el transporte en
  ``httpx.MockTransport``, una API que puede caerse y volver.

**El caso que da sentido al diseno** esta en
``test_registrar_funciona_con_la_api_central_caida``: la captura tiene que
funcionar sin red, y por eso la autorizacion de ese paso se apoya en el
aprovisionamiento del dispositivo y no en una consulta al servidor.

No hace falta PostgreSQL ni la API central en ningun caso. El recorrido contra
una API y una base reales se documenta aparte, en el informe del ticket.

Todas las cuentas y los datos son ficticios y simulados.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.etl.reglas import clasificar_lectura
from app.edge.cliente import CABECERA_IDEMPOTENCIA, RUTA_IDENTIDAD
from app.gestante import envio_automatico, movimientos, provision
from app.gestante.central import EstadoRespuesta, RespuestaClinica
from app.gestante.provision import Provision, ProvisionInvalida, SemanaGestacional
from app.gestante.simulacion import (
    HR_SIMULADO,
    MOV_SIMULADO,
    SPO2_SIMULADO,
    SimulacionNoAplicable,
    construir_paquete_simulado,
)
from app.models.enums import TipoSesion
from app.schemas.monitoreo import SesionMonitoreoEntrada
from tests.test_gestante_clinico import ClienteClinicoDoble, embarazo, portal
from tests.test_gestante_rutas import (
    ID_USUARIO_DE_PRUEBA,
    RelojFalso,
    construir_cliente,
    construir_settings,
    iniciar_sesion,
)

UTC = timezone.utc

# El mismo instante que usa ``RelojFalso`` por omision, para que el reloj de
# las rutas y el de las pruebas puras digan lo mismo.
AHORA = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

SEMANA_DE_PRUEBA = 28
ID_EMBARAZO = 130
ID_DISPOSITIVO = 77

# Identificadores de catalogo con la forma que tienen de verdad: no empiezan en
# 1. Que estas pruebas usen numeros distintos de los de la semana es
# deliberado -- si el codigo asumiera "id = semana", fallarian.
PRIMER_ID_TIEMPO = 100
SEMAFOROS = {"OK": 100, "WARNING": 101, "ERROR": 102}

RUTA_SESIONES_MONITOREO = "/api/v1/sesiones-monitoreo"


# ---------------------------------------------------------------------------
# Constructores del aprovisionamiento
# ---------------------------------------------------------------------------


def _inicio_para_la_semana(semana: int, ahora: datetime = AHORA) -> date:
    """Inicio de embarazo que deja ``ahora`` en esa semana gestacional."""
    return (ahora - timedelta(days=(semana - 1) * 7)).date()


def _catalogo_gestacional() -> list[dict]:
    """Semanas 1 a 40, con el trimestre que les corresponde."""
    return [
        {
            "semana": semana,
            "id_tiempo_gest": PRIMER_ID_TIEMPO + semana - 1,
            "trimestre": 1 if semana <= 13 else (2 if semana <= 27 else 3),
        }
        for semana in range(1, 41)
    ]


def provision_de_prueba(
    *,
    id_usuario: int = ID_USUARIO_DE_PRUEBA,
    id_embarazo: int = ID_EMBARAZO,
    semana: int = SEMANA_DE_PRUEBA,
) -> Provision:
    return Provision(
        id_usuario=id_usuario,
        id_embarazo=id_embarazo,
        fecha_inicio_embarazo=_inicio_para_la_semana(semana),
        id_dispositivo=ID_DISPOSITIVO,
        semaforos=dict(SEMAFOROS),
        semanas={
            fila["semana"]: SemanaGestacional(
                id_tiempo_gest=fila["id_tiempo_gest"], trimestre=fila["trimestre"]
            )
            for fila in _catalogo_gestacional()
        },
    )


def escribir_provision(
    tmp_path: Path,
    *,
    id_usuario: int = ID_USUARIO_DE_PRUEBA,
    id_embarazo: int = ID_EMBARAZO,
    semana: int = SEMANA_DE_PRUEBA,
    version: int = provision.VERSION_SOPORTADA,
) -> Path:
    """Un archivo de aprovisionamiento con la misma forma que escribe el script."""
    ruta = tmp_path / "provision.json"
    ruta.write_text(
        json.dumps(
            {
                "version": version,
                "generado_en": AHORA.isoformat(),
                "cuenta": {"id_usuario": id_usuario},
                "embarazo": {
                    "id_embarazo": id_embarazo,
                    "fecha_inicio": _inicio_para_la_semana(semana).isoformat(),
                },
                "dispositivo": {"id_dispositivo": ID_DISPOSITIVO},
                "catalogos": {
                    "semaforo": SEMAFOROS,
                    "tiempo_gestacional": _catalogo_gestacional(),
                },
            }
        ),
        encoding="utf-8",
    )
    return ruta


def central_que_reconoce_el_embarazo() -> ClienteClinicoDoble:
    """El doble por omision: el servidor confirma que el episodio es de ella.

    Importa que sea el valor por omision, porque el adaptador **si** contrasta
    con el servidor cuando hay token: un doble que devolviera una lista vacia
    haria que toda prueba de registro terminara en 404 por la razon
    equivocada.
    """
    return ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(
            EstadoRespuesta.OK, datos=(embarazo(ID_EMBARAZO),)
        )
    )


def cliente_con_provision(tmp_path, *, central=None, semana: int = SEMANA_DE_PRUEBA, **ajustes):
    """Un portal con sesion iniciada y el dispositivo ya aprovisionado."""
    escribir_provision(tmp_path, semana=semana)
    cliente, doble, reloj, settings = construir_cliente(
        tmp_path,
        central=central or central_que_reconoce_el_embarazo(),
        provision_path=tmp_path / "provision.json",
        **ajustes,
    )
    iniciar_sesion(cliente)
    return cliente, doble, reloj, settings


# ===========================================================================
# EL APROVISIONAMIENTO
# ===========================================================================


def test_sin_archivo_el_dispositivo_no_esta_aprovisionado(tmp_path):
    with pytest.raises(ProvisionInvalida):
        provision.cargar(tmp_path / "no-existe.json")


def test_una_version_desconocida_no_se_interpreta(tmp_path):
    ruta = escribir_provision(tmp_path, version=99)

    with pytest.raises(ProvisionInvalida):
        provision.cargar(ruta)


def test_un_archivo_ilegible_no_se_completa_con_valores_por_omision(tmp_path):
    ruta = tmp_path / "provision.json"
    ruta.write_text("{ esto no es json", encoding="utf-8")

    with pytest.raises(ProvisionInvalida):
        provision.cargar(ruta)


def test_el_aprovisionamiento_se_lee_con_los_identificadores_reales(tmp_path):
    cargada = provision.cargar(escribir_provision(tmp_path))

    assert cargada.id_usuario == ID_USUARIO_DE_PRUEBA
    assert cargada.id_embarazo == ID_EMBARAZO
    assert cargada.id_dispositivo == ID_DISPOSITIVO
    assert cargada.id_semaforo_de("OK") == SEMAFOROS["OK"]
    assert cargada.semana(SEMANA_DE_PRUEBA).id_tiempo_gest == (
        PRIMER_ID_TIEMPO + SEMANA_DE_PRUEBA - 1
    )


def test_servir_exige_la_cuenta_y_el_embarazo(tmp_path):
    """Con una sola de las dos condiciones, el aislamiento se cae."""
    cargada = provision.cargar(escribir_provision(tmp_path))

    assert cargada.sirve_a(ID_USUARIO_DE_PRUEBA, ID_EMBARAZO) is True
    assert cargada.sirve_a(ID_USUARIO_DE_PRUEBA, ID_EMBARAZO + 1) is False
    assert cargada.sirve_a(ID_USUARIO_DE_PRUEBA + 1, ID_EMBARAZO) is False


def test_el_archivo_no_guarda_datos_personales_de_la_cuenta(tmp_path):
    """Ni correo, ni nombre, ni telefono: la vinculacion es por id_usuario."""
    contenido = escribir_provision(tmp_path).read_text(encoding="utf-8")

    assert "@" not in contenido
    assert "correo" not in contenido
    assert "email" not in contenido


# ===========================================================================
# EL PAQUETE: REFERENCIAS RESUELTAS Y SEMAFORO DERIVADO
# ===========================================================================


def test_el_paquete_de_signos_maternos_cumple_el_contrato_real():
    paquete = construir_paquete_simulado(
        provision=provision_de_prueba(),
        tipo_sesion=TipoSesion.SIGNOS_MATERNOS,
        ahora=AHORA,
    )

    validado = SesionMonitoreoEntrada.model_validate(paquete)

    assert validado.id_embarazo == ID_EMBARAZO
    assert validado.id_dispositivo == ID_DISPOSITIVO
    assert validado.tipo_sesion is TipoSesion.SIGNOS_MATERNOS
    lectura = validado.lecturas[0]
    assert lectura.hr_valor == HR_SIMULADO
    assert lectura.spo2_valor == SPO2_SIMULADO
    assert lectura.mov_valor is None


def test_el_paquete_de_movimiento_cumple_el_contrato_real():
    paquete = construir_paquete_simulado(
        provision=provision_de_prueba(),
        tipo_sesion=TipoSesion.MOVIMIENTOS_FETALES,
        ahora=AHORA,
    )

    validado = SesionMonitoreoEntrada.model_validate(paquete)

    assert validado.tipo_sesion is TipoSesion.MOVIMIENTOS_FETALES
    lectura = validado.lecturas[0]
    assert lectura.mov_valor == MOV_SIMULADO
    assert lectura.hr_valor is None
    assert lectura.spo2_valor is None


def test_el_identificador_gestacional_sale_del_catalogo_y_no_de_la_semana():
    """Si el codigo asumiera «id = semana», este numero seria 28 y no 127."""
    paquete = construir_paquete_simulado(
        provision=provision_de_prueba(),
        tipo_sesion=TipoSesion.SIGNOS_MATERNOS,
        ahora=AHORA,
    )

    esperado = PRIMER_ID_TIEMPO + SEMANA_DE_PRUEBA - 1
    assert paquete["lecturas"][0]["id_tiempo_gest"] == esperado
    assert esperado != SEMANA_DE_PRUEBA


@pytest.mark.parametrize(
    "tipo, hr, spo2, mov",
    [
        (TipoSesion.SIGNOS_MATERNOS, HR_SIMULADO, SPO2_SIMULADO, None),
        (TipoSesion.MOVIMIENTOS_FETALES, None, None, MOV_SIMULADO),
    ],
)
def test_el_semaforo_se_deriva_con_sim_1_0_y_no_se_escoge(tipo, hr, spo2, mov):
    """El ETL aborta la corrida entera si el nivel operacional no coincide con
    el que SIM-1.0 deriva (``DiscrepanciaDeSemaforo``). Por eso el paquete no
    puede llevar un semaforo elegido a mano: esta prueba compara contra la
    misma autoridad, llamada aqui de forma independiente."""
    prov = provision_de_prueba()
    paquete = construir_paquete_simulado(provision=prov, tipo_sesion=tipo, ahora=AHORA)

    fila = prov.semana(SEMANA_DE_PRUEBA)
    estados = clasificar_lectura(
        hr_valor=hr,
        spo2_valor=spo2,
        mov_valor=mov,
        semana=SEMANA_DE_PRUEBA,
        trimestre=fila.trimestre,
    )

    assert paquete["lecturas"][0]["id_semaforo"] == SEMAFOROS[estados.codigo_global]


def test_dos_llamadas_con_la_misma_hora_producen_el_mismo_paquete():
    """Reproducible: nada al azar, nada que dependa del reloj del sistema."""
    prov = provision_de_prueba()
    a = construir_paquete_simulado(
        provision=prov, tipo_sesion=TipoSesion.SIGNOS_MATERNOS, ahora=AHORA
    )
    b = construir_paquete_simulado(
        provision=prov, tipo_sesion=TipoSesion.SIGNOS_MATERNOS, ahora=AHORA
    )
    assert a == b


def test_una_semana_fuera_del_catalogo_no_se_registra():
    """El caso real del dataset canonico: episodios que hoy van por la semana
    50 o mas, que el contrato de ingesta no admite. Mejor negarse aqui que
    guardar algo que el servidor rechazaria despues."""
    lejano = Provision(
        id_usuario=ID_USUARIO_DE_PRUEBA,
        id_embarazo=ID_EMBARAZO,
        fecha_inicio_embarazo=_inicio_para_la_semana(60),
        id_dispositivo=ID_DISPOSITIVO,
        semaforos=dict(SEMAFOROS),
        semanas=provision_de_prueba().semanas,
    )

    with pytest.raises(SimulacionNoAplicable):
        construir_paquete_simulado(
            provision=lejano, tipo_sesion=TipoSesion.SIGNOS_MATERNOS, ahora=AHORA
        )


def test_no_se_registra_movimiento_antes_de_la_semana_veinte():
    temprano = provision_de_prueba(semana=12)

    with pytest.raises(SimulacionNoAplicable):
        construir_paquete_simulado(
            provision=temprano,
            tipo_sesion=TipoSesion.MOVIMIENTOS_FETALES,
            ahora=AHORA,
        )

    # Los signos maternos si se admiten en la semana 12: la regla es del
    # movimiento fetal, no de la sesion simulada en general.
    construir_paquete_simulado(
        provision=temprano, tipo_sesion=TipoSesion.SIGNOS_MATERNOS, ahora=AHORA
    )


# ===========================================================================
# CAPTURA Y ESTADO LOCAL (SQLite real, app.edge real, sin dobles)
# ===========================================================================


def test_registrar_una_sesion_simulada_queda_pendiente(tmp_path):
    settings = construir_settings(tmp_path)

    movimientos.registrar_sesion_simulada(
        settings,
        id_usuario=1,
        provision=provision_de_prueba(),
        tipo_sesion=TipoSesion.SIGNOS_MATERNOS,
        ahora=AHORA,
    )

    estado = movimientos.leer_estado_de_la_cuenta(settings, 1)
    assert estado.inicializado is True
    assert estado.pendientes == 1
    assert estado.total == 1


def test_una_cuenta_sin_registros_lo_dice_sin_mandar_a_inicializar_el_nodo(tmp_path):
    """El archivo por cuenta lo crea el portal solo; no se pide a la paciente
    que ejecute un comando del nodo edge."""
    settings = construir_settings(tmp_path)

    estado = movimientos.leer_estado_de_la_cuenta(settings, 999)

    assert estado.inicializado is False
    assert estado.pendientes is None
    assert estado.detalle == movimientos.MENSAJE_SIN_REGISTROS
    assert "edge_node" not in estado.detalle


def test_el_registro_sobrevive_a_una_nueva_conexion(tmp_path):
    """«Reiniciar el componente» es exactamente esto: una conexion nueva."""
    settings = construir_settings(tmp_path)
    movimientos.registrar_sesion_simulada(
        settings,
        id_usuario=7,
        provision=provision_de_prueba(),
        tipo_sesion=TipoSesion.SIGNOS_MATERNOS,
        ahora=AHORA,
    )

    assert movimientos.leer_estado_de_la_cuenta(settings, 7).total == 1
    assert movimientos.leer_estado_de_la_cuenta(settings, 7).total == 1


def test_dos_cuentas_tienen_archivos_distintos_y_no_se_mezclan(tmp_path):
    settings = construir_settings(tmp_path)

    movimientos.registrar_sesion_simulada(
        settings,
        id_usuario=1,
        provision=provision_de_prueba(),
        tipo_sesion=TipoSesion.SIGNOS_MATERNOS,
        ahora=AHORA,
    )

    assert movimientos.ruta_para_la_cuenta(
        settings, 1
    ) != movimientos.ruta_para_la_cuenta(settings, 2)
    assert movimientos.leer_estado_de_la_cuenta(settings, 1).total == 1
    assert movimientos.leer_estado_de_la_cuenta(settings, 2).inicializado is False


# ===========================================================================
# LA RUTA DE REGISTRO
# ===========================================================================


def test_registrar_sin_sesion_local_responde_401(tmp_path):
    escribir_provision(tmp_path)
    cliente, _, _, _ = construir_cliente(
        tmp_path,
        central=ClienteClinicoDoble(),
        provision_path=tmp_path / "provision.json",
    )

    respuesta = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 401


def test_sin_aprovisionamiento_el_dispositivo_lo_dice(tmp_path):
    """503 y no 500: no es un fallo, es un dispositivo que falta preparar."""
    cliente = portal(tmp_path, ClienteClinicoDoble())

    respuesta = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 503
    assert "aprovisionado" in respuesta.json()["detail"]


def test_un_embarazo_distinto_al_aprovisionado_responde_404(tmp_path):
    cliente, _, _, _ = cliente_con_provision(tmp_path)

    respuesta = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO + 1}/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 404


def test_otra_cuenta_no_puede_usar_el_aprovisionamiento_de_esta(tmp_path):
    """El archivo vincula cuenta **y** embarazo; una sesion de otra cuenta en
    el mismo dispositivo no hereda el aprovisionamiento."""
    escribir_provision(tmp_path, id_usuario=ID_USUARIO_DE_PRUEBA + 1)
    cliente, _, _, _ = construir_cliente(
        tmp_path,
        central=ClienteClinicoDoble(),
        provision_path=tmp_path / "provision.json",
    )
    iniciar_sesion(cliente)

    respuesta = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 404


def test_registrar_un_embarazo_propio_queda_local_y_pendiente(tmp_path):
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(
            EstadoRespuesta.OK, datos=(embarazo(ID_EMBARAZO),)
        )
    )
    cliente, _, _, _ = cliente_con_provision(tmp_path, central=central)

    respuesta = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["registrado"] is True
    assert cuerpo["estado"] == "local"
    assert "clave" in cuerpo

    estado = cliente.get("/adaptador/movimientos/estado").json()
    assert estado["pendientes"] == 1
    assert estado["total"] == 1


def test_registrar_funciona_con_la_api_central_caida(tmp_path):
    """**El caso que justifica el diseno.** Sin red no se puede preguntar de
    quien es el embarazo, y aun asi la captura tiene que funcionar: la
    autorizacion de este paso la da el aprovisionamiento del dispositivo, que
    el navegador no puede alterar. El servidor vuelve a comprobarlo todo
    cuando el paquete se sincroniza."""
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.SIN_CONEXION),
        disponible_=False,
    )
    cliente, _, _, _ = cliente_con_provision(tmp_path, central=central)

    respuesta = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 201
    assert cliente.get("/adaptador/movimientos/estado").json()["pendientes"] == 1


def test_si_el_servidor_dice_que_el_embarazo_no_es_suyo_se_rechaza(tmp_path):
    """Cuando si hay con que preguntar, la respuesta del servidor manda sobre
    el aprovisionamiento: es una comprobacion adicional, no una alternativa."""
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(EstadoRespuesta.OK, datos=())
    )
    cliente, _, _, _ = cliente_con_provision(tmp_path, central=central)

    respuesta = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 404


def test_una_semana_fuera_de_rango_responde_422_y_no_guarda_nada(tmp_path):
    cliente, _, _, _ = cliente_con_provision(tmp_path, semana=60)

    respuesta = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    assert respuesta.status_code == 422
    assert cliente.get("/adaptador/movimientos/estado").json()["inicializado"] is False


def test_un_tipo_de_sesion_invalido_es_422(tmp_path):
    cliente, _, _, _ = cliente_con_provision(tmp_path)

    respuesta = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "ALGO_QUE_NO_EXISTE"},
    )

    assert respuesta.status_code == 422


def test_un_cuerpo_con_un_campo_extra_es_422(tmp_path):
    cliente, _, _, _ = cliente_con_provision(tmp_path)

    respuesta = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS", "hr_valor": "999.00"},
    )

    assert respuesta.status_code == 422


def test_el_navegador_nunca_recibe_los_identificadores_tecnicos(tmp_path):
    """``id_dispositivo``, ``id_tiempo_gest`` e ``id_semaforo`` viven en el
    aprovisionamiento y en el paquete; la respuesta al navegador no los lleva."""
    cliente, _, _, _ = cliente_con_provision(tmp_path)

    texto = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    ).text

    for campo in ("id_dispositivo", "id_tiempo_gest", "id_semaforo"):
        assert campo not in texto


# ===========================================================================
# EL ENVIO AUTOMATICO (mecanismo real de app.edge, transporte con doble)
# ===========================================================================
#
# Nadie pulsa «enviar»: ``envio_automatico.ciclo`` es lo que el hilo del portal
# ejecuta cada pocos segundos. Aqui se llama directamente, sin hilo ni esperas,
# y la API es un ``httpx.MockTransport`` que puede caerse y volver.

IDENTIDAD_PACIENTE = {"id_usuario": ID_USUARIO_DE_PRUEBA, "rol": "PACIENTE"}

ENTREGA_ACEPTADA = (
    201,
    {"id_sesion": 555, "lecturas_creadas": 1, "ids_lectura": [999]},
    {"Idempotency-Replayed": "false"},
)
ENTREGA_REPRODUCIDA = (
    201,
    {"id_sesion": 555, "lecturas_creadas": 1, "ids_lectura": [999]},
    {"Idempotency-Replayed": "true"},
)


class ApiFalsa:
    """La API central vista por el emisor: ``/yo`` e ingesta. Puede caerse.

    ``respuestas`` se consumen en orden, una por envio; la ultima se repite.
    Un elemento que sea una excepcion de httpx se lanza, como haria la red.
    """

    def __init__(self, *respuestas, identidad=(200, IDENTIDAD_PACIENTE)):
        self.caida = False
        self.identidad = identidad
        self.respuestas = list(respuestas) or [ENTREGA_ACEPTADA]
        self.envios: list[tuple[str, bytes]] = []
        self.consultas_identidad = 0

    def manejador(self, peticion: httpx.Request) -> httpx.Response:
        if self.caida:
            raise httpx.ConnectError("sin red", request=peticion)
        assert peticion.headers.get("authorization", "").startswith("Bearer ")
        if peticion.url.path == RUTA_IDENTIDAD:
            self.consultas_identidad += 1
            codigo, cuerpo = self.identidad
            return httpx.Response(codigo, json=cuerpo)
        assert peticion.url.path == RUTA_SESIONES_MONITOREO
        self.envios.append((peticion.headers[CABECERA_IDEMPOTENCIA], peticion.content))
        respuesta = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        if isinstance(respuesta, Exception):
            raise type(respuesta)(str(respuesta), request=peticion)
        codigo, cuerpo, cabeceras = respuesta
        return httpx.Response(codigo, json=cuerpo, headers=cabeceras)

    def constructor(self, settings, token):
        return httpx.Client(
            base_url=settings.api_base_url,
            transport=httpx.MockTransport(self.manejador),
            headers={"Authorization": f"Bearer {token}"},
        )


def _portal_con_api(tmp_path, api: ApiFalsa, **ajustes):
    cliente, central, reloj, settings = cliente_con_provision(
        tmp_path, constructor_cliente_edge=api.constructor, **ajustes
    )
    return cliente, central, reloj, settings


def _capturar(cliente):
    """La captura del dispositivo simulado: la interfaz web no la ofrece."""
    respuesta = cliente.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "MOVIMIENTOS_FETALES"},
    )
    assert respuesta.status_code == 201
    return respuesta.json()["clave"]


def _ciclo(cliente):
    return envio_automatico.ciclo(cliente.app.state.contexto)


def _cola(settings, id_usuario=ID_USUARIO_DE_PRUEBA):
    return movimientos.leer_estado_de_la_cuenta(settings, id_usuario)


def _intentos(settings, id_usuario=ID_USUARIO_DE_PRUEBA) -> list[int]:
    ruta = movimientos.ruta_para_la_cuenta(settings, id_usuario)
    with closing(sqlite3.connect(ruta)) as conexion:
        return [fila[0] for fila in conexion.execute("SELECT intentos FROM outbox")]


def test_no_existe_una_ruta_para_enviar_a_mano(tmp_path):
    """El navegador no es responsable del envio: la ruta manual ya no existe."""
    cliente, _, _, _ = cliente_con_provision(tmp_path)

    respuesta = cliente.post("/adaptador/movimientos/sincronizar")

    assert respuesta.status_code in (404, 405)


def test_la_lista_de_embarazos_ya_no_habla_de_registrar(tmp_path):
    cliente, _, _, _ = cliente_con_provision(tmp_path)

    datos = cliente.get("/adaptador/embarazos").json()["datos"]

    assert "registro_en_este_dispositivo" not in datos


def test_sin_nada_pendiente_el_ciclo_no_toca_la_red(tmp_path):
    api = ApiFalsa()
    cliente, _, _, _ = _portal_con_api(tmp_path, api)

    assert _ciclo(cliente)[ID_USUARIO_DE_PRUEBA].sin_pendientes is True
    assert api.consultas_identidad == 0 and api.envios == []


def test_sin_red_lo_pendiente_se_conserva_y_no_gasta_intentos(tmp_path):
    """Varios ciclos con la API caida: la verificacion previa falla y no se
    reclama nada, asi que el presupuesto de intentos queda intacto."""
    api = ApiFalsa()
    api.caida = True
    cliente, _, _, settings = _portal_con_api(tmp_path, api)
    _capturar(cliente)

    for _ in range(10):
        envio = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]
        assert envio.sin_credencial_util is True and envio.pasada is None

    assert _cola(settings).pendientes == 1
    assert _intentos(settings) == [0]
    assert api.envios == []


def test_al_volver_la_red_lo_pendiente_se_envia_solo(tmp_path):
    """offline -> PENDIENTE -> vuelve la red -> siguiente ciclo -> ENVIADO."""
    api = ApiFalsa(ENTREGA_ACEPTADA)
    api.caida = True
    cliente, _, _, settings = _portal_con_api(tmp_path, api)
    _capturar(cliente)
    _ciclo(cliente)
    assert _cola(settings).pendientes == 1

    api.caida = False
    envio = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]

    assert envio.pasada is not None and envio.pasada.entregados == 1
    cola = _cola(settings)
    assert (cola.pendientes, cola.enviados) == (0, 1)
    assert len(api.envios) == 1


def test_lo_enviado_no_se_vuelve_a_enviar(tmp_path):
    """``ENVIADO`` es terminal en app.edge: otro ciclo no toca la red."""
    api = ApiFalsa(ENTREGA_ACEPTADA)
    cliente, _, _, settings = _portal_con_api(tmp_path, api)
    _capturar(cliente)

    _ciclo(cliente)
    consultas = api.consultas_identidad
    _ciclo(cliente)
    _ciclo(cliente)

    assert len(api.envios) == 1
    assert api.consultas_identidad == consultas
    assert (_cola(settings).enviados, _cola(settings).total) == (1, 1)


def test_un_reintento_reenvia_la_misma_clave_y_los_mismos_bytes(tmp_path):
    """La respuesta de la primera entrega se pierde. El reintento automatico,
    tras la espera programada, lleva la misma ``Idempotency-Key`` y el mismo
    cuerpo, y el servidor lo reconoce como replay: una sola sesion remota."""
    api = ApiFalsa(httpx.ReadTimeout("sin respuesta"), ENTREGA_REPRODUCIDA)
    cliente, _, reloj, settings = _portal_con_api(tmp_path, api)
    clave = _capturar(cliente)

    primero = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]
    assert primero.pasada.reintentables == 1
    assert _cola(settings).fallidos_reintentables == 1

    # Antes de la espera programada, el ciclo no reintenta.
    assert _ciclo(cliente)[ID_USUARIO_DE_PRUEBA].sin_pendientes is True
    assert len(api.envios) == 1

    reloj.avanzar(seconds=5)
    segundo = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]

    assert segundo.pasada.entregados == 1
    assert len(api.envios) == 2
    assert api.envios[0] == api.envios[1]
    assert api.envios[0][0] == clave
    assert (_cola(settings).enviados, _cola(settings).total) == (1, 1)


def test_un_rechazo_real_no_finge_exito(tmp_path):
    api = ApiFalsa((404, {"detail": "no existe"}, {}))
    cliente, _, _, settings = _portal_con_api(tmp_path, api)
    _capturar(cliente)

    envio = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]

    assert envio.pasada.entregados == 0 and envio.pasada.rechazados == 1
    cola = _cola(settings)
    assert (cola.pendientes, cola.fallidos_en_revision, cola.enviados) == (0, 1, 0)


def test_una_credencial_rechazada_se_olvida_y_no_gasta_intentos(tmp_path):
    api = ApiFalsa(identidad=(401, {"detail": "token vencido"}))
    cliente, _, _, settings = _portal_con_api(tmp_path, api)
    _capturar(cliente)

    envio = _ciclo(cliente)[ID_USUARIO_DE_PRUEBA]

    assert envio.credencial_rechazada is True
    assert _intentos(settings) == [0]
    assert cliente.app.state.contexto.tokens.activos() == []
    # Sin token, el siguiente ciclo ni siquiera pregunta.
    assert _ciclo(cliente) == {}
    assert api.consultas_identidad == 1


def test_lo_pendiente_sobrevive_a_un_reinicio_y_sale_al_volver_a_entrar(tmp_path):
    """El token nunca se persiste: tras reiniciar el portal no hay con que
    enviar, y lo pendiente espera en disco hasta que la paciente entra."""
    api = ApiFalsa(ENTREGA_ACEPTADA)
    api.caida = True
    cliente, _, _, settings = _portal_con_api(tmp_path, api)
    _capturar(cliente)
    _ciclo(cliente)

    # Reinicio: una aplicacion nueva sobre los mismos archivos, sin tokens.
    api.caida = False
    reiniciado, _, _, _ = construir_cliente(
        tmp_path,
        central=central_que_reconoce_el_embarazo(),
        provision_path=tmp_path / "provision.json",
        constructor_cliente_edge=api.constructor,
    )
    assert _ciclo(reiniciado) == {}
    assert _cola(settings).pendientes == 1

    iniciar_sesion(reiniciado)
    envio = _ciclo(reiniciado)[ID_USUARIO_DE_PRUEBA]

    assert envio.pasada.entregados == 1
    assert _cola(settings).enviados == 1


def test_el_token_de_una_cuenta_no_envia_la_cola_de_otra(tmp_path):
    api = ApiFalsa(ENTREGA_ACEPTADA)
    cliente_a, central, _, settings = _portal_con_api(tmp_path, api)
    _capturar(cliente_a)
    cliente_a.post("/adaptador/cerrar-sesion")

    # Otra cuenta abre sesion en el mismo portal.
    central.id_usuario = ID_USUARIO_DE_PRUEBA + 1
    cliente_b = TestClient(cliente_a.app)
    iniciar_sesion(cliente_b)

    hechos = _ciclo(cliente_b)

    assert ID_USUARIO_DE_PRUEBA not in hechos
    assert hechos[ID_USUARIO_DE_PRUEBA + 1].sin_pendientes is True
    assert api.envios == []
    assert _cola(settings).pendientes == 1
    assert not movimientos.ruta_para_la_cuenta(settings, ID_USUARIO_DE_PRUEBA + 1).exists()


def test_el_emisor_arranca_y_se_detiene_con_el_portal(tmp_path):
    """Con un intervalo configurado, el hilo vive mientras vive la aplicacion."""
    cliente, _, _, _ = construir_cliente(tmp_path, envio_automatico_segundos=60)

    with cliente:
        assert any(h.name == "envio-automatico" for h in threading.enumerate())
    assert not any(h.name == "envio-automatico" for h in threading.enumerate())


def test_estado_de_movimientos_sin_sesion_local_responde_401(tmp_path):
    cliente, _, _, _ = construir_cliente(tmp_path, central=ClienteClinicoDoble())

    assert cliente.get("/adaptador/movimientos/estado").status_code == 401


def test_dos_cuentas_registran_en_archivos_separados_por_http(tmp_path):
    """Lo que aisla no es el id_embarazo: es el id_usuario de la sesion local,
    que el navegador no elige."""
    central = ClienteClinicoDoble(
        respuesta_embarazos=RespuestaClinica(
            EstadoRespuesta.OK, datos=(embarazo(ID_EMBARAZO),)
        )
    )
    cliente_a, _, _, settings = cliente_con_provision(tmp_path, central=central)
    cliente_b = TestClient(cliente_a.app)

    cliente_a.post(
        f"/adaptador/embarazos/{ID_EMBARAZO}/sesiones-simuladas",
        json={"tipo_sesion": "SIGNOS_MATERNOS"},
    )

    estado_a = cliente_a.get("/adaptador/movimientos/estado").json()
    assert estado_a["total"] == 1

    # La cuenta B no tiene sesion: ni siquiera llega a mirar un archivo.
    assert cliente_b.get("/adaptador/movimientos/estado").status_code == 401
    # Y el archivo de otra cuenta sigue sin existir.
    assert (
        movimientos.leer_estado_de_la_cuenta(
            settings, ID_USUARIO_DE_PRUEBA + 1
        ).inicializado
        is False
    )
