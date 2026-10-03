"""Usuarios, cartera (tenant) y baja de equipos. Spec 3.2 + decisiones 2 y 3. IDs: US-* (usuarios), BJ-* (baja)."""
import pytest

from conftest import PASSWORD

NEW = {"email": "nuevo@cliente.test", "nombre_completo": "Cliente Nuevo", "rol": "cliente"}


def post(w, uid, path, json=None):
    return w.client.post(path, json=json, headers=w.auth(uid))


# ---------------------------------------------------------------- listado y alcance
def test_US01_list_scope_by_role(world):
    ids = lambda uid: sorted(u["id"] for u in world.client.get("/api/v1/users", headers=world.auth(uid)).json())  # noqa: E731
    assert ids(1) == [1, 2, 3, 4, 5, 6, 7, 8, 9]
    assert ids(3) == [3, 5, 7, 9] == ids(9)               # toda la cartera A, incluido el otro partner de A
    assert ids(4) == [4, 6, 8]


@pytest.mark.parametrize("uid", [2, 5, 7])
def test_US02_soporte_tecnico_cliente_cannot_list_users(world, uid):
    assert world.client.get("/api/v1/users", headers=world.auth(uid)).status_code == 403


def test_US03_user_rows_never_expose_hashes_or_temp_passwords(world):
    t = world.client.get("/api/v1/users", headers=world.auth(1)).text
    assert "password" not in t.lower() and "hash" not in t.lower()


# ---------------------------------------------------------------- crear
def test_US10_partner_creates_cliente_inside_own_tenant(world):
    r = post(world, 3, "/api/v1/users", NEW)
    assert r.status_code == 201, r.text
    j = r.json()
    from app.security import password_is_strong, verify_password
    assert password_is_strong(j["password_temporal"]) and j["aviso"]
    row = world.db.usuarios[j["user"]["id"]]
    assert row["partner_id"] == 3 and row["rol"] == "cliente" and row["require_password_change"] is True
    assert verify_password(row["password_hash"], j["password_temporal"]) and j["password_temporal"] not in row["password_hash"]
    assert j["password_temporal"] not in str(world.db.audit)


def test_US11_partner_creates_tecnico_with_same_tenant(world):
    r = post(world, 3, "/api/v1/users", {**NEW, "rol": "tecnico"})
    assert r.status_code == 201 and world.db.usuarios[r.json()["user"]["id"]]["tenant_id"] == 3


@pytest.mark.parametrize("rol", ["superadmin", "soporte", "partner"])
def test_US12_partner_cannot_create_privileged_roles(world, rol):
    n = len(world.db.usuarios)
    assert post(world, 3, "/api/v1/users", {**NEW, "rol": rol}).status_code == 403
    assert len(world.db.usuarios) == n


def test_US13_partner_cannot_place_users_in_another_tenant_via_body(world):
    r = post(world, 3, "/api/v1/users", {**NEW, "partner_id": 4, "tenant_id": 4})
    assert r.status_code == 201
    assert world.db.usuarios[r.json()["user"]["id"]]["partner_id"] == 3
    r = post(world, 3, "/api/v1/users", {**NEW, "email": "otro@cliente.test", "rol": "tecnico", "tenant_id": 4})
    assert world.db.usuarios[r.json()["user"]["id"]]["tenant_id"] == 3


@pytest.mark.parametrize("uid", [2, 5, 7])
def test_US14_soporte_cliente_tecnico_cannot_create_users(world, uid):
    assert post(world, uid, "/api/v1/users", NEW).status_code == 403


def test_US15_superadmin_creates_any_role_and_partner_gets_own_tenant(world):
    r = post(world, 1, "/api/v1/users", {**NEW, "rol": "partner"})
    assert r.status_code == 201
    row = world.db.usuarios[r.json()["user"]["id"]]
    assert row["tenant_id"] == row["id"], "un aliado nuevo es su propia cartera"
    assert post(world, 1, "/api/v1/users", {**NEW, "email": "s@x.test", "rol": "soporte"}).status_code == 201


def test_US16_superadmin_tecnico_requires_existing_tenant(world):
    t = {**NEW, "rol": "tecnico"}
    assert post(world, 1, "/api/v1/users", t).status_code == 422
    assert post(world, 1, "/api/v1/users", {**t, "tenant_id": 5}).status_code == 422           # 5 es un cliente
    assert post(world, 1, "/api/v1/users", {**t, "tenant_id": 999}).status_code == 422
    assert post(world, 1, "/api/v1/users", {**t, "tenant_id": 4}).status_code == 201


@pytest.mark.parametrize("email", ["", "sin-arroba", "a@b", "a@b.", "@x.com", "a b@x.com", "a@x.com\nBcc: v@evil.test", "a@x.com,b@y.com",
                                   "a@@x.com", "x" * 80 + "@x.com", "a" * 200 + "@x.com"])
def test_US17_hostile_emails_rejected(world, email):
    assert post(world, 1, "/api/v1/users", {**NEW, "email": email}).status_code == 422


@pytest.mark.parametrize("nombre", ["", "a", "x" * 151])
def test_US18_name_length_bounds(world, nombre):
    assert post(world, 1, "/api/v1/users", {**NEW, "nombre_completo": nombre}).status_code == 422


def test_US19_duplicate_email_case_insensitive_409(world):
    assert post(world, 1, "/api/v1/users", {**NEW, "email": "Admin@AVZ.test"}).status_code == 409


def test_US20_unknown_role_and_extra_fields(world):
    assert post(world, 1, "/api/v1/users", {**NEW, "rol": "root"}).status_code == 422
    r = post(world, 1, "/api/v1/users", {**NEW, "password_hash": "x:y", "activo": False, "require_password_change": False})
    assert r.status_code == 201
    row = world.db.usuarios[r.json()["user"]["id"]]
    assert row["password_hash"] != "x:y" and row["require_password_change"] is True and row["activo"] is True, "mass-assignment"


def test_US21_db_failure_creating_user_is_json_500_and_leaves_no_row(world):
    world.db.fail_on = "enter"
    n = len(world.db.usuarios)
    r = post(world, 1, "/api/v1/users", NEW)
    assert r.status_code == 500 and r.json() == {"detail": "Error interno del servidor."} and len(world.db.usuarios) == n


def test_US22_temp_password_delivered_once_and_login_forces_change(world):
    pwd = post(world, 3, "/api/v1/users", NEW).json()["password_temporal"]
    r = world.client.post("/api/v1/auth/login", json={"email": NEW["email"], "password": pwd})
    assert r.status_code == 200 and r.json()["require_password_change"] is True


# ---------------------------------------------------------------- activar / desactivar / reset
def test_US30_partner_deactivates_own_cliente_and_login_stops(world):
    r = post(world, 3, "/api/v1/users/5/active", {"activo": False})
    assert r.status_code == 200 and r.json() == {"status": "success", "activo": False}
    assert world.client.post("/api/v1/auth/login", json={"email": "cliente.a@avz.test", "password": PASSWORD}).status_code == 401
    assert any(a["accion"] == "usuario_desactivado" for a in world.db.audit)
    assert post(world, 3, "/api/v1/users/5/active", {"activo": True}).status_code == 200


@pytest.mark.parametrize("target,code", [(6, 404), (8, 404), (4, 404), (1, 404), (2, 404), (9, 403), (3, 400)])
def test_US31_partner_cannot_touch_outside_scope_or_peers_or_self(world, target, code):
    """6/8/4 = otro aliado, 1/2 = AVZ (no revelar: 404), 9 = otro partner del mismo aliado (403), 3 = sí mismo (400)."""
    r = post(world, 3, f"/api/v1/users/{target}/active", {"activo": False})
    assert r.status_code == code, (target, r.status_code, r.text)
    assert world.db.usuarios[target]["activo"] is True


def test_US32_superadmin_cannot_deactivate_itself(world):
    assert post(world, 1, "/api/v1/users/1/active", {"activo": False}).status_code == 400


@pytest.mark.parametrize("uid", [2, 5, 7])
def test_US33_soporte_tecnico_cliente_cannot_manage_users(world, uid):
    assert post(world, uid, "/api/v1/users/5/active", {"activo": False}).status_code == 403
    assert post(world, uid, "/api/v1/users/5/reset-password").status_code == 403


def test_US34_reset_password_new_one_works_old_fails_and_forces_change(world):
    r = post(world, 3, "/api/v1/users/5/reset-password")
    assert r.status_code == 200
    pwd = r.json()["password_temporal"]
    assert world.client.post("/api/v1/auth/login", json={"email": "cliente.a@avz.test", "password": PASSWORD}).status_code == 401
    ok = world.client.post("/api/v1/auth/login", json={"email": "cliente.a@avz.test", "password": pwd})
    assert ok.status_code == 200 and ok.json()["require_password_change"] is True
    assert pwd not in str(world.db.audit) and any(a["accion"] == "usuario_clave_restablecida" for a in world.db.audit)


@pytest.mark.parametrize("target,code", [(6, 404), (8, 404), (1, 404), (9, 403), (2, 404)])
def test_US35_reset_password_scope(world, target, code):
    before = world.db.usuarios[target]["password_hash"]
    assert post(world, 3, f"/api/v1/users/{target}/reset-password").status_code == code
    assert world.db.usuarios[target]["password_hash"] == before


@pytest.mark.parametrize("body", [{}, {"activo": "si"}, {"activo": None}, {"activo": 1, "x": 1}, []])
def test_US36_active_body_validation(world, body):
    r = post(world, 1, "/api/v1/users/5/active", body)
    assert r.status_code in (200, 422) and r.headers["content-type"].startswith("application/json")
    if body in ({}, {"activo": None}, [], {"activo": "si"}):
        assert r.status_code == 422


@pytest.mark.parametrize("uid", ["abc", "-1", "99999999999999999999"])
def test_US37_hostile_user_id_not_500(world, uid):
    r = post(world, 1, f"/api/v1/users/{uid}/reset-password")
    assert r.status_code in (404, 422)


def test_US38_tecnico_shares_tenant_scope_with_partner(world):
    h = world.auth(7)
    assert sorted(s["id"] for s in world.client.get("/api/v1/sedes", headers=h).json()) == [10, 11]
    assert sorted(d["id"] for d in world.client.get("/api/v1/devices", headers=h).json()) == [100, 101]
    assert world.client.get("/api/v1/devices/200", headers=h).status_code == 404


def test_US39_new_partner_colleague_inherits_visibility(world):
    r = post(world, 1, "/api/v1/users", {**NEW, "rol": "partner", "tenant_id": 3})
    uid = r.json()["user"]["id"]
    from app import security as sec
    h = {"Authorization": f"Bearer {sec.create_token({'id': uid, 'rol': 'partner'})}"}
    assert sorted(d["id"] for d in world.client.get("/api/v1/devices", headers=h).json()) == [100, 101]


def test_US40_create_sede_by_superadmin_validates_partner_exists(world):
    assert world.client.post("/api/v1/sedes", json={"nombre": "Nueva", "ciudad": "Cali", "partner_id": 999}, headers=world.auth(1)).status_code == 422
    assert world.client.post("/api/v1/sedes", json={"nombre": "Nueva", "ciudad": "Cali", "partner_id": 5}, headers=world.auth(1)).status_code == 422  # un cliente no es aliado
    assert world.client.post("/api/v1/sedes", json={"nombre": "Nueva", "ciudad": "Cali", "partner_id": 3}, headers=world.auth(1)).status_code == 201


@pytest.mark.parametrize("field", ["nombre", "ciudad", "direccion", "contacto_nombre", "contacto_telefono", "contacto_email"])
@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "a\x00b", "a\x1bb", "a\x7fb"])
def test_US41_sede_text_fields_reject_control_chars(world, field, bad):
    body = {"nombre": "Sede QA", "ciudad": "Cali", field: "Texto " + bad}
    assert world.client.post("/api/v1/sedes", json=body, headers=world.auth(1)).status_code == 422


# ---------------------------------------------------------------- baja de equipos
def delete(w, uid, dev=100, qs="?confirmar=true"):
    return w.client.delete(f"/api/v1/devices/{dev}{qs}", headers=w.auth(uid))


def test_BJ01_requires_explicit_confirmation(world):
    r = delete(world, 1, qs="")
    assert r.status_code == 400 and any(d["id"] == 100 for d in world.db.dispositivos) and world.api.removed == []


@pytest.mark.parametrize("qs", ["?confirmar=false", "?confirmar=0", "?confirmar=no"])
def test_BJ02_confirm_false_does_not_delete(world, qs):
    assert delete(world, 1, qs=qs).status_code == 400 and any(d["id"] == 100 for d in world.db.dispositivos)


def test_BJ03_confirm_wrong_type_is_422(world):
    assert delete(world, 1, qs="?confirmar=quizas").status_code == 422


def test_BJ04_superadmin_baja_removes_peer_route_and_row_leaving_no_orphans(world):
    r = delete(world, 1)
    assert r.status_code == 200 and r.json() == {"status": "success"}
    assert not any(d["id"] == 100 for d in world.db.dispositivos)
    assert "*P100" not in [p[".id"] for p in world.api.peers], "peer huérfano en el hub"
    assert "192.168.100.0/24" not in [x["dst-address"] for x in world.api.routes], "ruta huérfana en el hub"
    assert any(a["accion"] == "equipo_baja" and "id=100" in a["detalles"] for a in world.db.audit)


def test_BJ05_baja_touches_only_own_resources(world):
    foreign = {".id": "*F1", "dst-address": "192.168.100.0/24", "gateway": "10.40.0.9", "comment": "Notaria", "dynamic": False}
    world.api.routes.append(foreign)
    before_peers = {p[".id"] for p in world.api.peers} - {"*P100"}
    delete(world, 1)
    assert "*F1" in [r[".id"] for r in world.api.routes], "no se debe borrar una ruta de otro servicio aunque tenga el mismo destino"
    assert {p[".id"] for p in world.api.peers} == before_peers, "solo se elimina el peer del equipo"


@pytest.mark.parametrize("uid,code", [(2, 403), (7, 403), (5, 403), (4, 404), (8, 403), (6, 403)])
def test_BJ06_baja_permissions_and_isolation(world, uid, code):
    assert delete(world, uid).status_code == code
    assert any(d["id"] == 100 for d in world.db.dispositivos) and world.api.removed == []


def test_BJ07_partner_can_baja_own_device_with_confirmation(world):
    assert delete(world, 3).status_code == 200 and delete(world, 9, dev=101).status_code == 200


def test_BJ08_hub_failure_blocks_baja_unless_forced_and_nothing_deleted(world):
    world.api.fail_remove = True
    r = delete(world, 3)
    assert r.status_code == 502 and any(d["id"] == 100 for d in world.db.dispositivos)


def test_BJ09_force_is_superadmin_only(world):
    world.api.fail_remove = True
    assert delete(world, 3, qs="?confirmar=true&forzar=true").status_code == 403
    assert any(d["id"] == 100 for d in world.db.dispositivos)


def test_BJ10_superadmin_force_baja_records_orphan_for_cleanup(world):
    world.api.fail_remove = True
    r = delete(world, 1, qs="?confirmar=true&forzar=true")
    assert r.status_code == 200 and not any(d["id"] == 100 for d in world.db.dispositivos)
    assert any(a["accion"] == "peer_huerfano" and "100" in a["detalles"] for a in world.db.audit)


def test_BJ11_unknown_device_404_and_double_delete_404(world):
    assert delete(world, 1, dev=424242).status_code == 404
    assert delete(world, 1).status_code == 200 and delete(world, 1).status_code == 404


def test_BJ12_tunnel_ip_can_be_reassigned_after_baja(world):
    delete(world, 1)
    r = world.client.post("/api/v1/provision", json={"sede_id": 10, "nombre": "MK nuevo", "wan": {"interface": "e", "mode": "dhcp"},
                                                       "lan": {"interface": "b", "cidr": "192.168.100.0/24"}, "tunnel_ip": "10.254.0.100"},
                          headers=world.auth(1))
    assert r.status_code == 201, "tras la baja se libera IP de túnel y LAN"


def test_BJ13_baja_error_text_does_not_leak_hub_internals(world):
    world.api.fail_remove = True
    t = delete(world, 3).text
    for leak in ("RuntimeError", "no such item", "wireguard1", "10.10.10.3"):
        assert leak not in t, f"filtración '{leak}' en 502 de baja: {t[:200]}"
