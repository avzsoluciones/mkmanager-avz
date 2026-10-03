"""Endpoints de plantillas: permisos, alcance, reversión obligatoria, idempotencia y ciclo completo (sin red)."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import audit
from app.main import app
from app.security import current_user
from app.services import edge
from app.services.ops import FILTER
from app.tasks import reverter

from test_templates_fakes import DEV, T0, FakeRouter, MemStore

FW = {"ips_admin": ["203.0.113.0/24"]}
PF = {"protocolo": "tcp", "puerto_externo": 8080, "ip_destino": "192.168.10.20", "puerto_destino": 80}
DNS = {"dns": ["1.1.1.1", "8.8.8.8"], "ntp": "pool.ntp.org"}
APPLY = "/api/v1/devices/5/templates/{}/apply"
PREVIEW = "/api/v1/devices/5/templates/{}/preview"


def as_user(role, uid=1, tenant=None):
    app.dependency_overrides[current_user] = lambda: {
        "id": uid, "email": f"{role}{uid}@x", "role": role, "partner_id": None, "tenant_id": tenant}


@pytest.fixture
def env(monkeypatch):
    router, store, events = FakeRouter(), MemStore(), []
    monkeypatch.setattr(edge, "session_factory", router.factory)
    monkeypatch.setattr(edge, "store", store)
    monkeypatch.setattr(audit, "log", lambda u, a, d="", ip="": events.append((u, a, d)))
    as_user("superadmin")
    yield TestClient(app, raise_server_exceptions=False), router, store, events
    app.dependency_overrides.clear()


def apply(c, tid, params, **extra):
    return c.post(APPLY.format(tid), json={"params": params, **extra})


# ---------------------------------------------------------------- catálogo y permisos
def test_catalog_endpoint_matches_ui_contract(env):
    c, *_ = env
    r = c.get("/api/v1/templates")
    assert r.status_code == 200
    data = r.json()
    assert [t["id"] for t in data] == ["firewall-seguro", "port-forward", "vlan", "limite-ancho-banda", "dns-ntp"]
    fw = data[0]
    assert fw["riesgo"] == "alto" and fw["revierte"] is True and fw["campos"][0]["type"] == "list"
    assert "VAULT" not in r.text and "3HC" not in r.text


def test_cliente_has_no_access_to_any_template_endpoint(env):
    c, *_ = env
    as_user("cliente", uid=9)
    assert c.get("/api/v1/templates").status_code == 403
    assert c.post(PREVIEW.format("dns-ntp"), json={"params": DNS}).status_code == 403
    assert apply(c, "dns-ntp", DNS).status_code == 403
    assert c.post("/api/v1/applications/1/confirm").status_code == 403
    assert c.get("/api/v1/devices/5/applications").status_code == 403


def test_unauthenticated_is_401():
    app.dependency_overrides.clear()
    assert TestClient(app).get("/api/v1/templates").status_code == 401


def test_partner_isolation_between_allies(env):
    c, router, store, _ = env
    as_user("partner", uid=11, tenant=11)               # otro aliado: el equipo 5 es de la cartera 10
    assert c.post(PREVIEW.format("dns-ntp"), json={"params": DNS}).status_code == 404
    assert apply(c, "dns-ntp", DNS).status_code == 404
    assert c.get("/api/v1/devices/5/applications").status_code == 404
    assert store.apps == {} and router.mutating_calls() == []
    as_user("tecnico", uid=12, tenant=10)               # técnico de la cartera correcta
    assert c.post(PREVIEW.format("dns-ntp"), json={"params": DNS}).status_code == 200


def test_application_of_other_ally_is_404_for_confirm_and_revert(env):
    c, _, store, _ = env
    assert apply(c, "port-forward", PF, revertir_minutos=5).status_code == 200
    as_user("partner", uid=11, tenant=11)
    assert c.post("/api/v1/applications/1/confirm").status_code == 404
    assert c.post("/api/v1/applications/1/revert").status_code == 404
    assert store.apps[1]["estado"] == "aplicada"


def test_unknown_template_and_device_are_404(env):
    c, *_ = env
    assert apply(c, "no-existe", {}).status_code == 404
    assert c.post("/api/v1/devices/999/templates/dns-ntp/apply", json={"params": DNS}).status_code == 404


def test_device_without_full_management_is_rejected(env):
    c, _, store, _ = env
    store.devices[5]["marca"] = "Ubiquiti"
    assert apply(c, "dns-ntp", DNS).status_code == 409
    store.devices[5].update(marca="MikroTik", api_password_enc=None)
    assert c.post(PREVIEW.format("dns-ntp"), json={"params": DNS}).status_code == 409
    assert store.apps == {}


# ---------------------------------------------------------------- vista previa
def test_preview_returns_script_and_warnings_without_side_effects(env):
    c, router, store, events = env
    r = c.post(PREVIEW.format("port-forward"), json={"params": {**PF, "puerto_externo": 3389}})
    assert r.status_code == 200
    j = r.json()
    assert j["script"].startswith("# Servicio: Abrir un puerto") and "dst-port=3389" in j["script"]
    assert "AVZ-APP-<id>" in j["script"] and j["avisos"]
    assert router.calls == [] and router.connects == 0 and store.apps == {} and events == []


def test_preview_firewall_documents_placement_and_never_mentions_secrets(env):
    c, *_ = env
    j = c.post(PREVIEW.format("firewall-seguro"), json={"params": FW}).json()
    assert "AVZCloud mgmt" in j["script"] and "place-before=1" in j["script"]
    assert "password" not in j["script"].lower()


def test_validation_errors_are_json_spanish_and_per_field(env):
    c, *_ = env
    r = c.post(PREVIEW.format("port-forward"), json={"params": {**PF, "puerto_externo": 22, "ip_destino": "8.8.8.8"}})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/json")
    by_loc = {tuple(d["loc"]): d["msg"] for d in r.json()["detail"]}
    assert by_loc[("body", "params", "puerto_externo")].startswith("Ese puerto es de gestión")
    assert "red local" in by_loc[("body", "params", "ip_destino")]
    r = c.post(PREVIEW.format("port-forward"), json={"params": {}})
    assert {d["loc"][-1] for d in r.json()["detail"]} == {"protocolo", "puerto_externo", "ip_destino", "puerto_destino"}
    assert all(d["msg"] == "Este campo es obligatorio." for d in r.json()["detail"])
    r = c.post(PREVIEW.format("dns-ntp"), json={"params": {"dns": ["1.1.1.1"], "ntp": 'x"; /user add'}})
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "params", "ntp"]
    assert c.post(PREVIEW.format("dns-ntp"), json={"params": "x"}).status_code == 422


# ---------------------------------------------------------------- aplicar
def test_staff_applies_low_risk_template_end_to_end(env):
    c, router, store, events = env
    as_user("soporte", uid=2)
    r = apply(c, "dns-ntp", DNS)
    assert r.status_code == 200, r.text
    assert r.json() == {"aplicacion_id": 1, "estado": "aplicada", "revertir_en": None}
    assert router.tables[("ip", "dns")][0]["servers"] == "1.1.1.1,8.8.8.8"
    a = store.apps[1]
    assert a["estado"] == "aplicada" and a["parametros"]["dns"] == ["1.1.1.1", "8.8.8.8"] and a["ops"] and a["inversas"]
    assert events[-1][1] == "plantilla_aplicada" and "app=1" in events[-1][2] and "device=5" in events[-1][2]


@pytest.mark.parametrize("role,uid,tenant", [("partner", 10, 10), ("tecnico", 12, 10)])
def test_high_risk_requires_reversal_for_partner_and_tecnico(env, role, uid, tenant):
    c, router, store, _ = env
    as_user(role, uid, tenant)
    r = apply(c, "firewall-seguro", FW)
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == ["body", "revertir_minutos"] and "obligatoria" in r.json()["detail"][0]["msg"]
    for bad in (0, 1, 31):
        assert apply(c, "firewall-seguro", FW, revertir_minutos=bad).status_code == 422
    assert apply(c, "firewall-seguro", FW, revertir_minutos="5").status_code == 422        # tipo estricto
    assert store.apps == {} and router.mutating_calls() == []
    r = apply(c, "firewall-seguro", FW, revertir_minutos=5)
    assert r.status_code == 200, r.text
    assert r.json()["revertir_en"] == (T0 + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    assert store.apps[1]["revertir_en"] == T0 + timedelta(minutes=5)


@pytest.mark.parametrize("role", ["superadmin", "soporte"])
def test_staff_may_apply_high_risk_without_reversal(env, role):
    c, router, store, _ = env
    as_user(role, uid=3)
    r = apply(c, "firewall-seguro", FW)
    assert r.status_code == 200 and r.json()["revertir_en"] is None
    assert router.tables[FILTER][0]["comment"] == "AVZCloud mgmt"


def test_reversal_rejected_for_non_reversible_template(env):
    c, *_ = env
    r = apply(c, "dns-ntp", DNS, revertir_minutos=5)
    assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "revertir_minutos"]


def test_param_and_reversal_errors_are_reported_together(env):
    c, *_ = env
    as_user("partner", 10, 10)
    r = apply(c, "firewall-seguro", {"ips_admin": ["mal"]})
    assert {tuple(d["loc"][1:]) for d in r.json()["detail"]} == {("params", "ips_admin"), ("revertir_minutos",)}


def test_unknown_body_fields_are_rejected(env):
    c, *_ = env
    r = c.post(APPLY.format("dns-ntp"), json={"params": DNS, "script": "/system reset-configuration"})
    assert r.status_code == 422


def test_firewall_application_never_moves_the_management_rule(env):
    c, router, store, _ = env
    as_user("partner", 10, 10)
    assert apply(c, "firewall-seguro", FW, revertir_minutos=3).status_code == 200
    rules = router.tables[FILTER]
    assert rules[0]["comment"] == "AVZCloud mgmt" and rules[0]["disabled"] is False
    assert rules[1]["comment"] == "AVZ-APP-1" and rules[-1]["comment"] == "regla previa"


def test_duplicate_apply_is_409_and_does_not_touch_the_router_twice(env):
    c, router, store, _ = env
    assert apply(c, "port-forward", PF, revertir_minutos=5).status_code == 200
    calls = len(router.mutating_calls())
    r = apply(c, "port-forward", PF, revertir_minutos=10)
    assert r.status_code == 409 and "ya está en curso" in r.json()["detail"]
    assert len(router.mutating_calls()) == calls and len(store.apps) == 1
    assert apply(c, "port-forward", {**PF, "puerto_externo": 8081}, revertir_minutos=5).status_code == 200   # otros parámetros: sí
    assert store.confirm(1)
    assert apply(c, "port-forward", PF, revertir_minutos=5).status_code == 200                              # ya confirmada: se puede repetir


def test_concurrent_application_on_same_device_is_409(env):
    c, _, store, _ = env
    store.begin(5, "vlan", {}, "h", "x@x")              # otra aplicación sigue 'aplicando'
    r = apply(c, "dns-ntp", DNS)
    assert r.status_code == 409 and "otra aplicación" in r.json()["detail"]


def test_router_rejection_returns_409_marks_error_and_restores(env):
    c, router, store, events = env
    router.reject = lambda path, attrs: path == FILTER and attrs.get("chain") == "forward"
    as_user("soporte", 2)
    before = [dict(r) for r in router.tables[FILTER]]
    r = apply(c, "firewall-seguro", FW)
    assert r.status_code == 409 and "rechazó la operación" in r.json()["detail"] and "Se deshizo" in r.json()["detail"]
    assert router.tables[FILTER] == before
    assert store.apps[1]["estado"] == "error" and events[-1][1] == "plantilla_error"


def test_unreachable_device_is_502_and_error(env):
    c, router, store, _ = env
    router.fail_connect_from = 1
    r = apply(c, "dns-ntp", DNS)
    assert r.status_code == 502 and "No se pudo conectar" in r.json()["detail"]
    assert store.apps[1]["estado"] == "error" and router.mutating_calls() == []
    assert "librouteros" not in r.text and "10.254" not in r.text


def test_incomplete_rollback_hands_over_to_the_reverter(env):
    c, router, store, _ = env
    router.reject = lambda path, attrs: path == FILTER and attrs.get("chain") == "forward"
    router.fail_remove = True
    r = apply(c, "firewall-seguro", FW)
    assert r.status_code == 409 and "reintentará" in r.json()["detail"]
    a = store.apps[1]
    assert a["estado"] == "aplicada" and a["revertir_en"] == T0 and a["intentos_reversion"] == 1
    router.fail_remove = False
    store.now = T0 + timedelta(minutes=1)
    assert reverter.tick() == 1 and store.apps[1]["estado"] == "revertida"
    assert [r["comment"] for r in router.tables[FILTER]] == ["AVZCloud mgmt", "regla previa"]


def test_unexpected_server_error_is_json_and_marks_error(env, monkeypatch):
    c, _, store, _ = env
    monkeypatch.setattr(edge, "run_apply", lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")))
    r = apply(c, "dns-ntp", DNS)
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
    assert store.apps[1]["estado"] == "error"


# ---------------------------------------------------------------- confirmar / revertir / historial
def test_confirm_cancels_the_reversion_and_is_idempotent(env):
    c, router, store, events = env
    as_user("partner", 10, 10)
    apply(c, "firewall-seguro", FW, revertir_minutos=2)
    r = c.post("/api/v1/applications/1/confirm")
    assert r.status_code == 200 and r.json() == {"estado": "confirmada"}
    assert c.post("/api/v1/applications/1/confirm").json() == {"estado": "confirmada"}
    assert [e[1] for e in events].count("plantilla_confirmada") == 1 and events[-1][0] == "partner10@x"
    store.now = T0 + timedelta(minutes=30)
    assert reverter.tick() == 0 and len(router.tables[FILTER]) > 2


def test_confirm_after_deadline_or_after_reversion_is_409(env):
    c, router, store, _ = env
    as_user("partner", 10, 10)
    apply(c, "firewall-seguro", FW, revertir_minutos=2)
    store.now = T0 + timedelta(minutes=2, seconds=1)                       # vencida pero el ciclo aún no corrió
    r = c.post("/api/v1/applications/1/confirm")
    assert r.status_code == 409 and "plazo" in r.json()["detail"]
    reverter.tick()
    r = c.post("/api/v1/applications/1/confirm")
    assert r.status_code == 409 and "ya se revirtió" in r.json()["detail"]
    assert store.apps[1]["estado"] == "revertida"
    assert c.post("/api/v1/applications/99/confirm").status_code == 404


def test_manual_revert_is_picked_up_by_the_reverter(env):
    c, router, store, events = env
    apply(c, "port-forward", PF, revertir_minutos=20)
    assert c.post("/api/v1/applications/1/revert").status_code == 200
    assert reverter.tick() == 1
    assert store.apps[1]["estado"] == "revertida" and router.tables[("ip", "firewall", "nat")] == []
    assert c.post("/api/v1/applications/1/revert").status_code == 409
    assert "plantilla_reversion_solicitada" in [e[1] for e in events]


def test_full_cycle_apply_expire_revert_and_history(env):
    c, router, store, events = env
    as_user("partner", 10, 10)
    assert apply(c, "firewall-seguro", FW, revertir_minutos=2).status_code == 200
    as_user("soporte", 2)
    assert apply(c, "dns-ntp", DNS).status_code == 200
    store.now = T0 + timedelta(minutes=3)
    reverter.tick()
    hist = c.get("/api/v1/devices/5/applications").json()
    assert [(h["id"], h["plantilla"], h["estado"]) for h in hist] == [
        (2, "Servidores DNS y hora", "aplicada"), (1, "Firewall seguro", "revertida")]
    assert set(hist[0]) >= {"id", "plantilla", "estado", "creado_en", "revertir_en", "usuario"}
    assert hist[1]["creado_en"].endswith("Z") and hist[1]["revertir_en"] == "2026-10-01T12:02:00Z"
    assert hist[1]["usuario"] == "partner10@x"
    assert [e[1] for e in events if e[0] == "sistema"] == ["plantilla_revertida"]
    assert router.tables[("ip", "dns")][0]["servers"] == "1.1.1.1,8.8.8.8"                # lo no vencido se conserva


def test_vlan_and_qos_apply_and_revert_leave_no_residue(env):
    c, router, store, _ = env
    assert apply(c, "vlan", {"vlan_id": 30, "interfaz": "bridge-lan", "subred": "192.168.50.0/24"}, revertir_minutos=5).status_code == 200
    assert apply(c, "limite-ancho-banda", {"objetivo": "192.168.10.0/24", "bajada": 20, "subida": 5}, revertir_minutos=5).status_code == 200
    assert len(router.tables[("interface", "vlan")]) == 1 and len(router.tables[("queue", "simple")]) == 1
    store.now = T0 + timedelta(minutes=6)
    assert reverter.tick() == 2
    for path in (("interface", "vlan"), ("ip", "address"), ("ip", "pool"), ("ip", "dhcp-server"),
                 ("ip", "dhcp-server", "network"), ("queue", "simple")):
        assert router.tables[path] == [], path
