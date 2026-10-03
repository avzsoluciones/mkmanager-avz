"""Alertas por correo de caída y recuperación de equipos. Una alerta por incidente (tabla `alertas`).
El envío usa un `sender` inyectable; nunca se envía nada si SMTP no está configurado. Sin secretos en el correo."""
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from typing import Callable, Optional

from . import monitoring

log = logging.getLogger("avz.alertas")

MAX_ATTEMPTS = 3
SMTP_TIMEOUT = 10
_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")


def _clean(text, limit: int = 120) -> str:
    return re.sub(r"[\r\n\t]+", " ", str(text or "")).strip()[:limit]


def recipients_for(row: dict) -> list:
    """Contacto de la sede + cuenta del aliado + copia opcional ALERT_EXTRA_RECIPIENTS (lista separada por comas)."""
    out, seen = [], set()
    extra = os.environ.get("ALERT_EXTRA_RECIPIENTS", "").split(",")
    for e in (row.get("contacto_email"), row.get("partner_email"), *extra):
        e = (e or "").strip()
        if e and _EMAIL_RE.match(e) and e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out


def _fmt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "-"


def build_message(kind: str, row: dict, cfg: dict, to: list) -> EmailMessage:
    equipo, sede = _clean(row.get("equipo")), _clean(row.get("sede")) or "sin sede"
    msg = EmailMessage()
    msg["From"] = f"{_clean(cfg.get('remitente_nombre') or 'AVZCloud Alertas', 80)} <{_clean(cfg['remitente_email'], 150)}>"
    msg["To"] = ", ".join(to)
    if kind == "caida":
        msg["Subject"] = f"[AVZCloud] Equipo fuera de línea: {equipo} ({sede})"
        body = (f"El equipo \"{equipo}\" de la sede \"{sede}\" lleva sin conexión con AVZCloud desde {_fmt(row.get('inicio'))}.\n\n"
                "Qué puede revisar: que el equipo tenga energía, que tenga salida a internet y que no haya cambios recientes en su red.\n"
                "Si el problema continúa, responda a este mensaje o abra un ticket de soporte.\n")
    else:
        dur = ""
        if row.get("inicio") and row.get("cerrado_en"):
            mins = int((row["cerrado_en"] - row["inicio"]).total_seconds() // 60)
            dur = f" Estuvo fuera de línea unos {mins} minutos."
        msg["Subject"] = f"[AVZCloud] Equipo recuperado: {equipo} ({sede})"
        body = f"El equipo \"{equipo}\" de la sede \"{sede}\" volvió a estar en línea el {_fmt(row.get('cerrado_en'))}.{dur}\n"
    msg.set_content(body + "\nMensaje automático de AVZCloud Central Manager.\n")
    return msg


def smtp_sender(cfg: dict, msg: EmailMessage) -> None:
    """Envío real por SMTP con timeout. Nunca se usa en pruebas."""
    enc = str(cfg.get("smtp_encryption") or "").upper()
    host, port = cfg["smtp_host"], int(cfg["smtp_port"])
    ctx = ssl.create_default_context()
    srv = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=ctx) if enc == "SSL" else smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
    with srv:
        if enc in ("TLS", "STARTTLS"):
            srv.starttls(context=ctx)
        if cfg.get("smtp_user"):
            srv.login(cfg["smtp_user"], cfg["smtp_password"])
        srv.send_message(msg)


def notify_pending(store, sender: Callable[[dict, EmailMessage], None], now: Optional[datetime] = None) -> dict:
    """Envía las alertas pendientes (caída de incidentes abiertos, recuperación de los cerrados que se notificaron)."""
    now = now or monitoring.utcnow()
    res = {"smtp": True, "enviadas": 0, "fallidas": 0, "sin_destinatarios": 0}
    if not store.try_lock(monitoring.LOCK_AVISOS):
        res["smtp"] = False
        return res
    cfg = store.smtp_config()
    if not cfg or not cfg.get("smtp_host") or not cfg.get("remitente_email"):
        res["smtp"] = False
        return res
    for row in store.pending_notifications(MAX_ATTEMPTS):
        kind = "caida" if row["estado"] == "abierto" else "recuperacion"
        to = recipients_for(row)
        if not to:
            res["sin_destinatarios"] += 1
            continue
        try:
            sender(cfg, build_message(kind, row, cfg, to))
            ok = True
        except Exception as e:                      # nunca registrar el mensaje de error: puede traer datos del SMTP
            log.warning("no se pudo enviar el aviso %s de la alerta %s: %s", kind, row["id"], type(e).__name__)
            ok = False
        store.mark_attempt(row["id"], kind, ok, now)
        res["enviadas" if ok else "fallidas"] += 1
    return res


def run_notify() -> dict:
    """Envío de avisos contra PostgreSQL (se ejecuta en un hilo)."""
    from .. import db
    with db.cursor() as cur:
        return notify_pending(monitoring.PgStore(cur), smtp_sender)
