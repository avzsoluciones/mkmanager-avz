import ipaddress
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import audit, db
from ..config import get_settings
from ..schemas import ProvisionRequest
from ..deps import get_hub
from ..permissions import require
from ..security import client_ip, sede_scope
from ..services import addressing as ad
from ..services import wgkeys
from ..services.hub import HubClient, HubError
from ..services.routeros_scripts import ProvisionData, render_provision

router = APIRouter(prefix="/api/v1/provision", tags=["provision"])
_allowed = require("equipo.aprovisionar")
_script_perm = require("equipo.script")


def _sede_visible(user: dict, sede_id: int) -> dict:
    where, params = sede_scope(user)
    sede = db.fetch_one(f"SELECT s.id, s.nombre FROM sedes s WHERE s.id = %s AND {where}", (sede_id, *params))
    if not sede:
        raise HTTPException(status_code=404, detail="Sede no encontrada.")
    return sede


def _plan(req: ProvisionRequest, hub: HubClient):
    """Valida y calcula (red LAN, IP de túnel, gateway, rango DHCP). Sin efectos secundarios."""
    try:
        lan_net = ad.parse_lan(req.lan.cidr)
        existing = [r["lan_cidr"] for r in db.fetch_all("SELECT lan_cidr FROM dispositivos WHERE lan_cidr IS NOT NULL")]
        ad.check_lan(req.lan.cidr, existing + hub.used_lan_networks())
        used = [r["tunnel_ip"] for r in db.fetch_all("SELECT tunnel_ip FROM dispositivos WHERE tunnel_ip IS NOT NULL")]
        used += hub.used_tunnel_ips()
        if req.tunnel_ip:
            if req.tunnel_ip in {u.split("/")[0] for u in used}:
                raise ValueError(f"La IP de túnel {req.tunnel_ip} ya está en uso.")
            tunnel_ip = req.tunnel_ip
        else:
            tunnel_ip = ad.next_tunnel_ip(used)
        gateway = req.lan.gateway or ad.default_gateway(lan_net)
        if ipaddress.IPv4Address(gateway) not in lan_net:
            raise ValueError("El gateway de la LAN no pertenece a la subred indicada.")
        start, end = (req.lan.dhcp_start, req.lan.dhcp_end) if (req.lan.dhcp_start and req.lan.dhcp_end) \
            else ad.default_dhcp_range(lan_net)
        for ip in (start, end):
            if ipaddress.IPv4Address(ip) not in lan_net:
                raise ValueError("El rango DHCP debe estar dentro de la subred LAN.")
    except HubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    return lan_net, tunnel_ip, gateway, start, end


def _script(req: ProvisionRequest, sede_nombre: str, plan, wg_private: str, hub_key: str, api_password: str) -> str:
    lan_net, tunnel_ip, gateway, start, end = plan
    s = get_settings()
    return render_provision(ProvisionData(
        site_name=sede_nombre, device_name=req.nombre, tunnel_ip=tunnel_ip, wg_private_key=wg_private,
        hub_public_key=hub_key, hub_endpoint=s.hub_public_endpoint, hub_port=s.hub_public_port,
        wan_interface=req.wan.interface, wan_mode=req.wan.mode, wan_address=req.wan.address,
        wan_gateway=req.wan.gateway, lan_interface=req.lan.interface, lan_network=lan_net,
        lan_gateway=gateway, dhcp_start=start, dhcp_end=end, dns=req.lan.dns,
        api_user="avz-api", api_password=api_password,
    ))


@router.post("/preview")
def preview(req: ProvisionRequest, user: dict = Depends(_allowed), hub: HubClient = Depends(get_hub)):
    """Vista previa del script y del plan, sin crear nada. Las claves son marcadores."""
    sede = _sede_visible(user, req.sede_id)
    plan = _plan(req, hub)
    try:
        hub_key = hub.public_key()
    except HubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    script = _script(req, sede["nombre"], plan, "<CLAVE_PRIVADA_SE_GENERA_AL_CREAR>", hub_key, "<CLAVE_API_SE_GENERA_AL_CREAR>")
    return {"tunnel_ip": plan[1], "lan_gateway": plan[2], "dhcp": [plan[3], plan[4]], "script": script}


@router.post("", status_code=201)
def create(req: ProvisionRequest, request: Request, user: dict = Depends(_allowed), hub: HubClient = Depends(get_hub)):
    sede = _sede_visible(user, req.sede_id)
    plan = _plan(req, hub)
    lan_net, tunnel_ip, gateway, start, end = plan
    try:
        hub_key = hub.public_key()
    except HubError as e:
        raise HTTPException(status_code=502, detail=str(e))

    wg_private, wg_public = wgkeys.generate_keypair()
    api_password = secrets.token_urlsafe(18)
    try:
        private_enc, api_enc = wgkeys.encrypt_secret(wg_private), wgkeys.encrypt_secret(api_password)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    try:
        peer_id = hub.add_peer(wg_public, tunnel_ip, str(lan_net), f"AVZ {sede['nombre']} - {req.nombre}")
    except HubError as e:
        raise HTTPException(status_code=502, detail=str(e))

    try:
        hub.add_route(str(lan_net), tunnel_ip, f"{sede['nombre']} - {req.nombre}")
    except HubError as e:
        try:
            hub.remove_peer(peer_id)
        except HubError:
            audit.log(user["email"], "peer_huerfano", f"hub_peer_id={peer_id} tunnel={tunnel_ip}", client_ip(request))
        raise HTTPException(status_code=502, detail=str(e))

    try:
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO dispositivos (nombre, tipo, marca, modelo, sede_id, ip_local, tunnel_ip, lan_cidr,
                       puerto_gestion, estado, api_user, api_password_enc, wan_config, lan_config, detalles_config)
                   VALUES (%s, 'router', 'MikroTik', 'RouterOS v7', %s, %s, %s, %s, 8728, 'pendiente', 'avz-api', %s,
                           %s::jsonb, %s::jsonb, 'Aprovisionado por AVZCloud Central Manager') RETURNING id""",
                (req.nombre, req.sede_id, gateway, tunnel_ip, str(lan_net), api_enc,
                 req.wan.model_dump_json(), req.lan.model_dump_json()),
            )
            device_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO wg_peers (tipo, nombre, dispositivo_id, tunnel_ip, public_key, private_key_enc,
                       hub_peer_id, allowed_extra, creado_por)
                   VALUES ('router', %s, %s, %s, %s, %s, %s, %s, %s)""",
                (req.nombre, device_id, tunnel_ip, wg_public, private_enc, peer_id, str(lan_net), user["email"]),
            )
    except Exception:
        try:
            hub.remove_routes(str(lan_net))   # compensación: no dejar peer ni ruta huérfanos en el hub
            hub.remove_peer(peer_id)
        except HubError:
            audit.log(user["email"], "peer_huerfano", f"hub_peer_id={peer_id} tunnel={tunnel_ip}", client_ip(request))
        raise

    script = _script(req, sede["nombre"], plan, wg_private, hub_key, api_password)
    audit.log(user["email"], "router_aprovisionado", f"id={device_id} sede={req.sede_id} tunnel={tunnel_ip} lan={lan_net}",
              client_ip(request))
    return {"status": "success", "device": {"id": device_id, "nombre": req.nombre, "tunnel_ip": tunnel_ip,
                                             "lan_cidr": str(lan_net)}, "script": script}


@router.get("/{device_id}/script")
def get_script(device_id: int, request: Request, user: dict = Depends(_script_perm), hub: HubClient = Depends(get_hub)):
    """Reconstruye el script de un router ya creado (p. ej. para reinstalarlo)."""
    where, params = sede_scope(user)
    row = db.fetch_one(
        f"""SELECT d.id, d.nombre, d.sede_id, d.tunnel_ip, d.lan_cidr, d.api_password_enc, d.wan_config, d.lan_config,
                   s.nombre AS sede, p.private_key_enc
            FROM dispositivos d JOIN sedes s ON s.id = d.sede_id
            JOIN wg_peers p ON p.dispositivo_id = d.id AND p.revocado_en IS NULL
            WHERE d.id = %s AND {where}""", (device_id, *params))
    if not row or not row["api_password_enc"]:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado o sin datos de aprovisionamiento.")
    try:
        hub_key = hub.public_key()
        wg_private = wgkeys.decrypt_secret(row["private_key_enc"])
        api_password = wgkeys.decrypt_secret(row["api_password_enc"])
    except (HubError, RuntimeError) as e:
        raise HTTPException(status_code=502, detail=str(e))
    req = ProvisionRequest(sede_id=row["sede_id"], nombre=row["nombre"], wan=row["wan_config"], lan=row["lan_config"],
                           tunnel_ip=row["tunnel_ip"])
    lan_net = ipaddress.ip_network(row["lan_cidr"])
    gateway = req.lan.gateway or ad.default_gateway(lan_net)
    start, end = (req.lan.dhcp_start, req.lan.dhcp_end) if (req.lan.dhcp_start and req.lan.dhcp_end) \
        else ad.default_dhcp_range(lan_net)
    audit.log(user["email"], "script_consultado", f"device={device_id}", client_ip(request))
    return {"script": _script(req, row["sede"], (lan_net, row["tunnel_ip"], gateway, start, end), wg_private, hub_key, api_password)}
