from __future__ import annotations

import hashlib
import importlib.util
import json
from collections import Counter
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

    alertas = Counter(
        x["id_semaforo"]
        for x in lecturas
    )

    assert alertas["SEM-OK"] == 826
    assert alertas["SEM-WARNING"] == 295
    assert alertas["SEM-ERROR"] == 59


def test_telefonos_paciente(dataset):
    telefonos = dataset["telefonos_paciente"]
    pacientes = dataset["pacientes"]

    ids_paciente_validos = {
        x["id_paciente"] for x in pacientes
    }

    assert len(telefonos) >= len(pacientes)

    for telefono in telefonos:
        assert telefono["id_paciente"] in ids_paciente_validos
        assert telefono["tipo_contacto"] in (
            "CELULAR",
            "FIJO",
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

    assert len(telefonos) >= len(medicos)

    for telefono in telefonos:
        assert telefono["id_medico"] in ids_medico_validos
        assert telefono["tipo_contacto"] in (
            "CELULAR",
            "FIJO",
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


def test_usuarios_y_roles(dataset):
    usuarios = dataset["usuarios"]
    roles = dataset["roles"]

    assert len(usuarios) == 37

    ids_rol_validos = {x["id_rol"] for x in roles}
    assert ids_rol_validos == {
        "ROL-001",
        "ROL-002",
        "ROL-003",
    }

    conteo_por_rol = Counter(
        x["id_rol"] for x in usuarios
    )

    assert conteo_por_rol["ROL-001"] == 2
    assert conteo_por_rol["ROL-002"] == 5
    assert conteo_por_rol["ROL-003"] == 30

    ids_usuario = [x["id_usuario"] for x in usuarios]
    assert len(ids_usuario) == len(set(ids_usuario))


def test_usuario_paciente_y_usuario_medico(dataset):
    pacientes = dataset["pacientes"]
    medicos = dataset["medicos"]
    usuarios = dataset["usuarios"]
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
        assert clinica["calle"]


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