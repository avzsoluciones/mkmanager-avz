import hashlib
import secrets
import time
from collections import defaultdict, deque
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings
from . import db

ROLES = ("superadmin", "soporte", "partner", "tecnico", "cliente")
_bearer = HTTPBearer(auto_error=False)
_PBKDF2_ROUNDS = 100_000


# --- contraseñas (formato existente en BD: "salt_hex:clave_hex", PBKDF2-SHA256) ---
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS).hex()
    return f"{salt}:{key}"


def verify_password(stored: str, provided: str) -> bool:
    try:
        salt, key = stored.split(":")
        new_key = hashlib.pbkdf2_hmac("sha256", provided.encode(), salt.encode(), _PBKDF2_ROUNDS).hex()
        return secrets.compare_digest(key, new_key)
    except Exception:
        return False


def temp_password() -> str:
    """Contraseña temporal que cumple la política (mayúscula, minúscula, número y símbolo)."""
    return f"Av{secrets.token_urlsafe(9)}9*"


def password_is_strong(password: str) -> bool:
    return (
        len(password) >= 10
        and any(c.isupper() for c in password)
        and any(c.islower() for c in password)
        and any(c.isdigit() for c in password)
        and any(not c.isalnum() for c in password)
    )


# --- tokens ---
def create_token(user: dict) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {"sub": str(user["id"]), "role": user["rol"], "iat": now, "exp": now + s.token_hours * 3600}
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# --- limitador de intentos de login (en memoria, por IP + identificador) ---
_FAILS = defaultdict(deque)
_MAX_FAILS = 5
_WINDOW = 300


def login_blocked(key: str) -> bool:
    q = _FAILS[key]
    now = time.time()
    while q and now - q[0] > _WINDOW:
        q.popleft()
    return len(q) >= _MAX_FAILS


def login_failed(key: str) -> None:
    _FAILS[key].append(time.time())


def login_ok(key: str) -> None:
    _FAILS.pop(key, None)


# --- usuario actual y autorización ---
def public_user(row: dict) -> dict:
    # tenant_id: cartera a la que pertenece un usuario `partner` (cuenta principal del aliado).
    tenant = row.get("tenant_id") or (row["id"] if row["rol"] == "partner" else None)
    return {
        "id": row["id"],
        "email": row["email"],
        "username": row.get("username"),
        "full_name": row["nombre_completo"],
        "role": row["rol"],
        "partner_id": row.get("partner_id"),
        "tenant_id": tenant,
    }


def current_user(creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer)) -> dict:
    if not creds:
        raise HTTPException(status_code=401, detail="No autenticado.")
    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Sesión inválida o expirada.")
    row = db.fetch_one(
        "SELECT id, email, username, nombre_completo, rol, partner_id, tenant_id, activo FROM usuarios WHERE id = %s",
        (int(payload["sub"]),),
    )
    if not row or not row["activo"]:
        raise HTTPException(status_code=401, detail="Usuario inactivo o inexistente.")
    return public_user(row)


def require_roles(*allowed: str):
    def checker(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(status_code=403, detail="Acceso denegado.")
        return user
    return checker


def sede_scope(user: dict, alias: str = "s") -> tuple:
    """Fragmento SQL y parámetros que limitan sedes a lo que el usuario puede ver."""
    role = user["role"]
    if role in ("superadmin", "soporte"):
        return "TRUE", ()
    if role in ("partner", "tecnico"):
        return f"{alias}.partner_id = %s", (user.get("tenant_id") or user["id"],)
    if role == "cliente":
        return f"{alias}.cliente_id = %s", (user["id"],)
    return "FALSE", ()                       # rol desconocido: sin acceso


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""
