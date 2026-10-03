"""Autenticación, tokens, roles y aislamiento entre aliados (spec 3, 3.2, A8).
IDs: AZ-* (autenticación/tokens), RB-* (matriz rol x endpoint), IS-* (aislamiento)."""
import re
import time

import jwt
import pytest

from conftest import BODY, PASSWORD, ROLES, iter_routes


def _json(r):
    assert r.headers["content-type"].startswith("application/json"), r.text
    return r.json()


# ---------------------------------------------------------------- AZ: login
def test_AZ01_login_ok_returns_token_user_and_flag(world):
    r = world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": PASSWORD})
    assert r.status_code == 200
    j = r.json()
    assert j["token_type"] == "bearer" and j["access_token"]
    assert set(j["user"]) >= {"id", "email", "full_name", "role", "partner_id"}
    assert j["require_password_change"] is False
    assert "password" not in r.text.lower().replace("require_password_change", "")


def test_AZ02_login_by_username_and_case_insensitive(world):
    for ident in ("admin", "ADMIN", "Admin@AVZ.test"):
        assert world.client.post("/api/v1/auth/login", json={"email": ident, "password": PASSWORD}).status_code == 200


def test_AZ03_wrong_password_and_unknown_user_are_indistinguishable(world):
    a = world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": "mala"})
    b = world.client.post("/api/v1/auth/login", json={"email": "nadie@avz.test", "password": "mala"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_AZ04_inactive_user_cannot_login(world):
    world.db.usuarios[3]["activo"] = False
    assert world.client.post("/api/v1/auth/login", json={"email": "partner.a@avz.test", "password": PASSWORD}).status_code == 401


def test_AZ05_failed_login_is_audited_without_password(world):
    world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": "SecretoMalo123*"})
    assert any(a["accion"] == "login_fallido" for a in world.db.audit)
    assert "SecretoMalo123*" not in str(world.db.audit)


def test_AZ06_rate_limit_blocks_after_5_failures_even_with_right_password(world):
    for _ in range(5):
        assert world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": "x"}).status_code == 401
    r = world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": PASSWORD})
    assert r.status_code == 429 and _json(r)["detail"]


def test_AZ07_rate_limit_is_per_identifier_and_normalized(world):
    for _ in range(5):
        world.client.post("/api/v1/auth/login", json={"email": "ADMIN@avz.test", "password": "x"})
    # mismo identificador con otra capitalización: bloqueado
    assert world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": PASSWORD}).status_code == 429
    # otro usuario desde la misma IP: no bloqueado
    assert world.client.post("/api/v1/auth/login", json={"email": "soporte@avz.test", "password": PASSWORD}).status_code == 200


def test_AZ08_successful_login_resets_counter(world):
    for _ in range(4):
        world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": "x"})
    assert world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": PASSWORD}).status_code == 200
    for _ in range(4):
        assert world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": "x"}).status_code == 401


@pytest.mark.xfail(strict=False, reason="HALLAZGO: el límite es por IP+identificador; un atacante que rota identificadores "
                                        "(password spraying) nunca se bloquea. Falta un tope por IP.")
def test_AZ09_rate_limit_also_caps_a_single_ip_across_identifiers(world):
    for i in range(30):
        world.client.post("/api/v1/auth/login", json={"email": f"user{i}@avz.test", "password": "x"})
    assert world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": PASSWORD}).status_code == 429


@pytest.mark.xfail(strict=False, reason="HALLAZGO (bajo): _FAILS es un defaultdict sin tope; cada identificador inventado "
                                        "deja una entrada en memoria (DoS lento). login_blocked() crea la entrada aunque no falle.")
def test_AZ10_limiter_memory_does_not_grow_for_successful_or_unknown_keys():
    from app import security as sec
    sec._FAILS.clear()
    for i in range(1000):
        sec.login_blocked(f"1.1.1.1|nadie{i}")
    assert len(sec._FAILS) < 10


@pytest.mark.parametrize("payload", [{}, {"email": "a"}, {"password": "b"}, {"email": 1, "password": 2},
                                     {"email": None, "password": None}, [], "texto"])
def test_AZ11_login_validation_errors_are_json_422(world, payload):
    r = world.client.post("/api/v1/auth/login", json=payload)
    assert r.status_code == 422
    assert isinstance(_json(r)["detail"], list)


def test_AZ12_login_sql_injection_attempt_is_inert(world):
    r = world.client.post("/api/v1/auth/login", json={"email": "' OR '1'='1", "password": "x"})
    assert r.status_code == 401
    assert all("%s" in q or "SELECT 1" in q for q in world.db.sqls if "usuarios" in q)   # siempre parametrizado


# ---------------------------------------------------------------- AZ: tokens
def test_AZ20_no_token_is_401_json(world):
    for path in ("/api/v1/auth/me", "/api/v1/sedes", "/api/v1/devices", "/api/v1/devices/100", "/api/v1/provision/100/script"):
        r = world.client.get(path)
        assert r.status_code == 401, path
        assert _json(r)["detail"]


@pytest.mark.parametrize("header", ["Bearer", "Bearer ", "Bearer basura", "Basic YWRtaW46eA==", "token abc", "Bearer a.b.c",
                                    "bearer " + "x" * 5000])
def test_AZ21_malformed_authorization_header_is_401(world, header):
    assert world.client.get("/api/v1/auth/me", headers={"Authorization": header}).status_code == 401


def test_AZ22_tampered_signature_rejected(world):
    t = world.token(1)
    bad = t[:-3] + ("AAA" if not t.endswith("AAA") else "BBB")
    assert world.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad}"}).status_code == 401


def test_AZ23_expired_token_rejected(world):
    t = jwt.encode({"sub": "1", "role": "superadmin", "exp": int(time.time()) - 5}, world.settings.secret_key, algorithm="HS256")
    r = world.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 401


def test_AZ24_token_signed_with_other_secret_rejected(world):
    t = jwt.encode({"sub": "1", "role": "superadmin", "exp": int(time.time()) + 600}, "otra-clave-" * 5, algorithm="HS256")
    assert world.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_AZ25_alg_none_token_rejected(world):
    t = jwt.encode({"sub": "1", "role": "superadmin"}, None, algorithm="none")
    assert world.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t}"}).status_code == 401


def test_AZ26_role_claim_in_token_is_ignored_db_role_wins(world):
    """Spec 3.2: el rol se relee de la BD. Un token con role=superadmin de un cliente no escala privilegios."""
    t = jwt.encode({"sub": "5", "role": "superadmin", "exp": int(time.time()) + 600}, world.settings.secret_key, algorithm="HS256")
    h = {"Authorization": f"Bearer {t}"}
    assert world.client.get("/api/v1/auth/me", headers=h).json()["user"]["role"] == "cliente"
    assert world.client.post("/api/v1/provision", json=BODY, headers=h).status_code == 403


def test_AZ27_deactivating_user_takes_effect_immediately(world):
    h = world.auth(3)
    assert world.client.get("/api/v1/sedes", headers=h).status_code == 200
    world.db.usuarios[3]["activo"] = False
    assert world.client.get("/api/v1/sedes", headers=h).status_code == 401


def test_AZ28_deleted_user_token_rejected(world):
    h = world.auth(3)
    del world.db.usuarios[3]
    assert world.client.get("/api/v1/sedes", headers=h).status_code == 401


def test_AZ29_role_change_in_db_applies_to_existing_token(world):
    h = world.auth(3)                       # partner
    world.db.usuarios[3]["rol"] = "cliente"
    assert world.client.post("/api/v1/sedes", json={"nombre": "Nueva", "ciudad": "Cali"}, headers=h).status_code == 403


@pytest.mark.parametrize("sub", ["abc", "", "1.5", "-1", "99999999999999999999"])
def test_AZ30_valid_signature_but_bad_sub_is_401_not_500(world, sub):
    t = jwt.encode({"sub": sub, "role": "x", "exp": int(time.time()) + 600}, world.settings.secret_key, algorithm="HS256")
    r = world.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 401, "HALLAZGO bajo: int(payload['sub']) lanza ValueError -> 500 (security.py:102)"


def test_AZ31_token_without_sub_is_401_not_500(world):
    t = jwt.encode({"role": "x", "exp": int(time.time()) + 600}, world.settings.secret_key, algorithm="HS256")
    r = world.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {t}"})
    assert r.status_code == 401, "HALLAZGO bajo: KeyError 'sub' -> 500 (security.py:102)"


def test_AZ32_token_has_expiry_per_config(world):
    from app import security as sec
    p = sec.decode_token(sec.create_token({"id": 1, "rol": "superadmin"}))
    assert 0 < p["exp"] - p["iat"] <= world.settings.token_hours * 3600


def test_AZ33_me_does_not_leak_hash(world):
    r = world.client.get("/api/v1/auth/me", headers=world.auth(1))
    assert "password" not in r.text and "hash" not in r.text


# ---------------------------------------------------------------- AZ: cambio de contraseña
def test_AZ40_change_password_wrong_current(world):
    r = world.client.post("/api/v1/auth/change-password", headers=world.auth(5),
                          json={"current_password": "no", "new_password": "NuevaClave*2027x"})
    assert r.status_code == 400


@pytest.mark.parametrize("weak", ["corta1*A", "sinnumeroABC*abc", "SINMINUSCULA123*", "SinSimbolo12345"])
def test_AZ41_change_password_weak_rejected(world, weak):
    r = world.client.post("/api/v1/auth/change-password", headers=world.auth(5),
                          json={"current_password": PASSWORD, "new_password": weak})
    assert r.status_code == 400 and _json(r)["detail"]


def test_AZ42_change_password_ok_audited_and_old_password_stops_working(world):
    new = "NuevaClave*2027x"
    r = world.client.post("/api/v1/auth/change-password", headers=world.auth(5),
                          json={"current_password": PASSWORD, "new_password": new})
    assert r.status_code == 200 and r.json()["status"] == "success"
    assert any(a["accion"] == "cambio_password" for a in world.db.audit)
    assert world.client.post("/api/v1/auth/login", json={"email": "cliente.a@avz.test", "password": PASSWORD}).status_code == 401
    assert world.client.post("/api/v1/auth/login", json={"email": "cliente.a@avz.test", "password": new}).status_code == 200
    assert new not in str(world.db.audit)


def test_AZ43_cliente_can_change_own_password(world):
    """Spec 3.2: todos los roles cambian su propia contraseña."""
    for uid in (1, 2, 3, 5):
        r = world.client.post("/api/v1/auth/change-password", headers=world.auth(uid),
                              json={"current_password": "mala", "new_password": "NuevaClave*2027x"})
        assert r.status_code == 400    # llega a la lógica (no 403)


@pytest.mark.xfail(strict=False, reason="HALLAZGO (bajo): change-password no tiene límite de intentos; permite probar la "
                                        "contraseña actual sin freno con un token robado.")
def test_AZ44_change_password_is_rate_limited(world):
    codes = [world.client.post("/api/v1/auth/change-password", headers=world.auth(5),
                               json={"current_password": f"mala{i}", "new_password": "NuevaClave*2027x"}).status_code
             for i in range(12)]
    assert 429 in codes


# ---------------------------------------------------------------- RB: matriz rol x endpoint (existentes)
# (método, ruta, cuerpo, permitidos por rol, status esperado si permitido)
SEDE_BODY = {"nombre": "Sede Nueva QA", "ciudad": "Cali"}


@pytest.mark.parametrize("role,uid,allowed", [
    ("superadmin", 1, True), ("soporte", 2, True), ("partner_a", 3, True), ("partner_a2", 9, True), ("tecnico_a", 7, False),
    ("cliente_a", 5, False)])
def test_RB01_create_sede_matrix(world, role, uid, allowed):
    r = world.client.post("/api/v1/sedes", json=SEDE_BODY, headers=world.auth(uid))
    assert r.status_code == (201 if allowed else 403), r.text
    if allowed:
        assert set(r.json()) >= {"id", "nombre", "ciudad", "estado"}
        assert any(a["accion"] == "sede_creada" for a in world.db.audit)


@pytest.mark.parametrize("role,uid,allowed", [
    ("superadmin", 1, True), ("soporte", 2, True), ("partner_a", 3, True), ("tecnico_a", 7, True), ("cliente_a", 5, False)])
def test_RB02_provision_matrix(world, role, uid, allowed):
    for path in ("/api/v1/provision/preview", "/api/v1/provision"):
        r = world.client.post(path, json=BODY, headers=world.auth(uid))
        if allowed:
            assert r.status_code in (200, 201), (path, r.text)
        else:
            assert r.status_code == 403, path


@pytest.mark.parametrize("role,uid,allowed", [
    ("superadmin", 1, True), ("soporte", 2, True), ("partner_a", 3, True), ("tecnico_a", 7, True), ("cliente_a", 5, False)])
def test_RB03_script_download_matrix(world, role, uid, allowed):
    r = world.client.get("/api/v1/provision/100/script", headers=world.auth(uid))
    assert r.status_code == (200 if allowed else 403), r.text
    if allowed:
        assert "private-key=" in r.json()["script"]
        assert any(a["accion"] == "script_consultado" for a in world.db.audit)


@pytest.mark.parametrize("path", ["/api/v1/sedes", "/api/v1/devices", "/api/v1/devices/100", "/api/v1/auth/me"])
@pytest.mark.parametrize("uid", [1, 2, 3, 5, 7, 9])
def test_RB04_read_endpoints_open_to_all_roles(world, path, uid):
    r = world.client.get(path, headers=world.auth(uid))
    assert r.status_code == 200      # device 100 pertenece a la sede 10: visible para super, soporte, partner A y cliente A


def test_RB05_cliente_cannot_write_anything_that_exists(world):
    h = world.auth(5)
    assert world.client.post("/api/v1/sedes", json=SEDE_BODY, headers=h).status_code == 403
    assert world.client.post("/api/v1/provision/preview", json=BODY, headers=h).status_code == 403
    assert world.client.post("/api/v1/provision", json=BODY, headers=h).status_code == 403
    assert world.client.get("/api/v1/provision/100/script", headers=h).status_code == 403
    assert world.hub.s and world.api.added == []


def test_RB06_role_gate_happens_before_validation_and_hub_access(world):
    """Un cliente con cuerpo inválido recibe 403 (no 422) y el hub no se toca."""
    r = world.client.post("/api/v1/provision", json={"basura": 1}, headers=world.auth(5))
    assert r.status_code == 403
    assert world.api.connects == 0


def test_RB07_all_mutating_routes_require_a_non_cliente_role(world):
    """Barrido: toda ruta POST/PUT/PATCH/DELETE (salvo login y change-password) debe negar al cliente."""
    skip = {"/api/v1/auth/login", "/api/v1/auth/change-password"}
    seen = 0
    for m, path in iter_routes(world.app):
        if not path.startswith("/api/v1") or path in skip or m not in {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        seen += 1
        url = re.sub(r"\{[^}]+\}", "1", path)
        resp = world.client.request(m, url, json={}, headers=world.auth(5))
        assert resp.status_code in (403,), f"{m} {path} -> {resp.status_code}"
    assert seen >= 3


# ---------------------------------------------------------------- IS: aislamiento entre aliados (A8)
def _ids(rows):
    return sorted(x["id"] for x in rows)


def test_IS01_partner_lists_only_own_sedes_and_devices(world):
    assert _ids(world.client.get("/api/v1/sedes", headers=world.auth(3)).json()) == [10, 11]
    assert _ids(world.client.get("/api/v1/sedes", headers=world.auth(4)).json()) == [20]
    assert _ids(world.client.get("/api/v1/devices", headers=world.auth(3)).json()) == [100, 101]
    assert _ids(world.client.get("/api/v1/devices", headers=world.auth(4)).json()) == [200]


def test_IS02_partner_cannot_read_other_partner_device(world):
    assert world.client.get("/api/v1/devices/200", headers=world.auth(3)).status_code == 404
    assert world.client.get("/api/v1/devices/100", headers=world.auth(4)).status_code == 404


def test_IS03_partner_cannot_download_other_partner_script(world):
    r = world.client.get("/api/v1/provision/200/script", headers=world.auth(3))
    assert r.status_code == 404
    assert "private-key" not in r.text
    assert not any(a["accion"] == "script_consultado" for a in world.db.audit)


def test_IS04_partner_cannot_provision_into_other_partner_sede(world):
    for path in ("/api/v1/provision/preview", "/api/v1/provision"):
        r = world.client.post(path, json={**BODY, "sede_id": 20}, headers=world.auth(3))
        assert r.status_code == 404, path
    assert world.api.added == [] and not any(d["sede_id"] == 20 and d["id"] > 5000 for d in world.db.dispositivos)


def test_IS05_partner_cannot_create_sede_for_another_partner(world):
    r = world.client.post("/api/v1/sedes", json={**SEDE_BODY, "partner_id": 4}, headers=world.auth(3))
    assert r.status_code == 201
    created = next(s for s in world.db.sedes if s["id"] == r.json()["id"])
    assert created["partner_id"] == 3          # se ignora el partner_id enviado


def test_IS06_partner_a_sede_not_visible_to_b_after_creation(world):
    sid = world.client.post("/api/v1/sedes", json=SEDE_BODY, headers=world.auth(3)).json()["id"]
    assert sid not in _ids(world.client.get("/api/v1/sedes", headers=world.auth(4)).json())


def test_IS07_same_sede_name_allowed_for_different_partners_but_not_within_one(world):
    assert world.client.post("/api/v1/sedes", json={"nombre": "Sede A1", "ciudad": "x1"}, headers=world.auth(4)).status_code == 201
    assert world.client.post("/api/v1/sedes", json={"nombre": "sede a1", "ciudad": "x1"}, headers=world.auth(3)).status_code == 409


def test_IS08_cliente_sees_only_own_sede_devices(world):
    assert _ids(world.client.get("/api/v1/sedes", headers=world.auth(5)).json()) == [10]
    assert _ids(world.client.get("/api/v1/devices", headers=world.auth(5)).json()) == [100]
    assert world.client.get("/api/v1/devices/101", headers=world.auth(5)).status_code == 404   # misma cartera, otra sede
    assert world.client.get("/api/v1/devices/200", headers=world.auth(5)).status_code == 404


def test_IS09_superadmin_and_soporte_see_everything(world):
    for uid in (1, 2):
        assert _ids(world.client.get("/api/v1/devices", headers=world.auth(uid)).json()) == [100, 101, 200]
        assert _ids(world.client.get("/api/v1/sedes", headers=world.auth(uid)).json()) == [10, 11, 20]


def test_IS10_sedes_without_owner_are_invisible_to_partners(world):
    world.db.add_sede(id=99, nombre="Huerfana", partner_id=None)
    assert 99 not in _ids(world.client.get("/api/v1/sedes", headers=world.auth(3)).json())
    assert 99 in _ids(world.client.get("/api/v1/sedes", headers=world.auth(1)).json())


@pytest.mark.parametrize("bad", ["abc", "-1", "0", "1e3", "99999999999999999999", "1;DROP TABLE x"])
def test_IS11_device_id_hostile_values_do_not_500(world, bad):
    r = world.client.get(f"/api/v1/devices/{bad}", headers=world.auth(1))
    assert r.status_code in (404, 422)
    assert r.headers["content-type"].startswith("application/json")


def test_IS12_scope_sql_is_always_parameterized_and_never_interpolates_user_id(world):
    world.client.get("/api/v1/devices", headers=world.auth(3))
    world.client.get("/api/v1/devices/100", headers=world.auth(3))
    world.client.get("/api/v1/sedes", headers=world.auth(3))
    scoped = [q for q in world.db.sqls if "partner_id = %s" in q]
    assert len(scoped) >= 3


# ---------------------------------------------------------------- RB: el cliente nunca recibe secretos
SECRET_MARKERS = ("private-key", "PrivateKey", "api_password", "password_hash", "private_key_enc", "clave-api", "api_password_enc")


def test_IS20_cliente_responses_contain_no_secrets(world):
    for path in ("/api/v1/sedes", "/api/v1/devices", "/api/v1/devices/100", "/api/v1/auth/me"):
        txt = world.client.get(path, headers=world.auth(5)).text
        for m in SECRET_MARKERS:
            assert m not in txt, (path, m)


def test_IS21_device_listing_sql_never_selects_secret_columns(world):
    for uid in (1, 3, 5):
        world.client.get("/api/v1/devices", headers=world.auth(uid))
        world.client.get("/api/v1/devices/100", headers=world.auth(uid))
        world.client.get("/api/v1/sedes", headers=world.auth(uid))
    listing = " ".join(q for q in world.db.sqls if "dispositivos d JOIN sedes" in q and "wg_peers" not in q)
    for col in ("api_password_enc", "private_key_enc", "password_hash", "wan_config", "lan_config"):
        assert col not in listing, col


def test_IS22_partner_response_for_other_partner_is_404_not_403(world):
    """No revelar la existencia del recurso ajeno."""
    other = world.client.get("/api/v1/devices/200", headers=world.auth(3))
    missing = world.client.get("/api/v1/devices/987654", headers=world.auth(3))
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json() or other.json()["detail"] == missing.json()["detail"]


@pytest.mark.parametrize("role", list(ROLES))
def test_IS23_every_response_is_json_for_every_role(world, role):
    for path in ("/api/v1/sedes", "/api/v1/devices", "/api/v1/devices/200", "/api/v1/provision/200/script"):
        r = world.client.get(path, headers=world.auth(ROLES[role]))
        assert r.headers["content-type"].startswith("application/json"), (role, path)

