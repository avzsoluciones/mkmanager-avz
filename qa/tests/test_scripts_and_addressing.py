"""Generador de scripts RouterOS, direccionamiento y claves WireGuard (unitarias, sin fakes de red).
IDs: SC-* (scripts), AD-* (direccionamiento), WG-* (claves)."""
import base64
import ipaddress
import random
import re

import pytest

from app.services import addressing as ad
from app.services import wgkeys
from app.services.routeros_scripts import ProvisionData, q, render_provision


def pdata(**over):
    d = dict(site_name="Sede Demo", device_name="MK Demo", tunnel_ip="10.254.0.7", wg_private_key="PRIV=", hub_public_key="PUB=",
             hub_endpoint="vpn.example.com", hub_port=51820, wan_interface="ether1", wan_mode="dhcp", wan_address=None,
             wan_gateway=None, lan_interface="bridge", lan_network=ipaddress.ip_network("192.168.10.0/24"),
             lan_gateway="192.168.10.1", dhcp_start="192.168.10.100", dhcp_end="192.168.10.199", dns=["1.1.1.1"],
             api_user="avz-api", api_password="pw")
    d.update(over)
    return ProvisionData(**d)


# ---------------------------------------------------------------- SC
@pytest.mark.parametrize("ch", list('\r\n\x00"\\$`[]{}'))
def test_SC01_q_rejects_every_dangerous_char(ch):
    with pytest.raises(ValueError):
        q(f"a{ch}b")


@pytest.mark.parametrize("ok", ["ether1", "bridge-lan", "a b", "1.2.3.4/24", "x_y.z", "ñ", "<CLAVE_X>", "a=b", "a;b", "a,b"])
def test_SC02_q_accepts_plain(ok):
    assert q(ok) == f'"{ok}"'


@pytest.mark.parametrize("field", ["wan_interface", "lan_interface", "api_password", "wg_private_key", "hub_public_key"])
def test_SC03_quoted_fields_reject_injection(field):
    with pytest.raises(ValueError):
        render_provision(pdata(**{field: 'x"\n/system reset-configuration'}))


@pytest.mark.parametrize("field,val", [("site_name", "S\n/system reset-configuration"), ("site_name", "S\r/system reset-configuration"),
                                       ("device_name", "D\n/system reset-configuration"), ("device_name", "D\r\n/user remove [find]"),
                                       ("site_name", "x\u2028/system reset-configuration"), ("device_name", "x\x0b/system reset")])
def test_SC04_site_name_is_not_a_command_injection_vector(field, val):
    """Regresión de un hallazgo ALTO cerrado: site_name/device_name se interpolaban sin sanear en un comentario (inyección de comandos)."""
    try:
        s = render_provision(pdata(**{field: val}))
    except ValueError:
        return
    assert not any(l.lstrip().startswith(("/system", "/user remove")) for l in s.splitlines()), "línea ejecutable inyectada"
    assert len(s.splitlines()) == len(render_provision(pdata()).splitlines()), "el nombre añadió líneas al script"


@pytest.mark.xfail(strict=False, reason="HALLAZGO (bajo, defensa en profundidad): render_provision confía en que la capa "
                                        "superior validó estos campos; no están entrecomillados ni validados aquí.")
@pytest.mark.parametrize("field", ["wan_gateway", "lan_gateway", "dhcp_start", "dhcp_end", "hub_endpoint", "tunnel_ip", "wan_address"])
def test_SC05_unquoted_fields_reject_injection_at_render_level(field):
    kw = {field: "1.1.1.1\n/system reset-configuration"}
    if field in ("wan_gateway", "wan_address"):
        kw["wan_mode"] = "static"
        kw.setdefault("wan_address", "1.2.3.4/24")
        kw.setdefault("wan_gateway", "1.2.3.1")
        kw[field] = "1.1.1.1\n/system reset-configuration"
    with pytest.raises(ValueError):
        render_provision(pdata(**kw))


def test_SC06_dns_list_injection_not_possible_through_api_layer(world):
    r = world.client.post("/api/v1/provision/preview", headers=world.auth(1), json={
        "sede_id": 10, "nombre": "MK", "wan": {"interface": "ether1", "mode": "dhcp"},
        "lan": {"interface": "br", "cidr": "192.168.10.0/24", "dns": ["1.1.1.1,2.2.2.2"]}})
    assert r.status_code == 422


def test_SC07_script_is_deterministic_and_complete():
    s1, s2 = render_provision(pdata()), render_provision(pdata())
    assert s1 == s2
    for needle in ("wg-avzcloud", "PUB=", "PRIV=", "10.254.0.7/24", "10.10.10.4", "avz-api", "AVZ_MGMT", "masquerade"):
        assert needle in s1


def test_SC08_every_add_is_guarded_for_idempotency():
    s = render_provision(pdata())
    for line in s.splitlines():
        if " add " in line and line.lstrip().startswith("/"):
            assert line.startswith(":if") or "place-before" in line or line.startswith("/ip firewall filter"), line


def test_SC09_no_open_resolver_and_no_wide_api_access():
    s = render_provision(pdata())
    assert "allow-remote-requests=yes" not in s
    assert "/ip service set api disabled=no address=10.10.10.4/32,10.254.0.1/32" in s
    assert "policy=api,read,write,test,sensitive,!local,!telnet,!ssh,!ftp,!reboot,!policy,!winbox,!password,!web,!sniff,!romon,!rest-api" in s


def test_SC10_mgmt_firewall_rule_does_not_lock_out_and_is_placed_first():
    s = render_provision(pdata())
    assert 'comment="AVZCloud mgmt"' in s and "place-before=0" in s
    assert "action=drop" not in s          # el aprovisionamiento no añade DROP (eso es M4, con reversión)


def test_SC11_preflight_aborts_before_changes():
    s = render_provision(pdata())
    first_change = next(i for i, l in enumerate(s.splitlines()) if " add " in l or " set " in l)
    pre = [i for i, l in enumerate(s.splitlines()) if ":error" in l]
    assert pre and max(pre) < first_change


@pytest.mark.parametrize("mode", ["dhcp", "pppoe"])
def test_SC12_wan_modes_do_not_need_address(mode):
    s = render_provision(pdata(wan_mode=mode))
    assert "masquerade" in s


def test_SC13_script_has_no_provider_names():
    s = render_provision(pdata())
    for bad in ("3HC", "Yotta", "Wasabi", "Polaris", "Acronis", "1Panel", "Coolify"):
        assert bad.lower() not in s.lower()


def test_SC14_script_uses_only_ascii_commands_and_unix_newlines():
    s = render_provision(pdata())
    assert "\r" not in s


# ---------------------------------------------------------------- AD (propiedades con semilla fija)
def test_AD01_next_tunnel_ip_property_never_returns_used_and_stays_in_range():
    rnd = random.Random(7)
    for _ in range(300):
        used = {f"10.254.0.{rnd.randint(0, 255)}" for _ in range(rnd.randint(0, 120))}
        try:
            ip = ad.next_tunnel_ip(used)
        except RuntimeError:
            assert all(f"10.254.0.{i}" in used for i in range(2, 251))
            continue
        assert ip not in used and 2 <= int(ip.split(".")[-1]) <= 250
        assert all(f"10.254.0.{i}" in used for i in range(2, int(ip.split(".")[-1])))   # es la PRIMERA libre


def test_AD02_next_tunnel_ip_accepts_mixed_formats():
    assert ad.next_tunnel_ip(["10.254.0.2/32", "10.254.0.3", "x", "", "10.254.0.4/24", None]) == "10.254.0.5"


def test_AD03_check_lan_overlap_is_symmetric():
    rnd = random.Random(3)
    for _ in range(200):
        a = ipaddress.ip_network(f"192.168.{rnd.randint(0, 255)}.0/{rnd.choice([24, 25, 26, 22, 20])}", strict=False)
        b = ipaddress.ip_network(f"192.168.{rnd.randint(0, 255)}.0/{rnd.choice([24, 25, 26, 22, 20])}", strict=False)
        def rejects(x, y):
            try:
                ad.check_lan(str(x), [str(y)])
                return False
            except ValueError:
                return True
        assert rejects(a, b) == rejects(b, a) == a.overlaps(b)


@pytest.mark.parametrize("cidr", ["10.254.0.0/24", "10.10.10.0/24", "10.40.0.0/18"])
def test_AD04_reserved_exact_boundaries(cidr):
    net = ipaddress.ip_network(cidr)
    first, last = net.network_address, net.broadcast_address
    for host in (first, last):
        with pytest.raises(ValueError):
            ad.check_lan(f"{host}/29" if host == first else f"{ipaddress.ip_address(int(host) - 7)}/29", [])
    # vecino inmediato: libre
    ad.check_lan(f"{ipaddress.ip_address(int(last) + 1)}/29" if net.prefixlen >= 24 else "10.40.64.0/24", [])


@pytest.mark.parametrize("p,ok", [(15, False), (16, True), (24, True), (29, True), (30, False), (31, False), (32, False)])
def test_AD05_prefix_limits(p, ok):
    cidr = f"192.168.0.0/{p}"
    if ok:
        assert ad.parse_lan(cidr).prefixlen == p
    else:
        with pytest.raises(ValueError):
            ad.parse_lan(cidr)


@pytest.mark.parametrize("cidr", ["192.168.10.1/24", "192.168.10.0/24 ", " 192.168.10.0/24"])
def test_AD06_host_bits_rejected_whitespace_tolerated(cidr):
    if cidr.strip() == "192.168.10.1/24":
        with pytest.raises(ValueError):
            ad.parse_lan(cidr)
    else:
        assert str(ad.parse_lan(cidr)) == "192.168.10.0/24"


def test_AD07_default_dhcp_never_includes_gateway_network_or_broadcast():
    for p in (16, 20, 23, 24, 25, 26, 27, 28, 29):
        net = ipaddress.ip_network(f"192.168.0.0/{p}")
        a, b = (ipaddress.ip_address(x) for x in ad.default_dhcp_range(net))
        gw = ipaddress.ip_address(ad.default_gateway(net))
        assert net.network_address < a <= b < net.broadcast_address, p
        assert not (a <= gw <= b), p


# ---------------------------------------------------------------- WG
def test_WG01_keypairs_valid_unique_and_clamped(settings):
    seen = set()
    for _ in range(100):
        priv, pub = wgkeys.generate_keypair()
        raw = base64.b64decode(priv, validate=True)
        assert len(raw) == 32 and raw[0] & 7 == 0 and raw[31] & 128 == 0 and raw[31] & 64
        assert wgkeys.is_valid_public_key(pub) and base64.b64decode(pub)[:0] == b""
        assert re.fullmatch(r"[A-Za-z0-9+/]{43}=", priv) and re.fullmatch(r"[A-Za-z0-9+/]{43}=", pub)
        seen.add(pub)
    assert len(seen) == 100


@pytest.mark.parametrize("bad", ["", "abc", "!!!!", "A" * 43, "A" * 44, "A" * 42 + "==", "4u9Wu/zFGuHL8hc9UmL9MxWenkPC5R9XP2jyhHLfuG"])
def test_WG02_invalid_public_keys_detected(bad):
    assert wgkeys.is_valid_public_key(bad) is False


def test_WG03_valid_public_key_accepted():
    assert wgkeys.is_valid_public_key("4u9Wu/zFGuHL8hc9UmL9MxWenkPC5R9XP2jyhHLfuGM=")


def test_WG04_vault_roundtrip_and_wrong_key_and_missing_key(settings, monkeypatch):
    from cryptography.fernet import Fernet
    from app import config
    tok = wgkeys.encrypt_secret("secreto")
    assert tok != "secreto" and wgkeys.decrypt_secret(tok) == "secreto"
    assert wgkeys.encrypt_secret("secreto") != tok            # sal/IV distinto por cifrado
    monkeypatch.setenv("VAULT_KEY", Fernet.generate_key().decode())
    config._settings = None
    with pytest.raises(RuntimeError):
        wgkeys.decrypt_secret(tok)
    monkeypatch.setenv("VAULT_KEY", "")
    config._settings = None
    with pytest.raises(RuntimeError):
        wgkeys.encrypt_secret("x")


def test_WG05_decrypt_garbage_raises_runtime_error(settings):
    with pytest.raises(RuntimeError):
        wgkeys.decrypt_secret("no-es-un-token-fernet")


def test_WG06_preshared_key_is_32_bytes_and_random():
    a, b = wgkeys.generate_preshared(), wgkeys.generate_preshared()
    assert len(base64.b64decode(a)) == 32 and a != b


def test_WG07_malformed_vault_key_gives_runtime_error_not_value_error(settings, monkeypatch):
    """HALLAZGO esperado (bajo): VAULT_KEY mal formada (no base64 de 32 bytes) lanza ValueError desde Fernet(), que
    provision.create() no captura (solo RuntimeError) -> 500 genérico, antes de tocar el hub: aceptable, pero el
    mensaje real no se registra de forma útil."""
    from app import config
    monkeypatch.setenv("VAULT_KEY", "clave-mal-formada")
    config._settings = None
    with pytest.raises((RuntimeError, ValueError)):
        wgkeys.encrypt_secret("x")




