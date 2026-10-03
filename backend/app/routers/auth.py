from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .. import audit, db
from ..security import (client_ip, create_token, current_user, hash_password, login_blocked,
                        login_failed, login_ok, password_is_strong, public_user, verify_password)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/login")
def login(req: LoginRequest, request: Request):
    ident = req.email.strip().lower()
    key = f"{client_ip(request)}|{ident}"
    if login_blocked(key):
        raise HTTPException(status_code=429, detail="Demasiados intentos. Espere unos minutos.")

    row = db.fetch_one(
        "SELECT id, email, username, nombre_completo, password_hash, rol, partner_id, tenant_id, activo, require_password_change "
        "FROM usuarios WHERE LOWER(email) = %s OR LOWER(username) = %s",
        (ident, ident),
    )
    if not row or not row["activo"] or not verify_password(row["password_hash"], req.password):
        login_failed(key)
        audit.log(ident, "login_fallido", "", client_ip(request))
        raise HTTPException(status_code=401, detail="Credenciales incorrectas o usuario inactivo.")

    login_ok(key)
    db.execute("UPDATE usuarios SET ultimo_login = now() WHERE id = %s", (row["id"],))
    audit.log(row["email"], "login", "", client_ip(request))
    return {
        "access_token": create_token(row),
        "token_type": "bearer",
        "user": public_user(row),
        "require_password_change": bool(row["require_password_change"]),
    }


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {"user": user}


@router.post("/change-password")
def change_password(req: ChangePasswordRequest, request: Request, user: dict = Depends(current_user)):
    row = db.fetch_one("SELECT password_hash FROM usuarios WHERE id = %s", (user["id"],))
    if not row or not verify_password(row["password_hash"], req.current_password):
        raise HTTPException(status_code=400, detail="La contraseña actual no es correcta.")
    if not password_is_strong(req.new_password):
        raise HTTPException(status_code=400, detail="La nueva contraseña debe tener 10+ caracteres con mayúscula, minúscula, número y símbolo.")
    db.execute("UPDATE usuarios SET password_hash = %s, require_password_change = FALSE WHERE id = %s",
               (hash_password(req.new_password), user["id"]))
    audit.log(user["email"], "cambio_password", "", client_ip(request))
    return {"status": "success"}
