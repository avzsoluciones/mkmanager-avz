"""Claves WireGuard (Curve25519) y su cifrado en reposo."""
import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from ..config import get_settings


def _clamp(k: bytearray) -> bytes:
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    return bytes(k)


def public_from_private_bytes(raw: bytes) -> bytes:
    pub = X25519PrivateKey.from_private_bytes(raw).public_key()
    return pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def generate_keypair() -> tuple:
    """Devuelve (privada_b64, publica_b64) en el formato de `wg genkey` / `wg pubkey`."""
    raw = _clamp(bytearray(os.urandom(32)))
    return base64.b64encode(raw).decode(), base64.b64encode(public_from_private_bytes(raw)).decode()


def generate_preshared() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def is_valid_public_key(key: str) -> bool:
    try:
        return len(base64.b64decode(key, validate=True)) == 32
    except Exception:
        return False


def _fernet() -> Fernet:
    key = get_settings().vault_key
    if not key:
        raise RuntimeError("VAULT_KEY no configurada; no se pueden guardar claves privadas.")
    return Fernet(key.encode())


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise RuntimeError("No se pudo descifrar la clave: VAULT_KEY distinta a la usada al guardarla.")
