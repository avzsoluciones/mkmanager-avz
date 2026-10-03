"""Panel, estado por equipo, historial y lectura de auditoría (spec 001, secciones 2.1 y 3.2).

Alcance de la auditoría (`GET /audit`):
  - superadmin y soporte: todo el registro.
  - partner y tecnico: solo acciones realizadas por usuarios de SU cartera (el propio aliado, sus técnicos y sus clientes),
    filtradas en SQL por la tabla `usuarios` (tenant_id / partner_id), nunca por texto libre. Las acciones de personal de
    AVZCloud sobre equipos del aliado no aparecen hasta que `audit_logs` registre el equipo o la sede de forma estructurada
    (cambio solicitado en audit.py).
  - cliente: 403 (la matriz de permisos no le concede `auditoria.ver`).
`detalles` se sanea antes de salir: valores de claves/tokens y cadenas largas tipo clave quedan ocultos.
"""
import re
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..deps import get_hub
from ..permissions import TENANT_ROLES, require
from ..security import sede_scope
from ..services import monitoring as mon
from ..services.hub import HubClient

router = APIRouter(prefix="/api/v1", tags=["monitor"])

AUDIT_MAX_LIMIT = 200
AUDIT_DEFAULT_LIMIT = 50
HISTORY_MAX_POINTS = 500
HISTORY_MAX_ROWS = 20000
HISTORY_MAX_HOURS = 24 * 90
DASHBOARD_ACTIVITY = 8


def _cfg() -> mon.MonitorConfig:
    return mon.MonitorConfig.from_env()


# ---------------- saneo de detalles ----------------
_SECRET_KV = re.compile(
    r"(?i)\b(pass(?:word)?|passwd|clave|contrase\w*|secret\w*|token|private[_-]?key|api[_-]?key|psk|authorization|bearer)\b(\s*[=:]\s*)\S+")
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
_PEM = re.compile(r"-----BEGIN [^-]*-----.*", re.S)


def sanitize_detail(text) -> str:
    s = str(text or "")
    s = _PEM.sub("[oculto]", s)
    s = _SECRET_KV.sub(lambda m: f"{m.group(1)}{m.group(2)}[oculto]", s)
    s = _LONG_TOKEN.sub("[oculto]", s)
    return s[:300]


def _audit_scope(user: dict) -> tuple:
    """Fragmento SQL (alias `a` = audit_logs) con las acciones que el usuario puede ver."""
    if user["role"] in ("superadmin", "soporte"):
        return "TRUE", ()
    if user["role"] in TENANT_ROLES:
        t = user.get("tenant_id") or user["id"]
        return ("lower(a.usuario_email) IN (SELECT lower(u.email) FROM usuarios u "
                "WHERE u.id = %s OR u.tenant_id = %s OR u.partner_id = %s)", (t, t, t))
    return "FALSE", ()


def _load_device(device_id: int, user: dict, extra_select: str = "", extra_join: str = "") -> dict:
    where, params = sede_scope(user)
    row = db.fetch_one(
        f"""SELECT d.id, d.estado, d.ultimo_heartbeat {extra_select}
            FROM dispositivos d JOIN sedes s ON s.id = d.sede_id {extra_join}
            WHERE d.id = %s AND {where}""", (device_id, *params))
    if not row:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")
    return row


# ---------------- panel ----------------
@router.get("/dashboard")
def dashboard(user: dict = Depends(require("panel.ver")), hub: HubClient = Depends(get_hub)):
    where, params = sede_scope(user)
    sedes = db.fetch_one(f"SELECT count(*) AS n FROM sedes s WHERE {where}", params) or {}
    eq = db.fetch_one(
        f"""SELECT count(*) AS equipos,
                   count(*) FILTER (WHERE d.estado = 'online') AS online,
                   count(*) FILTER (WHERE d.estado = 'offline') AS offline,
                   count(*) FILTER (WHERE d.estado = 'pendiente') AS pendientes
            FROM dispositivos d JOIN sedes s ON s.id = d.sede_id WHERE {where}""", params) or {}
    out = {
        "sedes": int(sedes.get("n") or 0),
        "equipos": int(eq.get("equipos") or 0),
        "online": int(eq.get("online") or 0),
        "offline": int(eq.get("offline") or 0),
        "pendientes": int(eq.get("pendientes") or 0),
        "ips_libres": None, "ips_total": None, "ips_fuente": None,
        "actividad": [],
    }
    if user["role"] != "cliente":
        # Pool global del rango de routers: BD ∪ peers del hub. Si el hub falla, solo BD y se indica.
        first, last = mon.addressing.ROUTER_RANGE
        db_ips = [r["tunnel_ip"] for r in db.fetch_all(
            """SELECT tunnel_ip FROM dispositivos WHERE tunnel_ip IS NOT NULL
               UNION SELECT tunnel_ip FROM wg_peers WHERE revocado_en IS NULL""")]
        peers = mon.peer_cache.get(hub)
        hub_ips = mon.tunnel_ips_from_peers(peers) if peers is not None else []
        out["ips_libres"] = mon.free_router_ips(db_ips, hub_ips)
        out["ips_total"] = last - first + 1
        out["ips_fuente"] = "bd+hub" if peers is not None else "bd"
        a_where, a_params = _audit_scope(user)
        rows = db.fetch_all(
            f"""SELECT a.fecha, a.usuario_email, a.accion, a.detalles FROM audit_logs a
                WHERE {a_where} ORDER BY a.id DESC LIMIT %s""", (*a_params, DASHBOARD_ACTIVITY))
        out["actividad"] = [{"fecha": r["fecha"], "usuario": (r["usuario_email"] or "sistema").split("@")[0],
                             "accion": r["accion"], "detalle": sanitize_detail(r["detalles"])} for r in rows]
    return out


# ---------------- estado e historial por equipo ----------------
@router.get("/devices/{device_id}/status")
def device_status(device_id: int, user: dict = Depends(require("equipo.estado")), hub: HubClient = Depends(get_hub)):
    row = _load_device(
        device_id, user,
        extra_select=", d.rx_activo_en, p.public_key, m.rx_bytes, m.tx_bytes",
        extra_join="""LEFT JOIN wg_peers p ON p.dispositivo_id = d.id AND p.revocado_en IS NULL
                      LEFT JOIN LATERAL (SELECT rx_bytes, tx_bytes FROM metricas_equipo
                                         WHERE equipo_id = d.id ORDER BY instante DESC LIMIT 1) m ON TRUE""")
    peers = mon.peer_cache.get(hub)
    peer = mon.peer_index(peers).get(row.get("public_key") or "") if peers is not None else None
    if peer and not peer["invalid"]:
        cfg, now = _cfg(), mon.utcnow()
        act = row.get("rx_activo_en")
        alive = bool(act and cfg.rx_window_seconds > 0 and 0 <= (now - act).total_seconds() <= cfg.rx_window_seconds)
        estado = mon.classify(peer["age"], cfg.online_seconds, rx_alive=alive, rx_seen=bool(peer["rx"]),
                              established=row["estado"] in (mon.ONLINE, mon.OFFLINE))
        contacto = now - timedelta(seconds=peer["age"]) if peer["age"] is not None else row.get("ultimo_heartbeat")
        if estado == mon.PENDIENTE:
            contacto = None
        return {"estado": estado, "ultimo_contacto": contacto, "rx_bytes": peer["rx"], "tx_bytes": peer["tx"],
                "fuente": "en_vivo", "en_vivo": True}
    aviso = ("No se pudo consultar el estado en vivo; se muestra el último estado conocido." if peers is None
             else "El equipo no figura en el servidor de túneles; se muestra el último estado conocido.")
    return {"estado": row["estado"],
            "ultimo_contacto": None if row["estado"] == mon.PENDIENTE else row.get("ultimo_heartbeat"),
            "rx_bytes": row.get("rx_bytes"), "tx_bytes": row.get("tx_bytes"),
            "fuente": "ultimo_conocido", "en_vivo": False, "aviso": aviso}


@router.get("/devices/{device_id}/history")
def device_history(device_id: int, horas: int = 24, user: dict = Depends(require("equipo.estado"))):
    _load_device(device_id, user)
    horas = max(1, min(int(horas), HISTORY_MAX_HOURS))
    desde = mon.utcnow() - timedelta(hours=horas)
    rows = db.fetch_all(
        """SELECT instante, estado, rx_bytes, tx_bytes FROM metricas_equipo
           WHERE equipo_id = %s AND instante >= %s ORDER BY instante DESC LIMIT %s""",
        (device_id, desde, HISTORY_MAX_ROWS))
    rows.reverse()
    total = len(rows)
    online = sum(1 for r in rows if r["estado"] == mon.ONLINE)
    pts = mon.downsample(rows, HISTORY_MAX_POINTS)
    return {
        "equipo_id": device_id, "horas": horas, "total_muestras": total,
        "online_pct": round(100.0 * online / total, 1) if total else None,
        "reducido": len(pts) < total,
        "puntos": [{"t": r["instante"], "estado": r["estado"], "rx_bytes": r["rx_bytes"], "tx_bytes": r["tx_bytes"]}
                   for r in pts],
    }


# ---------------- auditoría ----------------
@router.get("/audit")
def read_audit(limit: int = AUDIT_DEFAULT_LIMIT, antes_id: Optional[int] = None, accion: Optional[str] = None,
               usuario: Optional[str] = None, desde: Optional[datetime] = None, hasta: Optional[datetime] = None,
               user: dict = Depends(require("auditoria.ver"))):
    """Solo lectura. Paginación por cursor: pase `antes_id` = id del último registro recibido. Tope: 200."""
    limit = max(1, min(int(limit), AUDIT_MAX_LIMIT))
    where, params = _audit_scope(user)
    conds, args = [where], list(params)
    if antes_id is not None:
        conds.append("a.id < %s")
        args.append(antes_id)
    if accion:
        conds.append("a.accion = %s")
        args.append(accion[:100])
    if usuario:
        conds.append("lower(a.usuario_email) = lower(%s)")
        args.append(usuario[:150])
    if desde:
        conds.append("a.fecha >= %s")
        args.append(desde)
    if hasta:
        conds.append("a.fecha <= %s")
        args.append(hasta)
    args.append(limit)
    rows = db.fetch_all(
        f"""SELECT a.id, a.fecha, a.usuario_email, a.accion, a.detalles FROM audit_logs a
            WHERE {' AND '.join(conds)} ORDER BY a.id DESC LIMIT %s""", tuple(args))
    return [{"id": r["id"], "fecha": r["fecha"], "usuario_email": r["usuario_email"], "accion": r["accion"],
             "detalles": sanitize_detail(r["detalles"])} for r in rows]
