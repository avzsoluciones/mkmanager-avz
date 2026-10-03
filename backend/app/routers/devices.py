from fastapi import APIRouter, Depends, HTTPException, Request

from .. import audit, db
from ..deps import get_hub
from ..permissions import require
from ..security import client_ip, sede_scope
from ..services.hub import HubClient, HubError

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])

# Lista blanca de columnas: nunca api_user, api_password_enc, detalles_config ni comando_adopcion.
_COLUMNS = """d.id, d.nombre, d.tipo, d.marca, d.modelo, d.sede_id, s.nombre AS sede, d.ip_local,
              d.tunnel_ip, d.lan_cidr, d.estado, d.ultimo_heartbeat, d.wan_config, d.lan_config"""

_WAN_KEYS = ("interface", "mode", "address", "gateway")
_LAN_KEYS = ("interface", "cidr", "gateway", "dhcp_start", "dhcp_end", "dns")


def _pick(cfg, keys):
    """Copia solo las claves conocidas de un JSON de configuración (None si no hay)."""
    if not isinstance(cfg, dict):
        return None
    return {k: cfg.get(k) for k in keys if k in cfg}


def _present(row: dict) -> dict:
    """Fila pública del equipo: añade modo y datos WAN/LAN ya filtrados, sin exponer el JSON crudo."""
    out = dict(row)
    wan, lan = _pick(out.pop("wan_config", None), _WAN_KEYS), _pick(out.pop("lan_config", None), _LAN_KEYS)
    out["modo"] = wan.get("mode") if wan else None
    out["wan"], out["lan"] = wan, lan
    return out


@router.get("")
def list_devices(user: dict = Depends(require("equipo.ver"))):
    where, params = sede_scope(user)
    rows = db.fetch_all(
        f"SELECT {_COLUMNS} FROM dispositivos d JOIN sedes s ON s.id = d.sede_id WHERE {where} ORDER BY d.id",
        params,
    )
    return [_present(r) for r in rows]


@router.get("/{device_id}")
def get_device(device_id: int, user: dict = Depends(require("equipo.ver"))):
    where, params = sede_scope(user)
    row = db.fetch_one(
        f"SELECT {_COLUMNS} FROM dispositivos d JOIN sedes s ON s.id = d.sede_id WHERE d.id = %s AND {where}",
        (device_id, *params),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")
    return _present(row)


@router.delete("/{device_id}")
def decommission(device_id: int, request: Request, confirmar: bool = False, forzar: bool = False,
                 user: dict = Depends(require("equipo.baja")), hub: HubClient = Depends(get_hub)):
    """Baja de un equipo: elimina su peer y su ruta del hub y luego el registro. Solo toca recursos AVZ."""
    if not confirmar:
        raise HTTPException(status_code=400, detail="Confirme la baja con confirmar=true.")
    if forzar and user["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="Solo un superadmin puede forzar la baja.")
    where, params = sede_scope(user)
    dev = db.fetch_one(
        f"""SELECT d.id, d.nombre, d.lan_cidr, d.tunnel_ip, p.hub_peer_id
            FROM dispositivos d JOIN sedes s ON s.id = d.sede_id
            LEFT JOIN wg_peers p ON p.dispositivo_id = d.id AND p.revocado_en IS NULL
            WHERE d.id = %s AND {where}""", (device_id, *params))
    if not dev:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")
    try:
        if dev["hub_peer_id"]:
            hub.remove_peer(dev["hub_peer_id"])
        if dev["lan_cidr"]:
            hub.remove_routes(dev["lan_cidr"])
    except HubError as e:
        if not forzar:
            raise HTTPException(status_code=502, detail=f"{e} No se dio de baja; reintente o use forzar.")
        audit.log(user["email"], "peer_huerfano", f"device={device_id} peer={dev['hub_peer_id']} lan={dev['lan_cidr']}", client_ip(request))
    db.execute("DELETE FROM dispositivos WHERE id = %s", (device_id,))
    audit.log(user["email"], "equipo_baja", f"id={device_id} nombre={dev['nombre']} tunnel={dev['tunnel_ip']}", client_ip(request))
    return {"status": "success"}
