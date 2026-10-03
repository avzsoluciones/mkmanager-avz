"""Dobles de prueba (sin red ni BD reales): hub RouterOS falso y base de datos en memoria que
interpreta las consultas SQL que usan los routers del backend (usuarios, sedes, equipos, aprovisionamiento, VPN)."""
import json
import re
import threading
from datetime import datetime, timezone

HUB_KEY = "4u9Wu/zFGuHL8hc9UmL9MxWenkPC5R9XP2jyhHLfuGM="


class UniqueViolation(Exception):
    """Simula psycopg2.errors.UniqueViolation."""


# ----------------------------------------------------------------------------- hub
class FakePath:
    def __init__(self, api, parts):
        self.api, self.parts = api, parts

    def _store(self):
        return self.api.data.setdefault(self.parts, [])

    def __iter__(self):
        if self.api.fail_list and self.parts == ("interface", "wireguard", "peers"):
            raise RuntimeError("timeout leyendo peers")
        return iter([dict(x) for x in self._store()])

    def add(self, **kw):
        is_peer = self.parts == ("interface", "wireguard", "peers")
        if (is_peer and self.api.fail_add) or (not is_peer and self.api.fail_route_add):
            raise RuntimeError("failure: invalid public key" if is_peer else "failure: route rejected")
        with self.api.lock:
            self.api.seq += 1
            pid = f"*A{self.api.seq}"
            row = {**kw, ".id": pid}
            if self.parts == ("ip", "route"):
                row["dynamic"] = False
            self.api.added.append(row) if is_peer else self.api.routes_added.append(row)
            self._store().append(row)
        return pid

    def remove(self, _id):
        if self.api.fail_remove:
            raise RuntimeError("no such item")
        with self.api.lock:
            (self.api.removed if self.parts == ("interface", "wireguard", "peers") else self.api.routes_removed).append(_id)
            store = self._store()
            store[:] = [p for p in store if p.get(".id") != _id]


DEFAULT_PEERS = [
    {".id": "*1", "name": "peer1", "interface": "wg1", "allowed-address": "10.40.1.11/32"},
    {".id": "*8", "name": "peer8", "interface": "wg1", "allowed-address": "10.254.0.50/32"},
    {".id": "*6", "name": "x", "interface": "wireguard1", "allowed-address": "10.254.0.10/32,192.168.3.0/24"},
]


class FakeApi:
    def __init__(self, peers=None, fail_add=False, fail_remove=False, fail_list=False, hub_key=HUB_KEY,
                 wg_name="wireguard1", routes=None, fail_route_add=False):
        self.lock = threading.Lock()
        self.seq = 0
        self.data = {
            ("interface", "wireguard"): [{"name": wg_name, "public-key": hub_key}],
            ("interface", "wireguard", "peers"): [dict(p) for p in (DEFAULT_PEERS if peers is None else peers)],
            ("ip", "route"): [dict(r) for r in (routes or [])],
        }
        self.added, self.removed, self.routes_added, self.routes_removed = [], [], [], []
        self.fail_add, self.fail_remove, self.fail_list, self.fail_route_add = fail_add, fail_remove, fail_list, fail_route_add
        self.connects = 0
        self.closes = 0

    def path(self, *parts):
        return FakePath(self, parts)

    def close(self):
        self.closes += 1

    def connect(self, **kw):
        self.connects += 1
        return self

    @property
    def peers(self):
        return self.data[("interface", "wireguard", "peers")]

    @property
    def routes(self):
        return self.data[("ip", "route")]


def failing_connect(**kw):
    raise ConnectionRefusedError("[Errno 111] 10.10.10.3:8728 secreto-no-debe-salir")


# ----------------------------------------------------------------------------- base de datos
_SCOPE = re.compile(r"(\w)\.(partner_id|cliente_id) = %s")


def _json(s):
    return json.loads(s) if isinstance(s, str) else s


def _now():
    return datetime.now(timezone.utc)


class MemDb:
    def __init__(self):
        self.usuarios, self.sedes, self.dispositivos, self.wg_peers = {}, [], [], []
        self.audit = []
        self.sqls = []
        self.unhandled = []      # SQL que el doble no emula (devuelve vacío); revisar si una prueba depende de ello
        self.fail_on = None            # None | "enter" | "wg_peers"
        self.fail_health = False
        self.fail_execute_like = None  # subcadena de SQL cuyo execute() debe fallar
        self.barrier = None
        self.lock = threading.Lock()
        self._next = {"sede": 1000, "disp": 5000, "peer": 7000, "user": 100}

    # --- siembra
    def add_user(self, **kw):
        u = {"username": None, "nombre_completo": kw.get("email", "x"), "partner_id": None, "tenant_id": None, "activo": True,
             "require_password_change": False, "password_hash": "x:y", "ultimo_login": None}
        u.update(kw)
        self.usuarios[u["id"]] = u
        return u

    def add_sede(self, **kw):
        s = {"ciudad": "Medellin", "direccion": None, "contacto_email": None, "estado": "activa",
             "partner_id": None, "cliente_id": None}
        s.update(kw)
        self.sedes.append(s)
        return s

    def add_device(self, **kw):
        d = {"tipo": "router", "marca": "MikroTik", "modelo": "RouterOS v7", "estado": "online",
             "ultimo_heartbeat": None, "tunnel_ip": None, "lan_cidr": None, "api_password_enc": None,
             "wan_config": None, "lan_config": None, "ip_local": None}
        d.update(kw)
        self.dispositivos.append(d)
        return d

    def add_peer(self, **kw):
        self._next["peer"] += 1
        p = {"id": self._next["peer"], "tipo": "router", "dispositivo_id": None, "preshared_key_enc": None, "hub_peer_id": None,
             "client_allowed": None, "dns": None, "sede_id": None, "tenant_id": None, "revocado_en": None,
             "tunnel_ip_historica": None, "creado_en": _now(), "allowed_extra": None, "creado_por": None}
        p.update(kw)
        self.wg_peers.append(p)
        return p

    # --- ámbito
    @staticmethod
    def _scoped(sql, params, sede):
        m = _SCOPE.search(sql)
        if not m:
            return True
        return sede.get(m.group(2)) == params[-1]

    def _sede_by_id(self, sid):
        return next((s for s in self.sedes if s["id"] == sid), None)

    def _sede_of(self, d):
        return self._sede_by_id(d["sede_id"])

    @staticmethod
    def _dev_view(d, s):
        return {"id": d["id"], "nombre": d["nombre"], "tipo": d["tipo"], "marca": d["marca"], "modelo": d["modelo"],
                "sede_id": d["sede_id"], "sede": s["nombre"], "tunnel_ip": d["tunnel_ip"], "lan_cidr": d["lan_cidr"],
                "estado": d["estado"], "ultimo_heartbeat": d["ultimo_heartbeat"]}

    def _user_scoped(self, sql, params, u):
        """Alcance de users.py: TRUE (superadmin) o (u.partner_id = t OR u.tenant_id = t)."""
        if "u.partner_id = %s OR u.tenant_id = %s" in sql:
            t = params[-1]
            return u.get("partner_id") == t or u.get("tenant_id") == t
        return True

    # --- API tipo db.py
    def fetch_one(self, sql, params=()):
        self.sqls.append(sql)
        f = " ".join(sql.split())
        if f.startswith("SELECT 1 AS ok"):
            if self.fail_health:
                raise RuntimeError("db down")
            return {"ok": 1}
        if "FROM usuarios WHERE LOWER(email) = %s OR LOWER(username)" in f:
            ident = params[0]
            for u in self.usuarios.values():
                if (u["email"] or "").lower() == ident or (u.get("username") or "").lower() == ident:
                    return dict(u)
            return None
        if "FROM usuarios WHERE LOWER(email) = %s" in f:
            return {"x": 1} if any((u["email"] or "").lower() == params[0] for u in self.usuarios.values()) else None
        if "FROM usuarios WHERE id = %s AND rol = 'partner' AND tenant_id = id" in f:
            u = self.usuarios.get(params[0])
            return {"x": 1, "id": u["id"]} if u and u["rol"] == "partner" and u["tenant_id"] == u["id"] else None
        if "FROM usuarios WHERE id = %s AND rol = 'partner'" in f:
            u = self.usuarios.get(params[0])
            return {"tenant_id": u["tenant_id"], "id": u["id"]} if u and u["rol"] == "partner" else None
        if "COUNT(*) AS n FROM usuarios WHERE rol = 'superadmin'" in f:
            return {"n": sum(1 for u in self.usuarios.values() if u["rol"] == "superadmin" and u["activo"] and u["id"] != params[0])}
        if "FROM usuarios u WHERE u.id = %s" in f:
            u = self.usuarios.get(params[0])
            return dict(u) if u and self._user_scoped(f, params, u) else None
        if "FROM usuarios WHERE id = %s" in f:
            u = self.usuarios.get(params[0])
            return dict(u) if u else None
        if "SELECT 1 AS x FROM sedes WHERE LOWER(nombre)" in f:
            nombre, pid = params
            return {"x": 1} if any(s["nombre"].lower() == nombre.lower() and s["partner_id"] == pid for s in self.sedes) else None
        if f.startswith("INSERT INTO sedes"):
            self._next["sede"] += 1
            nombre, ciudad, direccion, partner_id = params[:4]
            s = self.add_sede(id=self._next["sede"], nombre=nombre, ciudad=ciudad, direccion=direccion, partner_id=partner_id)
            return {"id": s["id"], "nombre": s["nombre"], "ciudad": s["ciudad"], "estado": s["estado"]}
        if "FROM sedes s WHERE s.id = %s" in f:
            for s in self.sedes:
                if s["id"] == params[0] and self._scoped(sql, params, s):
                    return {"id": s["id"], "nombre": s["nombre"], "partner_id": s["partner_id"]}
            return None
        if "FROM wg_peers p JOIN sedes s ON s.id = p.sede_id WHERE p.id = %s" in f:
            p = next((p for p in self.wg_peers if p["id"] == params[0] and p["tipo"] == "usuario" and p["revocado_en"] is None), None)
            s = self._sede_by_id(p["sede_id"]) if p else None
            return dict(p) if p and s and self._scoped(sql, params, s) else None
        if "COUNT(*) AS n FROM wg_peers WHERE tipo = 'usuario' AND sede_id = %s" in f:
            return {"n": sum(1 for p in self.wg_peers if p["tipo"] == "usuario" and p["sede_id"] == params[0] and p["revocado_en"] is None)}
        if "d.rx_activo_en" in f and "LEFT JOIN wg_peers p" in f:                      # estado en vivo del equipo
            for d in self.dispositivos:
                s = self._sede_of(d)
                if d["id"] == params[0] and s and self._scoped(sql, params, s):
                    p = next((p for p in self.wg_peers if p["dispositivo_id"] == d["id"] and p["revocado_en"] is None), None)
                    return {"id": d["id"], "estado": d["estado"], "ultimo_heartbeat": d["ultimo_heartbeat"], "rx_activo_en": None,
                            "public_key": p["public_key"] if p else None, "rx_bytes": None, "tx_bytes": None}
            return None
        if f.startswith("SELECT count(*) AS n FROM sedes s WHERE"):
            return {"n": sum(1 for s in self.sedes if self._scoped(sql, params, s))}
        if f.startswith("SELECT count(*) AS equipos"):
            ds = [d for d in self.dispositivos if self._sede_of(d) and self._scoped(sql, params, self._sede_of(d))]
            return {"equipos": len(ds), "online": sum(d["estado"] == "online" for d in ds),
                    "offline": sum(d["estado"] == "offline" for d in ds), "pendientes": sum(d["estado"] == "pendiente" for d in ds)}
        if "LEFT JOIN wg_peers p ON p.dispositivo_id" in f and "p.hub_peer_id" in f:
            for d in self.dispositivos:
                s = self._sede_of(d)
                if d["id"] == params[0] and s and self._scoped(sql, params, s):
                    p = next((p for p in self.wg_peers if p["dispositivo_id"] == d["id"] and p["revocado_en"] is None), None)
                    return {"id": d["id"], "nombre": d["nombre"], "lan_cidr": d["lan_cidr"], "tunnel_ip": d["tunnel_ip"],
                            "hub_peer_id": p["hub_peer_id"] if p else None}
            return None
        if "JOIN wg_peers p ON p.dispositivo_id = d.id" in f:
            for d in self.dispositivos:
                s = self._sede_of(d)
                if d["id"] == params[0] and s and self._scoped(sql, params, s):
                    p = next((p for p in self.wg_peers if p["dispositivo_id"] == d["id"] and p["revocado_en"] is None), None)
                    if not p:
                        return None
                    return {**d, "sede": s["nombre"], "private_key_enc": p["private_key_enc"]}
            return None
        if "FROM dispositivos d JOIN sedes s" in f and "d.id = %s" in f:
            for d in self.dispositivos:
                s = self._sede_of(d)
                if d["id"] == params[0] and s and self._scoped(sql, params, s):
                    return self._dev_view(d, s)
            return None
        self.unhandled.append(f[:140])
        return None

    def fetch_all(self, sql, params=()):
        self.sqls.append(sql)
        f = " ".join(sql.split())
        if f.startswith("SELECT lan_cidr, ip_local FROM dispositivos WHERE sede_id"):
            return [{"lan_cidr": d["lan_cidr"], "ip_local": d["ip_local"]} for d in self.dispositivos
                    if d["sede_id"] == params[0] and d["lan_cidr"]]
        if f.startswith("SELECT lan_cidr FROM dispositivos"):
            return [{"lan_cidr": d["lan_cidr"]} for d in self.dispositivos if d["lan_cidr"]]
        if f.startswith("SELECT tunnel_ip FROM dispositivos"):
            rows = [{"tunnel_ip": d["tunnel_ip"]} for d in self.dispositivos if d["tunnel_ip"]]
            if self.barrier is not None:
                try:
                    self.barrier.wait(timeout=5)
                except threading.BrokenBarrierError:
                    pass
            return rows
        if f.startswith("SELECT tunnel_ip FROM dispositivos WHERE tunnel_ip IS NOT NULL UNION"):
            return [{"tunnel_ip": x} for x in {d["tunnel_ip"] for d in self.dispositivos if d["tunnel_ip"]} |
                    {p["tunnel_ip"] for p in self.wg_peers if p["revocado_en"] is None}]
        if "FROM audit_logs a" in f:
            if "WHERE FALSE" in f:
                return []
            rows = list(self.audit)
            if "IN (SELECT lower(u.email) FROM usuarios u" in f:
                t = params[0]
                mails = {(u["email"] or "").lower() for u in self.usuarios.values()
                         if u["id"] == t or u.get("tenant_id") == t or u.get("partner_id") == t}
                rows = [a for a in rows if (a["usuario_email"] or "").lower() in mails]
            rows.sort(key=lambda a: -a["id"])
            return [dict(a) for a in rows[:params[-1]]]
        if f.startswith("SELECT tunnel_ip FROM wg_peers WHERE revocado_en IS NULL"):
            return [{"tunnel_ip": p["tunnel_ip"]} for p in self.wg_peers if p["revocado_en"] is None]
        if "FROM usuarios u WHERE" in f:
            return [dict(u) for u in self.usuarios.values() if self._user_scoped(f, params, u)]
        if "SELECT s.id, s.nombre, s.partner_id FROM sedes s WHERE" in f:
            rows = [{"id": s["id"], "nombre": s["nombre"], "partner_id": s["partner_id"]} for s in self.sedes if self._scoped(sql, params, s)]
            return rows[:2] if "LIMIT 2" in f else rows
        if "FROM sedes s WHERE" in f:
            return [{"id": s["id"], "nombre": s["nombre"], "ciudad": s["ciudad"], "direccion": s["direccion"],
                     "contacto_email": s["contacto_email"], "estado": s["estado"], "partner_id": s["partner_id"],
                     "dispositivos": sum(1 for d in self.dispositivos if d["sede_id"] == s["id"])}
                    for s in self.sedes if self._scoped(sql, params, s)]
        if "FROM wg_peers p JOIN sedes s ON s.id = p.sede_id WHERE p.tipo = 'usuario'" in f:
            out = []
            for p in self.wg_peers:
                s = self._sede_by_id(p["sede_id"]) if p["tipo"] == "usuario" else None
                if s and self._scoped(sql, params, s):
                    out.append({"id": p["id"], "nombre": p["nombre"], "tunnel_ip": p["tunnel_ip_historica"] or p["tunnel_ip"],
                                "creado_en": p["creado_en"], "revocado": p["revocado_en"] is not None, "sede_id": p["sede_id"]})
            return out
        if "FROM dispositivos d JOIN sedes s" in f:
            out = []
            for d in self.dispositivos:
                s = self._sede_of(d)
                if s and self._scoped(sql, params, s):
                    out.append(self._dev_view(d, s))
            return out
        self.unhandled.append(f[:140])
        return []

    def execute(self, sql, params=()):
        self.sqls.append(sql)
        f = " ".join(sql.split())
        if self.fail_execute_like and self.fail_execute_like in f:
            raise RuntimeError("fallo de BD simulado (execute)")
        if f.startswith("INSERT INTO audit_logs"):
            self.audit.append({"id": len(self.audit) + 1, "fecha": _now(), "usuario_email": params[0], "accion": params[1],
                               "detalles": params[2], "ip": params[3]})
        elif f.startswith("UPDATE usuarios SET password_hash"):
            u = self.usuarios[params[1]]
            u["password_hash"] = params[0]
            u["require_password_change"] = "require_password_change = TRUE" in f
        elif f.startswith("UPDATE usuarios SET activo"):
            self.usuarios[params[1]]["activo"] = params[0]
        elif f.startswith("UPDATE usuarios SET ultimo_login"):
            pass
        elif f.startswith("DELETE FROM dispositivos WHERE id"):
            self.dispositivos[:] = [d for d in self.dispositivos if d["id"] != params[0]]
            self.wg_peers[:] = [p for p in self.wg_peers if p["dispositivo_id"] != params[0]]
        elif f.startswith("UPDATE wg_peers SET revocado_en = now()"):
            for p in self.wg_peers:
                if p["id"] == params[0] and p["tipo"] == "usuario" and p["revocado_en"] is None:
                    p["revocado_en"], p["tunnel_ip_historica"], p["tunnel_ip"] = _now(), p["tunnel_ip"], f"rev-{p['id']}"
        else:
            self.unhandled.append(f[:140])

    def cursor(self):
        outer = self

        class Ctx:
            def __init__(self):
                self.pending = []
                self.last = None

            def __enter__(self):
                if outer.fail_on == "enter":
                    raise RuntimeError("fallo de BD simulado (conexión)")
                return self

            def __exit__(self, et, ev, tb):
                if et is not None:        # rollback
                    for kind, row in self.pending:
                        if kind == "user":
                            outer.usuarios.pop(row["id"], None)
                        else:
                            (outer.dispositivos if kind == "disp" else outer.wg_peers).remove(row)
                return False

            def execute(self, sql, params=()):
                outer.sqls.append(sql)
                f = " ".join(sql.split())
                if f.startswith("INSERT INTO dispositivos"):
                    with outer.lock:
                        nombre, sede_id, ip_local, tunnel_ip, lan_cidr, api_enc, wan_json, lan_json = params
                        if any(d["tunnel_ip"] == tunnel_ip for d in outer.dispositivos):
                            raise UniqueViolation(f"ux_dispositivos_tunnel_ip: {tunnel_ip}")
                        outer._next["disp"] += 1
                        row = outer.add_device(id=outer._next["disp"], nombre=nombre, sede_id=sede_id, ip_local=ip_local,
                                               tunnel_ip=tunnel_ip, lan_cidr=lan_cidr, api_password_enc=api_enc,
                                               estado="pendiente", wan_config=_json(wan_json), lan_config=_json(lan_json))
                        self.pending.append(("disp", row))
                        self.last = {"id": row["id"]}
                elif f.startswith("INSERT INTO wg_peers") and "'router'" in f:
                    if outer.fail_on == "wg_peers":
                        raise RuntimeError("fallo de BD simulado (wg_peers)")
                    nombre, device_id, tunnel_ip, pub, priv_enc, hub_id, extra, by = params
                    with outer.lock:
                        if any(p["tunnel_ip"] == tunnel_ip or p["public_key"] == pub for p in outer.wg_peers):
                            raise UniqueViolation("wg_peers unique")
                        row = outer.add_peer(tipo="router", nombre=nombre, dispositivo_id=device_id, tunnel_ip=tunnel_ip, public_key=pub,
                                             private_key_enc=priv_enc, hub_peer_id=hub_id, allowed_extra=extra, creado_por=by)
                        self.pending.append(("peer", row))
                elif f.startswith("INSERT INTO wg_peers") and "'usuario'" in f:
                    if outer.fail_on in ("wg_peers", "vpn"):
                        raise RuntimeError("fallo de BD simulado (wg_peers usuario)")
                    nombre, tunnel_ip, pub, priv_enc, psk_enc, hub_id, allowed, dns, sede_id, tenant_id, by = params
                    with outer.lock:
                        if any(p["tunnel_ip"] == tunnel_ip or p["public_key"] == pub for p in outer.wg_peers):
                            raise UniqueViolation("wg_peers unique")
                        row = outer.add_peer(tipo="usuario", nombre=nombre, tunnel_ip=tunnel_ip, public_key=pub, private_key_enc=priv_enc,
                                             preshared_key_enc=psk_enc, hub_peer_id=hub_id, client_allowed=allowed, dns=dns,
                                             sede_id=sede_id, tenant_id=tenant_id, creado_por=by)
                        self.pending.append(("peer", row))
                        self.last = {"id": row["id"]}
                elif f.startswith("INSERT INTO usuarios"):
                    email, username, nombre, ph, rol, partner_id, tenant_id = params
                    outer._next["user"] += 1
                    row = outer.add_user(id=outer._next["user"], email=email, username=username, nombre_completo=nombre,
                                         password_hash=ph, rol=rol, partner_id=partner_id, tenant_id=tenant_id,
                                         require_password_change=True)
                    self.pending.append(("user", row))
                    self.last = {"id": row["id"]}
                elif f.startswith("UPDATE usuarios SET tenant_id = id WHERE id"):
                    outer.usuarios[params[0]]["tenant_id"] = params[0]
                else:
                    outer.unhandled.append(f[:140])

            def fetchone(self):
                return self.last
        return Ctx()
