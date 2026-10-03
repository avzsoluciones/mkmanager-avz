"""Reglas de direccionamiento: LAN de sedes y pool de túneles. Funciones puras y testeables."""
import ipaddress
from typing import Iterable

TUNNEL_POOL = ipaddress.ip_network("10.254.0.0/24")

# Rangos que ninguna LAN de sede puede solapar: túneles, red privada entre servidores
# y la red WireGuard de otro servicio en producción del hub.
RESERVED_NETWORKS = [
    ipaddress.ip_network("10.254.0.0/24"),
    ipaddress.ip_network("10.10.10.0/24"),
    ipaddress.ip_network("10.40.0.0/18"),
]


def parse_lan(cidr: str) -> ipaddress.IPv4Network:
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=True)
    except ValueError:
        raise ValueError(f"Subred LAN inválida: '{cidr}'. Use el formato 192.168.10.0/24.")
    if net.version != 4:
        raise ValueError("Solo se admiten subredes IPv4.")
    if not net.is_private:
        raise ValueError("La subred LAN debe ser privada (RFC 1918).")
    if net.prefixlen > 29 or net.prefixlen < 16:
        raise ValueError("La máscara de la LAN debe estar entre /16 y /29.")
    return net


def check_lan(cidr: str, existing: Iterable[str]) -> ipaddress.IPv4Network:
    """Valida la LAN y rechaza solapes con reservadas y con las LAN de otras sedes."""
    net = parse_lan(cidr)
    for res in RESERVED_NETWORKS:
        if net.overlaps(res):
            raise ValueError(f"La subred {net} solapa con la red reservada {res}.")
    for other in existing:
        if not other:
            continue
        try:
            o = ipaddress.ip_network(other, strict=False)
        except ValueError:
            continue
        if net.overlaps(o):
            raise ValueError(f"La subred {net} solapa con otra sede ya registrada ({o}).")
    return net


ROUTER_RANGE = (2, 199)      # routers y equipos de sede
USER_RANGE = (200, 250)      # personas con VPN de acceso remoto


def next_tunnel_ip(used: Iterable[str], first: int = ROUTER_RANGE[0], last: int = ROUTER_RANGE[1]) -> str:
    """Primera IP libre del pool 10.254.0.X en el rango [first, last]. `used` incluye las del hub y las de la BD."""
    taken = set()
    for ip in used:
        try:
            addr = ipaddress.ip_address(str(ip).split("/")[0])
        except ValueError:
            continue
        if addr in TUNNEL_POOL:
            taken.add(int(addr))
    base = int(TUNNEL_POOL.network_address)
    for host in range(first, last + 1):
        if base + host not in taken:
            return str(ipaddress.ip_address(base + host))
    raise RuntimeError(f"No quedan IPs libres en el rango 10.254.0.{first}-{last}.")


def default_gateway(lan: ipaddress.IPv4Network) -> str:
    return str(next(lan.hosts()))


def default_dhcp_range(lan: ipaddress.IPv4Network) -> tuple:
    hosts = list(lan.hosts())
    if len(hosts) >= 254:
        return str(hosts[99]), str(hosts[198])
    return str(hosts[1]), str(hosts[-1])
