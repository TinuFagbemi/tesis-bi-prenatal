"""Hash y verificacion de contrasenas con Argon2id (SCRUM-70).

Lo que estas pruebas afirman es que el algoritmo acordado es el que realmente se
usa, que la verificacion pasa por la biblioteca y no por una comparacion escrita
a mano, y que el camino del usuario inexistente **no** es un atajo.

Lo que deliberadamente **no** afirman es rendimiento ni resistencia de
produccion: medir cuanto tarda un hash en una maquina de CI no demuestra nada
sobre su coste para un atacante, y prometerlo seria mentir.

Todos los datos son simulados y completamente ficticios.
"""

from __future__ import annotations

import logging

import pytest
from argon2 import PasswordHasher

from app.services import passwords
from app.services.passwords import PREFIJO_ARGON2ID, hashear, verificar

CLAVE = "clave-simulada-de-prueba"
OTRA_CLAVE = "otra-clave-simulada"


# ---------------------------------------------------------------------------
# 1. El algoritmo es el acordado
# ---------------------------------------------------------------------------


def test_el_digest_es_argon2id():
    assert hashear(CLAVE).startswith(PREFIJO_ARGON2ID)


def test_la_biblioteca_reconoce_su_propio_digest():
    """No basta con que el texto empiece por el prefijo: tiene que ser valido."""
    assert PasswordHasher().verify(hashear(CLAVE), CLAVE)


def test_el_digest_cabe_en_la_columna():
    """``operacional.usuario.password_hash`` es VARCHAR(255)."""
    from app.models.seguridad import Usuario

    ancho = Usuario.__table__.c.password_hash.type.length
    assert len(hashear(CLAVE)) <= ancho


# ---------------------------------------------------------------------------
# 2. Verificacion
# ---------------------------------------------------------------------------


def test_la_contrasena_correcta_verifica():
    assert verificar(hashear(CLAVE), CLAVE) is True


def test_una_contrasena_incorrecta_no_verifica():
    assert verificar(hashear(CLAVE), OTRA_CLAVE) is False


def test_la_cadena_vacia_no_verifica_contra_otro_digest():
    assert verificar(hashear(CLAVE), "") is False


# ---------------------------------------------------------------------------
# 3. El salt es aleatorio, y ambos digests valen
# ---------------------------------------------------------------------------


def test_dos_hashes_de_la_misma_clave_son_distintos():
    assert hashear(CLAVE) != hashear(CLAVE)


def test_los_dos_verifican():
    """El salt viaja dentro del digest, asi que ninguno depende del otro."""
    primero, segundo = hashear(CLAVE), hashear(CLAVE)

    assert verificar(primero, CLAVE)
    assert verificar(segundo, CLAVE)


def test_muchos_hashes_seguidos_no_se_repiten():
    digests = {hashear(CLAVE) for _ in range(8)}

    assert len(digests) == 8


# ---------------------------------------------------------------------------
# 4. Valores imposibles: respuesta controlada, nunca una excepcion suelta
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "corrupto",
    [
        "",
        "no-es-un-hash",
        "HASH_SIMULADO_NO_USAR_EN_PRODUCCION",
        "$argon2id$truncado",
        "$argon2id$v=19$m=65536,t=3,p=4$",
        "$2b$12$abcdefghijklmnopqrstuv",
    ],
    ids=[
        "vacio",
        "texto",
        "marcador-anterior-del-dataset",
        "truncado",
        "sin-salt-ni-digest",
        "otro-algoritmo",
    ],
)
def test_un_digest_corrupto_devuelve_falso_sin_reventar(corrupto):
    """Un hash ilegible no autentica a nadie, y no es una emergencia del llamador.

    El marcador que el dataset usaba antes de SCRUM-70 esta en la lista a
    proposito: si quedara una fila sin regenerar, tiene que comportarse como una
    credencial que no verifica, no como un error 500.
    """
    assert verificar(corrupto, CLAVE) is False


# ---------------------------------------------------------------------------
# 5. El usuario inexistente NO evita Argon2id
# ---------------------------------------------------------------------------


def test_el_usuario_inexistente_devuelve_falso():
    assert verificar(None, CLAVE) is False


def test_el_usuario_inexistente_ejecuta_la_verificacion(monkeypatch):
    """La comprobacion estructural del acuerdo: ``None`` no toma un atajo.

    No se mide tiempo -- eso seria una prueba fragil que no demuestra lo que
    dice --. Se observa el hecho: la biblioteca es invocada exactamente igual que
    cuando la cuenta existe, asi que no hay una rama trivial que confirme que un
    correo no esta registrado.
    """
    llamadas = []
    original = passwords._HASHER

    class Espia:
        """Delega en el verificador real y anota cada invocacion.

        Se sustituye el objeto entero y no su metodo: ``PasswordHasher`` usa
        ``__slots__``, asi que sus atributos son de solo lectura.
        """

        def hash(self, clave):
            return original.hash(clave)

        def verify(self, digest, clave):
            llamadas.append(digest)
            return original.verify(digest, clave)

    digest_real = hashear(CLAVE)
    monkeypatch.setattr(passwords, "_HASHER", Espia())

    verificar(None, CLAVE)
    con_usuario_inexistente = len(llamadas)

    verificar(digest_real, OTRA_CLAVE)
    con_clave_incorrecta = len(llamadas) - con_usuario_inexistente

    assert con_usuario_inexistente == 1
    assert con_clave_incorrecta == 1


def test_el_hash_dummy_se_construye_una_sola_vez():
    """No se genera uno nuevo por solicitud: seria un ataque de denegacion facil."""
    primero = passwords._HASH_DUMMY
    verificar(None, CLAVE)
    verificar(None, OTRA_CLAVE)

    assert passwords._HASH_DUMMY is primero


def test_el_hash_dummy_es_un_argon2id_valido():
    assert passwords._HASH_DUMMY.startswith(PREFIJO_ARGON2ID)


def test_ninguna_contrasena_verifica_contra_el_dummy():
    """El dummy no es una credencial: la cadena que lo produjo se descarto."""
    for intento in (CLAVE, OTRA_CLAVE, "", "admin", "password"):
        assert verificar(passwords._HASH_DUMMY, intento) is False


# ---------------------------------------------------------------------------
# 6. Nada sensible sale del modulo
# ---------------------------------------------------------------------------


def test_la_contrasena_no_aparece_en_los_logs(caplog):
    caplog.set_level(logging.DEBUG)

    digest = hashear(CLAVE)
    verificar(digest, CLAVE)
    verificar(digest, OTRA_CLAVE)
    verificar(None, CLAVE)

    registrado = caplog.text
    assert CLAVE not in registrado
    assert OTRA_CLAVE not in registrado
    assert digest not in registrado


def test_verificar_solo_devuelve_un_booleano():
    """Ni el digest, ni la contrasena, ni un diagnostico salen de aqui."""
    assert verificar(hashear(CLAVE), CLAVE) is True
    assert verificar(hashear(CLAVE), OTRA_CLAVE) is False


def test_no_hay_comparacion_manual_de_digests():
    """El codigo delega en la biblioteca; no compara cadenas por su cuenta.

    Una comparacion con ``==`` sobre digests seria incorrecta ademas de insegura:
    dos hashes de la misma contrasena son distintos por diseno, asi que el que
    la escribiera vería fallar todas las contrasenas correctas.
    """
    from pathlib import Path

    fuente = Path(passwords.__file__).read_text(encoding="utf-8")

    assert "hmac.compare_digest" not in fuente
    assert "_HASHER.verify" in fuente
