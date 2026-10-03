import ipaddress

import pytest
from pydantic import ValidationError

from app.schemas import LanConfig, ProvisionRequest, WanConfig
from app.services import routeros_scripts as rs

HUB_KEY = "4u9Wu/zFGuHL8hc9UmL9MxWenkPC5R9XP2jyhHLfuGM="


def data(**over):
    base = dict(
        site_name="Sede Prueba", device_name="MK Oficina", tunnel_ip="10.254.0.10",
        wg_private_key="cHJpdmF0ZV9rZXlfZGUgcHJ1ZWJhIDMyIGJ5dGVzISE=", hub_public_key=HUB_KEY,
        hub_endpoint="203.0.113.5", hub_port=51820, wan_interface="ether1", wan_mode="dhcp",
        wan_address=None, wan_gateway=None, lan_interface="bridge-lan",
        lan_network=ipaddress.ip_network("192.168.10.0/24"), lan_gateway="192.168.10.1",
        dhcp_start="192.168.10.100", dhcp_end="192.168.10.199", dns=["1.1.1.1", "8.8.8.8"],
        api_user="avz-api", api_password="PwAleatoria123456",
    )
    base.update(over)
    return rs.ProvisionData(**base)


def balanced(text):
    code = [l for l in text.splitlines() if not l.startswith("#")]
    assert sum(l.count("{") for l in code) == sum(l.count("}") for l in code)
    assert sum(l.count("[") for l in code) == sum(l.count("]") for l in code)
    for line in code:
        assert line.count('"') % 2 == 0, line


def test_script_contains_real_hub_key_and_tunnel():
    s = rs.render_provision(data())
    assert HUB_KEY in s
    assert "cHVibGljX2tleV9hdnpjbG91ZF9nYXRld2F5XzIwMjZfbWs=" not in s   # la clave ficticia antigua
    assert 'address="10.254.0.10/24"' in s
    assert "allowed-address=10.254.0.0/24,10.10.10.4/32" in s
    assert "endpoint-address=203.0.113.5 endpoint-port=51820" in s
    balanced(s)


def test_preflight_aborts_before_any_change():
    s = rs.render_provision(data())
    assert s.index(":error") < s.index("/ip dhcp-client")
    assert "no existe la interfaz WAN ether1" in s and "no existe la interfaz LAN bridge-lan" in s


def test_wan_modes():
    assert "/ip dhcp-client add" in rs.render_provision(data(wan_mode="dhcp"))
    st = rs.render_provision(data(wan_mode="static", wan_address="200.1.1.2/29", wan_gateway="200.1.1.1"))
    assert "200.1.1.2/29" in st and "gateway=200.1.1.1" in st and "/ip dhcp-client add" not in st
    pp = rs.render_provision(data(wan_mode="pppoe", wan_interface="pppoe-out1"))
    assert "se asume ya configurada" in pp and "/ip dhcp-client add" not in pp
    for s in (st, pp):
        balanced(s)


def test_lan_dhcp_and_nat():
    s = rs.render_provision(data())
    assert 'ranges=192.168.10.100-192.168.10.199' in s
    assert 'address="192.168.10.1/24" interface="bridge-lan"' in s
    assert 'dns-server=1.1.1.1,8.8.8.8' in s
    assert "action=masquerade out-interface=\"ether1\"" in s


def test_no_open_dns_resolver_and_least_privilege_api():
    s = rs.render_provision(data())
    assert "allow-remote-requests=yes" not in s
    assert "!policy" in s and "!ssh" in s and "!reboot" in s
    assert "address=10.10.10.4/32,10.254.0.1/32" in s


def test_mgmt_rule_accepts_hub_masqueraded_source():
    # El hub enmascara la gestión: el router ve 10.254.0.1; la regla debe cubrir ese origen y 10.10.10.4.
    s = rs.render_provision(data())
    assert 'list="AVZ_MGMT" address=10.254.0.1' in s and 'list="AVZ_MGMT" address=10.10.10.4' in s
    assert 'src-address-list="AVZ_MGMT" comment="AVZCloud mgmt"' in s
    assert "src-address=10.10.10.4 comment=" not in s
    balanced(s)


def test_script_is_idempotent_guarded():
    s = rs.render_provision(data())
    for key in ("/interface wireguard add", "/interface wireguard peers add", "/ip pool add", "/ip dhcp-server add"):
        line = next(l for l in s.splitlines() if key in l)
        assert line.startswith(":if ([:len ["), line


@pytest.mark.parametrize("bad", ['x"; /system reset-configuration; "', "a\nb", "ether1]", "ether$1"])
def test_injection_rejected_in_script_values(bad):
    with pytest.raises(ValueError):
        rs.q(bad)


@pytest.mark.parametrize("bad", ['ether1"; /x', "a b", "ether1;", "", "-ether", "x" * 40])
def test_injection_rejected_in_interface_names(bad):
    with pytest.raises(ValidationError):
        WanConfig(interface=bad)
    with pytest.raises(ValidationError):
        LanConfig(interface=bad, cidr="192.168.10.0/24")


def test_static_wan_needs_address_and_gateway():
    with pytest.raises(ValidationError):
        WanConfig(interface="ether1", mode="static")
    assert WanConfig(interface="ether1", mode="static", address="200.1.1.2/29", gateway="200.1.1.1")


def test_device_name_validation():
    ok = dict(sede_id=1, wan={"interface": "ether1"}, lan={"interface": "bridge", "cidr": "192.168.10.0/24"})
    assert ProvisionRequest(nombre="MK Oficina", **ok)
    with pytest.raises(ValidationError):
        ProvisionRequest(nombre='x"; /system reboot', **ok)


def test_dns_must_be_ipv4():
    with pytest.raises(ValidationError):
        LanConfig(interface="bridge", cidr="192.168.10.0/24", dns=["no-es-ip"])


def test_newline_in_site_name_cannot_inject_commands():
    # SEG-001: un nombre de sede con salto de línea no debe producir líneas de comando adicionales.
    evil = "Sede\n/user add name=evil group=full password=x"
    s = rs.render_provision(data(site_name=evil, device_name="MK\r\n/system reset-configuration"))
    assert "/user add name=evil" not in s and "reset-configuration" not in s.replace("-", " ").replace("reset configuration", "")
    for line in s.splitlines():
        assert not line.startswith("/user add name=evil") and not line.startswith("/system reset")
    balanced(s)


@pytest.mark.parametrize("bad", ["ether1\n", "ether1\n/user add name=x"])
def test_trailing_newline_in_interface_name_rejected(bad):
    # SEG-010: `$` aceptaba un salto de línea final; ahora se valida con fullmatch.
    with pytest.raises(ValidationError):
        WanConfig(interface=bad)
    with pytest.raises(ValidationError):
        ProvisionRequest(sede_id=1, nombre="MK\nOficina", wan={"interface": "ether1"},
                         lan={"interface": "bridge", "cidr": "192.168.10.0/24"})
