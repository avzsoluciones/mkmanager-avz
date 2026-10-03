from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .. import audit, db
from ..permissions import require
from ..security import client_ip

router = APIRouter(prefix="/api/v1/empresas", tags=["empresas"])


def _clean(v):
    if v is not None and any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise ValueError("El texto contiene caracteres no permitidos.")
    return v


class EmpresaIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=150)
    contacto_email: Optional[str] = Field(default=None, max_length=150)
    partner_id: Optional[int] = None          # solo lo usa AVZCloud; un aliado siempre crea en su cartera


    @field_validator("nombre", "contacto_email")
    @classmethod
    def _no_control_chars(cls, v):
        return _clean(v)


class EmpresaPatch(BaseModel):
    nombre: Optional[str] = Field(default=None, min_length=2, max_length=150)
    contacto_email: Optional[str] = Field(default=None, max_length=150)


    @field_validator("nombre", "contacto_email")
    @classmethod
    def _no_control_chars(cls, v):
        return _clean(v)


def empresa_scope(user: dict, alias: str = "e") -> tuple:
    """Fragmento SQL y parámetros que limitan las empresas a lo que el usuario puede ver."""
    role = user["role"]
    if role in ("superadmin", "soporte"):
        return "TRUE", ()
    if role in ("partner", "tecnico"):
        return f"{alias}.partner_id = %s", (user.get("tenant_id") or user["id"],)
    if role == "cliente":
        return f"EXISTS (SELECT 1 FROM sedes sc WHERE sc.empresa_id = {alias}.id AND sc.cliente_id = %s)", (user["id"],)
    return "FALSE", ()


def get_empresa(user: dict, empresa_id: int) -> dict:
    where, params = empresa_scope(user)
    row = db.fetch_one(f"SELECT e.id, e.nombre, e.partner_id, e.contacto_email FROM empresas e WHERE e.id = %s AND {where}",
                       (empresa_id, *params))
    if not row:
        raise HTTPException(status_code=404, detail="Empresa no encontrada.")
    return row


@router.get("")
def list_empresas(user: dict = Depends(require("empresa.ver"))):
    where, params = empresa_scope(user)
    return db.fetch_all(
        f"""SELECT e.id, e.nombre, e.contacto_email, e.partner_id, u.nombre_completo AS aliado,
                   (SELECT COUNT(*) FROM sedes s WHERE s.empresa_id = e.id) AS sedes
            FROM empresas e LEFT JOIN usuarios u ON u.id = e.partner_id
            WHERE {where} ORDER BY LOWER(u.nombre_completo), LOWER(e.nombre)""", params)


@router.post("", status_code=201)
def create_empresa(req: EmpresaIn, request: Request, user: dict = Depends(require("empresa.crear"))):
    # Un aliado siempre crea dentro de su propia cartera; AVZCloud puede indicar el aliado dueño.
    partner_id = (user.get("tenant_id") or user["id"]) if user["role"] == "partner" else req.partner_id
    if user["role"] != "partner" and partner_id is not None:
        if not db.fetch_one("SELECT 1 AS x FROM usuarios WHERE id = %s AND rol = 'partner' AND tenant_id = id", (partner_id,)):
            raise HTTPException(status_code=422, detail="El aliado indicado no existe.")
    nombre = req.nombre.strip()
    if db.fetch_one("SELECT 1 AS x FROM empresas WHERE LOWER(nombre) = LOWER(%s) AND partner_id IS NOT DISTINCT FROM %s",
                    (nombre, partner_id)):
        raise HTTPException(status_code=409, detail="Ya existe una empresa con ese nombre.")
    row = db.fetch_one(
        "INSERT INTO empresas (nombre, partner_id, contacto_email) VALUES (%s, %s, %s) RETURNING id, nombre, partner_id, contacto_email",
        (nombre, partner_id, (req.contacto_email or "").strip() or None))
    audit.log(user["email"], "empresa_creada", f"id={row['id']} nombre={row['nombre']}", client_ip(request))
    return {**row, "sedes": 0}


@router.patch("/{empresa_id}")
def update_empresa(empresa_id: int, req: EmpresaPatch, request: Request, user: dict = Depends(require("empresa.editar"))):
    emp = get_empresa(user, empresa_id)
    nombre = (req.nombre or emp["nombre"]).strip()
    if nombre.lower() != emp["nombre"].lower() and db.fetch_one(
            "SELECT 1 AS x FROM empresas WHERE LOWER(nombre) = LOWER(%s) AND partner_id IS NOT DISTINCT FROM %s AND id <> %s",
            (nombre, emp["partner_id"], empresa_id)):
        raise HTTPException(status_code=409, detail="Ya existe una empresa con ese nombre.")
    email = emp["contacto_email"] if req.contacto_email is None else ((req.contacto_email or "").strip() or None)
    row = db.fetch_one("UPDATE empresas SET nombre = %s, contacto_email = %s WHERE id = %s RETURNING id, nombre, partner_id, contacto_email",
                       (nombre, email, empresa_id))
    audit.log(user["email"], "empresa_editada", f"id={empresa_id} nombre={nombre}", client_ip(request))
    return row


@router.delete("/{empresa_id}")
def delete_empresa(empresa_id: int, request: Request, user: dict = Depends(require("empresa.eliminar"))):
    emp = get_empresa(user, empresa_id)
    n = db.fetch_one("SELECT COUNT(*) AS n FROM sedes WHERE empresa_id = %s", (empresa_id,))["n"]
    if n:
        raise HTTPException(status_code=409, detail=f"La empresa tiene {n} sede(s). Elimina las sedes antes de eliminarla.")
    db.execute("DELETE FROM empresas WHERE id = %s", (empresa_id,))
    audit.log(user["email"], "empresa_eliminada", f"id={empresa_id} nombre={emp['nombre']}", client_ip(request))
    return {"ok": True}
