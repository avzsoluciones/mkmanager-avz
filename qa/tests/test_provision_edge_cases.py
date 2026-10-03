"""M1+M2: aprovisionamiento. Entradas hostiles, límites, fallos del hub y de la BD, concurrencia, claves.
IDs: PV-* (aprovisionamiento), A1..A4 de la spec."""
import base64
import re
import threading

import pytest

import fakes
from conftest import BODY, body

P, PREV = "/api/v1/provision", "/api/v1/provision/preview"


def lan(cidr, **extra):
    return body(lan={"interface": "bridge-lan", "cidr": cidr, **extra})


def post(world, path, b, uid=1):
    return world.client.post(path, json=b, headers=world.auth(uid))


# ---------------------------------------------------------------- feliz + A2/A3
def test_PV01_happy_path_creates_device_peer_script_and_audit(world):
    r = post(world, P, BODY)
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["status"] == "success" and j["device"]["tunnel_ip"] == "10.254.0.2"
    assert j["device"]["lan_cidr"] == "192.168.10.0/24"
    assert any(a["accion"] == "router_aprovisionado" for a in world.db.audit)
    assert len(world.api.added) == 1 and world.db.dispositivos[-1]["estado"] == "pendiente"
    assert sum(1 for p in world.db.wg_peers if p["tipo"] == "router") == 4


def test_PV02_A2_script_has_real_hub_key_and_matching_peer_public_key(world):
    j = post(world, P, BODY).json()
    s = j["script"]
    assert fakes.HUB_KEY in s and "vpn.example.com" in s and "<CLAVE" not in s
    priv = re.search(r'private-key="([^"]+)"', s).group(1)
    from app.services import wgkeys
    pub_from_script = base64.b64encode(wgkeys.public_from_private_bytes(base64.b64decode(priv))).decode()
    assert world.api.added[0]["public-key"] == pub_from_script           # el hub registró la pública de ESTA privada
    assert wgkeys.is_valid_public_key(world.api.added[0]["public-key"])


def test_PV03_private_material_stored_encrypted_and_never_audited(world):
    j = post(world, P, BODY).json()
    priv = re.search(r'private-key="([^"]+)"', j["script"]).group(1)
    api_pwd = re.search(r'password="([^"]+)"', j["script"]).group(1)
    peer = world.db.wg_peers[-1]
    dev = world.db.dispositivos[-1]
    assert peer["private_key_enc"] != priv and dev["api_password_enc"] != api_pwd
    from app.services import wgkeys
    assert wgkeys.decrypt_secret(peer["private_key_enc"]) == priv
    assert wgkeys.decrypt_secret(dev["api_password_enc"]) == api_pwd
    blob = str(world.db.audit) + str(world.api.added)
    assert priv not in blob and api_pwd not in blob


def test_PV04_A3_tunnel_ip_is_next_free_and_never_repeats(world):
    ips = [post(world, P, lan(f"192.168.{50 + i}.0/24", )).json()["device"]["tunnel_ip"] for i in range(5)]
    assert ips == ["10.254.0.2", "10.254.0.3", "10.254.0.4", "10.254.0.5", "10.254.0.6"]
    assert len(set(ips)) == 5


def test_PV05_tunnel_ip_skips_addresses_used_only_in_hub_or_only_in_db(world):
    world.api.peers.append({".id": "*Z", "interface": "wg1", "allowed-address": "10.254.0.2/32"})   # solo hub
    world.db.add_device(id=9001, nombre="x", sede_id=10, tunnel_ip="10.254.0.3", lan_cidr="192.168.77.0/24")  # solo BD
    assert post(world, PREV, BODY).json()["tunnel_ip"] == "10.254.0.4"


def test_PV06_preview_has_no_side_effects(world):
    n_dev, n_peer, n_aud = len(world.db.dispositivos), len(world.db.wg_peers), len(world.db.audit)
    r = post(world, PREV, BODY)
    assert r.status_code == 200 and set(r.json()) >= {"tunnel_ip", "lan_gateway", "dhcp", "script"}
    assert world.api.added == [] and world.api.removed == []
    assert (len(world.db.dispositivos), len(world.db.wg_peers), len(world.db.audit)) == (n_dev, n_peer, n_aud)
    assert "<CLAVE_PRIVADA" in r.json()["script"] and "private-key=\"<" in r.json()["script"]


def test_PV07_preview_and_create_agree_on_plan(world):
    pv = post(world, PREV, BODY).json()
    cr = post(world, P, BODY).json()
    assert pv["tunnel_ip"] == cr["device"]["tunnel_ip"]


def test_PV08_script_reinstall_returns_same_keys_as_creation(world):
    cr = post(world, P, BODY).json()
    did = cr["device"]["id"]
    again = world.client.get(f"{P}/{did}/script", headers=world.auth(1)).json()["script"]
    key = lambda s: re.search(r'private-key="([^"]+)"', s).group(1)   # noqa: E731
    assert key(again) == key(cr["script"])
    assert fakes.HUB_KEY in again


def test_PV09_script_for_unknown_or_unprovisioned_device_is_404(world):
    assert world.client.get(f"{P}/424242/script", headers=world.auth(1)).status_code == 404
    world.db.add_device(id=7777, nombre="legacy", sede_id=10, tunnel_ip=None, lan_cidr=None)     # sin wg_peers
    assert world.client.get(f"{P}/7777/script", headers=world.auth(1)).status_code == 404


def test_PV10_script_with_wrong_vault_key_fails_cleanly_json(world, monkeypatch):
    from cryptography.fernet import Fernet
    from app import config
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    config._settings = None
    r = world.client.get(f"{P}/100/script", headers=world.auth(1))
    assert r.status_code >= 500 and r.headers["content-type"].startswith("application/json")


def test_PV11_missing_vault_key_blocks_creation_without_touching_hub_state(world, monkeypatch):
    from app import config
    monkeypatch.setenv("VAULT_KEY", "")
    config._settings = None
    r = post(world, P, BODY)
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
    assert world.api.added == []         # no se crea el peer si no se pueden guardar las claves


# ---------------------------------------------------------------- nombres hostiles (inyección)
HOSTILE = ['x"; /system reset-configuration', "a$(whoami)", "a`id`", "a[:put 1]", "a{b}", "a\\b", "a\nb", "a\r\n/user add",
           "a\x00b", "../../etc/passwd", "a;b", "a|b", "a&b", "a'b", "a<b>", "a b\t", " ", "", "a", "-abc", " abc-", "ñandú",
           "a" * 61, "💥💥", "a/b", "a:b", "a=b", "a,b"]


@pytest.mark.parametrize("bad", HOSTILE)
@pytest.mark.parametrize("path", [PREV, P])
def test_PV20_hostile_device_name_rejected_422_json(world, bad, path):
    # el nombre se hace strip(): "a b\t" y " abc-" quedan válidos tras recortar; se excluyen de la expectativa estricta
    stripped = bad.strip()
    import re as _re
    valid_after_strip = bool(_re.match(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{1,59}$", stripped))
    r = post(world, path, body(nombre=bad))
    if valid_after_strip:
        assert r.status_code in (200, 201)
    else:
        assert r.status_code == 422, r.text
        assert r.headers["content-type"].startswith("application/json")
        assert world.api.added == []


@pytest.mark.parametrize("bad", ["ether1\n", "ether1\n/system reset-configuration", "eth er", 'e"', "e;x", "e$", "é", "", "a" * 33,
                                 "-e", ".e", "e[1]", "e\\", "e\x00", "e\t", "e\r"])
@pytest.mark.parametrize("where", ["wan", "lan"])
@pytest.mark.parametrize("path", [PREV, P])
def test_PV21_hostile_interface_names_rejected_422(world, bad, where, path):
    """HALLAZGO esperado: 'ether1\\n' pasa IFACE_RE (el `$` de Python acepta un salto final) y rompe luego en q() -> 500
    DESPUÉS de crear peer y dispositivo (provision.py:133 / schemas.py:8)."""
    b = body()
    if where == "wan":
        b["wan"] = {"interface": bad, "mode": "dhcp"}
    else:
        b["lan"] = {"interface": bad, "cidr": "192.168.10.0/24"}
    r = post(world, path, b)
    assert r.status_code == 422, f"{r.status_code} {r.text[:200]}"


def test_PV22_interface_with_trailing_newline_leaves_no_orphans_if_it_ever_gets_through(world):
    """Aun si la validación falla, no debe quedar peer en el hub ni dispositivo en BD."""
    b = body(wan={"interface": "ether1\n", "mode": "dhcp"})
    post(world, P, b)
    assert len(world.api.added) == len(world.api.removed), "peer huérfano en el hub"
    assert not any(d["id"] > 5000 for d in world.db.dispositivos), "dispositivo creado sin script entregable"


@pytest.mark.parametrize("addr", ["1.2.3.4/24; /system reset", "abc", "1.2.3/24", "1.2.3.4/33", "999.1.1.1/24", "1.2.3.4/24\n/x",
                                  "::1/64", "", "1.2.3.4 /24"])
def test_PV23_static_wan_hostile_address_rejected(world, addr):
    b = body(wan={"interface": "ether1", "mode": "static", "address": addr, "gateway": "1.2.3.1"})
    assert post(world, PREV, b).status_code == 422


@pytest.mark.parametrize("gw", ["1.2.3.1; x", "abc", "", None, "1.2.3.256"])
def test_PV24_static_wan_hostile_gateway_rejected(world, gw):
    b = body(wan={"interface": "ether1", "mode": "static", "address": "1.2.3.4/24", "gateway": gw})
    assert post(world, PREV, b).status_code == 422


def test_PV25_static_wan_valid_and_pppoe_and_unknown_mode(world):
    ok = body(wan={"interface": "ether1", "mode": "static", "address": "203.0.113.5/24", "gateway": "203.0.113.1"})
    r = post(world, PREV, ok)
    assert r.status_code == 200 and "203.0.113.5/24" in r.json()["script"]
    assert post(world, PREV, body(wan={"interface": "pppoe-out1", "mode": "pppoe"})).status_code == 200
    assert post(world, PREV, body(wan={"interface": "ether1", "mode": "l2tp"})).status_code == 422


@pytest.mark.parametrize("dns", [[], ["1.1.1.1"] * 4, ["abc"], ["1.1.1.1; x"], ["1.1.1"], [""], "1.1.1.1", [1], ["1.1.1.1\n/x"]])
def test_PV26_dns_validation(world, dns):
    b = body(lan={"interface": "bridge-lan", "cidr": "192.168.10.0/24", "dns": dns})
    assert post(world, PREV, b).status_code == 422


# ---------------------------------------------------------------- CIDR LAN (A4)
@pytest.mark.parametrize("cidr", ["abc", "", "192.168.10.5/24", "192.168.10.0/33", "2001:db8::/64", "8.8.8.0/24", "192.168.10.0/30",
                                  "192.168.10.0/32", "10.0.0.0/8", "192.168.0.0/15", "192.168.10.0/24; /x",
                                  "192.168.10.0", "0.0.0.0/0", "100.64.0.0/24", "192.168.10.0/-1",
                                  "172.32.0.0/16", "11.0.0.0/16"])
def test_PV30_invalid_or_public_lan_rejected(world, cidr):
    r = post(world, PREV, lan(cidr))
    assert r.status_code == 422, (cidr, r.text[:200])
    assert r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("cidr", ["10.254.0.0/24", "10.254.0.0/25", "10.254.0.128/25", "10.254.0.64/26", "10.254.0.0/16",
                                  "10.10.10.0/24", "10.10.10.128/25", "10.10.0.0/16", "10.40.0.0/18", "10.40.0.0/24",
                                  "10.40.63.0/24", "10.40.32.0/20", "10.40.0.0/16", "10.0.0.0/12"])
def test_PV31_A4_reserved_ranges_rejected_with_clear_message(world, cidr):
    r = post(world, PREV, lan(cidr))
    assert r.status_code == 422
    assert "solapa" in r.json()["detail"] or "máscara" in r.json()["detail"]


@pytest.mark.parametrize("cidr", ["10.0.0.0/16", "10.40.64.0/24", "10.254.1.0/24", "10.10.11.0/24", "10.255.0.0/24", "10.10.9.0/24",
                                  "172.16.0.0/16", "172.31.255.0/24", "192.168.255.0/24", "192.168.255.248/29"])
def test_PV32_near_miss_ranges_accepted(world, cidr):
    r = post(world, PREV, lan(cidr))
    assert r.status_code == 200, (cidr, r.text[:200])


@pytest.mark.parametrize("cidr", ["192.168.20.0/24", "192.168.20.128/25", "192.168.0.0/16", "192.168.20.0/29", "192.168.3.0/24",
                                  "192.168.2.0/23", "192.168.3.64/26"])
def test_PV33_A4_overlap_with_db_site_and_hub_peer_rejected(world, cidr):
    world.db.add_device(id=9002, nombre="otra", sede_id=10, tunnel_ip="10.254.0.30", lan_cidr="192.168.20.0/24")
    r = post(world, P, lan(cidr))
    assert r.status_code == 422 and "solapa" in r.json()["detail"]
    assert world.api.added == []


def test_PV34_adjacent_lan_is_not_an_overlap(world):
    world.db.add_device(id=9003, nombre="otra", sede_id=10, tunnel_ip="10.254.0.31", lan_cidr="192.168.20.0/24")
    assert post(world, PREV, lan("192.168.21.0/24")).status_code == 200
    assert post(world, PREV, lan("192.168.19.0/24")).status_code == 200


def test_PV35_two_sites_with_same_lan_second_one_rejected_end_to_end(world):
    assert post(world, P, lan("192.168.55.0/24")).status_code == 201
    r = post(world, P, lan("192.168.55.0/24"))
    assert r.status_code == 422 and len(world.api.added) == 1


def test_PV36_malformed_lan_cidr_in_db_does_not_break_planning(world):
    world.db.add_device(id=9004, nombre="rota", sede_id=10, tunnel_ip="10.254.0.32", lan_cidr="no-es-cidr")
    assert post(world, PREV, BODY).status_code == 200


@pytest.mark.parametrize("cidr", ["127.0.0.0/16", "169.254.0.0/16", "192.0.2.0/24", "198.18.0.0/16", "240.0.0.0/16", "192.88.99.0/24"])
def test_PV37_non_routable_private_like_ranges_rejected(world, cidr):
    """HALLAZGO esperado (bajo): ipaddress.is_private incluye loopback, link-local, TEST-NET, etc.;
    la spec/mensaje dicen 'privada (RFC 1918)'. addressing.py:23."""
    assert post(world, PREV, lan(cidr)).status_code == 422


# ---------------------------------------------------------------- gateway / DHCP
def test_PV40_gateway_outside_subnet_rejected(world):
    assert post(world, PREV, lan("192.168.10.0/24", gateway="192.168.11.1")).status_code == 422


@pytest.mark.parametrize("gw", ["abc", "192.168.10.1; x", "", "192.168.10", "192.168.10.256"])
def test_PV41_gateway_garbage_rejected_422_not_500(world, gw):
    r = post(world, PREV, lan("192.168.10.0/24", gateway=gw))
    if gw == "":
        assert r.status_code in (200, 422)       # vacío = usar valor por defecto
    else:
        assert r.status_code == 422


@pytest.mark.parametrize("gw", ["192.168.10.0", "192.168.10.255"])
def test_PV42_gateway_equal_to_network_or_broadcast_rejected(world, gw):
    """HALLAZGO esperado (medio): el gateway puede ser la dirección de red o de broadcast (provision.py:47)."""
    assert post(world, PREV, lan("192.168.10.0/24", gateway=gw)).status_code == 422


def test_PV43_dhcp_range_outside_lan_rejected(world):
    assert post(world, PREV, lan("192.168.10.0/24", dhcp_start="192.168.11.10", dhcp_end="192.168.11.20")).status_code == 422
    assert post(world, PREV, lan("192.168.10.0/24", dhcp_start="192.168.10.10", dhcp_end="192.168.11.20")).status_code == 422
    assert post(world, PREV, lan("192.168.10.0/24", dhcp_start="192.168.9.255", dhcp_end="192.168.10.20")).status_code == 422


def test_PV44_dhcp_range_reversed_rejected(world):
    """HALLAZGO esperado (medio): start > end se acepta y genera 'ranges=...20-...10' inválido en RouterOS."""
    assert post(world, PREV, lan("192.168.10.0/24", dhcp_start="192.168.10.200", dhcp_end="192.168.10.20")).status_code == 422


@pytest.mark.parametrize("start,end", [("192.168.10.0", "192.168.10.50"), ("192.168.10.10", "192.168.10.255"),
                                       ("192.168.10.1", "192.168.10.50")])
def test_PV45_dhcp_range_must_not_include_network_broadcast_or_gateway(world, start, end):
    """HALLAZGO esperado (medio): se aceptan rangos que incluyen red/broadcast/IP del router."""
    assert post(world, PREV, lan("192.168.10.0/24", dhcp_start=start, dhcp_end=end)).status_code == 422


def test_PV46_only_one_of_dhcp_start_end_is_not_silently_ignored(world):
    """HALLAZGO esperado (bajo): si solo viene uno, se ignoran ambos y se usa el rango por defecto sin avisar."""
    r = post(world, PREV, lan("192.168.10.0/24", dhcp_start="192.168.10.50"))
    assert r.status_code == 422 or r.json()["dhcp"][0] == "192.168.10.50"


@pytest.mark.parametrize("a,b", [("abc", "192.168.10.20"), ("192.168.10.10", "x;y"), ("", "")])
def test_PV47_dhcp_garbage_rejected(world, a, b):
    r = post(world, PREV, lan("192.168.10.0/24", dhcp_start=a, dhcp_end=b))
    assert r.status_code in (200, 422)         # ("","") equivale a no enviar
    if (a, b) != ("", ""):
        assert r.status_code == 422


def test_PV48_defaults_dhcp_and_gateway(world):
    j = post(world, PREV, BODY).json()
    assert j["lan_gateway"] == "192.168.10.1" and j["dhcp"] == ["192.168.10.100", "192.168.10.199"]
    j = post(world, PREV, lan("192.168.10.0/29")).json()
    assert j["lan_gateway"] == "192.168.10.1" and j["dhcp"] == ["192.168.10.2", "192.168.10.6"]


def test_PV49_wan_and_lan_same_interface_rejected(world):
    """HALLAZGO esperado (bajo): WAN y LAN con la misma interfaz se aceptan (NAT sobre la propia LAN)."""
    b = body(wan={"interface": "ether1", "mode": "dhcp"}, lan={"interface": "ether1", "cidr": "192.168.10.0/24"})
    assert post(world, PREV, b).status_code == 422


# ---------------------------------------------------------------- IP de túnel fija
@pytest.mark.parametrize("ip", ["10.254.0.0", "10.254.0.1", "10.254.0.255", "10.254.1.5", "10.0.0.5", "abc", "10.254.0.5/32",
                                "10.254.0.5; x", "10.254.0.256", "::1"])
def test_PV50_fixed_tunnel_ip_out_of_range_or_invalid_rejected(world, ip):
    r = post(world, PREV, body(tunnel_ip=ip))
    assert r.status_code == 422, ip


@pytest.mark.parametrize("ip", ["10.254.0.2", "10.254.0.254", "10.254.0.77"])
def test_PV51_fixed_tunnel_ip_valid_accepted(world, ip):
    r = post(world, PREV, body(tunnel_ip=ip))
    assert r.status_code == 200 and r.json()["tunnel_ip"] == ip


@pytest.mark.parametrize("ip,where", [("10.254.0.50", "hub-otra-interfaz"), ("10.254.0.10", "hub-wireguard1"), ("10.254.0.40", "bd")])
def test_PV52_duplicate_tunnel_ip_rejected(world, ip, where):
    world.db.add_device(id=9005, nombre="dup", sede_id=10, tunnel_ip="10.254.0.40", lan_cidr="192.168.88.0/24")
    r = post(world, P, body(tunnel_ip=ip))
    assert r.status_code == 422 and "en uso" in r.json()["detail"]
    assert world.api.added == []


def test_PV53_empty_tunnel_ip_means_auto(world):
    assert post(world, PREV, body(tunnel_ip="")).json()["tunnel_ip"] == "10.254.0.2"
    assert post(world, PREV, body(tunnel_ip=None)).json()["tunnel_ip"] == "10.254.0.2"


def test_PV54_pool_exhausted_returns_clear_422_and_creates_nothing(world):
    for i in range(2, 200):
        world.db.add_device(id=8000 + i, nombre=f"d{i}", sede_id=10, tunnel_ip=f"10.254.0.{i}", lan_cidr=None)
    r = post(world, P, BODY)
    assert r.status_code == 422 and "No quedan IPs" in r.json()["detail"], r.text
    assert world.api.added == []


def test_PV55_last_free_ip_is_used_then_exhausted(world):
    for i in range(2, 199):
        world.db.add_device(id=8000 + i, nombre=f"d{i}", sede_id=10, tunnel_ip=f"10.254.0.{i}", lan_cidr=None)
    assert post(world, P, BODY).json()["device"]["tunnel_ip"] == "10.254.0.199"       # rango de routers 2-199 (200-250 = personas)
    assert post(world, P, lan("192.168.99.0/24")).status_code == 422


# ---------------------------------------------------------------- campos obligatorios y tipos
@pytest.mark.parametrize("drop", ["sede_id", "nombre", "wan", "lan"])
def test_PV60_missing_required_fields_422(world, drop):
    b = body()
    del b[drop]
    r = post(world, P, b)
    assert r.status_code == 422
    assert any(drop in x["loc"] for x in r.json()["detail"])


@pytest.mark.parametrize("sede", ["x", None, 1.5, [], {}, "1; DROP", True])
def test_PV61_sede_id_wrong_type_422(world, sede):
    r = post(world, P, body(sede_id=sede))
    assert r.status_code in (422, 404)
    assert r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("sede", [0, -1, 999999])
def test_PV62_unknown_sede_is_404(world, sede):
    assert post(world, P, body(sede_id=sede)).status_code == 404


@pytest.mark.parametrize("raw", ["not json", "{", "[]", "null", "\"x\"", ""])
def test_PV63_malformed_body_is_json_422(world, raw):
    r = world.client.post(P, content=raw, headers={**world.auth(1), "Content-Type": "application/json"})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/json")


def test_PV64_validation_error_shape_matches_frontend_expectation(world):
    """La interfaz lee detail[].loc / msg / type (api.js parseError)."""
    r = post(world, P, body(nombre="x;y", wan={"interface": "e t", "mode": "dhcp"}))
    d = r.json()["detail"]
    assert r.status_code == 422 and isinstance(d, list)
    locs = {tuple(x["loc"]) for x in d}
    assert ("body", "nombre") in locs and ("body", "wan", "interface") in locs
    assert all({"loc", "msg", "type"} <= set(x) for x in d)


def test_PV65_extra_unknown_fields_do_not_reach_script(world):
    b = body()
    b["extra"] = "/system reset-configuration"
    b["lan"]["extra"] = "/system reset-configuration"
    r = post(world, P, b)
    assert r.status_code == 201 and "reset-configuration" not in r.json()["script"]


# ---------------------------------------------------------------- fallos del hub
def test_PV70_hub_unreachable_on_planning_is_502_json_without_secrets(world):
    from app.services.hub import HubClient
    from app.routers import provision as prov
    bad = HubClient(world.settings, connect=fakes.failing_connect)
    world.app.dependency_overrides[prov.get_hub] = lambda: bad
    for path in (PREV, P):
        r = post(world, path, BODY)
        assert r.status_code == 502 and r.headers["content-type"].startswith("application/json")
        assert "secreto-no-debe-salir" not in r.text and "10.10.10.3" not in r.text
    assert world.db.audit == [] or not any(a["accion"] == "router_aprovisionado" for a in world.db.audit)


def test_PV71_hub_rejects_add_peer_502_and_no_db_rows(world):
    world.api.fail_add = True
    n = len(world.db.dispositivos)
    r = post(world, P, BODY)
    assert r.status_code == 502 and len(world.db.dispositivos) == n


def test_PV72_hub_list_peers_timeout_is_502(world):
    world.api.fail_list = True
    assert post(world, PREV, BODY).status_code == 502


def test_PV73_hub_interface_missing_is_502(world):
    world.api.data[("interface", "wireguard")] = [{"name": "otra", "public-key": "x"}]
    assert post(world, PREV, BODY).status_code == 502


def test_PV74_hub_returns_interface_without_public_key_is_502(world):
    world.api.data[("interface", "wireguard")] = [{"name": "wireguard1"}]
    assert post(world, PREV, BODY).status_code == 502


def test_PV75_hub_config_incomplete_is_502(world, monkeypatch):
    from app import config
    from app.services.hub import HubClient
    from app.routers import provision as prov
    monkeypatch.setenv("HUB_HOST", "")
    config._settings = None
    world.app.dependency_overrides[prov.get_hub] = lambda: HubClient(config.get_settings(), connect=world.api.connect)
    assert post(world, PREV, BODY).status_code == 502


def test_PV76_error_messages_do_not_leak_internals(world):
    """Spec 3.1 / CLAUDE.md: sin nombres internos ni detalles de excepción en respuestas de API.
    HALLAZGO esperado (bajo/medio): HubError expone tipo de excepción, texto del hub, nombre de interfaz y variables HUB_*."""
    world.api.fail_add = True
    t1 = post(world, P, BODY).text
    world.api.fail_add = False
    world.api.data[("interface", "wireguard")] = [{"name": "otra", "public-key": "x"}]
    t2 = post(world, PREV, BODY).text
    for txt in (t1, t2):
        for leak in ("RuntimeError", "invalid public key", "wireguard1", "HUB_", ".env", "librouteros"):
            assert leak not in txt, f"filtración '{leak}' en: {txt[:200]}"


def test_PV77_hub_connections_are_closed(world):
    """HALLAZGO esperado (medio): HubClient._api() abre una conexión por llamada y nunca la cierra (hub.py:20-27)."""
    post(world, P, BODY)
    assert world.api.connects > 0
    assert world.api.closes == world.api.connects, f"{world.api.connects - world.api.closes} conexiones al hub sin cerrar"


def test_PV78_hub_calls_per_create_are_bounded(world):
    post(world, P, BODY)
    assert world.api.connects <= 6


# ---------------------------------------------------------------- fallo de BD -> compensación
def test_PV80_db_failure_on_connect_removes_hub_peer(world):
    world.db.fail_on = "enter"
    r = post(world, P, BODY)
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
    assert world.api.removed == [world.api.added[0][".id"]]
    assert all(p[".id"] not in [a[".id"] for a in world.api.added] for p in world.api.peers)


def test_PV81_db_failure_after_device_insert_rolls_back_both_and_removes_peer(world):
    world.db.fail_on = "wg_peers"
    n = len(world.db.dispositivos)
    r = post(world, P, BODY)
    assert r.status_code == 500
    assert len(world.db.dispositivos) == n, "el dispositivo debió revertirse"
    assert world.api.removed == [world.api.added[0][".id"]]


def test_PV82_db_failure_and_hub_removal_failure_audits_orphan_peer(world):
    world.db.fail_on = "enter"
    world.api.fail_remove = True
    r = post(world, P, BODY)
    assert r.status_code == 500
    orphan = [a for a in world.db.audit if a["accion"] == "peer_huerfano"]
    assert orphan and world.api.added[0][".id"] in orphan[0]["detalles"]


def test_PV83_error_after_compensation_is_json_without_exception_text(world):
    world.db.fail_on = "enter"
    r = post(world, P, BODY)
    assert "fallo de BD" not in r.text and r.json() == {"detail": "Error interno del servidor."}


def test_PV84_every_failure_leaves_hub_peers_equal_to_original(world):
    original = [p[".id"] for p in world.api.peers]
    for mode in ("enter", "wg_peers"):
        world.db.fail_on = mode
        post(world, P, BODY)
    assert [p[".id"] for p in world.api.peers] == original


def test_PV85_unique_violation_on_tunnel_ip_reports_conflict_not_500(world):
    """HALLAZGO esperado (medio): la carrera/duplicado detectado por la BD (índice único) devuelve 500 en lugar de 409."""
    world.db.add_device(id=9100, nombre="oculto", sede_id=10, tunnel_ip="10.254.0.2", lan_cidr="192.168.250.0/24")

    # El plan no ve el dispositivo (simula lectura anterior a su commit): se oculta de la consulta de planificación.
    real = world.db.fetch_all

    def blind(sql, params=()):
        if sql.strip().startswith("SELECT tunnel_ip FROM dispositivos"):
            return []
        return real(sql, params)

    import app.db as appdb
    appdb.fetch_all = blind
    try:
        r = post(world, P, BODY)
    finally:
        appdb.fetch_all = real
    assert r.status_code in (409, 422, 503), f"{r.status_code}: la IP duplicada debe informarse como conflicto reintentable"
    assert world.api.removed, "el peer creado debe retirarse del hub"


# ---------------------------------------------------------------- concurrencia
def _race(world, bodies, uid=1):
    world.db.barrier = threading.Barrier(len(bodies))
    out = [None] * len(bodies)

    def run(i):
        c = world.make_client()
        out[i] = c.post(P, json=bodies[i], headers=world.auth(uid))

    ts = [threading.Thread(target=run, args=(i,)) for i in range(len(bodies))]
    [t.start() for t in ts]
    [t.join(20) for t in ts]
    world.db.barrier = None
    return out


def test_PV90_concurrent_creates_never_duplicate_tunnel_ip(world):
    res = _race(world, [lan("192.168.61.0/24"), lan("192.168.62.0/24")])
    ips = [d["tunnel_ip"] for d in world.db.dispositivos if d["id"] > 5000]
    assert len(ips) == len(set(ips))
    assert any(r.status_code == 201 for r in res)


def test_PV91_concurrent_creates_leave_no_orphan_peers(world):
    original = {p[".id"] for p in world.api.peers}
    _race(world, [lan("192.168.61.0/24"), lan("192.168.62.0/24")])
    new_peers = [p for p in world.api.peers if p[".id"] not in original]
    devs = [d for d in world.db.dispositivos if d["id"] > 5000]
    assert len(new_peers) == len(devs), "peers en el hub sin dispositivo (o al revés)"
    assert {p["allowed-address"].split("/")[0] for p in new_peers} == {d["tunnel_ip"] for d in devs}


def test_PV92_concurrent_creates_all_succeed_or_fail_cleanly(world):
    """HALLAZGO esperado (medio): sin bloqueo en la asignación, la segunda petición concurrente recibe 500 en vez de
    reintentar con otra IP (A3 pide 'la siguiente libre y no se repite')."""
    res = _race(world, [lan("192.168.61.0/24"), lan("192.168.62.0/24")])
    assert all(r.status_code in (201, 409, 503) for r in res), [r.status_code for r in res]


def test_PV93_concurrent_creates_with_same_lan_only_one_wins(world):
    res = _race(world, [lan("192.168.61.0/24"), lan("192.168.61.0/24")])
    ok = [r for r in res if r.status_code == 201]
    lans = [d["lan_cidr"] for d in world.db.dispositivos if d["id"] > 5000]
    assert len(ok) <= 1, "HALLAZGO: dos sedes con la misma LAN creadas en paralelo (no hay restricción única/lock)"
    assert len(lans) == len(set(lans))


# ---------------------------------------------------------------- sede name -> script (inyección alcanzable)
@pytest.mark.parametrize("name", ["Sede\n/system reset-configuration", "Sede\r\n/user remove [find]", "Sede \"x\" ; y"])
def test_PV95_hostile_sede_name_cannot_inject_commands_into_script(world, name):
    """HALLAZGO esperado (ALTO): SedeCreate.nombre no se valida y render_provision lo escribe sin q() en la línea
    '# Sede: ...' (routeros_scripts.py:55). Un salto de línea inyecta una línea de comando RouterOS ejecutable."""
    r = world.client.post("/api/v1/sedes", json={"nombre": name, "ciudad": "Cali"}, headers=world.auth(3))
    if r.status_code != 201:
        return                                   # rechazar la sede también es una corrección válida
    sid = r.json()["id"]
    pv = world.client.post(PREV, json={**BODY, "sede_id": sid}, headers=world.auth(3))
    script = pv.json().get("script", "") if pv.status_code == 200 else ""
    for line in script.splitlines():
        assert not line.startswith(("/system", "/user")) or line.startswith("/user add") or line.startswith("/user group"), line
    assert "reset-configuration" not in "".join(l for l in script.splitlines() if not l.startswith("#")), script[:300]
    assert "/user remove" not in script


def test_PV96_hostile_sede_name_does_not_reach_hub_comment_unbounded(world):
    long = "S" * 150
    sid = world.client.post("/api/v1/sedes", json={"nombre": long, "ciudad": "Cali"}, headers=world.auth(3)).json()["id"]
    post(world, P, {**BODY, "sede_id": sid}, uid=3)
    assert len(world.api.added[0]["comment"]) <= 120













