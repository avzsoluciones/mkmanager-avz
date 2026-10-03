import re
import unicodedata
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StrictInt, field_validator

from .. import audit, db
from ..permissions import require
from ..security import client_ip, sede_scope
from .empresas import get_empresa

router = APIRouter(prefix="/api/v1/sedes", tags=["sedes"])

_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_DUP = "Ya existe una sede con ese nombre en esa empresa."
_OPCIONALES = ("direccion", "contacto_nombre", "contacto_telefono", "contacto_email")


def _sin_control(v):
    """Rechaza controles (Cc), de formato (Cf) y separadores de línea/párrafo (Zl, Zp)."""
    if isinstance(v, str) and any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in v):
        raise ValueError("El texto contiene caracteres no permitidos.")
    return v


def _limpiar(v):
    """Valida caracteres y recorta; los valores que no son texto los rechaza pydantic."""
    v = _sin_control(v)
    return v.strip() if isinstance(v, str) else v


def _opcional(v):
    v = _limpiar(v)
    return None if v == "" else v


def _email(v):
    if v is not None and not _EMAIL_RE.match(v):
        raise ValueError("El correo de contacto no es válido.")
    return v


class SedeCreate(BaseModel):
    empresa_id: StrictInt = Field(gt=0)           # obligatorio: primero la empresa, luego la sede
    nombre: str = Field(min_length=2, max_length=150)
    ciudad: str = Field(min_length=2, max_length=100)
    direccion: Optional[str] = Field(default=None, max_length=255)
    contacto_nombre: Optional[str] = Field(default=None, max_length=150)
    contacto_telefono: Optional[str] = Field(default=None, max_length=50)
    contacto_email: Optional[str] = Field(default=None, max_length=150)

    @field_validator("nombre", "ciudad", mode="before")
    @classmethod
    def _req_texto(cls, v):
        return _limpiar(v)

    @field_validator(*_OPCIONALES, mode="before")
    @classmethod
    def _opt_texto(cls, v):
        return _opcional(v)

    @field_validator("contacto_email")
    @classmethod
    def _chk_email(cls, v):
        return _email(v)


class SedePatch(BaseModel):
    # Todos los campos son opcionales: solo se actualizan los enviados.
    # nombre, ciudad y empresa_id no admiten null explícito (se rechaza en el endpoint).
    nombre: Optional[str] = Field(default=None, min_length=2, max_length=150)
    ciudad: Optional[str] = Field(default=None, min_length=2, max_length=100)
    direccion: Optional[str] = Field(default=None, max_length=255)
    contacto_nombre: Optional[str] = Field(default=None, max_length=150)
    contacto_telefono: Optional[str] = Field(default=None, max_length=50)
    contacto_email: Optional[str] = Field(default=None, max_length=150)
    empresa_id: Optional[StrictInt] = Field(default=None, gt=0)

    @field_validator("nombre", "ciudad", mode="before")
    @classmethod
    def _req_texto(cls, v):
        return _limpiar(v)

    @field_validator(*_OPCIONALES, mode="before")
    @classmethod
    def _opt_texto(cls, v):
        return _opcional(v)

    @field_validator("contacto_email")
    @classmethod
    def _chk_email(cls, v):
        return _email(v)


def _es_duplicado(e: Exception) -> bool:
    return getattr(e, "pgcode", None) == "23505"


@router.get("")
def list_sedes(user: dict = Depends(require("sede.ver"))):
    where, params = sede_scope(user)
    return db.fetch_all(
        f"""SELECT s.id, s.nombre, s.ciudad, s.empresa_id, e.nombre AS empresa, s.direccion,
                   s.contacto_nombre, s.contacto_telefono, s.contacto_email, s.estado,
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
                    (req.nombre, empresa["id"])):
        raise HTTPException(status_code=409, detail=_DUP)
    try:
        row = db.fetch_one(
            """INSERT INTO sedes (nombre, ciudad, empresa_id, direccion, partner_id, contacto_nombre, contacto_telefono, contacto_email)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id, nombre, ciudad, empresa_id, direccion, contacto_nombre, contacto_telefono, contacto_email, estado""",
            (req.nombre, req.ciudad, empresa["id"], req.direccion, partner_id,
             req.contacto_nombre, req.contacto_telefono, req.contacto_email),
        )
    except Exception as e:
        if _es_duplicado(e):
            raise HTTPException(status_code=409, detail=_DUP)
        raise
    audit.log(user["email"], "sede_creada", f"id={row['id']} nombre={row['nombre']} empresa={empresa['nombre']}", client_ip(request))
    return {**row, "empresa": empresa["nombre"]}


@router.patch("/{sede_id}")
def update_sede(sede_id: int, req: SedePatch, request: Request, user: dict = Depends(require("sede.editar"))):
    for k in ("nombre", "ciudad", "empresa_id"):
        if k in req.model_fields_set and getattr(req, k) is None:
            raise HTTPException(status_code=422, detail=f"El campo {k} no puede ser nulo.")
    where, params = sede_scope(user)
    sede = db.fetch_one(
        f"""SELECT s.id, s.nombre, s.ciudad, s.empresa_id, s.partner_id,
                   (SELECT e0.nombre FROM empresas e0 WHERE e0.id = s.empresa_id) AS empresa_nombre
            FROM sedes s WHERE s.id = %s AND {where}""",
        (sede_id, *params))
    if not sede:
        raise HTTPException(status_code=404, detail="Sede no encontrada.")
    cambios = req.model_dump(exclude_unset=True)
    empresa_final = sede["empresa_id"]
    nuevo_empresa = cambios.pop("empresa_id", None)
    if nuevo_empresa is not None and nuevo_empresa != sede["empresa_id"]:
        destino = get_empresa(user, nuevo_empresa)          # 404 si no es visible
        if destino["partner_id"] != sede["partner_id"]:
            raise HTTPException(status_code=422, detail="La empresa pertenece a otro aliado.")
        empresa_final = destino["id"]
        cambios["empresa_id"] = empresa_final
    if "nombre" in cambios or "empresa_id" in cambios:
        nombre_final = cambios.get("nombre", sede["nombre"])
        if db.fetch_one("SELECT 1 AS x FROM sedes WHERE LOWER(nombre) = LOWER(%s) AND empresa_id = %s AND id <> %s",
                        (nombre_final, empresa_final, sede_id)):
            raise HTTPException(status_code=409, detail=_DUP)
    if cambios:
        # Los nombres de columna salen de las claves fijas del modelo, nunca del usuario.
        sets = ", ".join(f"{k} = %s" for k in cambios)
        try:
            db.execute(f"UPDATE sedes s SET {sets} WHERE s.id = %s AND {where}",
                       (*cambios.values(), sede_id, *params))
        except Exception as e:
            if _es_duplicado(e):
                raise HTTPException(status_code=409, detail=_DUP)
            raise
    row = db.fetch_one(
        """SELECT s.id, s.nombre, s.ciudad, s.empresa_id, e.nombre AS empresa, s.direccion, s.contacto_nombre,
                  s.contacto_telefono, s.contacto_email, s.estado, s.partner_id
           FROM sedes s JOIN empresas e ON e.id = s.empresa_id WHERE s.id = %s""", (sede_id,))
    if row["empresa_id"] != sede["empresa_id"]:
        emp = f"empresa_anterior={sede.get('empresa_nombre')} empresa={row['empresa']}"
    else:
        emp = f"empresa={row['empresa']}"
    audit.log(user["email"], "sede_editada", f"id={sede_id} nombre={row['nombre']} {emp}", client_ip(request))
    return row


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
