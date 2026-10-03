"""Esquemas de entrada. Todo valor que acabe dentro de un script RouterOS se valida aquí de forma estricta."""
import ipaddress
import re
from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

IFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,31}$")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{1,59}$")


def _ipv4(value: str) -> str:
    return str(ipaddress.IPv4Address(value.strip()))


class WanConfig(BaseModel):
    interface: str
    mode: Literal["dhcp", "static", "pppoe"] = "dhcp"
    address: Optional[str] = None      # CIDR, solo modo static
    gateway: Optional[str] = None      # solo modo static

    @field_validator("interface")
    @classmethod
    def _iface(cls, v):
        if not IFACE_RE.fullmatch(v):
            raise ValueError("Nombre de interfaz WAN inválido.")
        return v

    @model_validator(mode="after")
    def _static(self):
        if self.mode == "static":
            if not self.address or not self.gateway:
                raise ValueError("WAN estática requiere address (CIDR) y gateway.")
            ipaddress.IPv4Interface(self.address)
            self.gateway = _ipv4(self.gateway)
        return self


class LanConfig(BaseModel):
    interface: str
    cidr: str                          # red, p. ej. 192.168.10.0/24
    gateway: Optional[str] = None      # IP del router en la LAN; por defecto la primera
    dhcp_start: Optional[str] = None
    dhcp_end: Optional[str] = None
    dns: List[str] = Field(default_factory=lambda: ["1.1.1.1", "8.8.8.8"])

    @field_validator("interface")
    @classmethod
    def _iface(cls, v):
        if not IFACE_RE.fullmatch(v):
            raise ValueError("Nombre de interfaz o bridge LAN inválido.")
        return v

    @field_validator("dns")
    @classmethod
    def _dns(cls, v):
        if not 1 <= len(v) <= 3:
            raise ValueError("Indique entre 1 y 3 servidores DNS.")
        return [_ipv4(x) for x in v]


class ProvisionRequest(BaseModel):
    sede_id: int
    nombre: str
    wan: WanConfig
    lan: LanConfig
    tunnel_ip: Optional[str] = None     # opcional: fijar una IP concreta del pool 10.254.0.X

    @field_validator("tunnel_ip")
    @classmethod
    def _tunnel(cls, v):
        if v in (None, ""):
            return None
        ip = ipaddress.IPv4Address(v.strip())
        if ip not in ipaddress.ip_network("10.254.0.0/24") or int(ip) & 0xFF in (0, 1, 255):
            raise ValueError("La IP de túnel debe estar en 10.254.0.2-254.")
        return str(ip)

    @field_validator("nombre")
    @classmethod
    def _nombre(cls, v):
        v = v.strip()
        if not NAME_RE.fullmatch(v):
            raise ValueError("Nombre inválido: use letras, números, espacios, punto, guion o guion bajo.")
        return v
