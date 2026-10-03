"""Monitoreo de túneles y equipos (spec 001, sección 2.1): clasificación por handshake, ciclo de observación,
historial y utilidades de lectura. La lógica es pura o recibe un `store` inyectable para poder probarla sin red ni BD."""
import ipaddress
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List, Optional

from . import addressing

log = logging.getLogger("avz.monitor")

ONLINE, OFFLINE, PENDIENTE = "online", "offline", "pendiente"
LOCK_ESTADO = 80801        # pg_try_advisory_xact_lock: un solo ciclo de observación a la vez entre réplicas
LOCK_AVISOS = 80802        # ... y un solo envío de avisos


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip() or default)
    except ValueError:
        v = default
    return max(lo, min(hi, v))


@dataclass(frozen=True)
class MonitorConfig:
    interval: int = 60            # segundos entre ciclos
    online_seconds: int = 180     # handshake más reciente que esto = en línea
    alert_minutes: int = 5        # minutos sostenidos fuera de línea antes de alertar
    retention_days: int = 90      # retención de metricas_equipo
    sample_seconds: int = 300     # intervalo mínimo entre muestras de un equipo sin cambios de estado
    rx_window_seconds: int = 90   # señal de vida rápida: rx creció dentro de esta ventana (0 = desactivada)
    rx_stale_seconds: int = 300   # una muestra previa de rx más vieja que esto no sirve para comparar
    enabled: bool = True

    @staticmethod
    def from_env() -> "MonitorConfig":
        return MonitorConfig(
            interval=_env_int("MONITOR_INTERVAL_SECONDS", 60, 10, 3600),
            online_seconds=_env_int("MONITOR_ONLINE_SECONDS", 180, 30, 3600),
            alert_minutes=_env_int("MONITOR_ALERT_MINUTES", 5, 1, 1440),
            retention_days=_env_int("MONITOR_RETENTION_DAYS", 90, 1, 3650),
            sample_seconds=_env_int("MONITOR_SAMPLE_SECONDS", 300, 10, 3600),
            rx_window_seconds=_env_int("MONITOR_RX_WINDOW_SECONDS", 90, 0, 3600),
            rx_stale_seconds=_env_int("MONITOR_RX_STALE_SECONDS", 300, 30, 86400),
            enabled=os.environ.get("MONITOR_ENABLED", "1").strip().lower() not in ("0", "false", "no"),
        )


# ---------------- clasificación ----------------
_DUR_FULL = re.compile(r"^(?:\d+(?:ms|w|d|h|m|s))+$")
_DUR_PART = re.compile(r"(\d+)(ms|w|d|h|m|s)")
_DUR_CLOCK = re.compile(r"^(?:(\d+)d)?(\d{1,2}):(\d{2}):(\d{2})$")
_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_ros_duration(value) -> Optional[float]:
    """Edad en segundos de un `last-handshake` de RouterOS ('1m4s', '00:01:04', '1d02:03:04').
    None = nunca hubo handshake (campo ausente, vacío o 'never'). ValueError si el formato es ilegible."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    if s in ("", "never", "nunca"):
        return None
    if _DUR_FULL.match(s):
        return sum(int(n) * _UNITS[u] for n, u in _DUR_PART.findall(s))
    m = _DUR_CLOCK.match(s)
    if m:
        d, h, mi, se = (int(x or 0) for x in m.groups())
        return d * 86400 + h * 3600 + mi * 60 + se
    raise ValueError("last-handshake ilegible")


def classify(age_seconds: Optional[float], online_seconds: int = 180, rx_alive: bool = False,
             rx_seen: bool = False, established: bool = False) -> str:
    """online: handshake reciente O rx creciendo. pendiente: nunca hubo handshake ni tráfico (y el equipo no estaba
    ya establecido). offline: en cualquier otro caso."""
    if rx_alive or (age_seconds is not None and age_seconds < online_seconds):
        return ONLINE
    if age_seconds is None and not rx_seen and not established:
        return PENDIENTE
    return OFFLINE


def rx_step(prev_rx, prev_at, active_at, rx, now: datetime, cfg: "MonitorConfig"):
    """Segunda señal de vida (los keepalives hacen crecer `rx` cada ~25 s aunque el handshake sea de hace 2 min).
    Devuelve (vivo, tipo, nuevo_activo_en). tipo: 'primera' (sin muestra previa válida: no se decide por rx),
    'sube', 'quieto', 'reinicio' (rx bajó: contadores del hub reiniciados, no es una caída) o 'sin_dato'."""
    if rx is None:
        kind = "sin_dato"
    elif prev_rx is None or prev_at is None or (now - prev_at).total_seconds() > cfg.rx_stale_seconds:
        kind = "primera"
    elif rx > prev_rx:
        kind, active_at = "sube", now
    elif rx < prev_rx:
        kind = "reinicio"
    else:
        kind = "quieto"
    alive = (cfg.rx_window_seconds > 0 and active_at is not None
             and 0 <= (now - active_at).total_seconds() <= cfg.rx_window_seconds)
    return alive, kind, active_at


def _to_int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def peer_index(peers: Iterable[dict]) -> dict:
    """public-key -> {age, rx, tx, invalid}. Los peers sin clave pública se ignoran."""
    out = {}
    for p in peers or []:
        key = p.get("public-key")
        if not key:
            continue
        try:
            age, invalid = parse_ros_duration(p.get("last-handshake")), False
        except ValueError:
            age, invalid = None, True
        out[key] = {"age": age, "invalid": invalid, "rx": _to_int(p.get("rx")), "tx": _to_int(p.get("tx"))}
    return out


def tunnel_ips_from_peers(peers: Iterable[dict]) -> List[str]:
    used = []
    for p in peers or []:
        for a in str(p.get("allowed-address", "")).split(","):
            try:
                net = ipaddress.ip_network(a.strip(), strict=False)
            except ValueError:
                continue
            if net.prefixlen == 32 and net.network_address in addressing.TUNNEL_POOL:
                used.append(str(net.network_address))
    return used


def free_router_ips(*sources: Iterable[str]) -> int:
    """IPs libres del rango de routers (10.254.0.2-199) descontando la unión de las fuentes dadas."""
    first, last = addressing.ROUTER_RANGE
    taken = set()
    for src in sources:
        for ip in src or []:
            try:
                addr = ipaddress.ip_address(str(ip).split("/")[0])
            except ValueError:
                continue
            if addr in addressing.TUNNEL_POOL and first <= int(addr) & 0xFF <= last:
                taken.add(int(addr))
    return (last - first + 1) - len(taken)


# ---------------- caché de peers del hub (evita saturar el hub y esperar su timeout en cada petición) ----------------
class PeerCache:
    OK_TTL = 10.0
    FAIL_TTL = 30.0

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock, self._lock = clock, threading.Lock()
        self.clear()

    def clear(self) -> None:
        self._at, self._peers, self._ready = 0.0, None, False

    def get(self, hub) -> Optional[list]:
        """Peers del hub, o None si no responde. Nunca lanza."""
        with self._lock:
            ttl = self.OK_TTL if self._peers is not None else self.FAIL_TTL
            if self._ready and self._clock() - self._at < ttl:
                return self._peers
            try:
                self._peers = hub.list_peers()
            except Exception as e:
                log.warning("hub inalcanzable al consultar peers: %s", type(e).__name__)
                self._peers = None
            self._at, self._ready = self._clock(), True
            return self._peers


peer_cache = PeerCache()


# ---------------- ciclo de observación ----------------
def run_cycle(store, peers: Optional[list], cfg: MonitorConfig, now: Optional[datetime] = None, purge: bool = False) -> dict:
    """Un ciclo: actualiza estado, muestras e incidentes. `peers=None` = hub inalcanzable: no se toca ningún estado."""
    now = now or utcnow()
    res = {"omitido": False, "hub": peers is not None, "actualizados": 0, "sin_peer": 0,
           "alertas_abiertas": 0, "alertas_cerradas": 0, "muestras": 0, "purgadas": 0}
    if not store.try_lock(LOCK_ESTADO):
        res["omitido"] = True
        return res
    if peers is None:
        log.warning("hub inalcanzable: se conserva el último estado de todos los equipos")
        return res
    index = peer_index(peers)
    open_alerts = store.open_alerts()
    for d in store.devices():
        p = index.get(d.get("public_key") or "")
        if p is None:
            res["sin_peer"] += 1            # peer ausente en el hub: anomalía, no es prueba de caída
            continue
        if p["invalid"]:
            continue
        alive, kind, active_at = rx_step(d.get("rx_ultimo"), d.get("rx_muestra_en"), d.get("rx_activo_en"), p["rx"], now, cfg)
        prev_estado = d.get("estado")
        estado = classify(p["age"], cfg.online_seconds, rx_alive=alive, rx_seen=bool(p["rx"]),
                          established=prev_estado in (ONLINE, OFFLINE))
        if kind == "reinicio" and estado == OFFLINE and prev_estado == ONLINE:
            estado = ONLINE                  # contadores reiniciados: sin evidencia de caída, se conserva
        if p["age"] is not None:
            hb = now - timedelta(seconds=p["age"])
        else:
            hb = None
        if kind == "sube":
            hb = now                         # tráfico recibido ahora = último contacto
        changed = estado != prev_estado
        desde = now if (changed or d.get("estado_desde") is None) else d["estado_desde"]
        store.update_device(d["id"], estado, hb, desde, p["rx"], now if p["rx"] is not None else None, active_at)
        res["actualizados"] += 1

        alert = open_alerts.get(d["id"])
        if estado == OFFLINE and alert is None and now - desde >= timedelta(minutes=cfg.alert_minutes):
            store.open_alert(d["id"], desde)
            res["alertas_abiertas"] += 1
        elif estado == ONLINE and alert is not None:
            store.close_alert(alert["id"], now)
            res["alertas_cerradas"] += 1

        last = d.get("ultima_muestra")
        if changed or last is None or (now - last).total_seconds() >= cfg.sample_seconds:
            store.add_metric(d["id"], now, estado, p["rx"], p["tx"])
            res["muestras"] += 1
    if purge:
        res["purgadas"] = store.purge(now - timedelta(days=cfg.retention_days))
    return res


class PgStore:
    """Acceso a PostgreSQL de un ciclo, sobre un cursor abierto (misma transacción que el candado)."""

    def __init__(self, cur):
        self.cur = cur

    def _all(self, sql, params=()):
        self.cur.execute(sql, params)
        return [dict(r) for r in self.cur.fetchall()]

    def try_lock(self, key: int) -> bool:
        self.cur.execute("SELECT pg_try_advisory_xact_lock(%s) AS ok", (key,))
        return bool(self.cur.fetchone()["ok"])

    def devices(self) -> list:
        return self._all(
            """SELECT d.id, d.estado, d.ultimo_heartbeat, d.estado_desde, d.rx_ultimo, d.rx_muestra_en, d.rx_activo_en,
                      p.public_key,
                      (SELECT max(m.instante) FROM metricas_equipo m WHERE m.equipo_id = d.id) AS ultima_muestra
               FROM dispositivos d
               JOIN wg_peers p ON p.dispositivo_id = d.id AND p.tipo = 'router' AND p.revocado_en IS NULL""")

    def update_device(self, device_id, estado, heartbeat, desde, rx=None, rx_en=None, rx_activo_en=None) -> None:
        self.cur.execute(
            """UPDATE dispositivos SET estado = %s, ultimo_heartbeat = COALESCE(%s, ultimo_heartbeat), estado_desde = %s,
                      rx_ultimo = COALESCE(%s, rx_ultimo), rx_muestra_en = COALESCE(%s, rx_muestra_en),
                      rx_activo_en = %s
               WHERE id = %s""",
            (estado, heartbeat, desde, rx, rx_en, rx_activo_en, device_id))

    def add_metric(self, device_id, instante, estado, rx, tx) -> None:
        self.cur.execute(
            "INSERT INTO metricas_equipo (equipo_id, instante, estado, rx_bytes, tx_bytes) VALUES (%s, %s, %s, %s, %s)",
            (device_id, instante, estado, rx, tx))

    def open_alerts(self) -> dict:
        return {r["dispositivo_id"]: r for r in self._all(
            "SELECT id, dispositivo_id, notificado_caida_en FROM alertas WHERE estado = 'abierto'")}

    def open_alert(self, device_id, inicio) -> None:
        self.cur.execute(
            "INSERT INTO alertas (dispositivo_id, estado, inicio) VALUES (%s, 'abierto', %s) ON CONFLICT DO NOTHING",
            (device_id, inicio))

    def close_alert(self, alert_id, now) -> None:
        self.cur.execute("UPDATE alertas SET estado = 'cerrado', cerrado_en = %s WHERE id = %s AND estado = 'abierto'",
                         (now, alert_id))

    def purge(self, cutoff) -> int:
        self.cur.execute("DELETE FROM metricas_equipo WHERE instante < %s", (cutoff,))
        return self.cur.rowcount or 0

    # --- avisos ---
    def smtp_config(self) -> Optional[dict]:
        rows = self._all(
            """SELECT smtp_host, smtp_port, smtp_user, smtp_password, smtp_encryption, remitente_email, remitente_nombre
               FROM configuracion_smtp WHERE id = 1""")
        return rows[0] if rows else None

    def pending_notifications(self, max_attempts: int) -> list:
        return self._all(
            """SELECT a.id, a.estado, a.inicio, a.cerrado_en, a.notificado_caida_en, a.notificado_recuperacion_en,
                      d.nombre AS equipo, s.nombre AS sede, s.contacto_email, u.email AS partner_email
               FROM alertas a
               JOIN dispositivos d ON d.id = a.dispositivo_id
               LEFT JOIN sedes s ON s.id = d.sede_id
               LEFT JOIN usuarios u ON u.id = s.partner_id AND u.activo
               WHERE (a.estado = 'abierto' AND a.notificado_caida_en IS NULL AND a.intentos_caida < %s)
                  OR (a.estado = 'cerrado' AND a.notificado_caida_en IS NOT NULL
                      AND a.notificado_recuperacion_en IS NULL AND a.intentos_recuperacion < %s)
               ORDER BY a.id""", (max_attempts, max_attempts))

    def mark_attempt(self, alert_id: int, kind: str, ok: bool, now) -> None:
        if kind == "caida":
            sql = ("UPDATE alertas SET intentos_caida = intentos_caida + 1, "
                   "notificado_caida_en = CASE WHEN %s THEN %s ELSE notificado_caida_en END WHERE id = %s")
        else:
            sql = ("UPDATE alertas SET intentos_recuperacion = intentos_recuperacion + 1, "
                   "notificado_recuperacion_en = CASE WHEN %s THEN %s ELSE notificado_recuperacion_en END WHERE id = %s")
        self.cur.execute(sql, (ok, now, alert_id))


_last_purge = 0.0


def run_db_cycle(peers: Optional[list], cfg: MonitorConfig) -> dict:
    """Ciclo completo contra PostgreSQL (se ejecuta en un hilo). Limpieza de retención como máximo cada hora."""
    global _last_purge
    from .. import db
    due = _last_purge == 0.0 or time.monotonic() - _last_purge >= 3600
    with db.cursor() as cur:
        res = run_cycle(PgStore(cur), peers, cfg, utcnow(), purge=due and peers is not None)
    if due and res.get("hub") and not res.get("omitido"):
        _last_purge = time.monotonic()
    return res


# ---------------- historial ----------------
_RANK = {ONLINE: 0, PENDIENTE: 1, OFFLINE: 2}


def downsample(rows: List[dict], max_points: int) -> List[dict]:
    """Reduce la serie a `max_points` como máximo: un punto por tramo (último tráfico y peor estado del tramo)."""
    if len(rows) <= max_points or max_points < 2:
        return list(rows)
    t0, t1 = rows[0]["instante"], rows[-1]["instante"]
    span = (t1 - t0).total_seconds()
    if span <= 0:
        return rows[-max_points:]
    size = span / max_points
    buckets = {}
    for r in rows:
        i = min(int((r["instante"] - t0).total_seconds() / size), max_points - 1)
        prev = buckets.get(i)
        worst = r["estado"]
        if prev and _RANK.get(prev["estado"], 0) > _RANK.get(worst, 0):
            worst = prev["estado"]
        buckets[i] = {**r, "estado": worst}
    return [buckets[i] for i in sorted(buckets)]
