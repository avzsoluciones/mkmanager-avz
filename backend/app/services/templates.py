"""Catálogo de plantillas de servicio (M4/M5): esquema tipado, validación estricta y generación de `Op`.

Cada plantilla es un modelo pydantic con tipos cerrados (sin texto libre hacia el router). La validación que
depende del equipo (LAN, interfaz WAN, gateway) se hace con `DeviceCtx`. Funciones puras: sin red ni BD.
"""
import ipaddress
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError, field_validator
from typing_extensions import Annotated

from . import addressing as ad
from .ops import ADDRESS_LIST, FILTER, NAT, PLACE_AFTER_MGMT, Op
from .routeros_scripts import WG_IF
from ..schemas import IFACE_RE

WG_LISTEN_PORT = 13231                                 # puerto de escucha del túnel (ver routeros_scripts.render_provision)
MGMT_PORTS = {22, 23, 8291, 8728, 8729, WG_LISTEN_PORT}   # nunca se exponen por port-forward
RISKY_PORTS = {21, 25, 445, 1433, 3306, 3389, 5432, 5900, 6379, 27017}
PUBLIC_DNS = "1.1.1.1,8.8.8.8"
ADMIN_LIST = "AVZ_ADMIN"
MAX_REVERT, MIN_REVERT = 30, 2

Port = Annotated[StrictInt, Field(ge=1, le=65535)]
Mbps = Annotated[StrictInt, Field(ge=1, le=10000)]


class TemplateError(Exception):
    """Errores de validación. `errors` = [(campo | None, mensaje)]; None = error general."""

    def __init__(self, errors: List[Tuple[Optional[str], str]]):
        super().__init__("; ".join(m for _, m in errors))
        self.errors = errors


# --------------------------------------------------------------------------- contexto del equipo
@dataclass
class DeviceCtx:
    wan_interface: Optional[str] = None
    wan_mode: Optional[str] = None
    lan_interface: Optional[str] = None
    lan_cidr: Optional[str] = None
    gateway: Optional[str] = None
    tunnel_ip: Optional[str] = None

    @staticmethod
    def from_row(dev: dict) -> "DeviceCtx":
        wan, lan = dev.get("wan_config") or {}, dev.get("lan_config") or {}
        return DeviceCtx(
            wan_interface=wan.get("interface") if IFACE_RE.fullmatch(str(wan.get("interface") or "")) else None,
            wan_mode=wan.get("mode"),
            lan_interface=lan.get("interface") if IFACE_RE.fullmatch(str(lan.get("interface") or "")) else None,
            lan_cidr=dev.get("lan_cidr"), gateway=dev.get("ip_local"), tunnel_ip=dev.get("tunnel_ip"))

    def need_wan(self) -> str:
        if not self.wan_interface:
            raise TemplateError([(None, "El equipo no tiene registrada su interfaz WAN; no se puede aplicar este servicio.")])
        return self.wan_interface

    def need_lan(self) -> ipaddress.IPv4Network:
        try:
            return ipaddress.ip_network(self.lan_cidr or "", strict=False)
        except ValueError:
            raise TemplateError([(None, "El equipo no tiene registrada su red local (LAN).")])


# --------------------------------------------------------------------------- validadores reutilizables
def _short(v: Any) -> str:
    return "".join(c if c.isprintable() else "?" for c in str(v))[:40]


def _ipv4(v: str) -> str:
    try:
        return str(ipaddress.IPv4Address(v))
    except ValueError:
        raise ValueError(f"«{_short(v)}» no es una dirección IPv4 válida.")


def _network(v: str, min_prefix: int = 0) -> str:
    """IP suelta o CIDR (con o sin bits de host) -> CIDR canónico."""
    try:
        net = ipaddress.IPv4Network(v, strict=False)
    except ValueError:
        raise ValueError(f"«{_short(v)}» no es una red válida (ej. 192.168.10.0/24).")
    if net.prefixlen < min_prefix:
        raise ValueError(f"«{_short(v)}» abarca demasiadas direcciones; use una red más específica.")
    return str(net)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FirewallParams(_Base):
    ips_admin: List[StrictStr] = Field(default_factory=list, max_length=20)
    permitir_ping: StrictBool = True

    @field_validator("ips_admin")
    @classmethod
    def _admin(cls, v):
        out = []
        for x in v:
            n = _network(x, min_prefix=8)
            if n not in out:
                out.append(n)
        return out


class PortForwardParams(_Base):
    protocolo: Literal["tcp", "udp"]
    puerto_externo: Port
    ip_destino: StrictStr
    puerto_destino: Port

    @field_validator("ip_destino")
    @classmethod
    def _ip(cls, v):
        return _ipv4(v)


class VlanParams(_Base):
    vlan_id: Annotated[StrictInt, Field(ge=2, le=4094)]
    interfaz: StrictStr
    subred: StrictStr
    dhcp: StrictBool = True

    @field_validator("interfaz")
    @classmethod
    def _iface(cls, v):
        if not IFACE_RE.fullmatch(v):
            raise ValueError("Nombre de interfaz inválido.")
        return v

    @field_validator("subred")
    @classmethod
    def _subred(cls, v):
        try:
            return str(ad.parse_lan(v))
        except ValueError as e:
            raise ValueError(str(e))


class LimiteParams(_Base):
    objetivo: StrictStr
    bajada: Mbps
    subida: Mbps

    @field_validator("objetivo")
    @classmethod
    def _obj(cls, v):
        return _network(v)


_HOST_RE = re.compile(r"^(?=.{1,100}$)([A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?\.)*[A-Za-z0-9]([A-Za-z0-9\-]{0,61}[A-Za-z0-9])?$")


class DnsNtpParams(_Base):
    dns: List[StrictStr] = Field(min_length=1, max_length=3)
    ntp: Optional[StrictStr] = "pool.ntp.org"

    @field_validator("dns")
    @classmethod
    def _dns(cls, v):
        out = []
        for x in v:
            ip = ipaddress.IPv4Address(_ipv4(x))
            if ip.is_unspecified or ip.is_loopback or ip.is_multicast or ip.is_link_local or ip.is_reserved:
                raise ValueError(f"«{_short(x)}» no es un servidor DNS utilizable.")
            if str(ip) not in out:
                out.append(str(ip))
        return out

    @field_validator("ntp")
    @classmethod
    def _ntp(cls, v):
        if v is None or v == "":
            return None
        if not _HOST_RE.fullmatch(v):
            raise ValueError("El servidor de hora debe ser un nombre de host o una IP válidos.")
        return v


# --------------------------------------------------------------------------- comprobaciones con el equipo
def _check_firewall(p: FirewallParams, ctx: DeviceCtx) -> None:
    ctx.need_wan()


def _check_port_forward(p: PortForwardParams, ctx: DeviceCtx) -> None:
    ctx.need_wan()
    errs = []
    if p.puerto_externo in MGMT_PORTS:
        errs.append(("puerto_externo", "Ese puerto es de gestión del equipo y no se puede abrir hacia internet."))
    lan = ctx.need_lan()
    ip = ipaddress.IPv4Address(p.ip_destino)
    if ip not in lan or ip in (lan.network_address, lan.broadcast_address):
        errs.append(("ip_destino", f"La IP debe ser un equipo de la red local del router ({lan})."))
    elif p.ip_destino == ctx.gateway:
        errs.append(("ip_destino", "No se puede redirigir hacia el propio router."))
    if errs:
        raise TemplateError(errs)


def _check_vlan(p: VlanParams, ctx: DeviceCtx) -> None:
    errs = []
    try:
        ad.check_lan(p.subred, [ctx.lan_cidr] if ctx.lan_cidr else [])
    except ValueError as e:
        errs.append(("subred", str(e)))
    if p.interfaz == WG_IF or (ctx.wan_interface and p.interfaz == ctx.wan_interface):
        errs.append(("interfaz", "No se puede crear una VLAN sobre la interfaz WAN ni la del túnel."))
    if errs:
        raise TemplateError(errs)


def _check_limite(p: LimiteParams, ctx: DeviceCtx) -> None:
    net = ipaddress.IPv4Network(p.objetivo)
    if not net.is_private:
        raise TemplateError([("objetivo", "El objetivo debe ser una IP o red privada de la sede.")])
    if any(net.overlaps(r) for r in ad.RESERVED_NETWORKS):
        raise TemplateError([("objetivo", "El objetivo no puede incluir las redes del túnel ni las reservadas.")])


def _check_none(p, ctx) -> None:
    return None


# --------------------------------------------------------------------------- generación de operaciones
def _rule(chain: str, **kw) -> Op:
    attrs = {"chain": chain, **{k.replace("_", "-"): v for k, v in kw.items()}}
    return Op(FILTER, "add", attrs, place=PLACE_AFTER_MGMT)


def _build_firewall(p: FirewallParams, ctx: DeviceCtx) -> List[Op]:
    wan = ctx.need_wan()
    ops: List[Op] = [Op(ADDRESS_LIST, "add", {"list": ADMIN_LIST, "address": a}) for a in p.ips_admin]
    ops += [
        _rule("input", action="accept", connection_state="established,related"),
        _rule("input", action="drop", connection_state="invalid"),
    ]
    if p.permitir_ping:
        ops.append(_rule("input", action="accept", protocol="icmp", limit="10,5:packet"))
    if ctx.lan_interface:
        ops.append(_rule("input", action="accept", in_interface=ctx.lan_interface))
    ops.append(_rule("input", action="accept", in_interface=WG_IF))
    ops.append(_rule("input", action="accept", protocol="udp", dst_port=str(WG_LISTEN_PORT), in_interface=wan))
    if p.ips_admin:
        ops.append(_rule("input", action="accept", protocol="tcp", dst_port="8291,22", src_address_list=ADMIN_LIST))
    ops += [
        _rule("input", action="drop", in_interface=wan),
        _rule("forward", action="accept", connection_state="established,related"),
        _rule("forward", action="drop", connection_state="invalid"),
        _rule("forward", action="drop", in_interface=wan, connection_nat_state="!dstnat"),
    ]
    return ops


def _build_port_forward(p: PortForwardParams, ctx: DeviceCtx) -> List[Op]:
    return [Op(NAT, "add", {
        "chain": "dstnat", "action": "dst-nat", "protocol": p.protocolo, "dst-port": str(p.puerto_externo),
        "in-interface": ctx.need_wan(), "to-addresses": p.ip_destino, "to-ports": str(p.puerto_destino)})]


def _build_vlan(p: VlanParams, ctx: DeviceCtx) -> List[Op]:
    net = ipaddress.IPv4Network(p.subred)
    gw = ad.default_gateway(net)
    name = f"avz-vlan{p.vlan_id}"
    ops = [
        Op(("interface", "vlan"), "add", {"name": name, "vlan-id": str(p.vlan_id), "interface": p.interfaz}),
        Op(("ip", "address"), "add", {"address": f"{gw}/{net.prefixlen}", "interface": name}),
    ]
    if p.dhcp:
        start, end = ad.default_dhcp_range(net)
        pool, srv = f"avz-pool-vlan{p.vlan_id}", f"avz-dhcp-vlan{p.vlan_id}"
        ops += [
            Op(("ip", "pool"), "add", {"name": pool, "ranges": f"{start}-{end}"}),
            Op(("ip", "dhcp-server", "network"), "add", {"address": str(net), "gateway": gw, "dns-server": PUBLIC_DNS}),
            Op(("ip", "dhcp-server"), "add", {"name": srv, "interface": name, "address-pool": pool,
                                              "lease-time": "1d", "disabled": "no"}),
        ]
    return ops


def _build_limite(p: LimiteParams, ctx: DeviceCtx) -> List[Op]:
    net = ipaddress.IPv4Network(p.objetivo)
    name = "avz-qos-" + str(net).replace("/", "-")
    return [Op(("queue", "simple"), "add", {"name": name, "target": str(net), "max-limit": f"{p.subida}M/{p.bajada}M"})]


def _build_dns_ntp(p: DnsNtpParams, ctx: DeviceCtx) -> List[Op]:
    ops = [Op(("ip", "dns"), "set", {"servers": ",".join(p.dns)})]       # sin tocar allow-remote-requests
    if p.ntp:
        ops.append(Op(("system", "ntp", "client"), "set", {"enabled": "yes", "servers": p.ntp}))
    return ops


# --------------------------------------------------------------------------- avisos
def _avisos_firewall(p: FirewallParams, ctx: DeviceCtx) -> List[str]:
    out = ["Se bloquea lo que llega desde internet salvo conexiones ya establecidas. La regla «AVZCloud mgmt» queda siempre primera."]
    if not p.ips_admin:
        out.append("No indicaste redes de administración: los puertos 8291 y 22 solo serán accesibles desde la red local y el túnel.")
    if ctx.wan_mode == "pppoe":
        out.append("La conexión a internet es PPPoE: las reglas usan la interfaz física configurada; revisa que sea la correcta.")
    return out


def _avisos_pf(p: PortForwardParams, ctx: DeviceCtx) -> List[str]:
    if p.puerto_externo in RISKY_PORTS:
        return ["Abrir este puerto a internet es peligroso. Considera usar una VPN para las personas."]
    return []


def _avisos_vlan(p: VlanParams, ctx: DeviceCtx) -> List[str]:
    return [f"Se creará la red {p.subred} en la VLAN {p.vlan_id} sobre «{p.interfaz}». Verifica que el puerto esté configurado para esa VLAN."]


def _avisos_none(p, ctx) -> List[str]:
    return []


# --------------------------------------------------------------------------- catálogo
def _f(name, label, type_, required=False, default=None, help=None, options=None) -> dict:
    d = {"name": name, "label": label, "type": type_, "required": required}
    if default is not None:
        d["default"] = default
    if help:
        d["help"] = help
    if options:
        d["options"] = options
    return d


@dataclass(frozen=True)
class Template:
    id: str
    nombre: str
    descripcion: str
    categoria: str
    icono: str
    riesgo: str                  # bajo | medio | alto
    revierte: bool
    campos: List[dict]
    model: type
    check: Callable
    build: Callable
    avisos: Callable

    def public(self) -> dict:
        return {"id": self.id, "nombre": self.nombre, "descripcion": self.descripcion, "categoria": self.categoria,
                "icono": self.icono, "riesgo": self.riesgo, "revierte": self.revierte, "campos": self.campos}

    @property
    def capability(self) -> str:
        return "plantilla.alto" if self.riesgo == "alto" else "plantilla.bajo_medio"


CATALOG: Dict[str, Template] = {t.id: t for t in [
    Template(
        "firewall-seguro", "Firewall seguro", "Bloquea todo lo que llega desde internet y deja pasar solo lo necesario.",
        "Seguridad", "shield", "alto", True,
        [_f("ips_admin", "Redes que pueden administrar el equipo desde internet", "list", default=[],
            help="Opcional. Una por línea (ej. 203.0.113.0/24). Podrán usar Winbox (8291) y SSH (22)."),
         _f("permitir_ping", "Responder al ping desde internet", "bool", default=True,
            help="Limitado a pocas respuestas por segundo. Útil para diagnosticar.")],
        FirewallParams, _check_firewall, _build_firewall, _avisos_firewall),
    Template(
        "port-forward", "Abrir un puerto hacia un equipo", "Permite que algo del local (cámara, servidor) sea alcanzable desde internet.",
        "Red", "globe", "medio", True,
        [_f("protocolo", "Protocolo", "select", True, "tcp", options=[{"value": "tcp", "label": "TCP"}, {"value": "udp", "label": "UDP"}]),
         _f("puerto_externo", "Puerto de internet", "number", True, 8080, "Entre 1 y 65535. Los puertos de gestión del equipo no se pueden abrir."),
         _f("ip_destino", "Equipo de destino", "ip", True, help="La IP del equipo en la red local."),
         _f("puerto_destino", "Puerto del equipo", "number", True, 80)],
        PortForwardParams, _check_port_forward, _build_port_forward, _avisos_pf),
    Template(
        "vlan", "Separar una red (VLAN)", "Crea una red aparte, por ejemplo para invitados o cámaras.",
        "Red", "layers", "medio", True,
        [_f("vlan_id", "Número de VLAN", "number", True, 20, "Entre 2 y 4094."),
         _f("interfaz", "Puerto base", "text", True, "bridge-lan", "Interfaz o bridge sobre el que se crea la VLAN."),
         _f("subred", "Subred de la VLAN", "cidr", True, "192.168.50.0/24", "No puede coincidir con la red local ni con las reservadas."),
         _f("dhcp", "Repartir direcciones automáticamente (DHCP)", "bool", default=True)],
        VlanParams, _check_vlan, _build_vlan, _avisos_vlan),
    Template(
        "limite-ancho-banda", "Limitar la velocidad", "Reparte el internet para que nadie lo acapare.",
        "Calidad de servicio", "gauge", "bajo", True,
        [_f("objetivo", "Equipo o red a limitar", "cidr", True, "192.168.10.0/24", "Una IP o una red de la sede."),
         _f("bajada", "Bajada máxima (Mbps)", "number", True, 20, "Entre 1 y 10000."),
         _f("subida", "Subida máxima (Mbps)", "number", True, 5, "Entre 1 y 10000.")],
        LimiteParams, _check_limite, _build_limite, _avisos_none),
    Template(
        "dns-ntp", "Servidores DNS y hora", "Cambia los servidores que traducen nombres de páginas y la fuente de hora.",
        "Red", "server", "bajo", False,
        [_f("dns", "Servidores DNS", "list", True, ["1.1.1.1", "8.8.8.8"], "Entre 1 y 3, uno por línea. No se abre el DNS a internet."),
         _f("ntp", "Servidor de hora", "text", False, "pool.ntp.org")],
        DnsNtpParams, _check_none, _build_dns_ntp, _avisos_none),
]}


# --------------------------------------------------------------------------- validación y construcción
_ES = {
    "missing": "Este campo es obligatorio.", "int_type": "Escribe un número entero.", "int_parsing": "Escribe un número entero.",
    "bool_type": "Debe ser sí o no.", "bool_parsing": "Debe ser sí o no.", "string_type": "Escribe un texto.",
    "literal_error": "Elige una de las opciones disponibles.", "extra_forbidden": "Campo no reconocido.",
    "list_type": "Debe ser una lista.", "too_short": "Faltan elementos.", "too_long": "Hay demasiados elementos o es demasiado largo.",
    "greater_than_equal": "El valor es demasiado bajo.", "less_than_equal": "El valor es demasiado alto.",
}


def _convert(e: ValidationError) -> List[Tuple[Optional[str], str]]:
    out = []
    for err in e.errors():
        field = str(err["loc"][0]) if err["loc"] else None
        if err["type"] == "value_error":
            msg = str(err["ctx"]["error"])
        else:
            msg = _ES.get(err["type"], "Valor no válido.")
        out.append((field, msg))
    return out


def validate_params(tpl: Template, raw: Any, ctx: DeviceCtx) -> BaseModel:
    if not isinstance(raw, dict):
        raise TemplateError([(None, "Los parámetros deben ser un objeto.")])
    try:
        params = tpl.model.model_validate(raw)
    except ValidationError as e:
        raise TemplateError(_convert(e))
    tpl.check(params, ctx)
    return params


def plan(tpl: Template, raw: Any, ctx: DeviceCtx):
    """Valida y genera (parámetros normalizados, operaciones, avisos). Sin efectos secundarios."""
    params = validate_params(tpl, raw, ctx)
    return params.model_dump(), tpl.build(params, ctx), tpl.avisos(params, ctx)


def reversal_minutes(role: str, tpl: Template, requested: Optional[int]) -> Optional[int]:
    """Política de reversión: riesgo alto + partner/técnico = obligatoria (2 a 30 min); el resto, opcional."""
    if not tpl.revierte:
        if requested is not None:
            raise TemplateError([("revertir_minutos", "Este servicio no admite reversión automática.")])
        return None
    mandatory = tpl.riesgo == "alto" and role in ("partner", "tecnico")
    if requested is None:
        if mandatory:
            raise TemplateError([("revertir_minutos",
                                  f"La reversión automática es obligatoria para este servicio ({MIN_REVERT} a {MAX_REVERT} minutos).")])
        return None
    if isinstance(requested, bool) or not MIN_REVERT <= requested <= MAX_REVERT:
        raise TemplateError([("revertir_minutos", f"Indica entre {MIN_REVERT} y {MAX_REVERT} minutos.")])
    return requested
