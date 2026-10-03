"""M3: VPN para personas (acceso remoto). Contrato de la interfaz: ver frontend-new/js/mock.js."""
import ipaddress
import time
from collections import defaultdict, deque
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import audit, db
from ..config import get_settings
from ..deps import get_hub
from ..permissions import require
from ..security import client_ip, sede_scope
from ..services import vpn as svc
from ..services import wgkeys
from ..services.hub import HubClient, HubError

router = APIRouter(prefix="/api/v1/vpn", tags=["vpn"])
_manage = require("vpn.gestionar")

_NO_STORE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
_CREATE_LIMIT, _CREATE_WINDOW = 10, 60
_CREATES = defaultdict(deque)           # email -> instantes de alta (límite por minuto, en memoria)


class VpnUserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nombre: str
    sede_id: Optional[int] = Field(default=None, gt=0)
    rutas: Optional[List[str]] = Field(default=None, max_length=16)
    dns: Optional[str] = None
    preshared: bool = True

    @field_validator("nombre")
    @classmethod
    def _nombre(cls, v):
        return svc.clean_name(v)

    @field_validator("dns")
    @classmethod
    def _dns(cls, v):
        if v is None or v == "":
            return None
        try:
            return str(ipaddress.IPv4Address(v.strip()))
        except ValueError:
            raise ValueError("El DNS debe ser una dirección IPv4.")


def _rate_limit(email: str) -> None:
    q, now = _CREATES[email], time.time()
    while q and now - q[0] > _CREATE_WINDOW:
        q.popleft()
    if len(q) >= _CREATE_LIMIT:
        raise HTTPException(status_code=429, detail="Demasiados accesos creados en poco tiempo. Espera un minuto.")
    q.append(now)


def _sede(user: dict, sede_id: Optional[int]) -> dict:
    where, params = sede_scope(user)
    if sede_id is not None:
        sede = db.fetch_one(f"SELECT s.id, s.nombre, s.partner_id FROM sedes s WHERE s.id = %s AND {where}",
                            (sede_id, *params))
        if not sede:
            raise HTTPException(status_code=404, detail="Sede no encontrada.")
        return sede
    rows = db.fetch_all(f"SELECT s.id, s.nombre, s.partner_id FROM sedes s WHERE {where} ORDER BY s.id LIMIT 2", params)
    if len(rows) == 1:
        return rows[0]
    raise HTTPException(status_code=422, detail="Indica la sede (sede_id) a la que tendrá acceso la persona.")


def _profile(user: dict, uid: int) -> dict:
    """Perfil vigente dentro del alcance del usuario; fuera de alcance, inexistente o revocado = 404."""
    where, params = sede_scope(user)
    row = db.fetch_one(
        f"""SELECT p.id, p.nombre, p.tunnel_ip, p.public_key, p.private_key_enc, p.preshared_key_enc,
                   p.client_allowed, p.dns, p.hub_peer_id, p.sede_id
            FROM wg_peers p JOIN sedes s ON s.id = p.sede_id
            WHERE p.id = %s AND p.tipo = 'usuario' AND p.revocado_en IS NULL AND {where}""", (uid, *params))
    if not row:
        raise HTTPException(status_code=404, detail="Acceso VPN no encontrado.")
    return row


def _hub_key(hub: HubClient) -> str:
    try:
        return hub.public_key()
    except HubError as e:
        raise HTTPException(status_code=502, detail=str(e))


def _endpoint() -> tuple:
    s = get_settings()
    if not s.hub_public_endpoint:
        raise HTTPException(status_code=502, detail="Configuración del hub incompleta (endpoint público).")
    return s.hub_public_endpoint, s.hub_public_port


def _conf_for(row: dict, hub: HubClient) -> str:
    try:
        priv = wgkeys.decrypt_secret(row["private_key_enc"])
        psk = wgkeys.decrypt_secret(row["preshared_key_enc"]) if row.get("preshared_key_enc") else None
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    host, port = _endpoint()
    allowed = [x for x in (row["client_allowed"] or "").split(",") if x]
    return svc.build_conf(priv, row["tunnel_ip"], row["dns"] or svc.DEFAULT_DNS, _hub_key(hub), psk, allowed, host, port)


@router.get("/users")
def list_users(user: dict = Depends(_manage)):
    where, params = sede_scope(user)
    rows = db.fetch_all(
        f"""SELECT p.id, p.nombre, COALESCE(p.tunnel_ip_historica, p.tunnel_ip) AS tunnel_ip, p.creado_en,
                   (p.revocado_en IS NOT NULL) AS revocado, p.sede_id
            FROM wg_peers p JOIN sedes s ON s.id = p.sede_id
            WHERE p.tipo = 'usuario' AND {where} ORDER BY p.id""", params)
    return [{"id": r["id"], "nombre": r["nombre"], "tunnel_ip": r["tunnel_ip"], "creado_en": r["creado_en"],
             "revocado": bool(r["revocado"]), "sede_id": r["sede_id"]} for r in rows]


@router.post("/users", status_code=201)
def create_user(req: VpnUserCreate, request: Request, user: dict = Depends(_manage), hub: HubClient = Depends(get_hub)):
    ip = client_ip(request)
    sede = _sede(user, req.sede_id)
    devs = db.fetch_all("SELECT lan_cidr, ip_local FROM dispositivos WHERE sede_id = %s AND lan_cidr IS NOT NULL ORDER BY id",
                        (sede["id"],))
    lans = list(dict.fromkeys(d["lan_cidr"] for d in devs))
    if not lans:
        raise HTTPException(status_code=422, detail="La sede aún no tiene un equipo con red LAN; instala primero el router.")
    try:
        routes = svc.parse_routes(req.rutas, lans)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    n = db.fetch_one("SELECT COUNT(*) AS n FROM wg_peers WHERE tipo = 'usuario' AND sede_id = %s AND revocado_en IS NULL",
                     (sede["id"],))
    if n and n["n"] >= svc.MAX_PER_SEDE:
        raise HTTPException(status_code=409, detail=f"La sede alcanzó el máximo de {svc.MAX_PER_SEDE} accesos VPN activos.")
    _rate_limit(user["email"])

    hub_key = _hub_key(hub)
    host, port = _endpoint()
    try:
        used = [r["tunnel_ip"] for r in db.fetch_all("SELECT tunnel_ip FROM wg_peers WHERE revocado_en IS NULL")]
        used += [r["tunnel_ip"] for r in db.fetch_all("SELECT tunnel_ip FROM dispositivos WHERE tunnel_ip IS NOT NULL")]
        tunnel_ip = svc.free_ip(used, hub.used_tunnel_ips())
    except HubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except RuntimeError:
        raise HTTPException(status_code=409, detail="No quedan direcciones libres para personas; revoca accesos que ya no se usen.")

    dns = svc.pick_dns(req.dns, lans, devs[0].get("ip_local"))
    priv, pub = wgkeys.generate_keypair()
    psk = wgkeys.generate_preshared() if req.preshared else None
    try:
        priv_enc = wgkeys.encrypt_secret(priv)
        psk_enc = wgkeys.encrypt_secret(psk) if psk else None
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    allowed = svc.client_allowed(routes)

    try:
        peer_id = svc.add_user_peer(hub, pub, tunnel_ip, psk, f"AVZ VPN {req.nombre}")
    except HubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    try:
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO wg_peers (tipo, nombre, tunnel_ip, public_key, private_key_enc, preshared_key_enc,
                       hub_peer_id, client_allowed, dns, sede_id, tenant_id, usuario_id, creado_por)
                   VALUES ('usuario', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s) RETURNING id""",
                (req.nombre, tunnel_ip, pub, priv_enc, psk_enc, peer_id, ",".join(allowed), dns, sede["id"],
                 sede.get("partner_id"), user["email"]))
            uid = cur.fetchone()["id"]
    except Exception:
        try:
            hub.remove_peer(peer_id)           # compensación: sin fila en BD no puede quedar peer en el hub
        except HubError:
            audit.log(user["email"], "peer_huerfano", f"hub_peer_id={peer_id} tunnel={tunnel_ip}", ip)
        raise
    conf = svc.build_conf(priv, tunnel_ip, dns, hub_key, psk, allowed, host, port)
    audit.log(user["email"], "vpn_creada", f"id={uid} sede={sede['id']} tunnel={tunnel_ip} nombre={req.nombre}", ip)
    return JSONResponse(status_code=201, headers=_NO_STORE,
                        content={"id": uid, "nombre": req.nombre, "tunnel_ip": tunnel_ip, "conf_text": conf})


@router.get("/users/{uid}/qr.png")
def get_qr(uid: int, request: Request, user: dict = Depends(_manage), hub: HubClient = Depends(get_hub)):
    row = _profile(user, uid)
    png = svc.qr_png(_conf_for(row, hub))
    audit.log(user["email"], "vpn_config_consultada", f"id={uid} formato=qr", client_ip(request))
    return Response(content=png, media_type="image/png", headers=_NO_STORE)


@router.get("/users/{uid}/conf")
def get_conf(uid: int, request: Request, user: dict = Depends(_manage), hub: HubClient = Depends(get_hub)):
    row = _profile(user, uid)
    text = _conf_for(row, hub)
    audit.log(user["email"], "vpn_config_consultada", f"id={uid} formato=conf", client_ip(request))
    headers = {**_NO_STORE, "Content-Disposition": f'attachment; filename="{svc.safe_filename(row["nombre"])}"'}
    return Response(content=text, media_type="text/plain; charset=utf-8", headers=headers)


@router.delete("/users/{uid}")
def revoke_user(uid: int, request: Request, user: dict = Depends(_manage), hub: HubClient = Depends(get_hub)):
    row = _profile(user, uid)
    ip = client_ip(request)
    try:
        svc.remove_user_peer(hub, row["hub_peer_id"], row["public_key"])
    except HubError as e:
        raise HTTPException(status_code=502, detail=str(e))     # sigue activo en BD: se puede reintentar
    try:
        db.execute(
            """UPDATE wg_peers SET revocado_en = now(), tunnel_ip_historica = tunnel_ip, tunnel_ip = 'rev-' || id
               WHERE id = %s AND tipo = 'usuario' AND revocado_en IS NULL""", (uid,))
    except Exception:
        audit.log(user["email"], "vpn_revocacion_bd_pendiente", f"id={uid} peer ya eliminado del hub", ip)
        raise
    audit.log(user["email"], "vpn_revocada", f"id={uid} tunnel={row['tunnel_ip']}", ip)
    return {"status": "success"}
