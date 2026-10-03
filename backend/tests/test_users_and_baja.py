import pytest
from fastapi.testclient import TestClient

from app.deps import get_hub
from app.main import app
from app.routers import devices as devices_router
from app.routers import users as users_router
from app.security import current_user, verify_password
from app.services.hub import HubClient

from test_provision import BODY, FakeApi, hub_with


def as_user(role, uid=1, tenant=None):
    app.dependency_overrides[current_user] = lambda: {
        "id": uid, "email": f"{role}@x", "role": role, "partner_id": None, "tenant_id": tenant}


@pytest.fixture(autouse=True)
def _clean():
    yield
    app.dependency_overrides.clear()


class ScriptedDb:
    """BD simulada: `rules` = lista de (fragmento_sql, resultado). Registra lo que se escribe."""
    def __init__(self, rules=()):
        self.rules, self.writes, self.executed = list(rules), [], []

    def _match(self, sql):
        for frag, res in self.rules:
            if frag in sql:
                return res
        return None

    def fetch_one(self, sql, params=()):
        return self._match(sql)

    def fetch_all(self, sql, params=()):
        return self._match(sql) or []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def cursor(self):
        outer = self

        class Ctx:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def execute(s, sql, params=()):
                outer.writes.append((sql, params))

            def fetchone(s):
                return {"id": 77}
        return Ctx()


@pytest.fixture
def audit_off(monkeypatch):
    for mod in (users_router, devices_router):
        monkeypatch.setattr(mod.audit, "log", lambda *a, **k: None)


# ---------------- usuarios ----------------
def create(c, **over):
    body = {"email": "Nuevo@Ejemplo.com", "nombre_completo": "Alirio Técnico", "rol": "tecnico"}
    body.update(over)
    return c.post("/api/v1/users", json=body)


def test_partner_creates_tecnico_in_its_own_tenant(monkeypatch, audit_off):
    db = ScriptedDb()
    monkeypatch.setattr(users_router, "db", db)
    as_user("partner", uid=10, tenant=10)
    r = create(TestClient(app))
    assert r.status_code == 201, r.text
    insert_params = db.writes[0][1]
    assert insert_params[0] == "nuevo@ejemplo.com" and insert_params[4] == "tecnico"
    assert insert_params[6] == 10                       # tenant_id = cartera del aliado
    j = r.json()
    assert j["password_temporal"] and "hash" not in r.text
    assert not any(j["password_temporal"] in str(w) for w in db.writes)   # la clave no se guarda en claro
    assert verify_password(db.writes[0][1][3], j["password_temporal"])


def test_partner_creates_cliente_linked_to_its_tenant(monkeypatch, audit_off):
    db = ScriptedDb()
    monkeypatch.setattr(users_router, "db", db)
    as_user("partner", uid=11, tenant=10)               # técnico-partner de la cartera 10 creando cliente
    r = create(TestClient(app), rol="cliente")
    assert r.status_code == 201, r.text
    assert db.writes[0][1][5] == 10                     # partner_id = cartera, no el id del creador


@pytest.mark.parametrize("rol", ["partner", "soporte", "superadmin"])
def test_partner_cannot_create_privileged_roles(monkeypatch, audit_off, rol):
    monkeypatch.setattr(users_router, "db", ScriptedDb())
    as_user("partner", uid=10, tenant=10)
    assert create(TestClient(app), rol=rol).status_code == 403


@pytest.mark.parametrize("role", ["tecnico", "cliente", "soporte"])
def test_other_roles_cannot_manage_users(monkeypatch, role):
    monkeypatch.setattr(users_router, "db", ScriptedDb())
    as_user(role, uid=5, tenant=10)
    c = TestClient(app)
    assert create(c).status_code == 403
    assert c.get("/api/v1/users").status_code == 403
    assert c.post("/api/v1/users/1/active", json={"activo": False}).status_code == 403
    assert c.post("/api/v1/users/1/reset-password").status_code == 403


def test_superadmin_tecnico_needs_tenant(monkeypatch, audit_off):
    monkeypatch.setattr(users_router, "db", ScriptedDb())
    as_user("superadmin")
    assert create(TestClient(app)).status_code == 422


def test_duplicate_email_conflict(monkeypatch, audit_off):
    monkeypatch.setattr(users_router, "db", ScriptedDb([("LOWER(email)", {"x": 1})]))
    as_user("partner", uid=10, tenant=10)
    assert create(TestClient(app)).status_code == 409


def test_invalid_email_rejected(monkeypatch):
    monkeypatch.setattr(users_router, "db", ScriptedDb())
    as_user("partner", uid=10, tenant=10)
    assert create(TestClient(app), email="no-es-correo").status_code == 422


def test_partner_cannot_touch_user_outside_scope(monkeypatch, audit_off):
    monkeypatch.setattr(users_router, "db", ScriptedDb())            # el usuario no está en su alcance
    as_user("partner", uid=10, tenant=10)
    assert TestClient(app).post("/api/v1/users/99/active", json={"activo": False}).status_code == 404


def test_partner_cannot_manage_other_partner_user(monkeypatch, audit_off):
    row = {"id": 12, "rol": "partner", "email": "otro@x", "partner_id": None, "tenant_id": 10, "activo": True}
    monkeypatch.setattr(users_router, "db", ScriptedDb([("FROM usuarios u", row)]))
    as_user("partner", uid=10, tenant=10)
    assert TestClient(app).post("/api/v1/users/12/active", json={"activo": False}).status_code == 403


def test_cannot_deactivate_self_or_last_superadmin(monkeypatch, audit_off):
    me = {"id": 1, "rol": "superadmin", "email": "a@x", "partner_id": None, "tenant_id": None, "activo": True}
    monkeypatch.setattr(users_router, "db", ScriptedDb([("FROM usuarios u", me)]))
    as_user("superadmin", uid=1)
    assert TestClient(app).post("/api/v1/users/1/active", json={"activo": False}).status_code == 400
    other = {**me, "id": 2}
    monkeypatch.setattr(users_router, "db", ScriptedDb([("FROM usuarios u", other), ("COUNT(*)", {"n": 0})]))
    assert TestClient(app).post("/api/v1/users/2/active", json={"activo": False}).status_code == 400


def test_reset_password_returns_temp_once_and_forces_change(monkeypatch, audit_off):
    target = {"id": 20, "rol": "cliente", "email": "c@x", "partner_id": 10, "tenant_id": None, "activo": True}
    db = ScriptedDb([("FROM usuarios u", target)])
    monkeypatch.setattr(users_router, "db", db)
    as_user("partner", uid=10, tenant=10)
    r = TestClient(app).post("/api/v1/users/20/reset-password")
    assert r.status_code == 200 and r.json()["password_temporal"]
    sql, params = db.executed[0]
    assert "require_password_change = TRUE" in sql and verify_password(params[0], r.json()["password_temporal"])


# ---------------- baja de equipos ----------------
def baja_client(monkeypatch, api, rules=None):
    db = ScriptedDb(rules if rules is not None else [("FROM dispositivos d", {"id": 5, "nombre": "MK", "lan_cidr": "192.168.10.0/24",
                                                       "tunnel_ip": "10.254.0.3", "hub_peer_id": "*99"})])
    monkeypatch.setattr(devices_router, "db", db)
    app.dependency_overrides[get_hub] = lambda: hub_with(api)
    return TestClient(app, raise_server_exceptions=False), db


def test_baja_requires_confirmation(monkeypatch, audit_off):
    c, _ = baja_client(monkeypatch, FakeApi())
    as_user("partner", uid=10, tenant=10)
    assert c.delete("/api/v1/devices/5").status_code == 400


@pytest.mark.parametrize("role", ["soporte", "tecnico", "cliente"])
def test_baja_denied_for_non_authorized_roles(monkeypatch, audit_off, role):
    c, db = baja_client(monkeypatch, FakeApi())
    as_user(role, uid=3, tenant=10)
    assert c.delete("/api/v1/devices/5?confirmar=true").status_code == 403
    assert db.executed == []


def test_baja_removes_peer_route_and_record(monkeypatch, audit_off):
    api = FakeApi()
    api.data[("ip", "route")] = [
        {".id": "*R1", "dst-address": "192.168.10.0/24", "dynamic": False, "comment": "AVZ Sede - MK"},
        {".id": "*R2", "dst-address": "192.168.1.0/24", "dynamic": False, "comment": "LAN Notaria via tunel RB951G"},
    ]
    c, db = baja_client(monkeypatch, api)
    as_user("partner", uid=10, tenant=10)
    r = c.delete("/api/v1/devices/5?confirmar=true")
    assert r.status_code == 200, r.text
    assert "*99" in api.removed and "*R1" in api.removed
    assert "*R2" not in api.removed                          # nunca toca rutas ajenas (Notaria)
    assert any("DELETE FROM dispositivos" in s for s, _ in db.executed)


def test_baja_outside_scope_is_404(monkeypatch, audit_off):
    c, db = baja_client(monkeypatch, FakeApi(), rules=[])
    as_user("partner", uid=10, tenant=10)
    assert c.delete("/api/v1/devices/5?confirmar=true").status_code == 404 and db.executed == []


def test_baja_hub_failure_keeps_record_unless_forced(monkeypatch, audit_off):
    class Boom(FakeApi):
        def path(self, *parts):
            raise RuntimeError("hub caído")
    c, db = baja_client(monkeypatch, Boom())
    as_user("partner", uid=10, tenant=10)
    assert c.delete("/api/v1/devices/5?confirmar=true").status_code == 502 and db.executed == []
    assert c.delete("/api/v1/devices/5?confirmar=true&forzar=true").status_code == 403    # partner no puede forzar
    as_user("superadmin")
    assert c.delete("/api/v1/devices/5?confirmar=true&forzar=true").status_code == 200
    assert any("DELETE FROM dispositivos" in s for s, _ in db.executed)


def test_static_routes_of_other_services_block_overlapping_lan():
    api = FakeApi()
    api.data[("ip", "route")] = [{".id": "*1", "dst-address": "192.168.1.0/24", "dynamic": False, "comment": "LAN Notaria"}]
    assert "192.168.1.0/24" in hub_with(api).used_lan_networks()


def _sedes_client(rules):
    from app.routers import sedes as sedes_router
    fake = ScriptedDb(rules)
    sedes_router.db = fake
    sedes_router.audit = type("A", (), {"log": staticmethod(lambda *a, **k: None)})
    return TestClient(app), fake


def test_delete_sede_vacia():
    as_user("superadmin")
    c, fake = _sedes_client([("FROM sedes s WHERE", {"id": 5, "nombre": "X"}),
                             ("FROM dispositivos", {"n": 0}), ("FROM wg_peers", {"n": 0})])
    assert c.delete("/api/v1/sedes/5").status_code == 200
    assert any("DELETE FROM sedes" in s for s, _ in fake.executed)


def test_delete_sede_con_equipos_o_vpn_da_409():
    as_user("superadmin")
    c, fake = _sedes_client([("FROM sedes s WHERE", {"id": 5, "nombre": "X"}),
                             ("FROM dispositivos", {"n": 2}), ("FROM wg_peers", {"n": 0})])
    assert c.delete("/api/v1/sedes/5").status_code == 409
    c, fake = _sedes_client([("FROM sedes s WHERE", {"id": 5, "nombre": "X"}),
                             ("FROM dispositivos", {"n": 0}), ("FROM wg_peers", {"n": 1})])
    assert c.delete("/api/v1/sedes/5").status_code == 409
    assert not fake.executed


def test_delete_sede_solo_superadmin_y_404():
    as_user("partner", tenant=9)
    c, _ = _sedes_client([])
    assert c.delete("/api/v1/sedes/5").status_code == 403
    as_user("superadmin")
    c, _ = _sedes_client([])
    assert c.delete("/api/v1/sedes/5").status_code == 404
