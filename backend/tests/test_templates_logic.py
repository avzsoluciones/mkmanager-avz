"""Plantillas: operaciones, validación por tipo de campo, inyección y reglas de gestión (funciones puras)."""
import pytest

from app.services import templates as T
from app.services.ops import (
    ADDRESS_LIST, FILTER, NAT, PLACE_AFTER_MGMT, PREVIEW_TAG, Op, OpError, assert_safe, render_cli, stamp, tag_for,
)

CTX = T.DeviceCtx(wan_interface="ether1", wan_mode="dhcp", lan_interface="bridge-lan", lan_cidr="192.168.10.0/24",
                  gateway="192.168.10.1", tunnel_ip="10.254.0.10")

HOSTILE = ['x"; /system reset-configuration', "a\nb", "a\r\nb", "1.1.1.1\n", "$(x)", "[/user print]", "`id`", "a\\b", "{x}", "\x00"]


def plan(tid, params, ctx=CTX):
    return T.plan(T.CATALOG[tid], params, ctx)


def errors(tid, params, ctx=CTX):
    with pytest.raises(T.TemplateError) as e:
        plan(tid, params, ctx)
    return dict((f or "_", m) for f, m in e.value.errors)


def stamped(ops, app=7):
    s = stamp(ops, tag_for(app))
    assert_safe(s)
    return s


# ---------------------------------------------------------------- catálogo
def test_catalog_has_the_five_templates_with_expected_contract():
    cat = {t.id: t for t in T.CATALOG.values()}
    assert set(cat) == {"firewall-seguro", "port-forward", "vlan", "limite-ancho-banda", "dns-ntp"}
    assert cat["firewall-seguro"].riesgo == "alto" and cat["firewall-seguro"].categoria == "Seguridad"
    assert (cat["port-forward"].riesgo, cat["vlan"].riesgo) == ("medio", "medio")
    assert (cat["limite-ancho-banda"].riesgo, cat["dns-ntp"].riesgo) == ("bajo", "bajo")
    assert cat["dns-ntp"].revierte is False and cat["vlan"].revierte is True
    for t in cat.values():
        pub = t.public()
        assert set(pub) == {"id", "nombre", "descripcion", "categoria", "icono", "riesgo", "revierte", "campos"}
        assert all({"name", "label", "type", "required"} <= set(c) for c in pub["campos"])
        assert {c["type"] for c in pub["campos"]} <= {"text", "number", "ip", "cidr", "list", "bool", "select"}


# ---------------------------------------------------------------- Op: construcción y seguridad
@pytest.mark.parametrize("bad", HOSTILE)
def test_op_rejects_hostile_values(bad):
    with pytest.raises(OpError):
        Op(NAT, "add", {"to-addresses": bad})


def test_op_rejects_bad_paths_keys_and_shapes():
    for kwargs in (
        dict(path=("ip", "firewall;x"), action="add", attrs={"a": "b"}),
        dict(path=("IP",), action="add", attrs={"a": "b"}),
        dict(path=(), action="add", attrs={"a": "b"}),
        dict(path=NAT, action="add", attrs={"a b": "c"}),
        dict(path=NAT, action="add", attrs={"place-before": "*1"}),
        dict(path=NAT, action="drop", attrs={"a": "b"}),
        dict(path=NAT, action="remove", attrs={}),                       # borrar sin criterio
        dict(path=NAT, action="add", attrs={"a": "b"}, place="top"),
    ):
        with pytest.raises(OpError):
            Op(**kwargs)


def test_assert_safe_protects_the_management_rule_and_list():
    ok = Op(FILTER, "add", {"chain": "input", "action": "accept", "comment": "AVZ-APP-1"}, place=PLACE_AFTER_MGMT)
    assert_safe([ok])
    danger = [
        Op(FILTER, "add", {"chain": "input", "action": "drop", "comment": "AVZ-APP-1"}),                          # sin posición segura
        Op(FILTER, "add", {"chain": "input", "action": "drop", "src-address-list": "AVZ_MGMT", "comment": "AVZ-APP-1"},
           place=PLACE_AFTER_MGMT),                                                                                # bloquear AVZ_MGMT
        Op(FILTER, "add", {"chain": "input", "action": "accept", "comment": "AVZCloud mgmt"}, place=PLACE_AFTER_MGMT),
        Op(FILTER, "remove", {}, {"comment": "AVZCloud mgmt"}),                                                    # quitar la regla
        Op(FILTER, "remove", {}, {"chain": "input"}),                                                              # borrado ajeno
        Op(FILTER, "set", {"disabled": "yes"}, {"comment": "AVZCloud mgmt"}),
        Op(ADDRESS_LIST, "add", {"list": "AVZ_MGMT", "address": "1.2.3.4", "comment": "AVZ-APP-1"}),
        Op(ADDRESS_LIST, "remove", {}, {"list": "AVZ_MGMT", "comment": "AVZ-APP-1"}),
        Op(("user",), "add", {"name": "x", "comment": "AVZ-APP-1"}),
        Op(("ip", "service"), "set", {"disabled": "yes"}),
        Op(("interface", "wireguard"), "add", {"name": "x", "comment": "AVZ-APP-1"}),
        Op(("ip", "dns"), "set", {"allow-remote-requests": "yes"}),
        Op(("ip", "address"), "add", {"address": "1.1.1.1/24", "interface": "wg-avzcloud", "comment": "AVZ-APP-1"}),
        Op(("ip", "route"), "add", {"dst-address": "0.0.0.0/0", "comment": "AVZ-APP-1"}),
        Op(NAT, "add", {"chain": "dstnat"}),                                                                       # sin marca AVZ-APP
    ]
    for op in danger:
        with pytest.raises(OpError):
            assert_safe([op])


def test_render_cli_quotes_orders_and_marks_placement():
    ops = stamped(plan("firewall-seguro", {"ips_admin": ["203.0.113.0/24"]})[1], app=9)
    text = render_cli(ops, tag_for(9))
    lines = text.splitlines()
    assert 'comment=AVZ-APP-9' in text or 'comment="AVZ-APP-9"' in text
    nums = [int(l.split("place-before=")[1].split()[0]) for l in lines if "/ip firewall filter add" in l]
    assert nums == list(range(1, len(nums) + 1))                       # el orden de las reglas nuevas se conserva
    assert "AVZCloud mgmt" in text and "address-list add" in text
    assert render_cli(stamp(plan("dns-ntp", {"dns": ["1.1.1.1"]})[1], PREVIEW_TAG)).count("/ip dns set") == 1
    assert "\n\n" not in text


# ---------------------------------------------------------------- validación por tipo de campo
@pytest.mark.parametrize("bad", HOSTILE + ["no-es-red", "300.1.1.1/24", "0.0.0.0/0", "10.0.0.0/4", 5, None])
def test_firewall_admin_list_rejects_bad_items(bad):
    errors("firewall-seguro", {"ips_admin": [bad]})


def test_firewall_validation_and_normalisation():
    params, ops, avisos = plan("firewall-seguro", {"ips_admin": ["203.0.113.7", "203.0.113.7/32", "198.51.100.5/24"]})
    assert params["ips_admin"] == ["203.0.113.7/32", "198.51.100.0/24"]
    assert params["permitir_ping"] is True
    assert errors("firewall-seguro", {"ips_admin": ["1.1.1.1/32"] * 21}).get("ips_admin")
    assert errors("firewall-seguro", {"permitir_ping": "si"}).get("permitir_ping")
    assert errors("firewall-seguro", {"otra": 1}).get("otra") == "Campo no reconocido."
    assert errors("firewall-seguro", "texto")["_"]
    no_wan = T.DeviceCtx(lan_cidr="192.168.10.0/24")
    assert "WAN" in errors("firewall-seguro", {}, no_wan)["_"]


@pytest.mark.parametrize("field,value", [
    ("puerto_externo", 0), ("puerto_externo", 65536), ("puerto_externo", "80"), ("puerto_externo", True),
    ("puerto_externo", 80.5), ("puerto_destino", 0), ("puerto_destino", -1), ("protocolo", "icmp"), ("protocolo", "tcp\n"),
    ("ip_destino", "192.168.10.20\n"), ("ip_destino", "192.168.10.256"), ("ip_destino", "192.168.10.0/24"),
    ("ip_destino", 'x"y'), ("ip_destino", "1.1.1"),
])
def test_port_forward_field_types(field, value):
    base = {"protocolo": "tcp", "puerto_externo": 8080, "ip_destino": "192.168.10.20", "puerto_destino": 80}
    base[field] = value
    assert field in errors("port-forward", base)


def test_port_forward_lan_and_management_rules():
    base = {"protocolo": "tcp", "puerto_externo": 8080, "ip_destino": "192.168.10.20", "puerto_destino": 80}
    params, ops, _ = plan("port-forward", base)
    for port in (22, 23, 8291, 8728, 8729, 13231):
        assert "puerto_externo" in errors("port-forward", {**base, "puerto_externo": port})
    for ip in ("192.168.11.20", "10.254.0.5", "192.168.10.0", "192.168.10.255", "192.168.10.1", "8.8.8.8"):
        assert "ip_destino" in errors("port-forward", {**base, "ip_destino": ip}), ip
    assert set(errors("port-forward", {})) == {"protocolo", "puerto_externo", "ip_destino", "puerto_destino"}


def test_vlan_validation_uses_addressing_rules():
    base = {"vlan_id": 20, "interfaz": "bridge-lan", "subred": "192.168.50.0/24"}
    assert plan("vlan", base)[0]["dhcp"] is True
    assert "subred" in errors("vlan", {**base, "subred": "192.168.10.0/24"})            # LAN del equipo
    assert "subred" in errors("vlan", {**base, "subred": "192.168.10.128/25"})
    for reserved in ("10.254.0.0/24", "10.10.10.0/24", "10.40.1.0/24"):
        assert "subred" in errors("vlan", {**base, "subred": reserved}), reserved
    assert "subred" in errors("vlan", {**base, "subred": "8.8.8.0/24"})                  # pública
    assert "subred" in errors("vlan", {**base, "subred": "192.168.50.5/24"})             # con bits de host
    for vid in (0, 1, 4095, "20", 20.5, True):
        assert "vlan_id" in errors("vlan", {**base, "vlan_id": vid})
    assert "interfaz" in errors("vlan", {**base, "interfaz": "ether1"})                  # la WAN
    assert "interfaz" in errors("vlan", {**base, "interfaz": "wg-avzcloud"})
    for bad in HOSTILE:
        assert "interfaz" in errors("vlan", {**base, "interfaz": bad})
    assert "dhcp" in errors("vlan", {**base, "dhcp": "yes"})


def test_limite_validation():
    base = {"objetivo": "192.168.10.0/24", "bajada": 20, "subida": 5}
    assert plan("limite-ancho-banda", {**base, "objetivo": "192.168.10.50"})[0]["objetivo"] == "192.168.10.50/32"
    for bad in ("8.8.8.8", "10.254.0.0/24", "10.0.0.0/8", "no", 'a"b'):
        assert "objetivo" in errors("limite-ancho-banda", {**base, "objetivo": bad}), bad
    for field in ("bajada", "subida"):
        for v in (0, -5, 10001, "5", 5.5, None):
            assert field in errors("limite-ancho-banda", {**base, field: v})


def test_dns_ntp_validation():
    assert plan("dns-ntp", {"dns": ["1.1.1.1", "8.8.8.8", "1.1.1.1"]})[0]["dns"] == ["1.1.1.1", "8.8.8.8"]
    for dns in ([], ["1.1.1.1"] * 0, ["1.1.1.1", "8.8.8.8", "9.9.9.9", "4.4.4.4"], ["127.0.0.1"], ["0.0.0.0"], ["224.0.0.1"],
                ["1.1.1.1\n"], ["dns.google"], "1.1.1.1"):
        assert "dns" in errors("dns-ntp", {"dns": dns}), dns
    for ntp in ('x"; /system reset', "a b", "-bad.com", "a" * 101, "pool..ntp.org", "pool.ntp.org\n", 5):
        assert "ntp" in errors("dns-ntp", {"dns": ["1.1.1.1"], "ntp": ntp}), ntp
    assert plan("dns-ntp", {"dns": ["1.1.1.1"], "ntp": None})[1][-1].path == ("ip", "dns")       # sin NTP no se toca el reloj
    assert "dns" in errors("dns-ntp", {})


# ---------------------------------------------------------------- operaciones esperadas por plantilla
def test_firewall_ops_order_and_content():
    params, ops, avisos = plan("firewall-seguro", {"ips_admin": ["203.0.113.0/24"], "permitir_ping": True})
    rules = [o for o in ops if o.path == FILTER]
    assert all(o.place == PLACE_AFTER_MGMT for o in rules)
    assert [o.path for o in ops[:1]] == [ADDRESS_LIST] and ops[0].attrs == {"list": "AVZ_ADMIN", "address": "203.0.113.0/24"}
    inp = [o.attrs for o in rules if o.attrs["chain"] == "input"]
    fwd = [o.attrs for o in rules if o.attrs["chain"] == "forward"]
    assert inp[0]["connection-state"] == "established,related" and inp[0]["action"] == "accept"
    assert inp[1] == {"chain": "input", "action": "drop", "connection-state": "invalid"}
    assert any(a.get("protocol") == "icmp" and "limit" in a for a in inp)
    assert any(a.get("in-interface") == "bridge-lan" and a["action"] == "accept" for a in inp)
    assert any(a.get("in-interface") == "wg-avzcloud" and a["action"] == "accept" for a in inp)
    assert any(a.get("dst-port") == "13231" and a["protocol"] == "udp" for a in inp)               # el túnel no se corta
    assert any(a.get("src-address-list") == "AVZ_ADMIN" and a["dst-port"] == "8291,22" for a in inp)
    assert inp[-1] == {"chain": "input", "action": "drop", "in-interface": "ether1"}                # el drop va al final
    assert fwd[0]["connection-state"] == "established,related"
    assert fwd[1]["action"] == "drop" and fwd[1]["connection-state"] == "invalid"
    assert fwd[-1] == {"chain": "forward", "action": "drop", "in-interface": "ether1", "connection-nat-state": "!dstnat"}
    assert avisos


def test_firewall_optional_pieces_are_omitted():
    _, ops, avisos = plan("firewall-seguro", {"permitir_ping": False})
    attrs = [o.attrs for o in ops]
    assert not any(a.get("protocol") == "icmp" for a in attrs)
    assert not any(o.path == ADDRESS_LIST for o in ops) and not any("src-address-list" in a for a in attrs)
    assert any("administración" in a for a in avisos)
    _, ops2, _ = plan("firewall-seguro", {}, T.DeviceCtx(wan_interface="ether1", lan_cidr="192.168.10.0/24"))
    assert not any(o.attrs.get("in-interface") == "bridge-lan" for o in ops2)


def test_port_forward_ops():
    _, ops, avisos = plan("port-forward", {"protocolo": "udp", "puerto_externo": 3389, "ip_destino": "192.168.10.20", "puerto_destino": 3389})
    assert ops == [Op(NAT, "add", {"chain": "dstnat", "action": "dst-nat", "protocol": "udp", "dst-port": "3389",
                                   "in-interface": "ether1", "to-addresses": "192.168.10.20", "to-ports": "3389"})]
    assert avisos and "peligroso" in avisos[0]


def test_vlan_ops_with_and_without_dhcp():
    _, ops, _ = plan("vlan", {"vlan_id": 20, "interfaz": "bridge-lan", "subred": "192.168.50.0/24"})
    assert [o.path for o in ops] == [("interface", "vlan"), ("ip", "address"), ("ip", "pool"),
                                     ("ip", "dhcp-server", "network"), ("ip", "dhcp-server")]
    assert ops[0].attrs == {"name": "avz-vlan20", "vlan-id": "20", "interface": "bridge-lan"}
    assert ops[1].attrs == {"address": "192.168.50.1/24", "interface": "avz-vlan20"}
    assert ops[2].attrs["ranges"] == "192.168.50.100-192.168.50.199"
    assert ops[3].attrs["address"] == "192.168.50.0/24" and ops[3].attrs["gateway"] == "192.168.50.1"
    _, ops2, _ = plan("vlan", {"vlan_id": 20, "interfaz": "bridge-lan", "subred": "192.168.50.0/24", "dhcp": False})
    assert [o.path for o in ops2] == [("interface", "vlan"), ("ip", "address")]
    stamped(ops)


def test_limite_ops():
    _, ops, _ = plan("limite-ancho-banda", {"objetivo": "192.168.10.0/24", "bajada": 20, "subida": 5})
    assert ops == [Op(("queue", "simple"), "add", {"name": "avz-qos-192.168.10.0-24", "target": "192.168.10.0/24", "max-limit": "5M/20M"})]


def test_dns_ntp_ops_never_open_remote_requests():
    _, ops, _ = plan("dns-ntp", {"dns": ["1.1.1.1", "8.8.8.8"], "ntp": "pool.ntp.org"})
    assert ops[0] == Op(("ip", "dns"), "set", {"servers": "1.1.1.1,8.8.8.8"})
    assert ops[1] == Op(("system", "ntp", "client"), "set", {"enabled": "yes", "servers": "pool.ntp.org"})
    assert all("allow-remote-requests" not in o.attrs for o in ops)


VALID = {
    "firewall-seguro": {"ips_admin": ["203.0.113.0/24"]},
    "port-forward": {"protocolo": "tcp", "puerto_externo": 8080, "ip_destino": "192.168.10.20", "puerto_destino": 80},
    "vlan": {"vlan_id": 20, "interfaz": "bridge-lan", "subred": "192.168.50.0/24"},
    "limite-ancho-banda": {"objetivo": "192.168.10.0/24", "bajada": 20, "subida": 5},
    "dns-ntp": {"dns": ["1.1.1.1"]},
}


@pytest.mark.parametrize("tid", sorted(VALID))
def test_no_template_can_touch_the_management_rule(tid):
    _, ops, _ = plan(tid, VALID[tid])
    s = stamped(ops)
    for o in s:
        assert o.attrs.get("comment") in (None, "AVZ-APP-7")
        assert (o.find or {}).get("comment") != "AVZCloud mgmt"
        assert o.attrs.get("list") != "AVZ_MGMT"
        if o.action == "add" and o.path == FILTER:
            assert o.place == PLACE_AFTER_MGMT
    assert "AVZ_MGMT" not in render_cli(s)


# ---------------------------------------------------------------- política de reversión
@pytest.mark.parametrize("role", ["partner", "tecnico"])
def test_reversal_is_mandatory_for_high_risk_partners(role):
    fw = T.CATALOG["firewall-seguro"]
    with pytest.raises(T.TemplateError):
        T.reversal_minutes(role, fw, None)
    for bad in (0, 1, 31, 600, -3):
        with pytest.raises(T.TemplateError):
            T.reversal_minutes(role, fw, bad)
    assert T.reversal_minutes(role, fw, 2) == 2 and T.reversal_minutes(role, fw, 30) == 30


@pytest.mark.parametrize("role", ["superadmin", "soporte"])
def test_staff_may_skip_reversal_but_range_still_applies(role):
    fw = T.CATALOG["firewall-seguro"]
    assert T.reversal_minutes(role, fw, None) is None
    assert T.reversal_minutes(role, fw, 10) == 10
    with pytest.raises(T.TemplateError):
        T.reversal_minutes(role, fw, 45)


def test_reversal_optional_for_medium_and_rejected_for_non_reversible():
    assert T.reversal_minutes("partner", T.CATALOG["port-forward"], None) is None
    assert T.reversal_minutes("partner", T.CATALOG["port-forward"], 5) == 5
    with pytest.raises(T.TemplateError):
        T.reversal_minutes("superadmin", T.CATALOG["dns-ntp"], 5)
    assert T.reversal_minutes("partner", T.CATALOG["dns-ntp"], None) is None
