from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .. import audit, db
from ..permissions import require
from ..security import client_ip, sede_scope
from .empresas import get_empresa

router = APIRouter(prefix="/api/v1/sedes", tags=["sedes"])


class SedeCreate(BaseModel):
    empresa_id: int = Field(gt=0)                 # obligatorio: primero la empresa, luego la sede
    nombre: str = Field(min_length=2, max_length=150)
    ciudad: str = Field(min_length=2, max_length=100)
    direccion: Optional[str] = None
    contacto_nombre: Optional[str] = None
    contacto_telefono: Optional[str] = None
    contacto_email: Optional[str] = None

    @field_validator("nombre", "ciudad", "direccion", "contacto_nombre", "contacto_telefono", "contacto_email")
    @classmethod
    def _no_control_chars(cls, v):
        if v is not None and any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError("El texto contiene caracteres no permitidos.")
        return v


@router.get("")
def list_sedes(user: dict = Depends(require("sede.ver"))):
    where, params = sede_scope(user)
    return db.fetch_all(
        f"""SELECT s.id, s.nombre, s.ciudad, s.empresa_id, e.nombre AS empresa, s.direccion, s.contacto_email, s.estado,
                   s.partner_id,
                   (SELECT COUNT(*) FROM dispositivos d WHERE d.sede_id = s.id) AS dispositivos
            FROM sedes s JOIN empresas e ON e.id = s.empresa_id
            WHERE {where} ORDER BY s.id""",
        params,
    )


@router.post("", status_code=201)
def create_sede(req: SedeCreate, request: Request, user: dict = Depends(require("sede.crear"))):
    # La empresa debe existir y ser visible para quien crea; la sede hereda el aliado de la empresa.
    empresa = get_empresa(user, req.empresa_id)
    partner_id = empresa["partner_id"]
    if db.fetch_one("SELECT 1 AS x FROM sedes WHERE LOWER(nombre) = LOWER(%s) AND empresa_id = %s",
                    (req.nombre.strip(), empresa["id"])):
        raise HTTPException(status_code=409, detail="Ya existe una sede con ese nombre en esa empresa.")
    row = db.fetch_one(
        """INSERT INTO sedes (nombre, ciudad, empresa_id, direccion, partner_id, contacto_nombre, contacto_telefono, contacto_email)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, nombre, ciudad, empresa_id, estado""",
        (req.nombre.strip(), req.ciudad.strip(), empresa["id"], req.direccion, partner_id,
         req.contacto_nombre, req.contacto_telefono, req.contacto_email),
    )
    audit.log(user["email"], "sede_creada", f"id={row['id']} nombre={row['nombre']} empresa={empresa['nombre']}", client_ip(request))
    return {**row, "empresa": empresa["nombre"]}


@router.delete("/{sede_id}")
def delete_sede(sede_id: int, request: Request, user: dict = Depends(require("sede.eliminar"))):
    where, params = sede_scope(user)
    sede = db.fetch_one(f"SELECT s.id, s.nombre FROM sedes s WHERE s.id = %s AND {where}", (sede_id, *params))
    if not sede:
        raise HTTPException(status_code=404, detail="Sede no encontrada.")
    equipos = db.fetch_one("SELECT COUNT(*) AS n FROM dispositivos WHERE sede_id = %s", (sede_id,))["n"]
    if equipos:
        raise HTTPException(status_code=409, detail=f"La sede tiene {equipos} equipo(s). Da de baja los equipos antes de eliminarla.")
    vpn = db.fetch_one("SELECT COUNT(*) AS n FROM wg_peers WHERE sede_id = %s AND tipo = 'usuario' AND revocado_en IS NULL",
                       (sede_id,))["n"]
    if vpn:
        raise HTTPException(status_code=409, detail=f"La sede tiene {vpn} acceso(s) VPN activo(s). Revócalos antes de eliminarla.")
    db.execute("DELETE FROM sedes WHERE id = %s", (sede_id,))
    audit.log(user["email"], "sede_eliminada", f"id={sede_id} nombre={sede['nombre']}", client_ip(request))
    return {"ok": True}
