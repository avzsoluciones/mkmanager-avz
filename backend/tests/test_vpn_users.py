import io
import re

import pytest
from fastapi.testclient import TestClient

from app.deps import get_hub
from app.main import app
from app.routers import vpn as vpn_router
from app.security import current_user
from app.services import wgkeys
from app.services.hub import HubClient

from test_provision import HUB_KEY, FakeApi, hub_with


class VpnApi(FakeApi):
    """Hub simulado: los peers añadidos reciben .id y quedan visibles para list_peers."""
    def __init__(self, peers=None, fail_add=False):
        super().__init__(peers=peers if peers is not None else [], fail_add=fail_add)

    def path(self, *parts):
        api = self
        base = super().path(*parts)

        class P:
            def __iter__(s):
                return iter(api.data.get(parts, []))

            def add(s, **kw):
                r = base.add(**kw)
                if parts == ("interface", "wireguard", "peers"):
                    api.data[parts].append({".id": "*99", "public-key": kw["public-key"], "allowed-address": kw["allowed-address"]})
                return r

            def remove(s, _id):
                base.remove(_id)
                api.data[parts] = [p for p in api.data[parts] if p.get(".id") != _id]
        return P()


class VpnDb:
    """BD simulada en memoria con las consultas que usa el router de VPN (con alcance por partner_id)."""
    def __init__(self, fail_insert=False, active_ips=(), active_sede=1):
        self.sedes = {1: {"id": 1, "nombre": "Sede A", "partner_id": 10},
                      2: {"id": 2, "nombre": "Sede B", "partner_id": 20}}
        self.devs = [{"sede_id": 1, "lan_cidr": "192.168.10.0/24", "ip_local": "192.168.10.1", "tunnel_ip": "10.254.0.2"},
                     {"sede_id": 2, "lan_cidr": "192.168.20.0/24", "ip_local": "192.168.20.1", "tunnel_ip": "10.254.0.3"}]
        self.peers, self.fail_insert, self.executed, self.next = [], fail_insert, [], 1
        for i, ip in enumerate(active_ips):
            self.peers.append(self._row(100 + i, f"p{i}", ip, active_sede))

    def _row(self, pid, nombre, ip, sede, **kw):
        base = {"id": pid, "nombre": nombre, "tunnel_ip": ip, "public_key": f"PK{pid}", "private_key_enc": wgkeys.encrypt_secret("PRIV" + str(pid)),
                "preshared_key_enc": None, "client_allowed": "10.254.0.1/32,192.168.10.0/24", "dns": "192.168.10.1",
                "hub_peer_id": "*99", "sede_id": sede, "tipo": "usuario", "revocado_en": None, "creado_en": "2026-10-01T00:00:00",
                "tunnel_ip_historica": None}
        base.update(kw)
        return base

    @staticmethod
    def _allowed(sede, sql, params):
        if "s.partner_id = %s" in sql:
            return sede["partner_id"] == params[-1]
        return True

    def _scope_param(self, sql, params):
        return params

    def fetch_one(self, sql, params=()):
        if "FROM sedes s WHERE s.id" in sql:
            s = self.sedes.get(params[0])
            return dict(s) if s and self._allowed(s, sql, params) else None
        if "COUNT(*)" in sql:
            return {"n": len([p for p in self.peers if p["sede_id"] == params[0] and not p["revocado_en"]])}
        if "FROM wg_peers p JOIN sedes" in sql:
            for p in self.peers:
                s = self.sedes[p["sede_id"]]
                if p["id"] == params[0] and not p["revocado_en"] and self._allowed(s, sql, params):
                    return dict(p)
            return None
        raise AssertionError(sql)

    def fetch_all(self, sql, params=()):
        if "FROM sedes s WHERE" in sql:
            return [dict(s) for s in self.sedes.values() if self._allowed(s, sql, params)][:2]
        if "FROM dispositivos WHERE sede_id" in sql:
            return [d for d in self.devs if d["sede_id"] == params[0]]
        if "SELECT tunnel_ip FROM wg_peers" in sql:
            return [{"tunnel_ip": p["tunnel_ip"]} for p in self.peers if not p["revocado_en"]]
        if "SELECT tunnel_ip FROM dispositivos" in sql:
            return [{"tunnel_ip": d["tunnel_ip"]} for d in self.devs]
        if "FROM wg_peers p JOIN sedes" in sql:
            return [{"id": p["id"], "nombre": p["nombre"], "tunnel_ip": p["tunnel_ip_historica"] or p["tunnel_ip"],
                     "creado_en": p["creado_en"], "revocado": bool(p["revocado_en"]), "sede_id": p["sede_id"]}
                    for p in self.peers if self._allowed(self.sedes[p["sede_id"]], sql, params)]
        raise AssertionError(sql)

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        if sql.lstrip().startswith("UPDATE wg_peers SET revocado_en"):
            for p in self.peers:
                if p["id"] == params[0]:
                    p["revocado_en"], p["tunnel_ip_historica"], p["tunnel_ip"] = "now", p["tunnel_ip"], f"rev-{p['id']}"

    def cursor(self):
        outer = self

        class Ctx:
            def __enter__(s):
                if outer.fail_insert:
                    raise RuntimeError("fallo de BD simulado")
                return s

            def __exit__(s, *a):
                return False

            def execute(s, sql, params=()):
                (nombre, ip, pub, priv_enc, psk_enc, peer, allowed, dns, sede, tenant, creado_por) = params
                s.row = outer._row(outer.next + 0, nombre, ip, sede, public_key=pub, private_key_enc=priv_enc,
                                   preshared_key_enc=psk_enc, hub_peer_id=peer, client_allowed=allowed, dns=dns)
                s.row["_tenant"] = tenant
                outer.peers.append(s.row)
                outer.next += 1

            def fetchone(s):
                return {"id": s.row["id"]}
        return Ctx()


class Env:
    pass


@pytest.fixture
def env(monkeypatch):
    e = Env()
    e.api, e.db, e.audit = VpnApi(), VpnDb(), []
    e.hub = hub_with(e.api)                       # fija HUB_*/VAULT_KEY en el entorno
    import os
    os.environ["HUB_PUBLIC_PORT"] = "13231"
    from app import config
    config._settings = None
    e.hub = HubClient(config.get_settings(), connect=lambda **kw: e.api)
    monkeypatch.setattr(vpn_router, "db", e.db)
    monkeypatch.setattr(vpn_router.audit, "log", lambda *a, **k: e.audit.append(a))
    vpn_router._CREATES.clear()
    app.dependency_overrides[get_hub] = lambda: e.hub
    yield e
    app.dependency_overrides.clear()


def as_user(role, uid=10, tenant=10):
    app.dependency_overrides[current_user] = lambda: {
        "id": uid, "email": f"{role}@x", "role": role, "partner_id": None, "tenant_id": tenant}


def post(body):
    return TestClient(app).post("/api/v1/vpn/users", json=body)


def test_create_ok_ip_in_range_and_conf(env):
    as_user("partner")
    r = post({"nombre": "Carlos Pérez (técnico)"})              # la UI no envía sede_id: se infiere (1 sola sede)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["tunnel_ip"] == "10.254.0.200" and set(j) == {"id", "nombre", "tunnel_ip", "conf_text"}
    conf = j["conf_text"]
    assert f"PublicKey = {HUB_KEY}" in conf and "AllowedIPs = 10.254.0.1/32, 192.168.10.0/24" in conf
    assert "0.0.0.0/0" not in conf and "MTU = 1420" in conf and "PersistentKeepalive = 25" in conf
    assert "Endpoint = vpn.example.com:13231" in conf and "Address = 10.254.0.200/32" in conf
    assert "DNS = 192.168.10.1" in conf and "PresharedKey = " in conf
    peer = env.api.added[0]
    assert peer["allowed-address"] == "10.254.0.200/32" and peer["comment"] == "AVZ VPN Carlos Pérez (técnico)"
    assert peer["preshared-key"] in conf
    assert r.headers["cache-control"].startswith("no-store")
    # la clave privada del .conf corresponde a la pública registrada en el hub
    priv = re.search(r"PrivateKey = (\S+)", conf).group(1)
    assert wgkeys.public_from_private_bytes(__import__("base64").b64decode(priv)) == __import__("base64").b64decode(peer["public-key"])
    # en BD la privada va cifrada
    assert env.db.peers[-1]["private_key_enc"] != priv and env.db.peers[-1]["_tenant"] == 10


def test_ip_skips_db_and_hub_used(env):
    env.db = VpnDb(active_ips=["10.254.0.200"])
    vpn_router.db = env.db
    env.api.data[("interface", "wireguard", "peers")].append({".id": "*5", "public-key": "Z", "allowed-address": "10.254.0.201/32"})
    as_user("partner")
    assert post({"nombre": "Laura"}).json()["tunnel_ip"] == "10.254.0.202"


def test_range_exhausted(env):
    env.db = VpnDb(active_ips=[f"10.254.0.{i}" for i in range(200, 251)], active_sede=2)
    vpn_router.db = env.db
    as_user("superadmin", tenant=None)
    r = post({"nombre": "Laura", "sede_id": 1})
    assert r.status_code == 409 and "libres" in r.json()["detail"] and not env.api.added


def test_routes_subset_ok_and_foreign_rejected(env):
    env.db.devs.append({"sede_id": 1, "lan_cidr": "192.168.11.0/24", "ip_local": "192.168.11.1", "tunnel_ip": "10.254.0.9"})
    as_user("partner")
    r = post({"nombre": "Laura", "rutas": ["192.168.11.0/24"]})
    assert r.status_code == 201 and "AllowedIPs = 10.254.0.1/32, 192.168.11.0/24\n" in r.json()["conf_text"]
    for bad in (["192.168.20.0/24"], ["0.0.0.0/0"], ["10.10.10.0/24"], ["192.168.10.0/16"], ["no-es-red"]):
        r = post({"nombre": "Otra", "rutas": bad})
        assert r.status_code == 422, bad
    assert len(env.api.added) == 1


def test_db_failure_compensates_peer(env):
    env.db.fail_insert = True
    as_user("partner")
    with pytest.raises(RuntimeError):
        TestClient(app, raise_server_exceptions=True).post("/api/v1/vpn/users", json={"nombre": "Laura"})
    assert env.api.removed == ["*99"] and not env.audit


def test_db_failure_and_hub_failure_logs_orphan(env, monkeypatch):
    env.db.fail_insert = True
    monkeypatch.setattr(HubClient, "remove_peer", lambda self, i: (_ for _ in ()).throw(__import__("app.services.hub", fromlist=["HubError"]).HubError("x")))
    as_user("partner")
    with pytest.raises(RuntimeError):
        TestClient(app).post("/api/v1/vpn/users", json={"nombre": "Laura"})
    assert env.audit[-1][1] == "peer_huerfano"


def test_hub_rejects_peer_is_502(env):
    env.api.fail_add = True
    as_user("partner")
    r = post({"nombre": "Laura"})
    assert r.status_code == 502 and not env.db.peers


def test_revoke_removes_peer_and_frees_ip(env):
    as_user("partner")
    uid = post({"nombre": "Laura"}).json()["id"]
    assert env.api.data[("interface", "wireguard", "peers")]
    r = TestClient(app).delete(f"/api/v1/vpn/users/{uid}")
    assert r.status_code == 200 and env.api.removed == ["*99"] and not env.api.data[("interface", "wireguard", "peers")]
    lst = TestClient(app).get("/api/v1/vpn/users").json()
    assert lst[0]["revocado"] is True and lst[0]["tunnel_ip"] == "10.254.0.200"
    assert TestClient(app).get(f"/api/v1/vpn/users/{uid}/conf").status_code == 404      # revocado: ya no se entrega
    assert TestClient(app).delete(f"/api/v1/vpn/users/{uid}").status_code == 404
    assert post({"nombre": "Nueva"}).json()["tunnel_ip"] == "10.254.0.200"             # la IP se reutiliza
    assert any(a[1] == "vpn_revocada" for a in env.audit)


def test_revoke_hub_down_keeps_active(env, monkeypatch):
    as_user("partner")
    uid = post({"nombre": "Laura"}).json()["id"]
    env.api.fail_remove = True
    monkeypatch.setattr(HubClient, "remove_peer", lambda self, i: (_ for _ in ()).throw(__import__("app.services.hub", fromlist=["HubError"]).HubError("hub caído")))
    assert TestClient(app).delete(f"/api/v1/vpn/users/{uid}").status_code == 502
    assert not env.db.peers[0]["revocado_en"]


def test_revoke_when_peer_already_gone_still_revokes(env):
    as_user("partner")
    uid = post({"nombre": "Laura"}).json()["id"]
    env.api.data[("interface", "wireguard", "peers")].clear()
    assert TestClient(app).delete(f"/api/v1/vpn/users/{uid}").status_code == 200
    assert env.db.peers[0]["revocado_en"]


@pytest.mark.parametrize("role", ["cliente"])
def test_cliente_forbidden_everywhere(env, role):
    as_user(role, uid=5, tenant=None)
    c = TestClient(app)
    assert c.get("/api/v1/vpn/users").status_code == 403
    assert post({"nombre": "Laura"}).status_code == 403
    for path in ("qr.png", "conf"):
        assert c.get(f"/api/v1/vpn/users/1/{path}").status_code == 403
    assert c.delete("/api/v1/vpn/users/1").status_code == 403
    assert not env.api.added


@pytest.mark.parametrize("role", ["superadmin", "soporte", "partner", "tecnico"])
def test_allowed_roles_can_list(env, role):
    as_user(role)
    assert TestClient(app).get("/api/v1/vpn/users").status_code == 200


def test_partner_isolation_404(env):
    as_user("partner", uid=10, tenant=10)
    uid = post({"nombre": "Laura"}).json()["id"]
    as_user("partner", uid=20, tenant=20)
    c = TestClient(app)
    assert c.get("/api/v1/vpn/users").json() == []
    for path in ("qr.png", "conf"):
        assert c.get(f"/api/v1/vpn/users/{uid}/{path}").status_code == 404
    assert c.delete(f"/api/v1/vpn/users/{uid}").status_code == 404
    assert post({"nombre": "Intruso", "sede_id": 1}).status_code == 404            # sede ajena
    assert not env.db.peers[0]["revocado_en"] and len(env.api.added) == 1


def test_tecnico_same_tenant_sees_partner_profiles(env):
    as_user("partner", uid=10, tenant=10)
    post({"nombre": "Laura"})
    as_user("tecnico", uid=11, tenant=10)
    assert len(TestClient(app).get("/api/v1/vpn/users").json()) == 1


@pytest.mark.parametrize("name", ["Juan\nPrivateKey = x", "a", "x" * 61, "Ana\x00", "Ana\x1b[0m", "Ana;rm", "Ana\nB", "<script>", "  ", "Ana,Bob"])
def test_hostile_names_rejected(env, name):
    as_user("partner")
    assert post({"nombre": name}).status_code == 422
    assert not env.api.added


def test_unknown_fields_and_bad_dns_rejected(env):
    as_user("partner")
    assert post({"nombre": "Laura", "tipo": "router"}).status_code == 422
    assert post({"nombre": "Laura", "dns": "1.1.1.1\nX"}).status_code == 422
    assert post({"nombre": "Laura", "dns": "8.8.8.8"}).json()["conf_text"].count("DNS = 8.8.8.8") == 1


def test_sede_needed_when_several_and_no_lan(env):
    as_user("superadmin", tenant=None)
    assert post({"nombre": "Laura"}).status_code == 422                    # 2 sedes: hay que indicar sede_id
    env.db.devs = []
    assert post({"nombre": "Laura", "sede_id": 1}).status_code == 422      # sede sin LAN


def test_per_sede_limit(env):
    env.db = VpnDb(active_ips=[f"10.254.0.{i}" for i in range(200, 220)])
    vpn_router.db = env.db
    as_user("partner")
    r = post({"nombre": "Laura"})
    assert r.status_code == 409 and "máximo" in r.json()["detail"]


def test_rate_limit(env):
    as_user("superadmin", tenant=None)
    codes = [post({"nombre": f"P{i}x", "sede_id": 2}).status_code for i in range(12)]
    assert codes[:10] == [201] * 10 and codes[10] == 429


def test_qr_is_valid_png_no_store_and_audited(env):
    as_user("partner")
    uid = post({"nombre": "Laura"}).json()["id"]
    r = TestClient(app).get(f"/api/v1/vpn/users/{uid}/qr.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n" and r.headers["cache-control"].startswith("no-store")
    from PIL import Image
    assert Image.open(io.BytesIO(r.content)).size[0] > 100
    assert env.audit[-1][1] == "vpn_config_consultada"


def test_conf_download_headers_and_same_content(env):
    as_user("partner")
    j = post({"nombre": "Carlos Pérez (técnico)"}).json()
    r = TestClient(app).get(f"/api/v1/vpn/users/{j['id']}/conf")
    assert r.status_code == 200 and r.text == j["conf_text"]
    assert r.headers["cache-control"].startswith("no-store")
    assert r.headers["content-disposition"] == 'attachment; filename="Carlos-P-rez-t-cnico.conf"'
    assert [a for a in env.audit if a[1] == "vpn_config_consultada"]


def test_audit_has_no_secrets(env):
    as_user("partner")
    j = post({"nombre": "Laura"}).json()
    c = TestClient(app)
    c.get(f"/api/v1/vpn/users/{j['id']}/conf")
    c.get(f"/api/v1/vpn/users/{j['id']}/qr.png")
    c.delete(f"/api/v1/vpn/users/{j['id']}")
    secrets_ = [re.search(r"PrivateKey = (\S+)", j["conf_text"]).group(1), re.search(r"PresharedKey = (\S+)", j["conf_text"]).group(1)]
    blob = " ".join(str(a) for a in env.audit)
    assert env.audit and all(s not in blob for s in secrets_) and "PrivateKey" not in blob


def test_list_contract(env):
    as_user("partner")
    post({"nombre": "Laura"})
    row = TestClient(app).get("/api/v1/vpn/users").json()[0]
    assert {"id", "nombre", "tunnel_ip", "creado_en", "revocado"} <= set(row)
    assert "private_key_enc" not in str(row) and "public_key" not in row


def test_errors_are_json(env):
    as_user("partner")
    r = TestClient(app).get("/api/v1/vpn/users/999/conf")
    assert r.status_code == 404 and "detail" in r.json()


def test_surrounding_whitespace_is_stripped(env):
    as_user("partner")
    j = post({"nombre": "  Ana\n"}).json()
    assert j["nombre"] == "Ana" and env.api.added[0]["comment"] == "AVZ VPN Ana"
