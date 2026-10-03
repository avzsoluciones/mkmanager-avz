"""Cliente del hub WireGuard (MikroTik cloud) por API RouterOS."""
import ipaddress
from typing import Callable, List, Optional

from librouteros import connect as ros_connect

from ..config import Settings
from .addressing import TUNNEL_POOL


class HubError(RuntimeError):
    pass


class HubClient:
    def __init__(self, settings: Settings, connect: Optional[Callable] = None):
        self.s = settings
        self._connect = connect or ros_connect

    def _api(self):
        if not (self.s.hub_host and self.s.hub_api_user and self.s.hub_api_password and self.s.hub_wg_interface):
            raise HubError("Configuración del hub incompleta (HUB_* en .env).")
        try:
            return self._connect(host=self.s.hub_host, username=self.s.hub_api_user,
                                 password=self.s.hub_api_password, port=self.s.hub_api_port, timeout=8)
        except Exception as e:
            raise HubError(f"No se pudo conectar con el hub: {type(e).__name__}")

    def public_key(self) -> str:
        for i in self._api().path("interface", "wireguard"):
            if i.get("name") == self.s.hub_wg_interface:
                key = i.get("public-key")
                if key:
                    return key
        raise HubError(f"No se encontró la interfaz WireGuard '{self.s.hub_wg_interface}' en el hub.")

    def list_peers(self) -> List[dict]:
        return [dict(p) for p in self._api().path("interface", "wireguard", "peers")]

    def used_tunnel_ips(self) -> List[str]:
        """IPs del pool de túneles ya asignadas en CUALQUIER interfaz del hub."""
        used = []
        for p in self.list_peers():
            for a in str(p.get("allowed-address", "")).split(","):
                a = a.strip()
                try:
                    net = ipaddress.ip_network(a, strict=False)
                except ValueError:
                    continue
                if net.network_address in TUNNEL_POOL and net.prefixlen == 32:
                    used.append(str(net.network_address))
        return used

    def static_routes(self) -> List[dict]:
        return [dict(r) for r in self._api().path("ip", "route") if not r.get("dynamic")]

    def add_route(self, dst: str, gateway: str, comment: str) -> str:
        """Ruta estática hacia la LAN de una sede por el túnel (allowed-address no crea rutas)."""
        try:
            return str(self._api().path("ip", "route").add(**{
                "dst-address": dst, "gateway": gateway, "comment": f"AVZ {comment}"[:120]}))
        except Exception as e:
            raise HubError(f"El hub rechazó la ruta {dst}: {type(e).__name__}: {str(e)[:150]}")

    def remove_routes(self, dst: str) -> int:
        """Elimina solo rutas estáticas creadas por esta plataforma (comentario 'AVZ ...') hacia `dst`."""
        removed = 0
        try:
            path = self._api().path("ip", "route")
            for r in list(path):
                if r.get("dst-address") == dst and not r.get("dynamic") and str(r.get("comment", "")).startswith("AVZ "):
                    path.remove(r[".id"])
                    removed += 1
        except Exception as e:
            raise HubError(f"No se pudo eliminar la ruta {dst}: {type(e).__name__}")
        return removed

    def used_lan_networks(self) -> List[str]:
        """Redes de sede ya enrutadas en el hub: allowed-address de peers y rutas estáticas de cualquier servicio."""
        nets = []
        for r in self.static_routes():
            try:
                net = ipaddress.ip_network(str(r.get("dst-address", "")), strict=False)
            except ValueError:
                continue
            if 0 < net.prefixlen < 32 and not net.subnet_of(TUNNEL_POOL):
                nets.append(str(net))
        for p in self.list_peers():
            for a in str(p.get("allowed-address", "")).split(","):
                a = a.strip()
                try:
                    net = ipaddress.ip_network(a, strict=False)
                except ValueError:
                    continue
                if net.prefixlen < 32 and not net.subnet_of(TUNNEL_POOL):
                    nets.append(str(net))
        return nets

    def add_peer(self, public_key: str, tunnel_ip: str, lan_cidr: str, comment: str) -> str:
        allowed = f"{tunnel_ip}/32,{lan_cidr}"
        try:
            new_id = self._api().path("interface", "wireguard", "peers").add(**{
                "interface": self.s.hub_wg_interface,
                "public-key": public_key,
                "allowed-address": allowed,
                "comment": comment[:120],
            })
        except Exception as e:
            raise HubError(f"El hub rechazó el peer: {type(e).__name__}: {str(e)[:150]}")
        return str(new_id)

    def remove_peer(self, peer_id: str) -> None:
        try:
            self._api().path("interface", "wireguard", "peers").remove(peer_id)
        except Exception as e:
            raise HubError(f"No se pudo eliminar el peer {peer_id}: {type(e).__name__}")

    def peer_status(self, public_key: str) -> Optional[dict]:
        for p in self.list_peers():
            if p.get("public-key") == public_key:
                return {"last_handshake": p.get("last-handshake"), "rx": p.get("rx"), "tx": p.get("tx"),
                        "endpoint": p.get("current-endpoint-address")}
        return None
