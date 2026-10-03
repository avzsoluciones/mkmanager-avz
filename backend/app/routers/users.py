import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .. import audit, db
from ..permissions import require
from ..security import client_ip, hash_password, temp_password

router = APIRouter(prefix="/api/v1/users", tags=["users"])
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
_COLS = "id, email, username, nombre_completo, rol, partner_id, tenant_id, activo, ultimo_login"


class UserCreate(BaseModel):
    email: str = Field(max_length=150)
    nombre_completo: str = Field(min_length=2, max_length=150)
    rol: Literal["superadmin", "soporte", "partner", "tecnico", "cliente"]
    username: Optional[str] = Field(default=None, max_length=80)
    partner_id: Optional[int] = None    # solo superadmin: aliado dueño de un cliente
    tenant_id: Optional[int] = None     # solo superadmin: cartera existente para un técnico de aliado

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        v = v.strip().lower()
        if not _EMAIL.match(v):
            raise ValueError("Correo inválido.")
        return v


class ActiveRequest(BaseModel):
    activo: bool


def _tenant(actor: dict) -> int:
    return actor.get("tenant_id") or actor["id"]


def _scope(actor: dict, alias: str = "u") -> tuple:
    if actor["role"] == "superadmin":
        return "TRUE", ()
    t = _tenant(actor)
    return f"({alias}.partner_id = %s OR {alias}.tenant_id = %s)", (t, t)


def _target(actor: dict, user_id: int) -> dict:
    """Usuario gestionable por `actor`; 404 si no existe o está fuera de su alcance (sin revelar cuál)."""
    where, params = _scope(actor)
    row = db.fetch_one(f"SELECT {_COLS} FROM usuarios u WHERE u.id = %s AND {where}", (user_id, *params))
    if not row:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if actor["role"] == "partner" and row["rol"] not in ("cliente", "tecnico"):
        raise HTTPException(status_code=403, detail="Acceso denegado.")   # otros partner solo los gestiona un superadmin
    return row


@router.get("")
def list_users(actor: dict = Depends(require("usuario.gestionar"))):
    where, params = _scope(actor)
    return db.fetch_all(f"SELECT {_COLS} FROM usuarios u WHERE {where} ORDER BY u.id", params)


@router.post("", status_code=201)
def create_user(req: UserCreate, request: Request, actor: dict = Depends(require("usuario.crear"))):
    partner_id, tenant_id = None, None
    if actor["role"] == "partner":
        if req.rol not in ("cliente", "tecnico"):
            raise HTTPException(status_code=403, detail="Un aliado solo puede crear usuarios cliente o técnicos de su cartera.")
        if req.rol == "cliente":
            partner_id = _tenant(actor)
        else:
            tenant_id = _tenant(actor)
    else:
        if req.rol == "tecnico":
            if not req.tenant_id:
                raise HTTPException(status_code=422, detail="Indique la cartera (tenant_id) del aliado al que pertenece el técnico.")
            principal = db.fetch_one("SELECT id FROM usuarios WHERE id = %s AND rol = 'partner' AND tenant_id = id", (req.tenant_id,))
            if not principal:
                raise HTTPException(status_code=422, detail="La cartera indicada no existe.")
            tenant_id = principal["id"]
        if req.rol == "cliente" and req.partner_id:
            owner = db.fetch_one("SELECT tenant_id, id FROM usuarios WHERE id = %s AND rol = 'partner'", (req.partner_id,))
            if not owner:
                raise HTTPException(status_code=422, detail="El aliado indicado no existe.")
            partner_id = owner["tenant_id"] or owner["id"]
        if req.rol == "partner" and req.tenant_id:
            principal = db.fetch_one("SELECT id FROM usuarios WHERE id = %s AND rol = 'partner' AND tenant_id = id", (req.tenant_id,))
            if not principal:
                raise HTTPException(status_code=422, detail="La cartera indicada no existe.")
            tenant_id = principal["id"]

    if db.fetch_one("SELECT 1 AS x FROM usuarios WHERE LOWER(email) = %s", (req.email,)):
        raise HTTPException(status_code=409, detail="Ya existe un usuario con ese correo.")

    password = temp_password()
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO usuarios (email, username, nombre_completo, password_hash, rol, partner_id, tenant_id, require_password_change)
               VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE) RETURNING id""",
            (req.email, req.username, req.nombre_completo.strip(), hash_password(password), req.rol, partner_id, tenant_id),
        )
        new_id = cur.fetchone()["id"]
        if req.rol == "partner" and tenant_id is None:       # cuenta principal de un aliado nuevo
            cur.execute("UPDATE usuarios SET tenant_id = id WHERE id = %s", (new_id,))
    audit.log(actor["email"], "usuario_creado", f"id={new_id} rol={req.rol}", client_ip(request))
    return {"status": "success", "user": {"id": new_id, "email": req.email, "rol": req.rol},
            "password_temporal": password, "aviso": "Entregue esta contraseña de forma segura; no se volverá a mostrar."}


@router.post("/{user_id}/active")
def set_active(user_id: int, req: ActiveRequest, request: Request, actor: dict = Depends(require("usuario.gestionar"))):
    target = _target(actor, user_id)
    if target["id"] == actor["id"] and not req.activo:
        raise HTTPException(status_code=400, detail="No puede desactivar su propia cuenta.")
    if not req.activo and target["rol"] == "superadmin":
        left = db.fetch_one("SELECT COUNT(*) AS n FROM usuarios WHERE rol = 'superadmin' AND activo AND id <> %s", (target["id"],))
        if not left or left["n"] == 0:
            raise HTTPException(status_code=400, detail="Debe quedar al menos un superadmin activo.")
    db.execute("UPDATE usuarios SET activo = %s WHERE id = %s", (req.activo, target["id"]))
    audit.log(actor["email"], "usuario_activado" if req.activo else "usuario_desactivado", f"id={target['id']}", client_ip(request))
    return {"status": "success", "activo": req.activo}


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, request: Request, actor: dict = Depends(require("usuario.gestionar"))):
    target = _target(actor, user_id)
    password = temp_password()
    db.execute("UPDATE usuarios SET password_hash = %s, require_password_change = TRUE WHERE id = %s",
               (hash_password(password), target["id"]))
    audit.log(actor["email"], "usuario_clave_restablecida", f"id={target['id']}", client_ip(request))
    return {"status": "success", "password_temporal": password,
            "aviso": "Entregue esta contraseña de forma segura; no se volverá a mostrar."}
