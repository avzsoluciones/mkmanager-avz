"""Contrato frontend <-> backend. Extrae las llamadas reales de frontend-new/js y las compara con las rutas del backend.
IDs: CT-*. Las rutas de módulos aún no implementados (M3, M4, M5, panel, auditoría, estado) salen como xfail
(XPASS cuando existan). Cualquier otra ruta que el frontend use y el backend no tenga es un FALLO real."""
import os
import re

import pytest

from conftest import FRONT_JS, route_exists
from app.main import app as _app

SKIP_FILES = {"mock.js"}
CALL_RX = re.compile(r"""api\.(get|post|del)\(\s*(`[^`]*`|'[^']*'|"[^"]*")""")
REQ_RX = re.compile(r"""request\(\s*'(GET|POST|DELETE|PUT|PATCH)'\s*,\s*(`[^`]*`|'[^']*'|"[^"]*")""")
VERB = {"get": "GET", "post": "POST", "del": "DELETE"}


def _norm(path_literal):
    p = path_literal[1:-1]
    p = re.sub(r"\$\{[^}]*\}", "{p}", p)
    return p.split("?")[0]


def extract_calls():
    calls = set()
    for root, _, files in os.walk(FRONT_JS):
        for f in files:
            if not f.endswith(".js") or f in SKIP_FILES or f == "api.js":
                continue
            src = open(os.path.join(root, f), encoding="utf-8").read()
            for m in CALL_RX.finditer(src):
                calls.add((VERB[m.group(1)], "/api/v1" + _norm(m.group(2)), f))
            for m in REQ_RX.finditer(src):
                calls.add((m.group(1), "/api/v1" + _norm(m.group(2)), f))
    return sorted(calls)


CALLS = extract_calls()

PENDING_PREFIXES = ("/api/v1/templates", "/api/v1/vpn", "/api/v1/dashboard", "/api/v1/audit", "/api/v1/applications",
                    "/api/v1/devices/{p}/status", "/api/v1/devices/{p}/applications", "/api/v1/devices/{p}/templates")


def _params():
    out = []
    for method, path, f in CALLS:
        pending = path.startswith(PENDING_PREFIXES)
        marks = [pytest.mark.xfail(strict=False, reason="pendiente de implementar (contrato esperado por el frontend)")] \
            if pending and not route_exists(_app, method, path) else []
        out.append(pytest.param(method, path, f, marks=marks, id=f"{method} {path} <{f}>"))
    return out


def test_CT00_extractor_finds_the_api_surface():
    assert len(CALLS) >= 20, CALLS
    paths = {p for _, p, _ in CALLS}
    assert "/api/v1/auth/login" in paths and "/api/v1/provision/preview" in paths and "/api/v1/sedes" in paths


@pytest.mark.parametrize("method,path,fname", _params())
def test_CT01_every_frontend_call_has_a_backend_route(method, path, fname):
    assert route_exists(_app, method, path), f"{fname} llama {method} {path} y el backend no la define"


# ------------------------------------------------------------------ cuerpos que envía el frontend
def test_CT02_wizard_payload_shapes_are_accepted(world):
    """buildPayload() de asistente.js: wan(dhcp|static|pppoe), lan con gateway/dhcp opcionales, dns lista."""
    full = {"sede_id": 10, "nombre": "MK Tienda", "wan": {"interface": "ether1", "mode": "static", "address": "203.0.113.5/24",
                                                          "gateway": "203.0.113.1"},
            "lan": {"interface": "bridge", "cidr": "192.168.10.0/24", "dns": ["1.1.1.1", "8.8.8.8"], "gateway": "192.168.10.1",
                    "dhcp_start": "192.168.10.100", "dhcp_end": "192.168.10.199"}}
    r = world.client.post("/api/v1/provision/preview", json=full, headers=world.auth(1))
    assert r.status_code == 200, r.text
    minimal = {"sede_id": 10, "nombre": "MK Tienda", "wan": {"interface": "ether1", "mode": "dhcp"},
               "lan": {"interface": "bridge", "cidr": "192.168.10.0/24", "dns": ["1.1.1.1"]}}
    assert world.client.post("/api/v1/provision", json=minimal, headers=world.auth(1)).status_code == 201


def test_CT03_wizard_does_not_send_tunnel_ip_and_backend_default_works(world):
    r = world.client.post("/api/v1/provision/preview", json={"sede_id": 10, "nombre": "MK", "wan": {"interface": "e", "mode": "dhcp"},
                                                              "lan": {"interface": "b", "cidr": "192.168.10.0/24", "dns": ["1.1.1.1"]}},
                          headers=world.auth(1))
    assert r.json()["tunnel_ip"]


def test_CT04_login_response_fields_read_by_session_js(world):
    j = world.client.post("/api/v1/auth/login", json={"email": "partner.a@avz.test", "password": "Clave*Segura2026"}).json()
    assert {"access_token", "user", "require_password_change"} <= set(j)
    assert {"id", "email", "full_name", "role", "partner_id"} <= set(j["user"])


def test_CT05_preview_response_fields_read_by_asistente_js(world):
    j = world.client.post("/api/v1/provision/preview", json={"sede_id": 10, "nombre": "MK", "wan": {"interface": "e", "mode": "dhcp"},
                                                              "lan": {"interface": "b", "cidr": "192.168.10.0/24"}},
                         headers=world.auth(1)).json()
    assert isinstance(j["dhcp"], list) and len(j["dhcp"]) == 2
    assert {"tunnel_ip", "lan_gateway", "script"} <= set(j)


def test_CT06_create_response_fields_read_by_asistente_js(world):
    j = world.client.post("/api/v1/provision", json={"sede_id": 10, "nombre": "MK", "wan": {"interface": "e", "mode": "dhcp"},
                                                      "lan": {"interface": "b", "cidr": "192.168.10.0/24"}}, headers=world.auth(1)).json()
    assert {"id", "nombre", "tunnel_ip", "lan_cidr"} <= set(j["device"]) and j["script"]


def test_CT07_sedes_rows_fields_read_by_sedes_js(world):
    row = world.client.get("/api/v1/sedes", headers=world.auth(3)).json()[0]
    assert {"id", "nombre", "ciudad", "direccion", "contacto_email", "dispositivos"} <= set(row)


def test_CT08_devices_rows_fields_read_by_equipos_js_and_equipo_js(world):
    row = world.client.get("/api/v1/devices", headers=world.auth(3)).json()[0]
    assert {"id", "nombre", "tipo", "sede_id", "sede", "tunnel_ip", "lan_cidr", "estado", "ultimo_heartbeat"} <= set(row)
    one = world.client.get("/api/v1/devices/100", headers=world.auth(3)).json()
    assert {"id", "nombre", "sede", "tunnel_ip", "lan_cidr", "tipo", "estado"} <= set(one)


def test_CT09_script_endpoint_returns_script_key(world):
    assert "script" in world.client.get("/api/v1/provision/100/script", headers=world.auth(1)).json()


def test_CT10_create_sede_response_has_nombre_and_conflict_is_409(world):
    r = world.client.post("/api/v1/sedes", json={"nombre": "Nueva QA", "ciudad": "Cali"}, headers=world.auth(3))
    assert r.status_code == 201 and r.json()["nombre"] == "Nueva QA"
    assert world.client.post("/api/v1/sedes", json={"nombre": "Nueva QA", "ciudad": "Cali"}, headers=world.auth(3)).status_code == 409


def test_CT11_sede_dialog_body_fields_exist_in_schema():
    from app.routers.sedes import SedeCreate
    for f in ("nombre", "ciudad", "direccion", "contacto_nombre", "contacto_telefono", "contacto_email"):
        assert f in SedeCreate.model_fields


def test_CT12_change_password_body_matches(world):
    r = world.client.post("/api/v1/auth/change-password", headers=world.auth(5),
                          json={"current_password": "Clave*Segura2026", "new_password": "OtraClave*2027ok"})
    assert r.status_code == 200 and r.json()["status"] == "success"


# ------------------------------------------------------------------ manejo de errores esperado por api.js
def test_CT20_error_detail_types_by_status(world):
    """api.js: 422 con lista -> campos; 422 con texto -> mensaje; 401/403/404/409/429 con texto."""
    c, h = world.client, world.auth(1)
    base = {"sede_id": 10, "nombre": "MK", "wan": {"interface": "e", "mode": "dhcp"}}
    lst = c.post("/api/v1/provision/preview", headers=h, json={**base, "nombre": "x;y", "lan": {"interface": "b", "cidr": "192.168.10.0/24"}})
    txt = c.post("/api/v1/provision/preview", headers=h, json={**base, "lan": {"interface": "b", "cidr": "10.254.0.0/24"}})
    assert lst.status_code == 422 and isinstance(lst.json()["detail"], list)
    assert txt.status_code == 422 and isinstance(txt.json()["detail"], str)
    assert isinstance(c.get("/api/v1/devices/999999", headers=h).json()["detail"], str)
    assert isinstance(c.get("/api/v1/devices").json()["detail"], str)


@pytest.mark.xfail(strict=False, reason="DESAJUSTE (medio-bajo): los errores de planificación (LAN solapada, IP de túnel en uso, "
                                        "gateway/DHCP fuera de subred) llegan como texto sin 'loc'; la interfaz no puede resaltar el campo.")
def test_CT21_planning_errors_identify_the_field(world):
    r = world.client.post("/api/v1/provision/preview", headers=world.auth(1),
                          json={"sede_id": 10, "nombre": "MK", "wan": {"interface": "e", "mode": "dhcp"},
                                "lan": {"interface": "b", "cidr": "10.254.0.0/24"}})
    d = r.json()["detail"]
    assert isinstance(d, list) and any("cidr" in x["loc"] for x in d)


def test_CT22_login_429_and_401_texts_are_strings(world):
    for _ in range(5):
        r = world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": "x"})
    assert isinstance(r.json()["detail"], str)
    assert world.client.post("/api/v1/auth/login", json={"email": "admin@avz.test", "password": "x"}).status_code == 429


# ------------------------------------------------------------------ paridad de reglas cliente/servidor
def _js_const_regex(name):
    src = open(os.path.join(FRONT_JS, "views", "asistente.js"), encoding="utf-8").read()
    m = re.search(rf"const {name} = /(.+)/;", src)
    assert m, name
    return re.compile(m.group(1))


SAMPLES = ["ether1", "bridge-lan", "a", "ab", "MK Oficina", "MK_Oficina-1.2", "ether1\n", "a\n", "ñ", "a b", "-a", " a", "a" * 32,
           "a" * 33, "a" * 59, "a" * 60, "a" * 61, "", "e;1", 'e"1', "e$"]


def test_CT30_interface_regex_parity_frontend_vs_backend():
    from app.schemas import IFACE_RE
    js = _js_const_regex("IFACE_RE")
    diffs = [s for s in SAMPLES if bool(js.fullmatch(s)) != bool(IFACE_RE.match(s))]
    assert diffs == [], f"el frontend y el backend no coinciden en: {diffs!r} (Python `$` acepta salto de línea final)"


def test_CT31_name_regex_parity_frontend_vs_backend():
    from app.schemas import NAME_RE
    js = _js_const_regex("NAME_RE")
    diffs = [s for s in SAMPLES if bool(js.fullmatch(s.strip())) != bool(NAME_RE.match(s.strip()))]
    assert diffs == [], diffs


def test_CT32_reserved_networks_parity():
    from app.services.addressing import RESERVED_NETWORKS
    src = open(os.path.join(FRONT_JS, "net.js"), encoding="utf-8").read()
    js = set(re.findall(r"cidr: '([0-9./]+)'", src))
    assert js == {str(n) for n in RESERVED_NETWORKS}


def test_CT33_pool_total_in_panel_matches_backend_pool():
    src = open(os.path.join(FRONT_JS, "views", "panel.js"), encoding="utf-8").read()
    total = int(re.search(r"POOL_TOTAL = (\d+)", src).group(1))
    assert total == len(range(2, 251))


def test_CT34_default_dhcp_formula_parity():
    import ipaddress
    from app.services.addressing import default_dhcp_range
    src = open(os.path.join(FRONT_JS, "net.js"), encoding="utf-8").read()
    assert "net.network + 100, net.network + 199" in src and "net.network + 2, net.broadcast - 1" in src
    n = ipaddress.ip_network("192.168.10.0/24")
    assert default_dhcp_range(n) == ("192.168.10.100", "192.168.10.199")
    n = ipaddress.ip_network("192.168.10.0/28")
    assert default_dhcp_range(n) == ("192.168.10.2", "192.168.10.14")


def test_CT35_frontend_private_check_is_not_looser_than_backend():
    """El frontend solo acepta RFC1918; el backend acepta además 127/8, 169.254/16, etc. (desajuste de reglas)."""
    from app.services import addressing as ad
    for cidr in ("127.0.0.0/16", "169.254.0.0/16"):
        with pytest.raises(ValueError):
            ad.parse_lan(cidr)


def test_CT36_password_policy_parity():
    src = open(os.path.join(FRONT_JS, "views", "cuenta.js"), encoding="utf-8").read()
    assert "p.length >= 10" in src and "[A-Z]" in src and "[a-z]" in src and "\\d" in src and "[^A-Za-z0-9]" in src
    from app.security import password_is_strong
    assert password_is_strong("Abcdefgh1*") and not password_is_strong("Abcdefgh1")


def test_CT37_roles_known_to_frontend_match_backend():
    from app.security import ROLES
    src = open(os.path.join(FRONT_JS, "session.js"), encoding="utf-8").read()
    js_roles = set(re.findall(r"^\s{2}(\w+): '", src.split("ROLE_LABELS = {")[1].split("}")[0], re.M))
    assert js_roles == set(ROLES)


def test_CT38_estado_values_backend_emits_are_understood_by_frontend():
    src = open(os.path.join(FRONT_JS, "data.js"), encoding="utf-8").read()
    assert "'pendiente'" in src and "'online'" in src and "'offline'" in src      # backend inserta 'pendiente'; BD default 'online'


# ------------------------------------------------------------------ contratos esperados de lo pendiente (xfail)
PENDING = pytest.mark.xfail(strict=False, reason="pendiente de implementar")


@PENDING
def test_CT40_dashboard_contract(world):
    assert route_exists(world.app, "GET", "/api/v1/dashboard"), "ruta ausente"
    j = world.client.get("/api/v1/dashboard", headers=world.auth(1)).json()
    assert {"sedes", "equipos", "online", "offline", "pendientes", "ips_libres", "actividad"} <= set(j)
    assert all({"fecha", "usuario", "accion", "detalle"} <= set(a) for a in j["actividad"])


@PENDING
def test_CT41_device_status_contract(world):
    assert route_exists(world.app, "GET", "/api/v1/devices/{id}/status"), "ruta ausente"
    j = world.client.get("/api/v1/devices/100/status", headers=world.auth(1)).json()
    assert {"estado", "ultimo_contacto", "rx_bytes", "tx_bytes"} <= set(j)


@PENDING
def test_CT42_templates_list_contract(world):
    assert route_exists(world.app, "GET", "/api/v1/templates"), "ruta ausente"
    rows = world.client.get("/api/v1/templates", headers=world.auth(1)).json()
    assert rows and all({"id", "nombre", "descripcion", "categoria", "riesgo", "revierte", "campos"} <= set(t) for t in rows)
    assert {t["riesgo"] for t in rows} <= {"bajo", "medio", "alto"}
    for t in rows:
        for c in t["campos"]:
            assert {"name", "label", "type"} <= set(c)


@PENDING
def test_CT43_template_preview_and_apply_contract(world):
    assert route_exists(world.app, "POST", "/api/v1/devices/{id}/templates/{tid}/preview"), "ruta ausente"
    pv = world.client.post("/api/v1/devices/100/templates/dns-ntp/preview", headers=world.auth(1), json={"params": {"dns": ["1.1.1.1"]}})
    assert pv.status_code == 200 and "script" in pv.json()
    ap = world.client.post("/api/v1/devices/100/templates/dns-ntp/apply", headers=world.auth(1), json={"params": {"dns": ["1.1.1.1"]}})
    assert {"aplicacion_id", "estado", "revertir_en"} <= set(ap.json())


@PENDING
def test_CT44_applications_contract(world):
    assert route_exists(world.app, "GET", "/api/v1/devices/{id}/applications"), "ruta ausente"
    rows = world.client.get("/api/v1/devices/100/applications", headers=world.auth(1)).json()
    assert all({"id", "plantilla", "estado", "creado_en", "revertir_en", "usuario"} <= set(a) for a in rows)
    assert route_exists(world.app, "POST", "/api/v1/applications/{id}/confirm")


@PENDING
def test_CT45_vpn_contract(world):
    assert route_exists(world.app, "POST", "/api/v1/vpn/users"), "ruta ausente"
    c = world.client.post("/api/v1/vpn/users", headers=world.auth(1), json={"nombre": "Laura Gomez"})
    j = c.json()
    assert {"id", "nombre", "tunnel_ip", "conf_text"} <= set(j) and "[Interface]" in j["conf_text"]
    lst = world.client.get("/api/v1/vpn/users", headers=world.auth(1)).json()
    assert all({"id", "nombre", "tunnel_ip", "creado_en", "revocado"} <= set(u) for u in lst)
    assert isinstance(lst[0]["revocado"], bool)
    conf = world.client.get(f"/api/v1/vpn/users/{j['id']}/conf", headers=world.auth(1))
    assert conf.text.startswith("[Interface]"), "el .conf debe ser texto plano, no un string JSON entrecomillado"
    qr = world.client.get(f"/api/v1/vpn/users/{j['id']}/qr.png", headers=world.auth(1))
    assert qr.headers["content-type"] == "image/png" and qr.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert world.client.delete(f"/api/v1/vpn/users/{j['id']}", headers=world.auth(1)).status_code in (200, 204)


@PENDING
def test_CT46_audit_contract(world):
    assert route_exists(world.app, "GET", "/api/v1/audit"), "ruta ausente"
    rows = world.client.get("/api/v1/audit?limit=50", headers=world.auth(1)).json()
    assert all({"fecha", "usuario_email", "accion", "detalles"} <= set(r) for r in rows)
    assert world.client.get("/api/v1/audit", headers=world.auth(5)).status_code == 403
