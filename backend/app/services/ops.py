"""Operaciones estructuradas de RouterOS (`Op`): se ejecutan por la API y se renderizan a texto CLI/.rsc.

El usuario de API de los routers no tiene la política `policy`, así que las plantillas no pueden ser scripts:
son listas de `Op` (ruta, acción, atributos). Este módulo es puro: sin red ni base de datos.

Defensas (ver security/REVISION-SEGURIDAD.md, SEG-001 y checklist 5.3):
- cada segmento de ruta, clave y valor se valida al construir la `Op` (no hay forma de crear una `Op` hostil);
- `assert_safe` aplica listas blancas de rutas/atributos y las reglas duras de la regla de gestión;
- el texto CLI entrecomilla con `routeros_scripts.q`, que rechaza comillas, `\\`, `$`, corchetes y saltos de línea.
"""
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .routeros_scripts import WG_IF, q

TAG_PREFIX = "AVZ-APP-"
PREVIEW_TAG = f"{TAG_PREFIX}<id>"
MGMT_COMMENT = "AVZCloud mgmt"
MGMT_LIST = "AVZ_MGMT"
PLACE_AFTER_MGMT = "after_mgmt"

_SEG_RE = re.compile(r"^[a-z][a-z0-9\-]{0,40}$")
_KEY_RE = re.compile(r"^(\.id|[a-z][a-z0-9\-]{0,40})$")
_PLAIN_RE = re.compile(r"^[A-Za-z0-9_.,:/\-*!]+$")
_ACTIONS = ("add", "set", "remove")

FILTER = ("ip", "firewall", "filter")
NAT = ("ip", "firewall", "nat")
ADDRESS_LIST = ("ip", "firewall", "address-list")

# Rutas donde una plantilla puede CREAR elementos (siempre con el comentario AVZ-APP-<id>).
ADD_ALLOWED = {
    FILTER, NAT, ADDRESS_LIST,
    ("interface", "vlan"), ("ip", "address"), ("ip", "pool"),
    ("ip", "dhcp-server"), ("ip", "dhcp-server", "network"), ("queue", "simple"),
}
# Rutas y atributos que una plantilla puede MODIFICAR (singletons). `allow-remote-requests` queda fuera a propósito.
SET_ALLOWED = {
    ("ip", "dns"): {"servers"},
    ("system", "ntp", "client"): {"enabled", "servers"},
}
# Nunca se tocan, ni siquiera con atributos aparentemente inocuos.
FORBIDDEN_PREFIXES = (
    ("user",), ("ip", "service"), ("interface", "wireguard"), ("system", "script"), ("system", "scheduler"),
    ("system", "reset-configuration"), ("file",), ("ip", "ssh"),
)
_DROPPING = ("drop", "reject", "tarpit")


class OpError(ValueError):
    """Operación inválida o insegura."""


def _check_value(v) -> str:
    if not isinstance(v, str):
        raise OpError(f"Valor no textual en una operación: {type(v).__name__}")
    if len(v) > 300:
        raise OpError("Valor demasiado largo en una operación.")
    if v:
        try:
            q(v)
        except ValueError as e:
            raise OpError(str(e))
    return v


def _clean_map(m: Optional[dict], what: str) -> Optional[Dict[str, str]]:
    if m is None:
        return None
    out = {}
    for k, v in m.items():
        if not isinstance(k, str) or not _KEY_RE.match(k):
            raise OpError(f"Clave no válida en {what}: {k!r}")
        out[k] = _check_value(v if isinstance(v, str) else str(v))
    return out


@dataclass(frozen=True)
class Op:
    path: Tuple[str, ...]
    action: str
    attrs: Dict[str, str]
    find: Optional[Dict[str, str]] = None
    place: Optional[str] = None

    def __post_init__(self):
        path = tuple(self.path)
        if not path or not all(isinstance(s, str) and _SEG_RE.match(s) for s in path):
            raise OpError(f"Ruta no válida: {self.path!r}")
        if self.action not in _ACTIONS:
            raise OpError(f"Acción no válida: {self.action!r}")
        attrs = _clean_map(self.attrs, "atributos") or {}
        find = _clean_map(self.find, "búsqueda")
        if any(k in attrs for k in ("place-before", "place-after", ".id")):
            raise OpError("No se admiten atributos de posición ni .id; use `place`.")
        if self.place not in (None, PLACE_AFTER_MGMT):
            raise OpError(f"Posición no válida: {self.place!r}")
        if self.action == "add" and (find or not attrs):
            raise OpError("`add` requiere atributos y no admite búsqueda.")
        if self.action == "set" and not attrs:
            raise OpError("`set` requiere atributos.")
        if self.action == "remove" and not find:
            raise OpError("`remove` requiere criterios de búsqueda (nunca se borra todo).")
        if self.place and self.action != "add":
            raise OpError("`place` solo aplica a `add`.")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "attrs", attrs)
        object.__setattr__(self, "find", find)

    # --- serialización (JSONB) ---
    def to_json(self) -> dict:
        return {"path": list(self.path), "action": self.action, "attrs": dict(self.attrs),
                "find": dict(self.find) if self.find else None, "place": self.place}

    @staticmethod
    def from_json(d: dict) -> "Op":
        return Op(tuple(d["path"]), d["action"], dict(d.get("attrs") or {}), d.get("find"), d.get("place"))

    def describe(self) -> str:
        return f"{self.action} /{'/'.join(self.path)}"


def ops_to_json(ops: Iterable[Op]) -> List[dict]:
    return [o.to_json() for o in ops]


def ops_from_json(data) -> List[Op]:
    return [Op.from_json(d) for d in (data or [])]


def tag_for(app_id: int) -> str:
    return f"{TAG_PREFIX}{int(app_id)}"


def stamp(ops: Iterable[Op], tag: str) -> List[Op]:
    """Marca cada elemento creado con el comentario `AVZ-APP-<id>` (permite revertir exactamente lo propio)."""
    out = []
    for o in ops:
        if o.action == "add":
            out.append(Op(o.path, "add", {**o.attrs, "comment": tag}, None, o.place))
        else:
            out.append(o)
    return out


# --------------------------------------------------------------------------- seguridad
def assert_safe(ops: Iterable[Op]) -> None:
    """Rechaza cualquier operación que pueda tocar la gestión del equipo o salirse de lo permitido."""
    for o in ops:
        where = o.describe()
        for pref in FORBIDDEN_PREFIXES:
            if o.path[:len(pref)] == pref:
                raise OpError(f"Ruta prohibida para plantillas: {where}")
        # La lista de gestión y su regla son intocables.
        lst = o.attrs.get("list") or (o.find or {}).get("list")
        if o.path == ADDRESS_LIST and lst == MGMT_LIST:
            raise OpError("Ninguna plantilla puede modificar la lista AVZ_MGMT.")
        if MGMT_COMMENT in (o.attrs.get("comment"), (o.find or {}).get("comment")):
            raise OpError("Ninguna plantilla puede tocar la regla de gestión «AVZCloud mgmt».")
        if o.attrs.get("src-address-list") == MGMT_LIST and o.attrs.get("action") in _DROPPING:
            raise OpError("Ninguna plantilla puede bloquear a AVZ_MGMT.")
        if o.action == "add":
            if o.path not in ADD_ALLOWED:
                raise OpError(f"Ruta no permitida para crear elementos: {where}")
            if not o.attrs.get("comment", "").startswith(TAG_PREFIX):
                raise OpError(f"Todo elemento creado debe llevar el comentario {TAG_PREFIX}<id>: {where}")
            if o.path == FILTER and o.place != PLACE_AFTER_MGMT:
                raise OpError("Las reglas de firewall deben insertarse después de la regla de gestión.")
            if o.path == ("ip", "address") and o.attrs.get("interface") == WG_IF:
                raise OpError("Ninguna plantilla puede añadir direcciones a la interfaz del túnel.")
        elif o.action == "set":
            keys = SET_ALLOWED.get(o.path)
            if keys is None or (o.find and set(o.find) != {".id"}):
                raise OpError(f"Ruta no permitida para modificar valores: {where}")
            extra = set(o.attrs) - keys
            if extra:
                raise OpError(f"Atributos no permitidos en {where}: {sorted(extra)}")
        else:  # remove
            if o.path not in ADD_ALLOWED:
                raise OpError(f"Ruta no permitida para borrar: {where}")
            if not (o.find or {}).get("comment", "").startswith(TAG_PREFIX):
                raise OpError("Solo se pueden borrar elementos propios (comentario AVZ-APP-<id>).")


# --------------------------------------------------------------------------- texto CLI / .rsc
def _val(v: str) -> str:
    return v if (v and _PLAIN_RE.match(v)) else q(v)


def _kv(m: Dict[str, str]) -> str:
    return " ".join(f"{k}={_val(v)}" for k, v in m.items())


def render_cli(ops: Iterable[Op], tag: str = PREVIEW_TAG) -> str:
    """Texto RouterOS equivalente a las operaciones (vista previa y descarga). Solo informativo: no se ejecuta."""
    out = [
        "# AVZCloud Central Manager - servicio | RouterOS v7",
        f"# Todo lo creado lleva el comentario {tag}; así se revierte exactamente lo propio.",
    ]
    counters: Dict[tuple, int] = {}
    noted = False
    for o in ops:
        base = "/" + " ".join(o.path)
        if o.action == "add":
            attrs = dict(o.attrs)
            if o.place == PLACE_AFTER_MGMT:
                if not noted:
                    out.append("# Las reglas se insertan justo después de la regla «AVZCloud mgmt», que queda siempre primera.")
                    noted = True
                counters[o.path] = counters.get(o.path, 0) + 1
                attrs["place-before"] = str(counters[o.path])
            out.append(f"{base} add {_kv(attrs)}")
        elif o.action == "set":
            sel = f"[find {_kv(o.find)}] " if o.find else ""
            out.append(f"{base} set {sel}{_kv(o.attrs)}")
        else:
            out.append(f"{base} remove [find {_kv(o.find)}]")
    return "\n".join(out) + "\n"
