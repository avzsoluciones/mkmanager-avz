import base64

from cryptography.fernet import Fernet

from app import config
from app.services import wgkeys


def test_rfc7748_vector():
    # Vector de prueba X25519 de la RFC 7748 (clave de Alice).
    priv = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    pub = bytes.fromhex("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
    assert wgkeys.public_from_private_bytes(priv) == pub


def test_generated_keypair_format_and_clamping():
    priv, pub = wgkeys.generate_keypair()
    raw = base64.b64decode(priv)
    assert len(raw) == 32 and len(base64.b64decode(pub)) == 32
    assert raw[0] & 7 == 0 and raw[31] & 128 == 0 and raw[31] & 64 == 64
    assert wgkeys.is_valid_public_key(pub)
    assert base64.b64decode(pub) == wgkeys.public_from_private_bytes(raw)


def test_keypairs_are_unique():
    assert wgkeys.generate_keypair()[0] != wgkeys.generate_keypair()[0]


def test_old_fake_key_is_not_a_valid_key():
    # La clave ficticia del código anterior no es base64 de 32 bytes válidos de Curve25519.
    assert not wgkeys.is_valid_public_key("not-base64")
    assert not wgkeys.is_valid_public_key("cHVibGljX2tleQ==")


def test_vault_roundtrip(monkeypatch):
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    config._settings = None
    token = wgkeys.encrypt_secret("secreto")
    assert token != "secreto" and wgkeys.decrypt_secret(token) == "secreto"
    config._settings = None
