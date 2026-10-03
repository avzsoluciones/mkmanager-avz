"""Ejecución de operaciones sobre un router (API RouterOS) y persistencia de las aplicaciones de plantillas.

Contenido:
- `LibSession`: sesión sobre librouteros (puerto `puerto_gestion`, host `tunnel_ip`, timeout corto).
- `plan_inverses` / `execute` / `verify` / `rollback`: lógica de aplicar y deshacer, probada con una sesión falsa.
- `run_apply` y `revert_row`: orquestación (aplicar con auto-reversión si algo falla; revertir lo vencido).
- `PgStore`: único lugar con SQL de `plantillas_aplicadas` (el estado vive en BD: sobrevive a reinicios y a réplicas).

Los mensajes que llegan al navegador son genéricos: no incluyen credenciales ni detalles internos (SEG-028).
"""
import hashlib
import ipaddress
import json
import logging
from datetime import timezone
from typing import Callable, Dict, List, Optional

from librouteros import connect as ros_connect

from .. import db
from . import wgkeys
from .addressing import TUNNEL_POOL
from .ops import (
    FILTER, MGMT_COMMENT, PLACE_AFTER_MGMT, Op, OpError, assert_safe, ops_from_json, ops_to_json, render_cli, stamp,
    tag_for,
)

log = logging.getLogger("avz.edge")

CONNECT_TIMEOUT = 6
MAX_REVERT_ATTEMPTS = 5
RETRY_BASE_SECONDS = 30
STALE_APPLYING_MINUTES = 3


class EdgeError(Exception):
    """Fallo al hablar con el equipo. El mensaje es apto para mostrarse al usuario."""


class ApplyFailed(EdgeError):
    def __init__(self, message: str, rolled_back: bool):
        super().__init__(message)
        self.rolled_back = rolled_back


class Conflict(Exception):
    """Aplicación duplicada o concurrente (HTTP 409)."""


# --------------------------------------------------------------------------- sesión con el router
class LibSession:
    """Interfaz mínima sobre librouteros. Los tests la sustituyen por una sesión en memoria."""

    def __init__(self, api):
        self.api = api

    def rows(self, path) -> List[dict]:
        return [dict(r) for r in self.api.path(*path)]

    def add(self, path, attrs: dict) -> str:
        return str(self.api.path(*path).add(**attrs))

    def set(self, path, attrs: dict, id: Optional[str] = None) -> None:
        kw = dict(attrs)
        if id:
            kw[".id"] = id
        self.api.path(*path).update(**kw)

    def remove(self, path, id: str) -> None:
        self.api.path(*path).remove(id)

    def close(self) -> None:
        try:
            self.api.close()
        except Exception:
            pass


def _tunnel_host(dev: dict) -> str:
    try:
        ip = ipaddress.ip_address(str(dev.get("tunnel_ip") or ""))
    except ValueError:
        raise EdgeError("El equipo no tiene una IP de túnel válida.")
    if ip not in TUNNEL_POOL or int(ip) & 0xFF in (0, 1, 255):       # solo equipos de sede dentro del pool de túneles
        raise EdgeError("El equipo no tiene una IP de túnel válida.")
    return str(ip)


def _default_factory(dev: dict) -> LibSession:
    host = _tunnel_host(dev)
    if not (dev.get("api_user") and dev.get("api_password_enc")):
        raise EdgeError("El equipo no tiene credenciales de gestión.")
    try:
        password = wgkeys.decrypt_secret(dev["api_password_enc"])
        api = ros_connect(host=host, username=dev["api_user"], password=password,
                          port=int(dev.get("puerto_gestion") or 8728), timeout=CONNECT_TIMEOUT)
    except Exception as e:
        log.warning("Conexión API fallida con %s: %s", host, type(e).__name__)
        raise EdgeError("No se pudo conectar con el equipo por el túnel.")
    return LibSession(api)


# Punto de inyección para pruebas: callable(dev_dict) -> sesión.
session_factory: Callable[[dict], object] = _default_factory


def open_session(dev: dict):
    return session_factory(dev)


# --------------------------------------------------------------------------- utilidades
def _truthy(v) -> bool:
    return v is True or str(v).lower() in ("true", "yes")


def _matches(row: dict, find: Dict[str, str]) -> bool:
    return all(str(row.get(k, "")) == v for k, v in find.items())


def _mgmt_anchor(session) -> Optional[str]:
    """Devuelve el .id de la regla que sigue a la de gestión (las nuevas se insertan antes de ella)."""
    rows = session.rows(FILTER)
    idx = next((i for i, r in enumerate(rows) if r.get("comment") == MGMT_COMMENT), None)
    if idx is None:
        raise EdgeError("No se encontró la regla de gestión «AVZCloud mgmt» en el equipo; no se aplica por seguridad.")
    if _truthy(rows[idx].get("disabled")):
        raise EdgeError("La regla de gestión «AVZCloud mgmt» está deshabilitada; no se aplica por seguridad.")
    return rows[idx + 1][".id"] if idx + 1 < len(rows) else None


def _short(e: Exception) -> str:
    return " ".join(str(e).split())[:120]


# --------------------------------------------------------------------------- planificar, ejecutar, verificar, deshacer
def plan_inverses(session, ops: List[Op], tag: str) -> List[Op]:
    """Fase de solo lectura: calcula la operación inversa de cada operación. No cambia nada en el router."""
    inv: List[Op] = []
    seen_remove = set()
    for o in ops:
        if o.action == "add":
            key = o.path
            if key not in seen_remove:
                seen_remove.add(key)
                inv.append(Op(o.path, "remove", {}, {"comment": tag}))
        elif o.action == "set":
            rows = session.rows(o.path)
            if o.find:
                rows = [r for r in rows if _matches(r, o.find)]
            if not rows:
                raise EdgeError(f"El equipo no tiene el recurso /{'/'.join(o.path)} que se quiere modificar.")
            targets = rows if o.find else rows[:1]
            for r in targets:
                prev = {k: ("" if r.get(k) is None else _fmt(r.get(k))) for k in o.attrs}
                inv.append(Op(o.path, "set", prev, {".id": r[".id"]} if o.find else None))
        else:  # remove: la inversa recrea lo borrado
            for r in [r for r in session.rows(o.path) if _matches(r, o.find)]:
                attrs = {k: _fmt(v) for k, v in r.items() if not k.startswith(".") and k not in ("dynamic", "invalid", "inactive")}
                if attrs:
                    inv.append(Op(o.path, "add", attrs))
    inv.reverse()
    return inv


def _fmt(v) -> str:
    if v is True:
        return "yes"
    if v is False:
        return "no"
    return str(v)


def execute(session, ops: List[Op]) -> None:
    n = len(ops)
    # Antes de cambiar nada: si hay reglas de firewall, la regla de gestión debe existir y estar activa.
    anchor = _mgmt_anchor(session) if any(o.place == PLACE_AFTER_MGMT for o in ops) else None
    for i, o in enumerate(ops, 1):
        try:
            if o.action == "add":
                attrs = dict(o.attrs)
                if o.place == PLACE_AFTER_MGMT:
                    if anchor:
                        attrs["place-before"] = anchor
                session.add(o.path, attrs)
            elif o.action == "set":
                if o.find:
                    ids = [r[".id"] for r in session.rows(o.path) if _matches(r, o.find)]
                    for rid in ids:
                        session.set(o.path, o.attrs, rid)
                else:
                    session.set(o.path, o.attrs)
            else:
                for r in [r for r in session.rows(o.path) if _matches(r, o.find)]:
                    session.remove(o.path, r[".id"])
        except EdgeError:
            raise
        except Exception as e:
            raise EdgeError(f"El equipo rechazó la operación {i} de {n} ({o.describe()}): {_short(e)}")


def verify(session, ops: List[Op], tag: str) -> None:
    """Lee el router y comprueba el resultado real: elementos creados presentes y regla de gestión intacta."""
    adds: Dict[tuple, int] = {}
    for o in ops:
        if o.action == "add":
            adds[o.path] = adds.get(o.path, 0) + 1
    for path, expected in adds.items():
        found = [r for r in session.rows(path) if r.get("comment") == tag]
        if len(found) != expected:
            raise EdgeError(f"Verificación fallida: se esperaban {expected} elementos en /{'/'.join(path)} y hay {len(found)}.")
    if FILTER in adds:
        rows = session.rows(FILTER)
        mgmt = next((i for i, r in enumerate(rows) if r.get("comment") == MGMT_COMMENT), None)
        if mgmt is None or _truthy(rows[mgmt].get("disabled")):
            raise EdgeError("Verificación fallida: la regla de gestión no está activa.")
        if any(r.get("comment") == tag and i < mgmt for i, r in enumerate(rows)):
            raise EdgeError("Verificación fallida: una regla nueva quedó antes de la regla de gestión.")


def health_check(dev: dict) -> None:
    """Abre una conexión NUEVA (no la ya establecida) para comprobar que la gestión sigue entrando."""
    s2 = open_session(dev)
    try:
        s2.rows(("system", "identity"))
    except EdgeError:
        raise
    except Exception as e:
        raise EdgeError(f"El equipo dejó de responder a conexiones nuevas: {_short(e)}")
    finally:
        s2.close()


def rollback(session, inversas: List[Op]) -> List[str]:
    """Aplica las inversas. Idempotente y tolerante: devuelve la lista de errores (vacía si todo salió bien)."""
    errors: List[str] = []
    try:
        assert_safe(inversas)
    except OpError as e:
        return [f"Inversas no válidas: {e}"]
    for o in inversas:
        try:
            if o.action == "remove":
                for r in [r for r in session.rows(o.path) if _matches(r, o.find)]:
                    session.remove(o.path, r[".id"])
            elif o.action == "set":
                if o.find:
                    session.set(o.path, o.attrs, o.find[".id"])
                else:
                    session.set(o.path, o.attrs)
            else:
                if not any(_matches(r, o.attrs) for r in session.rows(o.path)):
                    session.add(o.path, dict(o.attrs))
        except Exception as e:
            errors.append(f"{o.describe()}: {_short(e)}")
    return errors


# --------------------------------------------------------------------------- orquestación
def run_apply(store, dev: dict, app_id: int, ops: List[Op]) -> List[Op]:
    """Aplica `ops` al equipo. Si algo falla (incluida la verificación o el chequeo de conexión nueva), deshace lo hecho."""
    tag = tag_for(app_id)
    final = stamp(ops, tag)
    assert_safe(final)
    sess = open_session(dev)
    try:
        inv = plan_inverses(sess, final, tag)
        assert_safe(inv)
        store.save_plan(app_id, final, inv, render_cli(final, tag))   # primero se persiste cómo deshacer
        try:
            execute(sess, final)
            verify(sess, final, tag)
            health_check(dev)
        except Exception as e:
            errs = rollback(sess, inv)
            msg = str(e) if isinstance(e, EdgeError) else "Error inesperado al aplicar en el equipo."
            if not isinstance(e, EdgeError):
                log.exception("Fallo inesperado aplicando app=%s", app_id)
            raise ApplyFailed(msg, rolled_back=not errs)
        return final
    finally:
        sess.close()


def revert_row(row: dict) -> dict:
    """Revierte una aplicación vencida. Devuelve el resultado a persistir (estado, resultado, intentos, reintento)."""
    attempts = int(row.get("intentos_reversion") or 0)
    inv_json = row.get("inversas")
    if not inv_json:
        if row["estado"] == "aplicando":
            return {"estado": "error", "resultado": "Interrumpida antes de aplicarse; no hay nada que revertir.",
                    "intentos": attempts, "reintentar_en": None}
        return {"estado": "error_reversion", "resultado": "No hay operaciones inversas registradas.",
                "intentos": attempts, "reintentar_en": None}
    try:
        inv = ops_from_json(inv_json)
        sess = open_session(row)
        try:
            errs = rollback(sess, inv)
        finally:
            sess.close()
    except EdgeError as e:
        errs = [str(e)]
    except OpError as e:
        errs = [f"Inversas no válidas: {e}"]
    except Exception as e:
        log.exception("Fallo inesperado revirtiendo app=%s", row.get("id"))
        errs = [f"Error inesperado ({type(e).__name__})."]
    attempts += 1
    if not errs:
        return {"estado": "revertida", "resultado": "Revertida automáticamente por no confirmarse a tiempo."
                if row["estado"] == "aplicada" else "Aplicación interrumpida: revertida.",
                "intentos": attempts, "reintentar_en": None}
    if attempts >= MAX_REVERT_ATTEMPTS:
        return {"estado": "error_reversion", "resultado": ("No se pudo revertir tras %d intentos: %s" % (attempts, errs[0]))[:480],
                "intentos": attempts, "reintentar_en": None}
    return {"estado": row["estado"], "resultado": f"Reversión pendiente (intento {attempts}): {errs[0]}"[:480],
            "intentos": attempts, "reintentar_en": RETRY_BASE_SECONDS * attempts}


def params_hash(params: dict) -> str:
    return hashlib.sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def iso(dt) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- persistencia
_LOCK_NS = 7006     # espacio de claves para pg_advisory_xact_lock(ns, dispositivo_id)

_DEVICE_COLS = """d.id, d.nombre, d.marca, d.estado, d.tunnel_ip, d.puerto_gestion, d.api_user, d.api_password_enc,
                  d.lan_cidr, d.ip_local, d.wan_config, d.lan_config"""

_DUE_SQL = f"""
SELECT a.id, a.dispositivo_id, a.plantilla, a.estado, a.inversas, a.intentos_reversion, a.usuario_email,
       d.tunnel_ip, d.puerto_gestion, d.api_user, d.api_password_enc
FROM plantillas_aplicadas a JOIN dispositivos d ON d.id = a.dispositivo_id
WHERE ((a.estado = 'aplicada' AND a.revertir_en IS NOT NULL AND a.revertir_en <= now())
    OR (a.estado = 'aplicando' AND a.creado_en < now() - interval '{STALE_APPLYING_MINUTES} minutes'))
  AND (a.reintentar_en IS NULL OR a.reintentar_en <= now())
ORDER BY COALESCE(a.revertir_en, a.creado_en)
LIMIT 1
FOR UPDATE OF a SKIP LOCKED
"""


class PgStore:
    def get_device(self, device_id: int, where: str, params: tuple) -> Optional[dict]:
        return db.fetch_one(
            f"SELECT {_DEVICE_COLS} FROM dispositivos d JOIN sedes s ON s.id = d.sede_id WHERE d.id = %s AND {where}",
            (device_id, *params))

    def begin(self, device_id: int, plantilla: str, params: dict, phash: str, email: str) -> int:
        """Reserva la aplicación bajo un candado por equipo: un solo trabajo activo y sin duplicados (409)."""
        with db.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_LOCK_NS, device_id))
            cur.execute(
                """SELECT plantilla, params_hash, estado FROM plantillas_aplicadas
                   WHERE dispositivo_id = %s
                     AND (estado = 'aplicando' OR (estado = 'aplicada' AND revertir_en IS NOT NULL AND revertir_en > now()))""",
                (device_id,))
            live = cur.fetchall()
            if any(r["plantilla"] == plantilla and r["params_hash"] == phash for r in live):
                raise Conflict("Esta misma aplicación ya está en curso en el equipo. Espera a que termine o confírmala.")
            if any(r["estado"] == "aplicando" for r in live):
                raise Conflict("Hay otra aplicación en curso en este equipo. Espera unos segundos.")
            cur.execute(
                """INSERT INTO plantillas_aplicadas (dispositivo_id, plantilla, parametros, params_hash, modo, estado, usuario_email)
                   VALUES (%s, %s, %s::jsonb, %s, 'api', 'aplicando', %s) RETURNING id""",
                (device_id, plantilla, json.dumps(params), phash, email))
            return cur.fetchone()["id"]

    def save_plan(self, app_id: int, ops: List[Op], inversas: List[Op], script: str) -> None:
        db.execute("UPDATE plantillas_aplicadas SET ops = %s::jsonb, inversas = %s::jsonb, script = %s WHERE id = %s",
                   (json.dumps(ops_to_json(ops)), json.dumps(ops_to_json(inversas)), script, app_id))

    def finish_applied(self, app_id: int, minutes: Optional[int]):
        """Marca 'aplicada' y fija el plazo de reversión (reloj de la BD). Devuelve revertir_en (datetime o None)."""
        row = db.fetch_one(
            """UPDATE plantillas_aplicadas
               SET estado = 'aplicada', resultado = 'Aplicada y verificada en el equipo.',
                   revertir_en = CASE WHEN %s::int IS NULL THEN NULL ELSE now() + %s::int * interval '1 minute' END
               WHERE id = %s RETURNING revertir_en""", (minutes, minutes, app_id))
        return row["revertir_en"] if row else None

    def mark_error(self, app_id: int, message: str) -> None:
        db.execute("UPDATE plantillas_aplicadas SET estado = 'error', resultado = %s WHERE id = %s", (message[:480], app_id))

    def schedule_revert_now(self, app_id: int, message: str) -> None:
        """Rollback inmediato incompleto: lo toma la tarea de reversión (reintentos con espera)."""
        db.execute(
            """UPDATE plantillas_aplicadas SET estado = 'aplicada', revertir_en = now(), intentos_reversion = 1,
                   reintentar_en = now() + interval '30 seconds', resultado = %s WHERE id = %s""",
            (message[:480], app_id))

    def get_application(self, app_id: int, where: str, params: tuple) -> Optional[dict]:
        return db.fetch_one(
            f"""SELECT a.id, a.dispositivo_id, a.plantilla, a.estado, a.revertir_en, a.usuario_email
                FROM plantillas_aplicadas a JOIN dispositivos d ON d.id = a.dispositivo_id
                JOIN sedes s ON s.id = d.sede_id WHERE a.id = %s AND {where}""", (app_id, *params))

    def confirm(self, app_id: int) -> bool:
        """Confirma si sigue 'aplicada' y dentro de plazo. Atómico: no compite con la reversión."""
        row = db.fetch_one(
            """UPDATE plantillas_aplicadas SET estado = 'confirmada', confirmada_en = now()
               WHERE id = %s AND estado = 'aplicada' AND (revertir_en IS NULL OR revertir_en > now()) RETURNING id""",
            (app_id,))
        return row is not None

    def request_revert(self, app_id: int) -> bool:
        row = db.fetch_one(
            """UPDATE plantillas_aplicadas SET revertir_en = now(), reintentar_en = NULL
               WHERE id = %s AND estado = 'aplicada' RETURNING id""", (app_id,))
        return row is not None

    def list_for_device(self, device_id: int) -> List[dict]:
        return db.fetch_all(
            """SELECT id, plantilla, estado, creado_en, revertir_en, usuario_email, resultado
               FROM plantillas_aplicadas WHERE dispositivo_id = %s ORDER BY id DESC LIMIT 100""", (device_id,))

    def process_due(self, handler: Callable[[dict], dict], limit: int = 5) -> List[tuple]:
        """Toma aplicaciones vencidas con FOR UPDATE SKIP LOCKED (varias réplicas no se pisan) y las procesa."""
        done = []
        for _ in range(limit):
            with db.cursor() as cur:
                cur.execute(_DUE_SQL)
                row = cur.fetchone()
                if not row:
                    break
                row = dict(row)
                out = handler(row)
                secs = out.get("reintentar_en")
                cur.execute(
                    """UPDATE plantillas_aplicadas
                       SET estado = %s, resultado = %s, intentos_reversion = %s,
                           reintentar_en = CASE WHEN %s::int IS NULL THEN NULL ELSE now() + %s::int * interval '1 second' END,
                           revertida_en = CASE WHEN %s = 'revertida' THEN now() ELSE revertida_en END
                       WHERE id = %s""",
                    (out["estado"], out["resultado"], out["intentos"], secs, secs, out["estado"], row["id"]))
            done.append((row, out))
        return done


store = PgStore()
