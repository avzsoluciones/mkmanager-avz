"""Endpoints de panel, estado, historial y auditoría: permisos por rol, aislamiento entre aliados, forma de las respuestas
que espera la interfaz (frontend-new/js/mock.js), límites y ausencia de datos sensibles. BD y hub simulados."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.deps import get_hub
from app.main import app
from app.routers import devices as devices_router
from app.routers import monitor as monitor_router
from app.security import current_user
from app.services import monitoring as mon

NOW = datetime.now(timezone.utc)

# sedes: 1,2 del aliado 10 (cliente 50 en la 1) · 3 del aliado 20
SEDES = {1: {"partner_id": 10, "cliente_id": 50}, 2: {"partner_id": 10, "cliente_id": None},
         3: {"partner_id": 20, "cliente_id": 60}}
USERS = [  # id, email, tenant_id, partner_id
    (1, "admin@avz.co", None, None), (10, "aliado10@x.co", 10, None), (11, "tec10@x.co", 10, None),
    (50, "cliente50@x.co", None, 10), (20, "aliado20@x.co", 20, None), (60, "cliente60@x.co", None, 20)]
KEY = {1: "K1", 2: "K2", 3: "K3", 4: "K4"}


def device(did, sede, estado="online", **kw):
    return {"id": did, "nombre": f"Router {did}", "tipo": "router", "marca": "MikroTik", "modelo": "RouterOS v7",
            "sede_id": sede, "sede": f"Sede {sede}", "ip_local": "192.168.10.1", "tunnel_ip": f"10.254.0.{did + 1}",
            "lan_cidr": f"192.168.{did}0.0/24", "estado": estado, "ultimo_heartbeat": NOW,
            "wan_config": {"interface": "ether1", "mode": "static", "address": "200.1.1.2/30", "gateway": "200.1.1.1",
                           "password": "NO-DEBE-SALIR", "api_password_enc": "gAAAA-secreto"},
            "lan_config": {"interface": "bridge", "cidr": f"192.168.{did}0.0/24", "gateway": "192.168.10.1",
                           "dhcp_start": "192.168.10.100", "dhcp_end": "192.168.10.199", "dns": ["1.1.1.1"],
                           "clave": "NO-DEBE-SALIR"},
            "rx_activo_en": None, "public_key": KEY[did], "rx_bytes": 111, "tx_bytes": 222, **kw}


DEVICES = [device(1, 1), device(2, 2, "offline"), device(3, 3), device(4, 1, "pendiente")]


def in_scope(sql, params, sede_id):
    """Interpreta el fragmento de alcance de sede_scope sobre la sede dada."""
    s = SEDES[sede_id]
    if "s.partner_id = %s" in sql:
        return s["partner_id"] == params[-1]
    if "s.cliente_id = %s" in sql:
        return s["cliente_id"] == params[-1]
    if "FALSE" in sql:
        return False
    return True


class FakeDb:
    def __init__(self, audit=(), metrics=(), devices=None):
        self.audit, self.metrics = list(audit), list(metrics)
        self.devices = devices if devices is not None else DEVICES
        self.calls = []

    def _devs(self, sql, params):
        return [d for d in self.devices if in_scope(sql, params, d["sede_id"])]

    def fetch_one(self, sql, params=()):
        self.calls.append((sql, params))
        if "count(*) AS n FROM sedes" in sql:
            return {"n": sum(1 for i in SEDES if in_scope(sql, params, i))}
        if "count(*) AS equipos" in sql:
            ds = self._devs(sql, params)
            return {"equipos": len(ds), **{k: sum(1 for d in ds if d["estado"] == k) for k in ("online", "offline")},
                    "pendientes": sum(1 for d in ds if d["estado"] == "pendiente")}
        if "WHERE d.id = %s" in sql:
            for d in self.devices:
                if d["id"] == params[0] and in_scope(sql, params, d["sede_id"]):
                    return d
            return None
        return None

    def fetch_all(self, sql, params=()):
        self.calls.append((sql, params))
        if "UNION SELECT tunnel_ip" in sql:
            return [{"tunnel_ip": d["tunnel_ip"]} for d in self.devices]
        if "FROM metricas_equipo" in sql:
            return [dict(r) for r in sorted(self.metrics, key=lambda r: r["instante"], reverse=True)][: params[-1]]
        if "FROM audit_logs a" in sql:
            return self._audit(sql, params)
        if "FROM dispositivos d JOIN sedes s" in sql:
            return self._devs(sql, params)
        return []

    def _audit(self, sql, params):
        rows = list(self.audit)
        if "FROM usuarios u" in sql:
            t = params[0]
            mails = {e.lower() for (i, e, ten, par) in USERS if i == t or ten == t or par == t}
            rows = [r for r in rows if (r["usuario_email"] or "").lower() in mails]
        elif "FALSE" in sql:
            rows = []
        if "a.id < %s" in sql:
            rows = [r for r in rows if r["id"] < params[[i for i, p in enumerate(params) if isinstance(p, int)][-2]]] \
                if False else [r for r in rows if r["id"] < _antes(sql, params)]
        if "a.accion = %s" in sql:
            rows = [r for r in rows if r["accion"] == _after(sql, params, "a.accion = %s")]
        rows.sort(key=lambda r: r["id"], reverse=True)
        return rows[: params[-1]]


def _placeholders(sql):
    """Orden de los fragmentos con parámetro en la cláusula WHERE de la auditoría."""
    order = []
    for frag in ("a.id < %s", "a.accion = %s", "lower(a.usuario_email) = lower(%s)", "a.fecha >= %s", "a.fecha <= %s"):
        if frag in sql:
            order.append((sql.index(frag), frag))
    return [f for _, f in sorted(order)]


def _after(sql, params, frag):
    scope = 3 if "FROM usuarios u" in sql else 0
    return params[scope + _placeholders(sql).index(frag)]


def _antes(sql, params):
    return _after(sql, params, "a.id < %s")


class FakeHub:
    def __init__(self, peers=None, fail=False):
        self.peers, self.fail, self.calls = peers or [], fail, 0

    def list_peers(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("hub caído")
        return self.peers


def peers_for(*specs):
    return [{"public-key": k, "last-handshake": hs, "rx": str(rx), "tx": str(tx),
             "allowed-address": f"10.254.0.{n}/32"} for (k, hs, rx, tx, n) in specs]


def as_user(role, uid=1, tenant=None):
    app.dependency_overrides[current_user] = lambda: {
        "id": uid, "email": f"{role}@x", "role": role, "partner_id": None, "tenant_id": tenant}


@pytest.fixture(autouse=True)
def _clean():
    mon.peer_cache.clear()
    yield
    app.dependency_overrides.clear()
    mon.peer_cache.clear()


@pytest.fixture
def client():
    return TestClient(app)


def wire(monkeypatch, db=None, hub=None):
    db = db or FakeDb()
    monkeypatch.setattr(monitor_router, "db", db)
    monkeypatch.setattr(devices_router, "db", db)
    hub = hub or FakeHub()
    app.dependency_overrides[get_hub] = lambda: hub
    return db, hub


def audit_rows():
    mk = lambda i, mail, acc, det="": {"id": i, "fecha": NOW - timedelta(minutes=i), "usuario_email": mail,
                                       "accion": acc, "detalles": det}
    return [mk(1, "admin@avz.co", "login"), mk(2, "aliado10@x.co", "sede_creada", "id=1 nombre=Centro"),
            mk(3, "tec10@x.co", "script_consultado", "device=1"), mk(4, "aliado20@x.co", "sede_creada", "id=3"),
            mk(5, "cliente50@x.co", "login"), mk(6, "intruso@evil.co", "login_fallido"),
            mk(7, "aliado10@x.co", "router_aprovisionado",
               "id=4 password=SuperClave123 clave: otraClave token=abc123 pk=" + "A" * 44),
            mk(8, "aliado20@x.co", "login")]


# ---------------- dashboard ----------------
UI_KEYS = {"sedes", "equipos", "online", "offline", "pendientes", "ips_libres", "actividad"}


def test_dashboard_shape_matches_ui_contract(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()), FakeHub(peers_for(("K1", "1m", 1, 1, 2))))
    as_user("superadmin")
    r = client.get("/api/v1/dashboard")
    assert r.status_code == 200
    d = r.json()
    assert UI_KEYS <= set(d)
    assert (d["sedes"], d["equipos"], d["online"], d["offline"], d["pendientes"]) == (3, 4, 2, 1, 1)
    assert d["ips_libres"] == 198 - 4 and d["ips_total"] == 198 and d["ips_fuente"] == "bd+hub"
    assert len(d["actividad"]) == 8
    assert set(d["actividad"][0]) == {"fecha", "usuario", "accion", "detalle"}
    assert d["actividad"][0]["usuario"] == "aliado20"          # parte local del correo, el más reciente primero


def test_dashboard_scoped_by_partner_and_isolated(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()))
    as_user("partner", uid=10, tenant=10)
    d = client.get("/api/v1/dashboard").json()
    assert (d["sedes"], d["equipos"], d["online"], d["offline"], d["pendientes"]) == (2, 3, 1, 1, 1)
    assert {a["usuario"] for a in d["actividad"]} <= {"aliado10", "tec10", "cliente50"}
    as_user("partner", uid=20, tenant=20)
    d = client.get("/api/v1/dashboard").json()
    assert (d["sedes"], d["equipos"]) == (1, 1)
    assert {a["usuario"] for a in d["actividad"]} == {"aliado20"}


def test_tecnico_shares_tenant_scope(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()))
    as_user("tecnico", uid=11, tenant=10)
    d = client.get("/api/v1/dashboard").json()
    assert (d["sedes"], d["equipos"]) == (2, 3)


def test_cliente_sees_only_own_site_and_no_activity_nor_pool(client, monkeypatch):
    _, hub = wire(monkeypatch, FakeDb(audit=audit_rows()))
    as_user("cliente", uid=50)
    d = client.get("/api/v1/dashboard").json()
    assert (d["sedes"], d["equipos"]) == (1, 2)
    assert d["actividad"] == [] and d["ips_libres"] is None
    assert hub.calls == 0


def test_dashboard_free_ips_union_of_db_and_hub(client, monkeypatch):
    # BD: 10.254.0.2-5 · hub: 5 (repetida), 9, 150, y 210 (fuera del rango de routers)
    hub = FakeHub(peers_for(("X", "1m", 0, 0, 5), ("Y", "1m", 0, 0, 9), ("Z", "1m", 0, 0, 150), ("W", "1m", 0, 0, 210)))
    wire(monkeypatch, FakeDb(), hub)
    as_user("soporte")
    d = client.get("/api/v1/dashboard").json()
    assert d["ips_libres"] == 198 - 4 - 2 and d["ips_fuente"] == "bd+hub"


def test_dashboard_with_hub_down_uses_db_only_and_says_so(client, monkeypatch):
    wire(monkeypatch, FakeDb(), FakeHub(fail=True))
    as_user("superadmin")
    r = client.get("/api/v1/dashboard")
    assert r.status_code == 200
    assert r.json()["ips_libres"] == 198 - 4 and r.json()["ips_fuente"] == "bd"


def test_dashboard_hub_failure_is_cached_not_retried_every_request(client, monkeypatch):
    _, hub = wire(monkeypatch, FakeDb(), FakeHub(fail=True))
    as_user("superadmin")
    client.get("/api/v1/dashboard")
    client.get("/api/v1/dashboard")
    assert hub.calls == 1


def test_dashboard_activity_is_sanitized(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()))
    as_user("superadmin")
    text = client.get("/api/v1/dashboard").text
    assert "SuperClave123" not in text and "otraClave" not in text and "abc123" not in text and "A" * 40 not in text


def test_dashboard_requires_authentication(client):
    assert client.get("/api/v1/dashboard").status_code == 401


# ---------------- estado ----------------
def test_status_live_shape(client, monkeypatch):
    wire(monkeypatch, hub=FakeHub(peers_for(("K1", "45s", 5000, 900, 2))))
    as_user("superadmin")
    r = client.get("/api/v1/devices/1/status")
    assert r.status_code == 200
    d = r.json()
    assert {"estado", "ultimo_contacto", "rx_bytes", "tx_bytes"} <= set(d)
    assert d["estado"] == "online" and d["rx_bytes"] == 5000 and d["tx_bytes"] == 900 and d["en_vivo"] is True
    got = datetime.fromisoformat(d["ultimo_contacto"].replace("Z", "+00:00"))
    assert abs((NOW - timedelta(seconds=45) - got).total_seconds()) < 30


@pytest.mark.parametrize("hs,expected", [("5m", "offline"), ("never", "pendiente")])
def test_status_classification_live(client, monkeypatch, hs, expected):
    devs = [device(1, 1, estado="pendiente" if hs == "never" else "online")]
    wire(monkeypatch, FakeDb(devices=devs), FakeHub(peers_for(("K1", hs, 0, 0, 2))))
    as_user("superadmin")
    d = client.get("/api/v1/devices/1/status").json()
    assert d["estado"] == expected
    if expected == "pendiente":
        assert d["ultimo_contacto"] is None


def test_status_uses_recent_rx_activity_signal(client, monkeypatch):
    devs = [device(1, 1, rx_activo_en=datetime.now(timezone.utc) - timedelta(seconds=30))]
    wire(monkeypatch, FakeDb(devices=devs), FakeHub(peers_for(("K1", "5m", 100, 1, 2))))
    as_user("superadmin")
    assert client.get("/api/v1/devices/1/status").json()["estado"] == "online"


def test_status_hub_down_returns_last_known_and_says_so(client, monkeypatch):
    wire(monkeypatch, hub=FakeHub(fail=True))
    as_user("superadmin")
    r = client.get("/api/v1/devices/2/status")
    assert r.status_code == 200
    d = r.json()
    assert d["estado"] == "offline" and d["en_vivo"] is False and d["fuente"] == "ultimo_conocido"
    assert d["rx_bytes"] == 111 and d["tx_bytes"] == 222 and "último estado conocido" in d["aviso"]
    assert "hub" not in d["aviso"].lower()


def test_status_peer_missing_falls_back_to_last_known(client, monkeypatch):
    wire(monkeypatch, hub=FakeHub(peers_for(("OTRO", "1s", 0, 0, 9))))
    as_user("superadmin")
    d = client.get("/api/v1/devices/1/status").json()
    assert d["en_vivo"] is False and d["estado"] == "online"


def test_status_pending_last_known_has_no_contact(client, monkeypatch):
    wire(monkeypatch, hub=FakeHub(fail=True))
    as_user("superadmin")
    assert client.get("/api/v1/devices/4/status").json()["ultimo_contacto"] is None


@pytest.mark.parametrize("role,uid,tenant,allowed,denied", [
    ("partner", 10, 10, 1, 3), ("tecnico", 11, 10, 2, 3), ("partner", 20, 20, 3, 1),
    ("cliente", 50, None, 1, 2), ("cliente", 60, None, 3, 1)])
def test_status_and_history_isolation(client, monkeypatch, role, uid, tenant, allowed, denied):
    wire(monkeypatch, hub=FakeHub(peers_for(("K1", "1s", 0, 0, 2), ("K2", "1s", 0, 0, 3), ("K3", "1s", 0, 0, 4))))
    as_user(role, uid=uid, tenant=tenant)
    for path in ("status", "history"):
        assert client.get(f"/api/v1/devices/{allowed}/{path}").status_code == 200
        r = client.get(f"/api/v1/devices/{denied}/{path}")
        assert r.status_code == 404 and r.json() == {"detail": "Dispositivo no encontrado."}


def test_status_unknown_device_is_404_json(client, monkeypatch):
    wire(monkeypatch)
    as_user("superadmin")
    r = client.get("/api/v1/devices/999/status")
    assert r.status_code == 404 and "detail" in r.json()


def test_status_requires_authentication(client):
    assert client.get("/api/v1/devices/1/status").status_code == 401
    assert client.get("/api/v1/devices/1/history").status_code == 401


# ---------------- historial ----------------
def metrics(n, step_min=5):
    return [{"instante": NOW - timedelta(minutes=step_min * i), "estado": "offline" if i % 10 == 0 else "online",
             "rx_bytes": 1000 - i, "tx_bytes": 500 - i} for i in range(n)]


def test_history_shape_and_order(client, monkeypatch):
    wire(monkeypatch, FakeDb(metrics=metrics(20)))
    as_user("superadmin")
    d = client.get("/api/v1/devices/1/history?horas=24").json()
    assert {"equipo_id", "horas", "puntos", "total_muestras", "online_pct", "reducido"} <= set(d)
    assert d["horas"] == 24 and d["total_muestras"] == 20 and d["reducido"] is False
    assert set(d["puntos"][0]) == {"t", "estado", "rx_bytes", "tx_bytes"}
    ts = [p["t"] for p in d["puntos"]]
    assert ts == sorted(ts)                                     # cronológico ascendente
    assert d["online_pct"] == 90.0


def test_history_point_limit(client, monkeypatch):
    wire(monkeypatch, FakeDb(metrics=metrics(3000, 1)))
    as_user("superadmin")
    d = client.get("/api/v1/devices/1/history?horas=48").json()
    assert d["total_muestras"] == 3000 and len(d["puntos"]) <= monitor_router.HISTORY_MAX_POINTS and d["reducido"]


def test_history_hours_are_clamped(client, monkeypatch):
    db = FakeDb()
    wire(monkeypatch, db)
    as_user("superadmin")
    assert client.get("/api/v1/devices/1/history?horas=999999").json()["horas"] == monitor_router.HISTORY_MAX_HOURS
    assert client.get("/api/v1/devices/1/history?horas=0").json()["horas"] == 1
    assert client.get("/api/v1/devices/1/history?horas=-5").json()["horas"] == 1
    sql, params = [c for c in db.calls if "FROM metricas_equipo" in c[0]][0]
    assert params[-1] == monitor_router.HISTORY_MAX_ROWS


def test_history_invalid_hours_is_json_422(client, monkeypatch):
    wire(monkeypatch)
    as_user("superadmin")
    r = client.get("/api/v1/devices/1/history?horas=abc")
    assert r.status_code == 422 and "detail" in r.json()


def test_history_empty(client, monkeypatch):
    wire(monkeypatch)
    as_user("superadmin")
    d = client.get("/api/v1/devices/1/history").json()
    assert d["puntos"] == [] and d["online_pct"] is None


# ---------------- auditoría ----------------
def get_audit(client, **q):
    return client.get("/api/v1/audit", params=q)


def test_audit_staff_sees_everything_with_ui_shape(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()))
    for role in ("superadmin", "soporte"):
        as_user(role)
        r = get_audit(client)
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 8
        assert set(rows[0]) == {"id", "fecha", "usuario_email", "accion", "detalles"}
        assert [x["id"] for x in rows] == sorted((x["id"] for x in rows), reverse=True)


def test_audit_partner_sees_only_own_portfolio(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()))
    as_user("partner", uid=10, tenant=10)
    emails = {x["usuario_email"] for x in get_audit(client).json()}
    assert emails == {"aliado10@x.co", "tec10@x.co", "cliente50@x.co"}
    as_user("partner", uid=20, tenant=20)
    emails = {x["usuario_email"] for x in get_audit(client).json()}
    assert emails == {"aliado20@x.co"}                       # nada de aliado10, staff ni anónimos


def test_audit_tecnico_gets_its_aliado_scope_not_more(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()))
    as_user("tecnico", uid=11, tenant=10)
    emails = {x["usuario_email"] for x in get_audit(client).json()}
    assert emails == {"aliado10@x.co", "tec10@x.co", "cliente50@x.co"}


def test_audit_never_shows_staff_or_unknown_senders_to_partner(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()))
    as_user("partner", uid=10, tenant=10)
    text = get_audit(client).text
    assert "admin@avz.co" not in text and "intruso@evil.co" not in text


def test_audit_cliente_is_forbidden(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()))
    as_user("cliente", uid=50)
    r = get_audit(client)
    assert r.status_code == 403 and r.json() == {"detail": "Acceso denegado."}


def test_audit_requires_authentication(client):
    assert get_audit(client).status_code == 401


def test_audit_limit_is_capped_and_floored(client, monkeypatch):
    db = FakeDb(audit=audit_rows())
    wire(monkeypatch, db)
    as_user("superadmin")
    get_audit(client, limit=100000)
    assert db.calls[-1][1][-1] == monitor_router.AUDIT_MAX_LIMIT == 200
    get_audit(client, limit=0)
    assert db.calls[-1][1][-1] == 1
    get_audit(client)
    assert db.calls[-1][1][-1] == 50
    assert len(get_audit(client, limit=3).json()) == 3


def test_audit_cursor_pagination(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()))
    as_user("superadmin")
    p1 = get_audit(client, limit=3).json()
    p2 = get_audit(client, limit=3, antes_id=p1[-1]["id"]).json()
    assert [x["id"] for x in p1] == [8, 7, 6] and [x["id"] for x in p2] == [5, 4, 3]


def test_audit_filter_by_action(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()))
    as_user("superadmin")
    assert {x["accion"] for x in get_audit(client, accion="sede_creada").json()} == {"sede_creada"}


def test_audit_filters_are_parameterized(client, monkeypatch):
    db = FakeDb(audit=audit_rows())
    wire(monkeypatch, db)
    as_user("superadmin")
    evil = "x'; DROP TABLE audit_logs;--"
    r = get_audit(client, accion=evil, usuario=evil)
    assert r.status_code == 200
    sql, params = db.calls[-1]
    assert "DROP" not in sql and evil[:100] in params


def test_audit_details_never_leak_secrets(client, monkeypatch):
    wire(monkeypatch, FakeDb(audit=audit_rows()))
    as_user("superadmin")
    text = get_audit(client).text
    for leak in ("SuperClave123", "otraClave", "abc123", "A" * 40):
        assert leak not in text
    assert "[oculto]" in text
    assert "id=1 nombre=Centro" in text                          # lo normal se conserva


def test_audit_details_are_truncated(client, monkeypatch):
    rows = [{"id": 1, "fecha": NOW, "usuario_email": "a@x", "accion": "x", "detalles": "ab " * 1000}]
    wire(monkeypatch, FakeDb(audit=rows))
    as_user("superadmin")
    assert len(get_audit(client).json()[0]["detalles"]) <= 300


@pytest.mark.parametrize("raw,bad", [
    ("password=Abc12345", "Abc12345"), ("clave: Zeta99", "Zeta99"), ("token=eyJhbGciOi", "eyJhbGciOi"),
    ("private_key=MFAKE", "MFAKE"), ("-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----", "MIIE")])
def test_sanitize_detail(raw, bad):
    assert bad not in monitor_router.sanitize_detail(f"id=3 {raw}")


# ---------------- GET /devices ampliado ----------------
def test_devices_list_adds_ui_fields_without_sensitive_data(client, monkeypatch):
    wire(monkeypatch)
    as_user("superadmin")
    r = client.get("/api/v1/devices")
    assert r.status_code == 200
    d = r.json()[0]
    assert {"id", "nombre", "tipo", "marca", "modelo", "sede_id", "sede", "ip_local", "tunnel_ip", "lan_cidr",
            "estado", "ultimo_heartbeat", "modo", "wan", "lan"} <= set(d)
    assert d["modo"] == "static" and d["ip_local"] == "192.168.10.1" and d["marca"] == "MikroTik"
    assert set(d["wan"]) == {"interface", "mode", "address", "gateway"}
    assert set(d["lan"]) == {"interface", "cidr", "gateway", "dhcp_start", "dhcp_end", "dns"}
    assert "wan_config" not in d and "lan_config" not in d
    text = r.text
    for leak in ("NO-DEBE-SALIR", "gAAAA", "api_password", "api_user", "password"):
        assert leak not in text


def test_device_detail_has_same_fields_and_scope(client, monkeypatch):
    wire(monkeypatch)
    as_user("partner", uid=10, tenant=10)
    d = client.get("/api/v1/devices/1").json()
    assert d["modo"] == "static" and d["lan"]["cidr"] == "192.168.10.0/24" and "NO-DEBE-SALIR" not in str(d)
    assert client.get("/api/v1/devices/3").status_code == 404
    assert [x["id"] for x in client.get("/api/v1/devices").json()] == [1, 2, 4]


def test_device_without_wan_config_still_works(client, monkeypatch):
    wire(monkeypatch, FakeDb(devices=[device(1, 1, wan_config=None, lan_config=None)]))
    as_user("superadmin")
    d = client.get("/api/v1/devices/1").json()
    assert d["modo"] is None and d["wan"] is None and d["lan"] is None


def test_devices_sql_never_selects_credential_columns():
    cols = devices_router._COLUMNS
    for forbidden in ("api_user", "api_password_enc", "detalles_config", "comando_adopcion"):
        assert forbidden not in cols


# ---------------- migración ----------------
def test_migration_008_defines_tables_and_indexes():
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations", "008_monitoreo.sql")
    sql = open(path, encoding="utf-8").read().lower()
    for frag in ("create table if not exists metricas_equipo", "create table if not exists alertas",
                 "equipo_id", "rx_bytes", "tx_bytes", "(equipo_id, instante", "ux_alertas_abierta",
                 "where estado = 'abierto'", "estado_desde", "rx_activo_en"):
        assert frag in sql
