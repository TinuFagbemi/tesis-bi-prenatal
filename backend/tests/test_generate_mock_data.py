from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = REPO_ROOT / "scripts" / "generate_mock_data.py"


def cargar_generador():
    spec = importlib.util.spec_from_file_location(
        "generate_mock_data",
        GENERATOR_PATH,
    )

    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


gm = cargar_generador()


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        gm,
        "CARPETA_SALIDA",
        tmp_path,
    )

    gm.main()

    ruta = tmp_path / "dataset_fetalalert.json"

    return json.loads(
        ruta.read_text(encoding="utf-8")
    )


def test_volumen_y_granularidad(dataset):
    sesiones = dataset["sesiones_monitoreo"]
    lecturas = dataset["lecturas_biometricas"]

    assert len(sesiones) == 732
    assert len(lecturas) == 1180
    assert len(lecturas) <= 1200

    hr_spo2 = [
        x
        for x in lecturas
        if x["hr_valor"] is not None
    ]

    movimientos = [
        x
        for x in lecturas
        if x["mov_valor"] is not None
    ]

    assert len(hr_spo2) == 560
    assert len(movimientos) == 620

    conteo_por_sesion = Counter(
        x["id_sesion"]
        for x in lecturas
    )

    # Para esta muestra técnica, cada sesión HR/SpO2 conserva
    # cinco lecturas procesadas representativas. La relación
    # SesionMonitoreo -> LecturaBiometrica es 1:N.
    assert sum(
        1
        for cantidad in conteo_por_sesion.values()
        if cantidad == 5
    ) == 112

    assert sum(
        1
        for cantidad in conteo_por_sesion.values()
        if cantidad == 1
    ) == 620


def test_reglas_biometricas(dataset):
    lecturas = dataset["lecturas_biometricas"]

    semana_por_id = {
        x["id_tiempo_gest"]: x["semana_gestacion"]
        for x in dataset["tiempo_gestacional"]
    }

    for lectura in lecturas:
        if lectura["hr_valor"] is not None:
            assert lectura["spo2_valor"] is not None
            assert lectura["mov_valor"] is None

        if lectura["mov_valor"] is not None:
            assert lectura["hr_valor"] is None
            assert lectura["spo2_valor"] is None

            assert (
                semana_por_id[
                    lectura["id_tiempo_gest"]
                ]
                >= 20
            )


def test_distribucion_alertas(dataset):
    lecturas = dataset["lecturas_biometricas"]

    codigo_por_id_semaforo = {
        s["id_semaforo"]: s["codigo_nivel"]
        for s in dataset["semaforos"]
    }

    alertas = Counter(
        codigo_por_id_semaforo[x["id_semaforo"]]
        for x in lecturas
    )

    assert alertas["OK"] == 826
    assert alertas["WARNING"] == 295
    assert alertas["ERROR"] == 59


def test_sincronizacion_no_anterior_a_captura(dataset):
    diferidas = 0

    for lectura in dataset["lecturas_biometricas"]:
        captura = datetime.fromisoformat(lectura["fecha_hora_captura"])
        sincronizacion = datetime.fromisoformat(
            lectura["fecha_hora_sincronizacion"]
        )

        assert sincronizacion >= captura

        if sincronizacion > captura:
            diferidas += 1

    assert diferidas > 0


def test_telefonos_paciente(dataset):
    telefonos = dataset["telefonos_paciente"]
    pacientes = dataset["pacientes"]

    ids_paciente_validos = {
        x["id_paciente"] for x in pacientes
    }

    assert len(telefonos) == 40

    for telefono in telefonos:
        assert telefono["id_paciente"] in ids_paciente_validos
        assert telefono["tipo_contacto"] in (
            "CELULAR",
            "TELEFONO_DOMICILIO",
            "CORREO_ALTERNO",
        )

    principales = Counter(
        x["id_paciente"]
        for x in telefonos
        if x["principal"]
    )

    assert all(
        principales[paciente["id_paciente"]] == 1
        for paciente in pacientes
    )

    ids_telefono = [
        x["id_telefono_paciente"] for x in telefonos
    ]
    assert len(ids_telefono) == len(set(ids_telefono))


def test_telefonos_medico(dataset):
    telefonos = dataset["telefonos_medico"]
    medicos = dataset["medicos"]

    ids_medico_validos = {
        x["id_medico"] for x in medicos
    }

    assert len(telefonos) == 10

    for telefono in telefonos:
        assert telefono["id_medico"] in ids_medico_validos
        assert telefono["tipo_contacto"] in (
            "CELULAR",
            "TELEFONO_DOMICILIO",
            "CORREO_ALTERNO",
        )

    principales = Counter(
        x["id_medico"]
        for x in telefonos
        if x["principal"]
    )

    assert all(
        principales[medico["id_medico"]] == 1
        for medico in medicos
    )

    ids_telefono = [
        x["id_telefono_medico"] for x in telefonos
    ]
    assert len(ids_telefono) == len(set(ids_telefono))


def test_telefono_medico_usa_telefono_domicilio_no_fijo(dataset):
    """AJUSTE 3: "FIJO" fue eliminado del enum TipoContacto real."""
    tipos_usados = {
        t["tipo_contacto"] for t in dataset["telefonos_medico"]
    } | {
        t["tipo_contacto"] for t in dataset["telefonos_paciente"]
    }

    assert "FIJO" not in tipos_usados
    assert "TELEFONO_DOMICILIO" in tipos_usados


def test_usuarios_y_roles(dataset):
    usuarios = dataset["usuarios"]
    roles = dataset["roles"]

    assert len(usuarios) == 37

    id_rol_por_nombre = {
        r["nombre_rol"]: r["id_rol"] for r in roles
    }

    assert set(id_rol_por_nombre) == {"ADMIN", "MEDICO", "PACIENTE"}

    conteo_por_rol = Counter(
        x["id_rol"] for x in usuarios
    )

    assert conteo_por_rol[id_rol_por_nombre["ADMIN"]] == 2
    assert conteo_por_rol[id_rol_por_nombre["MEDICO"]] == 5
    assert conteo_por_rol[id_rol_por_nombre["PACIENTE"]] == 30

    ids_usuario = [x["id_usuario"] for x in usuarios]
    assert len(ids_usuario) == len(set(ids_usuario))


def test_usuario_paciente_y_usuario_medico(dataset):
    pacientes = dataset["pacientes"]
    medicos = dataset["medicos"]
    usuarios = dataset["usuarios"]
    roles = dataset["roles"]
    usuario_paciente = dataset["usuario_paciente"]
    usuario_medico = dataset["usuario_medico"]

    ids_usuario_validos = {
        x["id_usuario"] for x in usuarios
    }
    ids_paciente_validos = {
        x["id_paciente"] for x in pacientes
    }
    ids_medico_validos = {
        x["id_medico"] for x in medicos
    }

    assert len(usuario_paciente) == len(pacientes)
    assert len(usuario_medico) == len(medicos)

    for relacion in usuario_paciente:
        assert relacion["id_usuario"] in ids_usuario_validos
        assert relacion["id_paciente"] in ids_paciente_validos

    for relacion in usuario_medico:
        assert relacion["id_usuario"] in ids_usuario_validos
        assert relacion["id_medico"] in ids_medico_validos

    pacientes_con_cuenta = [
        x["id_paciente"] for x in usuario_paciente
    ]
    assert len(pacientes_con_cuenta) == len(
        set(pacientes_con_cuenta)
    )

    medicos_con_cuenta = [
        x["id_medico"] for x in usuario_medico
    ]
    assert len(medicos_con_cuenta) == len(
        set(medicos_con_cuenta)
    )

    # Unicidad también del lado de id_usuario: una misma cuenta no
    # puede aparecer vinculada a más de un paciente ni a más de un
    # médico.
    usuarios_en_usuario_paciente = [
        x["id_usuario"] for x in usuario_paciente
    ]
    assert len(usuarios_en_usuario_paciente) == len(
        set(usuarios_en_usuario_paciente)
    )

    usuarios_en_usuario_medico = [
        x["id_usuario"] for x in usuario_medico
    ]
    assert len(usuarios_en_usuario_medico) == len(
        set(usuarios_en_usuario_medico)
    )

    # RBAC: la cuenta vinculada mediante usuario_medico debe tener
    # rol MEDICO, y la vinculada mediante usuario_paciente debe
    # tener rol PACIENTE.
    id_rol_por_nombre = {
        r["nombre_rol"]: r["id_rol"] for r in roles
    }
    id_rol_por_usuario = {
        u["id_usuario"]: u["id_rol"] for u in usuarios
    }

    for relacion in usuario_medico:
        assert (
            id_rol_por_usuario[relacion["id_usuario"]]
            == id_rol_por_nombre["MEDICO"]
        )

    for relacion in usuario_paciente:
        assert (
            id_rol_por_usuario[relacion["id_usuario"]]
            == id_rol_por_nombre["PACIENTE"]
        )


def test_geografia_clinicas(dataset):
    clinicas = dataset["clinicas"]

    assert len(clinicas) == 3

    combinaciones_esperadas = {
        ("Chiriquí", "Renacimiento", "Plaza Caisán"),
        ("Veraguas", "Santa Fe", "Calovébora"),
        ("Darién", "Chepigana", "Camogantí"),
    }

    combinaciones_generadas = {
        (
            x["provincia"],
            x["distrito"],
            x["corregimiento"],
        )
        for x in clinicas
    }

    assert combinaciones_generadas == combinaciones_esperadas

    for clinica in clinicas:
        assert clinica["direccion_fisica"]
        assert "calle" not in clinica


def test_medico_pertenece_a_una_unica_clinica(dataset):
    """
Valida que cada médico esté asociado a una única clínica.

`medico_clinica` debe contener exactamente una relación por médico,
las tres clínicas deben tener al menos un médico asociado y deben
existir combinaciones médico-clínica no vinculadas para permitir
casos negativos en futuras validaciones de RLS.
"""
    medicos = dataset["medicos"]
    clinicas = dataset["clinicas"]
    medico_clinica = dataset["medico_clinica"]

    ids_medico = {m["id_medico"] for m in medicos}
    ids_clinica = {c["id_clinica"] for c in clinicas}

    # 1 y 2: exactamente 5 relaciones, una por médico.
    assert len(medico_clinica) == 5

    medicos_en_relacion = [r["id_medico"] for r in medico_clinica]
    assert len(medicos_en_relacion) == len(set(medicos_en_relacion))
    assert set(medicos_en_relacion) == ids_medico

    # 3 y 6: cada médico pertenece exactamente a una clínica
    # (ningún médico pertenece a más de una).
    clinicas_por_medico = {
        r["id_medico"]: r["id_clinica"] for r in medico_clinica
    }
    assert len(clinicas_por_medico) == len(medicos)

    # 4: las 3 clínicas tienen al menos un médico.
    assert set(clinicas_por_medico.values()) == ids_clinica

    # 5: existen combinaciones médico-clínica NO asociadas
    # (casos negativos para futuras validaciones de RLS).
    combinaciones_posibles = {
        (id_medico, id_clinica)
        for id_medico in ids_medico
        for id_clinica in ids_clinica
    }
    combinaciones_existentes = {
        (r["id_medico"], r["id_clinica"]) for r in medico_clinica
    }
    combinaciones_no_asociadas = (
        combinaciones_posibles - combinaciones_existentes
    )

    assert len(combinaciones_no_asociadas) > 0

    # Ejemplo concreto de combinación no permitida: un médico
    # nunca debe aparecer asociado a una clínica distinta a la
    # que le fue asignada en medico_clinica.
    for id_medico, id_clinica in combinaciones_no_asociadas:
        assert clinicas_por_medico[id_medico] != id_clinica


def test_embarazos_por_clinica_y_medico(dataset):
    """
    7 y 8: cada clínica mantiene exactamente 10 embarazos y el
    total sigue siendo 30. 9: cada médico tiene al menos un
    embarazo asignado. 10: la cantidad por médico puede variar.
    """
    embarazos = dataset["embarazos"]
    medicos = dataset["medicos"]
    seguimientos = dataset["seguimiento_clinico"]

    assert len(embarazos) == 30

    por_clinica = Counter(e["id_clinica"] for e in embarazos)
    assert set(por_clinica.values()) == {10}

    por_medico = Counter(s["id_medico"] for s in seguimientos)

    ids_medico = {m["id_medico"] for m in medicos}
    assert set(por_medico) == ids_medico
    assert all(cantidad >= 1 for cantidad in por_medico.values())


def test_seguimiento_clinico_medico_pertenece_a_clinica_embarazo(dataset):
    """
    Punto 11 (prioritario): para CADA seguimiento_clinico, el
    médico asignado debe pertenecer a la clínica correspondiente
    al embarazo. Recorre cada seguimiento individualmente
    (seguimiento -> embarazo -> clínica -> médico -> medico_clinica)
    en lugar de validar solo cantidades globales.
    """
    embarazo_por_id = {
        e["id_embarazo"]: e for e in dataset["embarazos"]
    }

    clinica_por_medico = {
        r["id_medico"]: r["id_clinica"]
        for r in dataset["medico_clinica"]
    }

    seguimientos = dataset["seguimiento_clinico"]
    assert len(seguimientos) == 30

    for seguimiento in seguimientos:
        embarazo = embarazo_por_id[seguimiento["id_embarazo"]]
        id_medico = seguimiento["id_medico"]

        # El médico del seguimiento debe tener una asociación
        # válida en medico_clinica...
        assert id_medico in clinica_por_medico

        # ...y esa asociación debe ser exactamente la clínica del
        # embarazo correspondiente (nunca la de otra clínica).
        assert clinica_por_medico[id_medico] == embarazo["id_clinica"]


def test_pk_enteras_desde_100_y_unicas(dataset):
    """AJUSTE 1: PK Integer determinísticas, alineadas con SQLAlchemy."""
    entidades = [
        ("clinicas", "id_clinica"),
        ("especialidades", "id_especialidad"),
        ("medicos", "id_medico"),
        ("pacientes", "id_paciente"),
        ("embarazos", "id_embarazo"),
        ("seguimiento_clinico", "id_seguimiento"),
        ("dispositivos", "id_dispositivo"),
        ("asignacion_dispositivo", "id_asignacion"),
        ("sesiones_monitoreo", "id_sesion"),
        ("lecturas_biometricas", "id_lectura"),
        ("roles", "id_rol"),
        ("semaforos", "id_semaforo"),
        ("tiempo_gestacional", "id_tiempo_gest"),
        ("factores_riesgo", "id_factor_riesgo"),
        ("telefonos_paciente", "id_telefono_paciente"),
        ("telefonos_medico", "id_telefono_medico"),
        ("usuarios", "id_usuario"),
    ]

    for clave, campo in entidades:
        valores = [x[campo] for x in dataset[clave]]

        assert all(isinstance(v, int) for v in valores), clave
        assert len(valores) == len(set(valores)), clave
        assert min(valores) == gm.ID_BASE, clave


def test_fk_enteras_sin_huerfanas(dataset):
    """AJUSTE 1: las FK apuntan a PK Integer existentes, sin huérfanas."""
    ids_clinica = {x["id_clinica"] for x in dataset["clinicas"]}
    ids_paciente = {x["id_paciente"] for x in dataset["pacientes"]}
    ids_medico = {x["id_medico"] for x in dataset["medicos"]}
    ids_embarazo = {x["id_embarazo"] for x in dataset["embarazos"]}
    ids_dispositivo = {
        x["id_dispositivo"] for x in dataset["dispositivos"]
    }
    ids_sesion = {x["id_sesion"] for x in dataset["sesiones_monitoreo"]}
    ids_tiempo_gest = {
        x["id_tiempo_gest"] for x in dataset["tiempo_gestacional"]
    }
    ids_semaforo = {x["id_semaforo"] for x in dataset["semaforos"]}
    ids_usuario = {x["id_usuario"] for x in dataset["usuarios"]}

    for embarazo in dataset["embarazos"]:
        assert isinstance(embarazo["id_paciente"], int)
        assert embarazo["id_paciente"] in ids_paciente
        assert embarazo["id_clinica"] in ids_clinica

    for sesion in dataset["sesiones_monitoreo"]:
        assert sesion["id_embarazo"] in ids_embarazo
        assert sesion["id_dispositivo"] in ids_dispositivo

    for lectura in dataset["lecturas_biometricas"]:
        assert lectura["id_sesion"] in ids_sesion
        assert lectura["id_tiempo_gest"] in ids_tiempo_gest
        assert lectura["id_semaforo"] in ids_semaforo

    for relacion in dataset["usuario_medico"]:
        assert relacion["id_usuario"] in ids_usuario
        assert relacion["id_medico"] in ids_medico

    for relacion in dataset["usuario_paciente"]:
        assert relacion["id_usuario"] in ids_usuario
        assert relacion["id_paciente"] in ids_paciente


def test_embarazo_factor_riesgo_entidad_y_distribucion(dataset):
    """AJUSTE 10: la entidad/clave real es embarazo_factor_riesgo."""
    assert "embarazo_factor_riesgo" in dataset
    assert "paciente_factor_riesgo" not in dataset

    relaciones = dataset["embarazo_factor_riesgo"]
    embarazos = dataset["embarazos"]

    conteo = Counter(r["id_embarazo"] for r in relaciones)

    sin_factor = sum(
        1 for e in embarazos if conteo[e["id_embarazo"]] == 0
    )
    un_factor = sum(
        1 for e in embarazos if conteo[e["id_embarazo"]] == 1
    )
    dos_factores = sum(
        1 for e in embarazos if conteo[e["id_embarazo"]] == 2
    )

    assert sin_factor == 14
    assert un_factor == 9
    assert dos_factores == 7


def test_estados_embarazo_y_fecha_cierre(dataset):
    """AJUSTE 4: distribución 20/8/2 y coherencia de fecha_cierre."""
    embarazos = dataset["embarazos"]

    conteo = Counter(e["estado_embarazo"] for e in embarazos)

    assert conteo["ACTIVO"] == 20
    assert conteo["FINALIZADO"] == 8
    assert conteo["SUSPENDIDO"] == 2
    assert set(conteo) == {"ACTIVO", "FINALIZADO", "SUSPENDIDO"}

    for embarazo in embarazos:
        if embarazo["estado_embarazo"] == "ACTIVO":
            assert embarazo["fecha_cierre"] is None
        else:
            assert embarazo["fecha_cierre"] is not None
            assert date.fromisoformat(
                embarazo["fecha_cierre"]
            ) >= date.fromisoformat(embarazo["fecha_inicio"])


def test_estados_embarazo_distribuidos_entre_clinicas(dataset):
    """
    Evita una correlación artificial entre clínica/provincia y
    estado del embarazo: cada clínica debe contener una MEZCLA de
    embarazos ACTIVO y cerrados (FINALIZADO+SUSPENDIDO). No fija la
    distribución exacta actual por clínica (6/3/1, 6/3/1, 8/2/0):
    eso es un detalle determinístico de implementación, no el
    contrato funcional.
    """
    embarazos = dataset["embarazos"]

    por_clinica: dict[int, Counter] = {}

    for embarazo in embarazos:
        contador = por_clinica.setdefault(
            embarazo["id_clinica"], Counter()
        )
        contador[embarazo["estado_embarazo"]] += 1

    assert len(por_clinica) == 3

    # Totales globales, sin importar cómo se repartan por clínica.
    conteo_global = Counter(e["estado_embarazo"] for e in embarazos)
    assert conteo_global["ACTIVO"] == 20
    assert conteo_global["FINALIZADO"] == 8
    assert conteo_global["SUSPENDIDO"] == 2

    for contador in por_clinica.values():
        assert sum(contador.values()) == 10

        activos_en_clinica = contador["ACTIVO"]
        cerrados_en_clinica = (
            contador["FINALIZADO"] + contador["SUSPENDIDO"]
        )

        # Cada clínica debe tener al menos un embarazo ACTIVO y al
        # menos uno cerrado: ninguna clínica puede ser 100 % ACTIVO
        # ni tener sus 10 embarazos cerrados.
        assert activos_en_clinica >= 1
        assert cerrados_en_clinica >= 1
        assert cerrados_en_clinica < 10


def test_seguimiento_clinico_coherente_con_estado_embarazo(dataset):
    """
    El seguimiento_clinico debe reflejar el estado del embarazo:
    - ACTIVO -> activo=True, fecha_fin=None
    - FINALIZADO/SUSPENDIDO -> activo=False,
      fecha_fin=embarazo.fecha_cierre
    """
    embarazo_por_id = {
        e["id_embarazo"]: e for e in dataset["embarazos"]
    }

    seguimientos = dataset["seguimiento_clinico"]
    assert len(seguimientos) == 30

    for seguimiento in seguimientos:
        embarazo = embarazo_por_id[seguimiento["id_embarazo"]]

        if embarazo["estado_embarazo"] == "ACTIVO":
            assert seguimiento["activo"] is True
            assert seguimiento["fecha_fin"] is None
        else:
            assert seguimiento["activo"] is False
            assert seguimiento["fecha_fin"] == embarazo["fecha_cierre"]


def test_ninguna_captura_posterior_a_fecha_cierre(dataset):
    embarazo_por_id = {
        e["id_embarazo"]: e for e in dataset["embarazos"]
    }
    sesion_por_id = {
        s["id_sesion"]: s for s in dataset["sesiones_monitoreo"]
    }

    for lectura in dataset["lecturas_biometricas"]:
        sesion = sesion_por_id[lectura["id_sesion"]]
        embarazo = embarazo_por_id[sesion["id_embarazo"]]

        if embarazo["fecha_cierre"] is None:
            continue

        cierre = date.fromisoformat(embarazo["fecha_cierre"])
        captura = datetime.fromisoformat(
            lectura["fecha_hora_captura"]
        ).date()

        assert captura <= cierre


def test_dispositivos_coherentes_con_estado_embarazo(dataset):
    """AJUSTE 5: 20 ASIGNADO / 10 DISPONIBLE, sin MANTENIMIENTO/INACTIVO."""
    dispositivos = dataset["dispositivos"]
    embarazos = dataset["embarazos"]
    asignaciones = dataset["asignacion_dispositivo"]

    conteo_estado = Counter(d["estado"] for d in dispositivos)

    assert conteo_estado["ASIGNADO"] == 20
    assert conteo_estado["DISPONIBLE"] == 10
    assert conteo_estado["MANTENIMIENTO"] == 0
    assert conteo_estado["INACTIVO"] == 0

    dispositivo_por_id = {
        d["id_dispositivo"]: d for d in dispositivos
    }
    asignacion_por_embarazo = {
        a["id_embarazo"]: a for a in asignaciones
    }

    for embarazo in embarazos:
        asignacion = asignacion_por_embarazo[embarazo["id_embarazo"]]
        dispositivo = dispositivo_por_id[asignacion["id_dispositivo"]]

        # El dispositivo asignado debe pertenecer a la misma
        # clínica del embarazo, nunca a otra.
        assert dispositivo["id_clinica"] == embarazo["id_clinica"]

        if embarazo["estado_embarazo"] == "ACTIVO":
            assert asignacion["activo"] is True
            assert asignacion["fecha_fin"] is None
            assert dispositivo["estado"] == "ASIGNADO"
        else:
            assert asignacion["activo"] is False
            assert asignacion["fecha_fin"] is not None
            assert asignacion["fecha_fin"] >= asignacion["fecha_inicio"]
            assert asignacion["fecha_fin"] == embarazo["fecha_cierre"]
            assert dispositivo["estado"] == "DISPONIBLE"

    codigos = [d["codigo_dispositivo"] for d in dispositivos]
    assert len(codigos) == len(set(codigos))


def test_origen_dato_y_tipo_sesion(dataset):
    """AJUSTE 6 y 7: origen_dato=DISPOSITIVO y tipo_sesion presente."""
    sesiones = dataset["sesiones_monitoreo"]

    assert all(s["origen_dato"] == "DISPOSITIVO" for s in sesiones)
    assert all(s["origen_dato"] != "API" for s in sesiones)
    assert all("tipo_sesion" in s for s in sesiones)

    conteo_tipo = Counter(s["tipo_sesion"] for s in sesiones)

    assert conteo_tipo["SIGNOS_MATERNOS"] == 112
    assert conteo_tipo["MOVIMIENTOS_FETALES"] == 620


def test_datetime_offset_aware(dataset):
    """AJUSTE 8 y 9: DateTime(timezone=True) en los campos reales."""
    for sesion in dataset["sesiones_monitoreo"]:
        assert datetime.fromisoformat(sesion["fecha_inicio"]).tzinfo is not None
        assert datetime.fromisoformat(sesion["fecha_fin"]).tzinfo is not None

    for lectura in dataset["lecturas_biometricas"]:
        assert datetime.fromisoformat(
            lectura["fecha_hora_captura"]
        ).tzinfo is not None
        assert datetime.fromisoformat(
            lectura["fecha_hora_sincronizacion"]
        ).tzinfo is not None

    for dispositivo in dataset["dispositivos"]:
        assert datetime.fromisoformat(
            dispositivo["fecha_registro"]
        ).tzinfo is not None

    # Las fechas propias de Embarazo son Date en SQLAlchemy real:
    # deben seguir siendo fechas simples, sin componente de hora.
    for embarazo in dataset["embarazos"]:
        assert "T" not in embarazo["fecha_inicio"]
        assert "T" not in embarazo["fecha_probable_parto"]
        if embarazo["fecha_cierre"] is not None:
            assert "T" not in embarazo["fecha_cierre"]


def test_generador_reproducible(
    tmp_path,
    monkeypatch,
):
    salida_1 = tmp_path / "run_1"
    salida_2 = tmp_path / "run_2"

    monkeypatch.setattr(
        gm,
        "CARPETA_SALIDA",
        salida_1,
    )

    gm.main()

    hash_1 = hashlib.sha256(
        (
            salida_1
            / "dataset_fetalalert.json"
        ).read_bytes()
    ).hexdigest()

    monkeypatch.setattr(
        gm,
        "CARPETA_SALIDA",
        salida_2,
    )

    gm.main()

    hash_2 = hashlib.sha256(
        (
            salida_2
            / "dataset_fetalalert.json"
        ).read_bytes()
    ).hexdigest()

    assert hash_1 == hash_2
