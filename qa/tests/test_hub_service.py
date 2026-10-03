"""Cliente del hub (services/hub.py) con RouterOS falso. IDs: HB-*."""
import pytest

import fakes
from app.services.hub import HubClient, HubError


def hub(api=None, settings=None, **kw):
    return HubClient(settings, connect=(api or fakes.FakeApi(**kw)).connect)


def test_HB01_public_key_read_from_hub_not_hardcoded(settings):
    api = fakes.FakeApi(hub_key="CLAVE+OTRA+CLAVE+OTRA+CLAVE+OTRA+CLAVE+OTR=")
    assert hub(api, settings).public_key() == "CLAVE+OTRA+CLAVE+OTRA+CLAVE+OTRA+CLAVE+OTR="


def test_HB02_only_configured_interface_is_used(settings):
    api = fakes.FakeApi()
    api.data[("interface", "wireguard")] = [{"name": "wg1", "public-key": "NOTARIA"}, {"name": "wireguard1", "public-key": fakes.HUB_KEY}]
    assert hub(api, settings).public_key() == fakes.HUB_KEY


def test_HB03_add_peer_always_targets_configured_interface_and_never_wg1(settings):
    api = fakes.FakeApi()
    hub(api, settings).add_peer("PUB=", "10.254.0.9", "192.168.9.0/24", "c")
    assert api.added[0]["interface"] == "wireguard1" and api.added[0]["interface"] != "wg1"


def test_HB04_comment_truncated_to_120(settings):
    api = fakes.FakeApi()
    hub(api, settings).add_peer("PUB=", "10.254.0.9", "192.168.9.0/24", "x" * 500)
    assert len(api.added[0]["comment"]) == 120


def test_HB05_used_tunnel_ips_ignores_noise(settings):
    peers = [{"allowed-address": "10.254.0.5/32"}, {"allowed-address": " 10.254.0.6/32 , 192.168.1.0/24 "},
             {"allowed-address": "10.254.0.0/24"}, {"allowed-address": "10.254.0.128/25"}, {"allowed-address": "fd00::1/128"},
             {"allowed-address": "basura"}, {"allowed-address": ""}, {}, {"allowed-address": "10.40.1.2/32"}]
    assert sorted(hub(fakes.FakeApi(peers=peers), settings).used_tunnel_ips()) == ["10.254.0.5", "10.254.0.6"]


def test_HB06_used_lan_networks_excludes_host_routes_and_pool(settings):
    peers = [{"allowed-address": "10.254.0.5/32,192.168.5.0/24"}, {"allowed-address": "10.254.0.0/24"},
             {"allowed-address": "10.254.0.128/25"}, {"allowed-address": "192.168.6.7/32"}, {"allowed-address": "basura"}]
    assert hub(fakes.FakeApi(peers=peers), settings).used_lan_networks() == ["192.168.5.0/24"]


def test_HB07_dangling_peer_lan_is_counted_so_it_blocks_overlaps(settings):
    """peer7 del PROGRESO: 10.254.0.1/32 + 10.10.0.0/24 (interfaz colgante) sigue bloqueando 10.10.0.0/24."""
    peers = [{"allowed-address": "10.254.0.1/32,10.10.0.0/24"}]
    assert "10.10.0.0/24" in hub(fakes.FakeApi(peers=peers), settings).used_lan_networks()


def test_HB08_connect_failure_wrapped_and_does_not_leak_host_or_password(settings):
    h = HubClient(settings, connect=fakes.failing_connect)
    with pytest.raises(HubError) as e:
        h.public_key()
    assert "secreto-no-debe-salir" not in str(e.value) and "10.10.10.3" not in str(e.value) and settings.hub_api_password not in str(e.value)


@pytest.mark.parametrize("missing", ["hub_host", "hub_api_user", "hub_api_password", "hub_wg_interface"])
def test_HB09_incomplete_config_raises_hub_error(settings, missing):
    s = type(settings)(**{**settings.__dict__, missing: ""})
    with pytest.raises(HubError):
        HubClient(s, connect=fakes.FakeApi().connect).list_peers()


def test_HB10_remove_peer_error_wrapped(settings):
    api = fakes.FakeApi(fail_remove=True)
    with pytest.raises(HubError):
        hub(api, settings).remove_peer("*1")


def test_HB11_peer_status_by_public_key(settings):
    peers = [{"public-key": "AAA=", "last-handshake": "10s", "rx": 5, "tx": 6, "current-endpoint-address": "1.2.3.4"}]
    h = hub(fakes.FakeApi(peers=peers), settings)
    assert h.peer_status("AAA=") == {"last_handshake": "10s", "rx": 5, "tx": 6, "endpoint": "1.2.3.4"}
    assert h.peer_status("ZZZ=") is None


def test_HB12_hub_never_removes_foreign_peers_on_compensation(world):
    """Compensación: solo se elimina el peer recién creado, nunca peers existentes de otras interfaces."""
    world.db.fail_on = "enter"
    world.client.post("/api/v1/provision", headers=world.auth(1), json={
        "sede_id": 10, "nombre": "MK", "wan": {"interface": "ether1", "mode": "dhcp"}, "lan": {"interface": "br", "cidr": "192.168.10.0/24"}})
    assert set(world.api.removed) == {world.api.added[0][".id"]}
    assert {"*1", "*8", "*6"} <= {p[".id"] for p in world.api.peers}


def test_HB13_hub_connections_closed_after_each_operation(settings):
    api = fakes.FakeApi()
    h = hub(api, settings)
    h.public_key(); h.list_peers(); h.used_tunnel_ips(); h.add_peer("P=", "10.254.0.9", "192.168.9.0/24", "c")
    assert api.closes == api.connects, "HALLAZGO (medio): conexiones al hub sin cerrar (hub.py:_api)"
