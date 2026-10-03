import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.routers import provision as prov
from app.security import current_user
from app.services.hub import HubClient, HubError

HUB_KEY = "4u9Wu/zFGuHL8hc9UmL9MxWenkPC5R9XP2jyhHLfuGM="


class FakePath:
    def __init__(self, api, parts):
        self.api, self.parts = api, parts

    def __iter__(self):
        return iter(self.api.data.get(self.parts, []))

    def add(self, **kw):
        if self.api.fail_add:
            raise RuntimeError("failure: invalid public key")
        self.api.added.append(kw)
        return "*99"

    def remove(self, _id):
        self.api.removed.append(_id)


class FakeApi:
    def __init__(self, peers=None, fail_add=False):
        self.data = {
            ("interface", "wireguard"): [{"name": "wireguard1", "public-key": HUB_KEY}],
            ("interface", "wireguard", "peers"): peers if peers is not None else [
                {"name": "peer1", "interface": "wg1", "allowed-address": "10.40.1.11/32"},
                {"name": "peer8", "interface": "wg1", "allowed-address": "10.254.0.50/32"},
                {"name": "x", "interface": "wireguard1", "allowed-address": "10.254.0.10/32,192.168.3.0/24"},
            ],
        }
        self.added, self.removed, self.fail_add = [], [], fail_add

    def path(self, *parts):
        return FakePath(self, parts)


def settings():
    config._settings = None
    import os
    os.environ.update(HUB_HOST="10.10.10.3", HUB_API_USER="u", HUB_API_PASSWORD="p", HUB_WG_INTERFACE="wireguard1",
                      HUB_PUBLIC_ENDPOINT="vpn.example.com", VAULT_KEY=Fernet.generate_key().decode())
    return config.get_settings()


def hub_with(api):
    return HubClient(settings(), connect=lambda **kw: api)


def test_hub_reads_real_public_key():
    assert hub_with(FakeApi()).public_key() == HUB_KEY


def test_hub_reports_used_ips_across_all_interfaces():
    used = hub_with(FakeApi()).used_tunnel_ips()
    assert set(used) == {"10.254.0.50", "10.254.0.10"}


def test_hub_reports_lan_networks_in_use():
    assert hub_with(FakeApi()).used_lan_networks() == ["192.168.3.0/24"]


def test_hub_add_peer_builds_allowed_address():
    api = FakeApi()
    assert hub_with(api).add_peer("PUB", "10.254.0.7", "192.168.10.0/24", "c") == "*99"
    assert api.added[0]["allowed-address"] == "10.254.0.7/32,192.168.10.0/24"
    assert api.added[0]["interface"] == "wireguard1"


def test_hub_error_is_wrapped():
    with pytest.raises(HubError):
        hub_with(FakeApi(fail_add=True)).add_peer("PUB", "10.254.0.7", "192.168.10.0/24", "c")


def test_hub_incomplete_config_fails_clearly(monkeypatch):
    s = settings()
    bad = HubClient(type(s)(**{**s.__dict__, "hub_host": ""}), connect=lambda **kw: FakeApi())
    with pytest.raises(HubError):
        bad.public_key()


# ---------- endpoint con BD simulada ----------
class FakeDb:
    def __init__(self, fail_insert=False):
        self.fail_insert, self.inserted = fail_insert, []

    def fetch_all(self, sql, params=()):
        if "lan_cidr" in sql:
            return [{"lan_cidr": "192.168.20.0/24"}]
        if "tunnel_ip" in sql:
            return [{"tunnel_ip": "10.254.0.2"}]
        return []

    def fetch_one(self, sql, params=()):
        return {"id": 1, "nombre": "Sede Demo"}

    def execute(self, *a, **k):
        pass

    def cursor(self):
        outer = self

        class Ctx:
            def __enter__(self_inner):
                if outer.fail_insert:
                    raise RuntimeError("fallo de BD simulado")
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def execute(self_inner, sql, params=()):
                outer.inserted.append(sql)

            def fetchone(self_inner):
                return {"id": 42}
        return Ctx()


BODY = {"sede_id": 1, "nombre": "MK Oficina", "wan": {"interface": "ether1", "mode": "dhcp"},
        "lan": {"interface": "bridge-lan", "cidr": "192.168.10.0/24"}}


@pytest.fixture
def client(monkeypatch):
    api = FakeApi()
    hub = hub_with(api)
    fake_db = FakeDb()
    monkeypatch.setattr(prov, "db", fake_db)
    monkeypatch.setattr(prov.audit, "log", lambda *a, **k: None)
    app.dependency_overrides[current_user] = lambda: {"id": 1, "email": "a@x", "role": "superadmin", "partner_id": None}
    app.dependency_overrides[prov.get_hub] = lambda: hub
    yield TestClient(app, raise_server_exceptions=False), api, fake_db
    app.dependency_overrides.clear()


def test_preview_has_no_side_effects_and_uses_real_key(client):
    c, api, _ = client
    r = c.post("/api/v1/provision/preview", json=BODY)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["tunnel_ip"] == "10.254.0.3"           # .2 en BD, .10 y .50 en el hub
    assert HUB_KEY in j["script"] and "<CLAVE_PRIVADA" in j["script"]
    assert api.added == []


def test_create_registers_peer_and_returns_script(client):
    c, api, fake_db = client
    r = c.post("/api/v1/provision", json=BODY)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["device"]["tunnel_ip"] == "10.254.0.3" and j["device"]["id"] == 42
    assert api.added[0]["allowed-address"] == "10.254.0.3/32,192.168.10.0/24"
    assert "private-key=" in j["script"] and "<CLAVE" not in j["script"]
    assert len(fake_db.inserted) == 2


def test_fixed_tunnel_ip_is_honored_and_conflicts_rejected(client):
    c, *_ = client
    assert c.post("/api/v1/provision/preview", json={**BODY, "tunnel_ip": "10.254.0.77"}).json()["tunnel_ip"] == "10.254.0.77"
    assert c.post("/api/v1/provision/preview", json={**BODY, "tunnel_ip": "10.254.0.50"}).status_code == 422


def test_overlapping_lan_rejected(client):
    c, *_ = client
    for cidr in ("192.168.20.0/24", "192.168.3.0/24", "10.254.0.0/25"):
        body = {**BODY, "lan": {"interface": "bridge-lan", "cidr": cidr}}
        assert c.post("/api/v1/provision", json=body).status_code == 422, cidr


def test_db_failure_rolls_back_hub_peer(monkeypatch, client):
    c, api, _ = client
    monkeypatch.setattr(prov, "db", FakeDb(fail_insert=True))
    r = c.post("/api/v1/provision", json=BODY)
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
    assert api.removed == ["*99"]


def test_hub_failure_returns_502_without_db_writes(monkeypatch, client):
    c, _, fake_db = client
    bad = hub_with(FakeApi(fail_add=True))
    app.dependency_overrides[prov.get_hub] = lambda: bad
    r = c.post("/api/v1/provision", json=BODY)
    assert r.status_code == 502 and fake_db.inserted == []


def test_cliente_role_cannot_provision():
    app.dependency_overrides[current_user] = lambda: {"id": 5, "email": "c@x", "role": "cliente", "partner_id": None}
    try:
        r = TestClient(app).post("/api/v1/provision", json=BODY)
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()
