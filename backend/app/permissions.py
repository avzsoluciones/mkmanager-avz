"""Matriz de permisos por rol: única fuente de verdad (ver specs/001, sección 3.2).

Aquí solo se decide QUÉ rol puede intentar cada capacidad. Las condiciones de pertenencia
("solo su cartera", "solo sus clientes") las aplica cada endpoint con el alcance (security.sede_scope).

Roles: superadmin y soporte (AVZCloud, todo el parque) · partner (aliado, su cartera) ·
tecnico (técnico de un aliado, la misma cartera pero sin administración) · cliente (solo lectura de su sede).
"""
from fastapi import Depends, HTTPException

from .security import current_user

ALL = ("superadmin", "soporte", "partner", "tecnico", "cliente")
STAFF = ("superadmin", "soporte")
ADMINS = ("superadmin", "soporte", "partner")                 # pueden administrar sedes
FIELD = ("superadmin", "soporte", "partner", "tecnico")       # pueden operar equipos

PERMISSIONS = {
    "panel.ver": ALL,
    "empresa.ver": ALL,
    "empresa.crear": ADMINS,
    "empresa.editar": ADMINS,
    "empresa.eliminar": ("superadmin",),
    "sede.ver": ALL,
    "sede.crear": ADMINS,
    "sede.editar": ADMINS,
    "sede.eliminar": ("superadmin",),
    "equipo.ver": ALL,
    "equipo.estado": ALL,
    "equipo.aprovisionar": FIELD,
    "equipo.script": FIELD,
    "equipo.baja": ("superadmin", "partner"),
    "plantilla.bajo_medio": FIELD,
    "plantilla.alto": FIELD,               # partner y tecnico: reversión automática obligatoria (lo impone el endpoint)
    "aplicacion.confirmar": FIELD,
    "vpn.gestionar": FIELD,
    "usuario.crear": ("superadmin", "partner"),
    "usuario.gestionar": ("superadmin", "partner"),
    "auditoria.ver": FIELD,
    "global.gestionar": ("superadmin",),
    "hub.ver": STAFF,
}

# Roles cuyo alcance de datos es la cartera (tenant) de un aliado.
TENANT_ROLES = ("partner", "tecnico")


def allowed(role: str, capability: str) -> bool:
    return role in PERMISSIONS[capability]


def require(capability: str):
    """Dependencia FastAPI: 403 si el rol del usuario no tiene la capacidad."""
    if capability not in PERMISSIONS:
        raise KeyError(f"Capacidad desconocida: {capability}")

    def checker(user: dict = Depends(current_user)) -> dict:
        if not allowed(user["role"], capability):
            raise HTTPException(status_code=403, detail="Acceso denegado.")
        return user
    return checker
