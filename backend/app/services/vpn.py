"""VPN para personas (M3): reglas de negocio puras, generación de .conf/QR y operaciones del peer en el hub.

Modelo: cada persona recibe un par de claves generado en el servidor (privada cifrada en BD), una clave
precompartida opcional y una IP /32 del rango propio 10.254.0.200-250 (los routers usan 2-199).
AllowedIPs del cliente (decisión D2): solo la(s) LAN de SU sede + 10.254.0.1/32. Nunca 0.0.0.0/0.

REGLAS QUE EL HUB NECESITA (documentadas; este módulo NO las aplica ni las escribe como código):
 1. Ruta a la LAN de la sede: ya la crea el aprovisionamiento (ip route dst=<LAN> gateway=<IP túnel del router>).
 2. Forward (RouterOS, comentario "AVZ-managed ...", antes del primer drop/fasttrack):
      - accept: src-address=<IP del usuario> dst-address=<LAN de su sede> (una regla por usuario, o address-list
        AVZ_VPN_<sede> -> AVZ_LAN_<sede>); accept del retorno (connection-state established,related).
      - drop explícito de usuarios (src 10.254.0.200-250) hacia: puertos de gestión de routers (tcp 8728,8729,22,
        8291,23), 10.254.0.0/24 (otros peers y usuarios entre sí), 10.10.10.0/24 (servidores) y otras sedes.
 3. Input del hub: los usuarios solo pueden hacer ping a 10.254.0.1 (si no, drop); sin acceso a API/SSH/Winbox.
 4. NAT: NO enmascarar el tráfico de usuarios. El NAT "NAT VPC a WireGuard" debe limitarse a origen 10.10.10.4;
    si enmascarara a los usuarios saldrían como 10.254.0.1, que los routers aceptan como gestión (AVZ_MGMT).
 5. Retorno: el router de la sede debe poder devolver tráfico al rango de personas por el túnel (en su peer del
    hub, allowed-address con 10.254.0.200-250 y ruta de retorno por wireguard) y su firewall solo debe aceptar
    forward desde ese rango hacia su propia LAN.
 6. Revocación: al borrar el peer el handshake deja de ser posible de inmediato; limpiar también las reglas
    forward/address-list del usuario (conciliación T-016).
"""
import io
import ipaddress
import re
from typing import List, Optional

import qrcode
from qrcode.constants import ERROR_CORRECT_L

from . import addressing as ad
from .hub import HubClient, HubError

HUB_TUNNEL_IP = "10.254.0.1"
MTU = 1420
KEEPALIVE = 25
DEFAULT_DNS = "1.1.1.1"
MAX_PER_SEDE = 20
NAME_RE = re.compile(r"[\w][\w .()\-]{1,59}")          # sin saltos de línea ni caracteres de control


def clean_name(value: str) -> str:
    """Nombre seguro: acaba en comentarios del hub, en auditoría y en el nombre del archivo."""
    v = (value or "").strip()
    if not NAME_RE.fullmatch(v):
        raise ValueError("Nombre inválido: use 2 a 60 letras, números, espacios, punto, guion o paréntesis.")
    return v


def parse_routes(routes: Optional[List[str]], sede_lans: List[str]) -> List[str]:
    """Rutas del cliente: sin valor = todas las LAN de la sede; con valor = subconjunto estricto de ellas."""
    lans = [ipaddress.ip_network(x, strict=False) for x in sede_lans]
    if not routes:
        return [str(n) for n in lans]
    out = []
    for r in routes:
        try:
            net = ipaddress.ip_network(str(r).strip(), strict=True)
        except ValueError:
            raise ValueError(f"Ruta inválida: '{str(r)[:40]}'. Use el formato 192.168.10.0/24.")
        if net.version != 4 or not any(net.subnet_of(l) for l in lans):
            raise ValueError(f"La ruta {net} no pertenece a la red de la sede; solo se permiten redes de su propia sede.")
        if str(net) not in out:
            out.append(str(net))
    return out


def pick_dns(requested: Optional[str], sede_lans: List[str], gateway: Optional[str]) -> str:
    if requested:
        return requested
    try:
        gw = ipaddress.ip_address(str(gateway))
        if any(gw in ipaddress.ip_network(l, strict=False) for l in sede_lans):
            return str(gw)
    except ValueError:
        pass
    return DEFAULT_DNS


def free_ip(db_ips, hub_ips) -> str:
    """Primera IP libre del rango de personas; unión de BD y peers del hub. RuntimeError si se agotó."""
    return ad.next_tunnel_ip(list(db_ips) + list(hub_ips), *ad.USER_RANGE)


def build_conf(private_key: str, tunnel_ip: str, dns: str, hub_public_key: str, preshared: Optional[str],
               allowed: List[str], endpoint: str, port: int) -> str:
    lines = ["[Interface]", f"PrivateKey = {private_key}", f"Address = {tunnel_ip}/32", f"DNS = {dns}",
             f"MTU = {MTU}", "", "[Peer]", f"PublicKey = {hub_public_key}"]
    if preshared:
        lines.append(f"PresharedKey = {preshared}")
    lines += [f"AllowedIPs = {', '.join(allowed)}", f"Endpoint = {endpoint}:{port}",
              f"PersistentKeepalive = {KEEPALIVE}"]
    return "\n".join(lines) + "\n"


def client_allowed(routes: List[str]) -> List[str]:
    return [f"{HUB_TUNNEL_IP}/32"] + list(routes)


def qr_png(text: str) -> bytes:
    """QR en memoria (nunca toca disco)."""
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_L, box_size=6, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    return buf.getvalue()


def safe_filename(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.") or "vpn"
    return f"{base[:40]}.conf"


# ---- operaciones sobre el hub (solo peer; no toca rutas ni firewall) ----
def add_user_peer(hub: HubClient, public_key: str, tunnel_ip: str, preshared: Optional[str], comment: str) -> str:
    kw = {"interface": hub.s.hub_wg_interface, "public-key": public_key,
          "allowed-address": f"{tunnel_ip}/32", "comment": comment[:120]}
    if preshared:
        kw["preshared-key"] = preshared
    try:
        return str(hub._api().path("interface", "wireguard", "peers").add(**kw))
    except HubError:
        raise
    except Exception as e:
        raise HubError(f"El hub rechazó el peer de la persona: {type(e).__name__}")


def remove_user_peer(hub: HubClient, peer_id: Optional[str], public_key: str) -> bool:
    """Elimina el peer si existe. True si lo eliminó, False si ya no estaba (revocación idempotente)."""
    for p in hub.list_peers():
        if (peer_id and p.get(".id") == peer_id) or p.get("public-key") == public_key:
            hub.remove_peer(p[".id"])
            return True
    return False
