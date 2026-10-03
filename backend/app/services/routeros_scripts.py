"""Generación de scripts RouterOS v7. Funciones puras: sin red ni base de datos."""
import ipaddress
from dataclasses import dataclass
from typing import List, Optional

WG_IF = "wg-avzcloud"
MANAGER_IP = "10.10.10.4"          # srv-2: origen de la API tras el hub
TUNNEL_NET = "10.254.0.0/24"
HUB_TUNNEL_IP = "10.254.0.1"
COMMENT = "AVZCloud"


def safe_text(value: str) -> str:
    """Texto libre para comentarios del script: sin saltos de línea ni caracteres de control o de comando."""
    cleaned = "".join(c if (c.isalnum() or c in " .,-_()") else "?" for c in str(value))
    return " ".join(cleaned.split())[:80]


def q(value: str) -> str:
    """Entrecomilla un valor para RouterOS. Rechaza lo que podría romper o inyectar comandos."""
    s = str(value)
    if any(c in s for c in '\r\n\x00"\\$`[]{}'):
        raise ValueError(f"Carácter no permitido en el valor: {s!r}")
    return f'"{s}"'


@dataclass
class ProvisionData:
    site_name: str
    device_name: str
    tunnel_ip: str
    wg_private_key: str
    hub_public_key: str
    hub_endpoint: str
    hub_port: int
    wan_interface: str
    wan_mode: str                       # dhcp | static | pppoe
    wan_address: Optional[str]
    wan_gateway: Optional[str]
    lan_interface: str
    lan_network: ipaddress.IPv4Network
    lan_gateway: str
    dhcp_start: str
    dhcp_end: str
    dns: List[str]
    api_user: str
    api_password: str


def _guard(find: str, command: str) -> str:
    return f":if ([:len [{find}]] = 0) do={{ {command} }}"


def render_provision(d: ProvisionData) -> str:
    wan, lan = q(d.wan_interface), q(d.lan_interface)
    prefix = d.lan_network.prefixlen
    lan_addr = f"{d.lan_gateway}/{prefix}"
    out = [
        "# ======================================================================",
        f"# AVZCloud Central Manager - aprovisionamiento | RouterOS v7",
        f"# Sede: {safe_text(d.site_name)} | Equipo: {safe_text(d.device_name)}",
        "# Ejecutar completo en una terminal del router (/system script o pegar).",
        "# Es idempotente: se puede repetir sin duplicar configuración.",
        "# ======================================================================",
        "",
        "# --- 0. Comprobaciones previas: si falta algo, se aborta sin cambiar nada ---",
        f':if ([:len [/interface find name={wan}]] = 0) do={{ :error "AVZCloud: no existe la interfaz WAN {d.wan_interface}" }}',
        f':if ([:len [/interface find name={lan}]] = 0) do={{ :error "AVZCloud: no existe la interfaz LAN {d.lan_interface}" }}',
        "",
        "# --- 1. WAN ---",
    ]
    if d.wan_mode == "dhcp":
        out.append(_guard(f"/ip dhcp-client find interface={wan}",
                          f'/ip dhcp-client add interface={wan} disabled=no add-default-route=yes use-peer-dns=no use-peer-ntp=no comment={q(COMMENT)}'))
    elif d.wan_mode == "static":
        out.append(_guard(f"/ip address find address={q(d.wan_address)} interface={wan}",
                          f'/ip address add address={q(d.wan_address)} interface={wan} comment={q(COMMENT)}'))
        out.append(_guard(f"/ip route find dst-address=0.0.0.0/0 gateway={d.wan_gateway}",
                          f'/ip route add dst-address=0.0.0.0/0 gateway={d.wan_gateway} comment={q(COMMENT)}'))
    else:
        out.append(f"# WAN {d.wan_interface} ({d.wan_mode}): se asume ya configurada; solo se aplica NAT.")
    out += [
        f'/ip dns set servers={",".join(d.dns)}',
        "",
        "# --- 2. LAN, DHCP y NAT ---",
        _guard(f"/ip address find address={q(lan_addr)} interface={lan}",
               f'/ip address add address={q(lan_addr)} interface={lan} comment={q(COMMENT)}'),
        _guard('/ip pool find name="avz-lan-pool"',
               f'/ip pool add name="avz-lan-pool" ranges={d.dhcp_start}-{d.dhcp_end}'),
        _guard(f"/ip dhcp-server network find address={q(str(d.lan_network))}",
               f'/ip dhcp-server network add address={q(str(d.lan_network))} gateway={d.lan_gateway} dns-server={",".join(d.dns)} comment={q(COMMENT)}'),
        _guard('/ip dhcp-server find name="avz-lan-dhcp"',
               f'/ip dhcp-server add name="avz-lan-dhcp" interface={lan} address-pool="avz-lan-pool" lease-time=1d disabled=no'),
        _guard('/ip firewall nat find comment="AVZCloud NAT"',
               f'/ip firewall nat add chain=srcnat action=masquerade out-interface={wan} comment="AVZCloud NAT"'),
        "",
        "# --- 3. Túnel WireGuard hacia el hub central ---",
        _guard(f'/interface wireguard find name="{WG_IF}"',
               f'/interface wireguard add name="{WG_IF}" listen-port=13231 mtu=1420 private-key={q(d.wg_private_key)} comment={q(COMMENT)}'),
        _guard(f'/interface wireguard peers find interface="{WG_IF}" public-key={q(d.hub_public_key)}',
               f'/interface wireguard peers add interface="{WG_IF}" public-key={q(d.hub_public_key)} endpoint-address={d.hub_endpoint} endpoint-port={d.hub_port} '
               f'allowed-address={TUNNEL_NET},{MANAGER_IP}/32 persistent-keepalive=25s comment="AVZCloud hub"'),
        _guard(f'/ip address find address="{d.tunnel_ip}/24" interface="{WG_IF}"',
               f'/ip address add address="{d.tunnel_ip}/24" interface="{WG_IF}" comment={q(COMMENT)}'),
        _guard(f"/ip route find dst-address={MANAGER_IP}/32",
               f'/ip route add dst-address={MANAGER_IP}/32 gateway="{WG_IF}" comment={q(COMMENT)}'),
        "",
        "# --- 4. Acceso de gestión: usuario de API mínimo, solo desde el gestor por el túnel ---",
        _guard('/user group find name="avz-api"',
               '/user group add name="avz-api" policy=api,read,write,test,sensitive,!local,!telnet,!ssh,!ftp,!reboot,!policy,!winbox,!password,!web,!sniff,!romon,!rest-api'),
        _guard(f"/user find name={q(d.api_user)}",
               f'/user add name={q(d.api_user)} group="avz-api" password={q(d.api_password)} address={MANAGER_IP}/32,{HUB_TUNNEL_IP}/32 comment="AVZCloud Central Manager"'),
        f"/ip service set api disabled=no address={MANAGER_IP}/32,{HUB_TUNNEL_IP}/32",
        # El hub enmascara el tráfico de gestión (NAT hacia el túnel): el router lo ve llegar desde
        # 10.254.0.1, no desde 10.10.10.4. Se aceptan ambos orígenes mediante una lista de direcciones.
        _guard(f'/ip firewall address-list find list="AVZ_MGMT" address={HUB_TUNNEL_IP}',
               f'/ip firewall address-list add list="AVZ_MGMT" address={HUB_TUNNEL_IP} comment={q(COMMENT)}'),
        _guard(f'/ip firewall address-list find list="AVZ_MGMT" address={MANAGER_IP}',
               f'/ip firewall address-list add list="AVZ_MGMT" address={MANAGER_IP} comment={q(COMMENT)}'),
        '/ip firewall filter',
        ':if ([:len [find comment="AVZCloud mgmt"]] = 0) do={',
        f'  :if ([:len [find]] = 0) do={{ add chain=input action=accept in-interface="{WG_IF}" src-address-list="AVZ_MGMT" comment="AVZCloud mgmt" }} '
        f'else={{ add chain=input action=accept in-interface="{WG_IF}" src-address-list="AVZ_MGMT" comment="AVZCloud mgmt" place-before=0 }}',
        '}',
        "/",
        "",
        ':put "AVZCloud: aprovisionamiento aplicado. Verifique el handshake: /interface wireguard peers print"',
        "",
    ]
    return "\n".join(out)
