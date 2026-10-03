"""Dobles de prueba del motor de plantillas: router en memoria y almacén en memoria (sin red ni PostgreSQL)."""
import copy
from datetime import datetime, timedelta, timezone

from app.services import edge
from app.services.ops import FILTER

UTC = timezone.utc
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

MGMT_RULE = {".id": "*1", "chain": "input", "action": "accept", "in-interface": "wg-avzcloud",
             "src-address-list": "AVZ_MGMT", "comment": "AVZCloud mgmt", "disabled": False}
LEGACY_RULE = {".id": "*2", "chain": "input", "action": "accept", "comment": "regla previa"}

DEV = {"id": 5, "nombre": "MK Oficina", "marca": "MikroTik", "estado": "online", "tunnel_ip": "10.254.0.10",
       "puerto_gestion": 8728, "api_user": "avz-api", "api_password_enc": "cifrado", "lan_cidr": "192.168.10.0/24",
       "ip_local": "192.168.10.1", "wan_config": {"interface": "ether1", "mode": "dhcp"},
       "lan_config": {"interface": "bridge-lan"}, "partner_id": 10, "cliente_id": None}


class FakeRouter:
    """Router RouterOS mínimo: tablas por ruta, `place-before`, rechazos programables y conexiones nuevas caídas."""

    def __init__(self):
        self.tables = {
            FILTER: [dict(MGMT_RULE), dict(LEGACY_RULE)],
            ("ip", "dns"): [{".id": "*d", "servers": "9.9.9.9", "allow-remote-requests": False}],
            ("system", "ntp", "client"): [{".id": "*n", "enabled": False, "servers": ""}],
            ("system", "identity"): [{"name": "MK"}],
        }
        self.seq, self.calls = 100, []
        self.reject = None              # callable(path, attrs) -> bool: el router rechaza el add
        self.connects = 0
        self.fail_connect_from = None   # n-ésima conexión (1 = la primera) desde la cual falla
        self.fail_remove = False

    # --- fábrica de sesiones ---
    def factory(self, dev):
        self.connects += 1
        if self.fail_connect_from and self.connects >= self.fail_connect_from:
            raise edge.EdgeError("No se pudo conectar con el equipo por el túnel.")
        return self

    # --- interfaz de sesión ---
    def rows(self, path):
        return copy.deepcopy(self.tables.setdefault(tuple(path), []))

    def add(self, path, attrs):
        path = tuple(path)
        self.calls.append(("add", path, dict(attrs)))
        if self.reject and self.reject(path, attrs):
            raise RuntimeError("failure: already have such entry")
        attrs = dict(attrs)
        before = attrs.pop("place-before", None)
        self.seq += 1
        row = {".id": f"*{self.seq}", **attrs}
        table = self.tables.setdefault(path, [])
        if before is None:
            table.append(row)
        else:
            idx = next(i for i, r in enumerate(table) if r[".id"] == before)
            table.insert(idx, row)
        return row[".id"]

    def set(self, path, attrs, id=None):
        path = tuple(path)
        self.calls.append(("set", path, dict(attrs), id))
        table = self.tables[path]
        row = next(r for r in table if id is None or r[".id"] == id)
        row.update(attrs)

    def remove(self, path, id):
        path = tuple(path)
        self.calls.append(("remove", path, id))
        if self.fail_remove:
            raise RuntimeError("timeout")
        self.tables[path] = [r for r in self.tables[path] if r[".id"] != id]

    def close(self):
        pass

    # --- ayudas de prueba ---
    def mutating_calls(self):
        return [c for c in self.calls if c[0] in ("add", "set", "remove")]

    def comments(self, path):
        return [r.get("comment") for r in self.tables.get(tuple(path), [])]


class MemStore:
    """Mismo contrato que `edge.PgStore`, en memoria y con reloj controlable."""

    def __init__(self, devices=(DEV,)):
        self.devices = {d["id"]: dict(d) for d in devices}
        self.apps, self.seq, self.now = {}, 0, T0

    # --- alcance (equivale al fragmento SQL de security.sede_scope) ---
    def _in_scope(self, dev, where, params):
        if where == "TRUE":
            return True
        if "partner_id" in where:
            return dev.get("partner_id") == params[0]
        if "cliente_id" in where:
            return dev.get("cliente_id") == params[0]
        return False

    def get_device(self, device_id, where, params):
        dev = self.devices.get(device_id)
        return dict(dev) if dev and self._in_scope(dev, where, params) else None

    def begin(self, device_id, plantilla, params, phash, email):
        live = [a for a in self.apps.values() if a["dispositivo_id"] == device_id and (
            a["estado"] == "aplicando" or (a["estado"] == "aplicada" and a["revertir_en"] and a["revertir_en"] > self.now))]
        if any(a["plantilla"] == plantilla and a["params_hash"] == phash for a in live):
            raise edge.Conflict("Esta misma aplicación ya está en curso en el equipo. Espera a que termine o confírmala.")
        if any(a["estado"] == "aplicando" for a in live):
            raise edge.Conflict("Hay otra aplicación en curso en este equipo. Espera unos segundos.")
        self.seq += 1
        self.apps[self.seq] = {"id": self.seq, "dispositivo_id": device_id, "plantilla": plantilla, "parametros": params,
                               "params_hash": phash, "estado": "aplicando", "usuario_email": email, "creado_en": self.now,
                               "revertir_en": None, "ops": None, "inversas": None, "script": None, "resultado": None,
                               "intentos_reversion": 0, "reintentar_en": None, "confirmada_en": None, "revertida_en": None}
        return self.seq

    def save_plan(self, app_id, ops, inversas, script):
        from app.services.ops import ops_to_json
        a = self.apps[app_id]
        a["ops"], a["inversas"], a["script"] = ops_to_json(ops), ops_to_json(inversas), script

    def finish_applied(self, app_id, minutes):
        a = self.apps[app_id]
        a["estado"] = "aplicada"
        a["revertir_en"] = self.now + timedelta(minutes=minutes) if minutes else None
        return a["revertir_en"]

    def mark_error(self, app_id, message):
        self.apps[app_id].update(estado="error", resultado=message)

    def schedule_revert_now(self, app_id, message):
        self.apps[app_id].update(estado="aplicada", revertir_en=self.now, intentos_reversion=1,
                                 reintentar_en=self.now + timedelta(seconds=30), resultado=message)

    def get_application(self, app_id, where, params):
        a = self.apps.get(app_id)
        if not a or not self._in_scope(self.devices[a["dispositivo_id"]], where, params):
            return None
        return {k: a[k] for k in ("id", "dispositivo_id", "plantilla", "estado", "revertir_en", "usuario_email")}

    def confirm(self, app_id):
        a = self.apps[app_id]
        if a["estado"] == "aplicada" and (a["revertir_en"] is None or a["revertir_en"] > self.now):
            a.update(estado="confirmada", confirmada_en=self.now)
            return True
        return False

    def request_revert(self, app_id):
        a = self.apps[app_id]
        if a["estado"] != "aplicada":
            return False
        a.update(revertir_en=self.now, reintentar_en=None)
        return True

    def list_for_device(self, device_id):
        rows = [a for a in self.apps.values() if a["dispositivo_id"] == device_id]
        return [dict(r) for r in sorted(rows, key=lambda r: -r["id"])]

    def process_due(self, handler, limit=5):
        done = []
        for _ in range(limit):
            due = [a for a in self.apps.values() if (
                (a["estado"] == "aplicada" and a["revertir_en"] is not None and a["revertir_en"] <= self.now)
                or (a["estado"] == "aplicando" and a["creado_en"] < self.now - timedelta(minutes=edge.STALE_APPLYING_MINUTES)))
                and (a["reintentar_en"] is None or a["reintentar_en"] <= self.now)]
            if not due:
                break
            a = sorted(due, key=lambda r: r["revertir_en"] or r["creado_en"])[0]
            dev = self.devices[a["dispositivo_id"]]
            row = {**a, **{k: dev[k] for k in ("tunnel_ip", "puerto_gestion", "api_user", "api_password_enc")}}
            out = handler(row)
            secs = out.get("reintentar_en")
            a.update(estado=out["estado"], resultado=out["resultado"], intentos_reversion=out["intentos"],
                     reintentar_en=self.now + timedelta(seconds=secs) if secs else None)
            if out["estado"] == "revertida":
                a["revertida_en"] = self.now
            done.append((row, out))
        return done
