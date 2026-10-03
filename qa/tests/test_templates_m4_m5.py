"""M4/M5: plantillas con reversión (A5, A7). Contrato de la interfaz: frontend-new/js/mock.js.
Usa un almacén falso (misma interfaz que edge.PgStore) y sustituye edge.run_apply: NO se habla con ningún router.
IDs: TP-*. La lógica interna de las operaciones RouterOS no se prueba aquí (lo hace el backend); sí contrato, roles, límites y auditoría."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import ROLES

TPL_OK = {  # parámetros válidos por plantilla (dispositivo 100: LAN 192.168.100.0/24, gateway 192.168.100.1)
    "dns-ntp": {"dns": ["1.1.1.1", "8.8.8.8"], "ntp": "pool.ntp.org"},
    "limite-ancho-banda": {"objetivo": "192.168.100.0/24", "bajada": 20, "subida": 5},
    "port-forward": {"protocolo": "tcp", "puerto_externo": 8080, "ip_destino": "192.168.100.50", "puerto_destino": 80},
    "vlan": {"vlan_id": 20, "interfaz": "bridge", "subred": "192.168.150.0/24"},
    "firewall-seguro": {"ips_admin": [], "permitir_ping": True},
}


class FakeStore:
    """Misma interfaz pública que edge.PgStore, en memoria."""

    def __init__(self, db):
        self.db, self.apps, self.next_id = db, {}, 0
        self.errors, self.begin_conflict = [], None

    def get_device(self, device_id, where, params):
        for d in self.db.dispositivos:
            s = self.db._sede_of(d)
            if d["id"] == device_id and s and self.db._scoped(where, (device_id, *params), s):
                return dict(d)
        return None

    def begin(self, device_id, plantilla, params, phash, email):
        from app.services import edge
        if self.begin_conflict:
            raise edge.Conflict(self.begin_conflict)
        self.next_id += 1
        self.apps[self.next_id] = {"id": self.next_id, "dispositivo_id": device_id, "plantilla": plantilla, "estado": "aplicando",
                                   "revertir_en": None, "usuario_email": email, "creado_en": datetime.now(timezone.utc),
                                   "resultado": None, "parametros": params}
        return self.next_id

    def finish_applied(self, app_id, minutes):
        a = self.apps[app_id]
        a["estado"] = "aplicada"
        a["revertir_en"] = datetime.now(timezone.utc) + timedelta(minutes=minutes) if minutes else None
        return a["revertir_en"]

    def mark_error(self, app_id, message):
        self.apps[app_id].update(estado="error", resultado=message)
        self.errors.append(message)

    def schedule_revert_now(self, app_id, message):
        self.apps[app_id].update(estado="aplicada", revertir_en=datetime.now(timezone.utc), resultado=message)

    def get_application(self, app_id, where, params):
        a = self.apps.get(app_id)
        if not a:
            return None
        d = next(d for d in self.db.dispositivos if d["id"] == a["dispositivo_id"])
        return dict(a) if self.db._scoped(where, (app_id, *params), self.db._sede_of(d)) else None

    def confirm(self, app_id):
        a = self.apps[app_id]
        if a["estado"] == "aplicada" and (a["revertir_en"] is None or a["revertir_en"] > datetime.now(timezone.utc)):
            a["estado"] = "confirmada"
            return True
        return False

    def request_revert(self, app_id):
        a = self.apps[app_id]
        if a["estado"] == "aplicada":
            a["revertir_en"] = datetime.now(timezone.utc)
            return True
        return False

    def list_for_device(self, device_id):
        return [dict(a) for a in sorted(self.apps.values(), key=lambda x: -x["id"]) if a["dispositivo_id"] == device_id]


@pytest.fixture
def tw(world, monkeypatch):
    """world + almacén falso + run_apply sustituido. Modo de run_apply: world.mode in ok|rolled|unrolled|edge."""
    from app.services import edge
    store = FakeStore(world.db)
    world.store, world.mode, world.applied = store, "ok", []
    monkeypatch.setattr(edge, "store", store)

    def fake_run_apply(st, dev, app_id, ops):
        if world.mode == "rolled":
            raise edge.ApplyFailed("fallo simulado", rolled_back=True)
        if world.mode == "unrolled":
            raise edge.ApplyFailed("fallo simulado", rolled_back=False)
        if world.mode == "edge":
            raise edge.EdgeError("No se pudo conectar con el equipo.")
        world.applied.append((dev["id"], ops))
        return []

    monkeypatch.setattr(edge, "run_apply", fake_run_apply)
    return world


def URL(dev, tpl, action):
    return f"/api/v1/devices/{dev}/templates/{tpl}/{action}"


def apply(w, uid, tpl="dns-ntp", dev=100, params=None, **extra):
    return w.client.post(URL(dev, tpl, "apply"), json={"params": TPL_OK[tpl] if params is None else params, **extra}, headers=w.auth(uid))


def preview(w, uid, tpl="dns-ntp", dev=100, params=None):
    return w.client.post(URL(dev, tpl, "preview"), json={"params": TPL_OK[tpl] if params is None else params}, headers=w.auth(uid))


# ---------------------------------------------------------------- catálogo
def test_TP01_catalog_contract(tw):
    r = tw.client.get("/api/v1/templates", headers=tw.auth(1))
    assert r.status_code == 200
    rows = r.json()
    assert {t["id"] for t in rows} >= {"firewall-seguro", "port-forward", "vlan", "limite-ancho-banda", "dns-ntp"}
    for t in rows:
        assert {"id", "nombre", "descripcion", "categoria", "icono", "riesgo", "revierte", "campos"} <= set(t)
        assert t["riesgo"] in ("bajo", "medio", "alto") and isinstance(t["revierte"], bool)
        for c in t["campos"]:
            assert {"name", "label", "type", "required"} <= set(c)
    byid = {t["id"]: t for t in rows}
    assert byid["firewall-seguro"]["riesgo"] == "alto" and byid["firewall-seguro"]["revierte"] is True


def test_TP02_catalog_has_no_provider_names_or_internals(tw):
    txt = tw.client.get("/api/v1/templates", headers=tw.auth(1)).text
    for bad in ("3HC", "Yotta", "Wasabi", "Polaris", "Acronis", "librouteros", "wireguard1", "VAULT", "SECRET"):
        assert bad.lower() not in txt.lower(), bad


def test_TP03_categories_known_to_frontend(tw):
    from conftest import FRONT_JS
    import os, re
    src = open(os.path.join(FRONT_JS, "data.js"), encoding="utf-8").read()
    known = {k.lower() for k in re.findall(r"key: '([^']+)'", src)}
    norm = lambda s: s.lower().replace("é", "e").replace("ó", "o").replace("í", "i")  # noqa: E731
    for t in tw.client.get("/api/v1/templates", headers=tw.auth(1)).json():
        assert norm(t["categoria"]) in {norm(k) for k in known}, f"categoría '{t['categoria']}' sin icono/color en la interfaz"


@pytest.mark.parametrize("tpl,params", list(TPL_OK.items()))
def test_TP04_preview_ok_has_script_and_avisos_and_no_side_effects(tw, tpl, params):
    r = preview(tw, 1, tpl)
    assert r.status_code == 200, r.text
    j = r.json()
    assert isinstance(j["script"], str) and j["script"].startswith("# Servicio:") and isinstance(j["avisos"], list)
    assert tw.store.apps == {} and tw.applied == [] and not [a for a in tw.db.audit if a["accion"].startswith("plantilla")]
    assert tw.api.connects == 0, "la vista previa no debe tocar el hub"


def test_TP05_unknown_template_and_device_are_404(tw):
    assert tw.client.post(URL(100, "no-existe", "preview"), json={"params": {}}, headers=tw.auth(1)).status_code == 404
    assert preview(tw, 1, "dns-ntp", dev=424242).status_code == 404


@pytest.mark.parametrize("bad", [{"dns": []}, {"dns": ["1.1.1.1"] * 4}, {"dns": ["abc"]}, {"dns": ["1.1.1.1\n/system reset"]},
                                 {"dns": "1.1.1.1"}, {"dns": [1]}, {"dns": ["1.1.1.1"], "ntp": "a b;c"}, {"dns": ["1.1.1.1"], "ntp": "x" * 300},
                                 {"dns": ["1.1.1.1"], "ntp": "host\n/user add"}, {"dns": ["1.1.1.1"], "extra": 1}, {}])
def test_TP06_dns_ntp_hostile_or_invalid_params_422(tw, bad):
    r = preview(tw, 1, "dns-ntp", params=bad)
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/json"), r.text[:200]
    assert tw.store.apps == {}


@pytest.mark.parametrize("bad", [{"puerto_externo": 22}, {"puerto_externo": 8291}, {"puerto_externo": 8728}, {"puerto_externo": 8729},
                                 {"puerto_externo": 13231}, {"puerto_externo": 23}, {"puerto_externo": 0}, {"puerto_externo": 65536},
                                 {"puerto_externo": "80"}, {"puerto_externo": True}, {"puerto_externo": 80.5},
                                 {"ip_destino": "8.8.8.8"}, {"ip_destino": "192.168.100.0"}, {"ip_destino": "192.168.100.255"},
                                 {"ip_destino": "192.168.100.1"}, {"ip_destino": "192.168.101.5"}, {"ip_destino": "x;y"},
                                 {"ip_destino": "192.168.100.5\n"}, {"protocolo": "icmp"}, {"protocolo": "tcp; x"}, {"puerto_destino": 70000}])
def test_TP07_port_forward_guards(tw, bad):
    r = preview(tw, 1, "port-forward", params={**TPL_OK["port-forward"], **bad})
    assert r.status_code == 422, f"{bad}: {r.status_code} {r.text[:160]}"


def test_TP08_port_forward_risky_port_warns_but_management_never_opens(tw):
    r = preview(tw, 1, "port-forward", params={**TPL_OK["port-forward"], "puerto_externo": 3389})
    assert r.status_code == 200 and r.json()["avisos"], "puerto de riesgo (RDP) debe llevar aviso"


@pytest.mark.parametrize("bad", [{"vlan_id": 1}, {"vlan_id": 4095}, {"vlan_id": "20"}, {"vlan_id": True}, {"subred": "10.254.0.0/24"},
                                 {"subred": "10.10.10.0/24"}, {"subred": "10.40.1.0/24"}, {"subred": "192.168.100.0/24"},
                                 {"subred": "192.168.100.128/25"}, {"subred": "8.8.8.0/24"}, {"subred": "abc"}, {"interfaz": "ether1"},
                                 {"interfaz": "wg-avzcloud"}, {"interfaz": "br; x"}, {"interfaz": "br\n"}, {"interfaz": ""}])
def test_TP09_vlan_guards(tw, bad):
    r = preview(tw, 1, "vlan", params={**TPL_OK["vlan"], **bad})
    assert r.status_code == 422, f"{bad}: {r.status_code} {r.text[:160]}"


@pytest.mark.parametrize("bad", [{"bajada": 0}, {"bajada": 10001}, {"subida": -1}, {"bajada": "20"}, {"objetivo": "8.8.8.8"},
                                 {"objetivo": "10.254.0.0/24"}, {"objetivo": "10.10.10.4"}, {"objetivo": "0.0.0.0/0"}, {"objetivo": "x;y"}])
def test_TP10_limite_guards(tw, bad):
    r = preview(tw, 1, "limite-ancho-banda", params={**TPL_OK["limite-ancho-banda"], **bad})
    assert r.status_code == 422, f"{bad}: {r.status_code} {r.text[:160]}"


@pytest.mark.parametrize("bad", [{"ips_admin": ["0.0.0.0/0"]}, {"ips_admin": ["abc"]}, {"ips_admin": ["1.1.1.1/24;x"]}, {"ips_admin": "1.1.1.1"},
                                 {"ips_admin": ["203.0.113.0/24"] * 21}, {"permitir_ping": "yes"}, {"permitir_ping": 1}])
def test_TP11_firewall_param_guards(tw, bad):
    assert preview(tw, 1, "firewall-seguro", params={**TPL_OK["firewall-seguro"], **bad}).status_code == 422


def test_TP12_validation_errors_identify_the_field_for_the_form(tw):
    r = preview(tw, 1, "port-forward", params={**TPL_OK["port-forward"], "puerto_externo": 22})
    d = r.json()["detail"]
    assert isinstance(d, list) and d[0]["loc"] == ["body", "params", "puerto_externo"] and d[0]["msg"]


def test_TP13_body_strictness(tw):
    c, h = tw.client, tw.auth(1)
    assert c.post(URL(100, "dns-ntp", "preview"), json={"params": TPL_OK["dns-ntp"], "x": 1}, headers=h).status_code == 422
    assert c.post(URL(100, "dns-ntp", "preview"), json={"params": "texto"}, headers=h).status_code == 422
    assert c.post(URL(100, "dns-ntp", "preview"), json=[], headers=h).status_code == 422


# ---------------------------------------------------------------- aplicar (A5, A7)
@pytest.mark.parametrize("uid", [1, 2, 3, 7, 9])
@pytest.mark.parametrize("tpl", ["dns-ntp", "limite-ancho-banda", "port-forward", "vlan"])
def test_TP20_low_and_medium_risk_apply_allowed_for_field_roles(tw, uid, tpl):
    r = apply(tw, uid, tpl, revertir_minutos=5 if tpl != "dns-ntp" else None)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["estado"] == "aplicada" and isinstance(j["aplicacion_id"], int)
    assert tw.applied and tw.applied[-1][0] == 100


def test_TP21_cliente_cannot_apply_or_preview_or_list(tw):
    for fn in (lambda: apply(tw, 5), lambda: preview(tw, 5), lambda: tw.client.get("/api/v1/templates", headers=tw.auth(5)),
               lambda: tw.client.get("/api/v1/devices/100/applications", headers=tw.auth(5))):
        assert fn().status_code == 403
    assert tw.applied == []


@pytest.mark.parametrize("uid", [3, 7, 9])
def test_TP22_high_risk_requires_rollback_for_partner_and_tecnico(tw, uid):
    r = apply(tw, uid, "firewall-seguro")
    assert r.status_code == 422 and "revert" in str(r.json()["detail"]).lower()
    assert tw.applied == [] and tw.store.apps == {}, "no debe aplicarse nada sin reversión (A5)"


@pytest.mark.parametrize("uid", [3, 7])
@pytest.mark.parametrize("minutes", [0, 1, 31, 1000, -5])
def test_TP23_rollback_minutes_bounds_2_to_30(tw, uid, minutes):
    assert apply(tw, uid, "firewall-seguro", revertir_minutos=minutes).status_code == 422
    assert tw.applied == []


@pytest.mark.parametrize("val", ["5", True, 5.5, [5], {"m": 5}])
def test_TP24_rollback_minutes_type_strict(tw, val):
    assert apply(tw, 3, "firewall-seguro", revertir_minutos=val).status_code == 422


@pytest.mark.parametrize("minutes", [2, 5, 30])
def test_TP25_partner_firewall_with_valid_rollback_ok_and_returns_deadline(tw, minutes):
    r = apply(tw, 3, "firewall-seguro", revertir_minutos=minutes)
    assert r.status_code == 200
    due = datetime.fromisoformat(r.json()["revertir_en"].replace("Z", "+00:00"))
    delta = (due - datetime.now(timezone.utc)).total_seconds()
    assert minutes * 60 - 10 <= delta <= minutes * 60 + 10


@pytest.mark.parametrize("uid", [1, 2])
def test_TP26_staff_firewall_rollback_optional(tw, uid):
    r = apply(tw, uid, "firewall-seguro")
    assert r.status_code == 200 and r.json()["revertir_en"] is None


def test_TP27_dns_ntp_does_not_accept_rollback(tw):
    assert apply(tw, 1, "dns-ntp", revertir_minutos=5).status_code == 422


def test_TP28_A7_apply_is_audited_with_who_what_params(tw):
    apply(tw, 3, "port-forward", revertir_minutos=5)
    a = [x for x in tw.db.audit if x["accion"] == "plantilla_aplicada"]
    assert len(a) == 1 and a[0]["usuario_email"] == "partner.a@avz.test" and a[0]["ip"]
    assert "plantilla=port-forward" in a[0]["detalles"] and "device=100" in a[0]["detalles"] and "puerto_externo" in a[0]["detalles"]
    assert a[0]["fecha"] is not None


def test_TP29_A7_errors_are_audited_too(tw):
    tw.mode = "rolled"
    r = apply(tw, 3, "dns-ntp")
    assert r.status_code == 409
    assert any(x["accion"] == "plantilla_error" for x in tw.db.audit)


def test_TP30_failed_apply_with_rollback_reports_409_and_marks_error(tw):
    tw.mode = "rolled"
    r = apply(tw, 1, "dns-ntp")
    assert r.status_code == 409 and "deshizo" in r.json()["detail"].lower()
    assert list(tw.store.apps.values())[0]["estado"] == "error"


def test_TP31_failed_apply_without_rollback_schedules_immediate_revert(tw):
    tw.mode = "unrolled"
    r = apply(tw, 1, "dns-ntp")
    assert r.status_code == 409
    a = list(tw.store.apps.values())[0]
    assert a["estado"] == "aplicada" and a["revertir_en"] is not None, "debe quedar programada la reversión inmediata (A5)"


def test_TP32_device_unreachable_is_502_json_and_nothing_applied(tw):
    tw.mode = "edge"
    r = apply(tw, 1, "dns-ntp")
    assert r.status_code == 502 and r.headers["content-type"].startswith("application/json")
    assert list(tw.store.apps.values())[0]["estado"] == "error"


def test_TP33_concurrent_application_conflict_is_409(tw):
    tw.store.begin_conflict = "Hay otra aplicación en curso en este equipo."
    assert apply(tw, 1, "dns-ntp").status_code == 409 and tw.applied == []


def test_TP34_device_without_full_management_rejected_409(tw):
    for d in tw.db.dispositivos:
        if d["id"] == 100:
            d["marca"] = "Ubiquiti"
    assert apply(tw, 1, "dns-ntp").status_code == 409
    assert preview(tw, 1, "dns-ntp").status_code == 409


def test_TP35_device_without_api_credentials_rejected_409(tw):
    for d in tw.db.dispositivos:
        if d["id"] == 100:
            d["api_password_enc"] = None
    assert apply(tw, 1, "dns-ntp").status_code == 409


def test_TP36_error_responses_do_not_leak_internals(tw):
    tw.mode = "edge"
    t = apply(tw, 1, "dns-ntp").text
    for leak in ("Traceback", "avz-api", "api_password", "clave-api", "10.10.10.3"):
        assert leak not in t


# ---------------------------------------------------------------- aislamiento A8
@pytest.mark.parametrize("uid", [4, 8])
@pytest.mark.parametrize("tpl", list(TPL_OK))
def test_TP40_other_tenant_cannot_preview_or_apply(tw, uid, tpl):
    assert preview(tw, uid, tpl).status_code == 404
    assert apply(tw, uid, tpl, revertir_minutos=5).status_code == 404
    assert tw.applied == [] and tw.store.apps == {}


def test_TP41_partner_a2_same_tenant_can_operate_partner_a_devices(tw):
    assert apply(tw, 9, "dns-ntp").status_code == 200


def test_TP42_applications_list_scope_and_shape(tw):
    apply(tw, 3, "dns-ntp")
    ok = tw.client.get("/api/v1/devices/100/applications", headers=tw.auth(7))
    assert ok.status_code == 200 and len(ok.json()) == 1
    row = ok.json()[0]
    assert {"id", "plantilla", "plantilla_id", "estado", "creado_en", "revertir_en", "usuario", "resultado"} <= set(row)
    assert row["plantilla"] == "Servidores DNS y hora"
    assert tw.client.get("/api/v1/devices/100/applications", headers=tw.auth(4)).status_code == 404


# ---------------------------------------------------------------- confirmar / revertir
def _applied(tw, uid=3, tpl="firewall-seguro", minutes=5):
    return apply(tw, uid, tpl, revertir_minutos=minutes).json()["aplicacion_id"]


def test_TP50_confirm_ok_idempotent_and_audited(tw):
    aid = _applied(tw)
    r1 = tw.client.post(f"/api/v1/applications/{aid}/confirm", json={}, headers=tw.auth(7))
    r2 = tw.client.post(f"/api/v1/applications/{aid}/confirm", json={}, headers=tw.auth(3))
    assert r1.json() == r2.json() == {"estado": "confirmada"}
    assert len([a for a in tw.db.audit if a["accion"] == "plantilla_confirmada"]) == 1


def test_TP51_confirm_after_deadline_is_409_and_stays_unconfirmed(tw):
    aid = _applied(tw)
    tw.store.apps[aid]["revertir_en"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    r = tw.client.post(f"/api/v1/applications/{aid}/confirm", json={}, headers=tw.auth(3))
    assert r.status_code == 409 and tw.store.apps[aid]["estado"] != "confirmada"


def test_TP52_confirm_after_auto_revert_is_409(tw):
    aid = _applied(tw)
    tw.store.apps[aid]["estado"] = "revertida"
    assert tw.client.post(f"/api/v1/applications/{aid}/confirm", json={}, headers=tw.auth(3)).status_code == 409


def test_TP53_confirm_other_tenant_404_cliente_403_unknown_404(tw):
    aid = _applied(tw)
    assert tw.client.post(f"/api/v1/applications/{aid}/confirm", json={}, headers=tw.auth(4)).status_code == 404
    assert tw.client.post(f"/api/v1/applications/{aid}/confirm", json={}, headers=tw.auth(5)).status_code == 403
    assert tw.client.post("/api/v1/applications/99999/confirm", json={}, headers=tw.auth(3)).status_code == 404
    assert tw.store.apps[aid]["estado"] == "aplicada"


def test_TP54_manual_revert_brings_deadline_forward_and_is_audited(tw):
    aid = _applied(tw)
    r = tw.client.post(f"/api/v1/applications/{aid}/revert", json={}, headers=tw.auth(3))
    assert r.status_code == 200 and tw.store.apps[aid]["revertir_en"] <= datetime.now(timezone.utc)
    assert any(a["accion"] == "plantilla_reversion_solicitada" for a in tw.db.audit)
    assert tw.client.post(f"/api/v1/applications/{aid}/confirm", json={}, headers=tw.auth(3)).status_code == 409


def test_TP55_revert_confirmed_application_is_409(tw):
    aid = _applied(tw)
    tw.client.post(f"/api/v1/applications/{aid}/confirm", json={}, headers=tw.auth(3))
    assert tw.client.post(f"/api/v1/applications/{aid}/revert", json={}, headers=tw.auth(3)).status_code == 409


@pytest.mark.parametrize("bad", ["abc", "-1", "1;2"])
def test_TP56_application_id_hostile_is_422_or_404(tw, bad):
    r = tw.client.post(f"/api/v1/applications/{bad}/confirm", json={}, headers=tw.auth(3))
    assert r.status_code in (404, 422) and r.headers["content-type"].startswith("application/json")
