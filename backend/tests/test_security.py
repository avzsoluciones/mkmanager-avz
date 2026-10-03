import time

import pytest

from app import security as sec


def test_hash_roundtrip_and_format():
    h = sec.hash_password("AvzCloud2026*Test")
    assert h.count(":") == 1
    assert sec.verify_password(h, "AvzCloud2026*Test")
    assert not sec.verify_password(h, "otra")


def test_legacy_hash_in_db_still_verifies():
    # Formato existente en la BD: salt_hex:pbkdf2 (100000 rondas, sal usada como texto).
    import hashlib
    salt = "8e8a931a7bc8516d00e57e937d7a5b3a"
    key = hashlib.pbkdf2_hmac("sha256", b"x", salt.encode(), 100000).hex()
    assert sec.verify_password(f"{salt}:{key}", "x")


@pytest.mark.parametrize("bad", ["", "sinseparador", "a:b:c", None])
def test_malformed_stored_hash_is_false(bad):
    assert sec.verify_password(bad, "x") is False


@pytest.mark.parametrize("pwd,ok", [
    ("Corta1*", False), ("sinmayuscula1*abc", False), ("SINMINUSCULA1*ABC", False),
    ("SinNumero*abcdef", False), ("SinSimbolo12345A", False), ("AvzCloud2026*Ok", True),
])
def test_password_rules(pwd, ok):
    assert sec.password_is_strong(pwd) is ok


def test_token_roundtrip_and_tamper():
    tok = sec.create_token({"id": 7, "rol": "partner"})
    payload = sec.decode_token(tok)
    assert payload["sub"] == "7" and payload["role"] == "partner"
    assert sec.decode_token(tok[:-2] + "xx") is None
    assert sec.decode_token("basura") is None


def test_expired_token_rejected():
    import jwt
    from app.config import get_settings
    old = jwt.encode({"sub": "1", "role": "x", "exp": int(time.time()) - 10},
                     get_settings().secret_key, algorithm="HS256")
    assert sec.decode_token(old) is None


def test_login_rate_limit():
    key = "1.2.3.4|prueba"
    sec.login_ok(key)
    for _ in range(5):
        assert not sec.login_blocked(key)
        sec.login_failed(key)
    assert sec.login_blocked(key)
    sec.login_ok(key)
    assert not sec.login_blocked(key)


def test_sede_scope_by_role():
    assert sec.sede_scope({"role": "superadmin", "id": 1}) == ("TRUE", ())
    assert sec.sede_scope({"role": "soporte", "id": 2}) == ("TRUE", ())
    assert sec.sede_scope({"role": "partner", "id": 3}) == ("s.partner_id = %s", (3,))
    assert sec.sede_scope({"role": "cliente", "id": 4}, "x") == ("x.cliente_id = %s", (4,))
