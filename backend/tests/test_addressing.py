import ipaddress

import pytest

from app.services import addressing as ad


def test_next_tunnel_ip_skips_hub_and_db_addresses():
    used = ["10.254.0.1/32", "10.254.0.10", "10.254.0.2", "10.254.0.3"]
    assert ad.next_tunnel_ip(used) == "10.254.0.4"


def test_next_tunnel_ip_ignores_other_ranges_and_garbage():
    assert ad.next_tunnel_ip(["192.168.3.1", "basura", ""]) == "10.254.0.2"


def test_next_tunnel_ip_exhausted():
    used = [f"10.254.0.{i}" for i in range(2, 200)]
    with pytest.raises(RuntimeError):
        ad.next_tunnel_ip(used)


@pytest.mark.parametrize("bad", ["10.254.0.0/25", "10.10.10.0/28", "10.40.4.0/24", "10.0.0.0/8"])
def test_reserved_ranges_rejected(bad):
    with pytest.raises(ValueError):
        ad.check_lan(bad, [])


def test_overlap_with_existing_site_rejected():
    with pytest.raises(ValueError):
        ad.check_lan("192.168.3.0/25", ["192.168.3.0/24"])


def test_valid_lan_accepted():
    assert str(ad.check_lan("192.168.10.0/24", ["192.168.3.0/24"])) == "192.168.10.0/24"


@pytest.mark.parametrize("bad", ["8.8.8.0/24", "192.168.1.5/24", "nada", "192.168.1.0/30"])
def test_invalid_lan_rejected(bad):
    with pytest.raises(ValueError):
        ad.parse_lan(bad)


def test_defaults_for_24():
    lan = ipaddress.ip_network("192.168.10.0/24")
    assert ad.default_gateway(lan) == "192.168.10.1"
    assert ad.default_dhcp_range(lan) == ("192.168.10.100", "192.168.10.199")


def test_defaults_for_small_lan():
    lan = ipaddress.ip_network("10.20.0.0/28")
    start, end = ad.default_dhcp_range(lan)
    assert start == "10.20.0.2" and end == "10.20.0.14"


def test_router_and_user_pools_are_separate():
    used = [f"10.254.0.{i}" for i in range(2, 200)]
    assert ad.next_tunnel_ip(used, *ad.USER_RANGE) == "10.254.0.200"
    assert ad.next_tunnel_ip([], *ad.ROUTER_RANGE) == "10.254.0.2"
