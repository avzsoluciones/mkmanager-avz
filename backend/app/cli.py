"""Comandos de administracion: `python -m app.cli <comando>`.

Comandos
  check-env            Lista las variables obligatorias que faltan (nunca muestra valores).
  migrate              Aplica las migraciones pendientes (el mismo runner que usa la API al arrancar).
  set-admin-password   Crea o actualiza admin@avzcloud.com como superadmin con la contrasena de
                       la variable de entorno ADMIN_BOOTSTRAP_PASSWORD. Nunca la imprime.
  disable-demo-users   Desactiva (no borra) usuarios sembrados de demostracion.

Codigos de salida: 0 correcto, 1 error de uso o de datos, 2 error inesperado.
"""
import argparse
import os
import sys

ADMIN_EMAIL = "admin@avzcloud.com"
ADMIN_USERNAME = "adminavz"
ADMIN_NAME = "Super Administrador AVZCloud"

# Usuarios de demostracion que sembraba el init_postgres.sql del sistema anterior.
DEMO_EMAILS = ("victor@partner.avzcloud.com", "admin@chipichape.com")
DEMO_USERNAMES = ("victor_partner", "cc_chipichape")
# Sal de los hashes de relleno de esas semillas (a1b2c3d4... y b2c3d4e5...): identifica
# "usuarios similares" aunque tengan otro correo. Un hash real usa una sal aleatoria.
DEMO_SALTS = ("a1b2c3d4e5f60718293a4b5c6d7e8f90", "b2c3d4e5f60718293a4b5c6d7e8f90a1")

# (variable, descripcion). Sin las obligatorias la API no arranca o no puede operar.
REQUIRED = (
    ("SECRET_KEY", "firma de tokens (minimo 32 caracteres)"),
    ("VAULT_KEY", "clave Fernet que cifra claves privadas WireGuard y contrasenas de API"),
    ("POSTGRES_DB", "nombre de la base"),
    ("POSTGRES_USER", "usuario de la base"),
    ("POSTGRES_PASSWORD", "contrasena de la base"),
)
REQUIRED_HUB = (
    ("HUB_HOST", "IP del hub en la red privada"),
    ("HUB_API_USER", "usuario de API del hub"),
    ("HUB_API_PASSWORD", "contrasena de API del hub"),
    ("HUB_WG_INTERFACE", "interfaz WireGuard del proyecto en el hub"),
    ("HUB_PUBLIC_ENDPOINT", "nombre o IP publica del hub para los clientes WireGuard"),
)
OPTIONAL = ("EDGE_API_USER", "EDGE_API_PASSWORD", "CORS_ORIGINS", "TOKEN_HOURS", "POSTGRES_HOST",
            "POSTGRES_PORT", "HUB_API_PORT", "HUB_PUBLIC_PORT")


def _get(name: str) -> str:
    return os.environ.get(name, "").strip()


def _db():
    # Import tardio: `check-env` debe funcionar sin base de datos ni SECRET_KEY valido.
    from . import db
    return db


# --------------------------------------------------------------------------- check-env
def cmd_check_env(args=None) -> int:
    missing, warnings = [], []
    for group, label in ((REQUIRED, "obligatoria"), (REQUIRED_HUB, "obligatoria (hub)")):
        for name, desc in group:
            if not _get(name):
                missing.append((name, label, desc))

    secret, vault = _get("SECRET_KEY"), _get("VAULT_KEY")
    if secret and len(secret) < 32:
        warnings.append("SECRET_KEY tiene menos de 32 caracteres: la API no arrancara.")
    elif secret and len(set(secret)) < 8:
        warnings.append("SECRET_KEY parece poco aleatoria (muy pocos caracteres distintos).")
    if vault:
        try:
            from cryptography.fernet import Fernet
            Fernet(vault.encode())
        except Exception:
            warnings.append("VAULT_KEY no es una clave Fernet valida (32 bytes en base64 url-safe).")
    if secret and vault and secret == vault:
        warnings.append("SECRET_KEY y VAULT_KEY son iguales: deben ser independientes.")
    if _get("ADMIN_BOOTSTRAP_PASSWORD"):
        warnings.append("ADMIN_BOOTSTRAP_PASSWORD esta definida en el entorno: no la guarde en .env; "
                        "usela solo para un comando y borrela.")

    for name, label, desc in missing:
        print(f"FALTA  {name}  [{label}]  {desc}")
    for w in warnings:
        print(f"AVISO  {w}")
    for name in OPTIONAL:
        if not _get(name):
            print(f"info   {name} vacia (opcional)")
    if not missing and not warnings:
        print("OK  todas las variables obligatorias estan definidas.")
    return 1 if missing or any("no arrancara" in w or "Fernet" in w for w in warnings) else 0


# --------------------------------------------------------------------------- migrate
def cmd_migrate(args=None) -> int:
    migrations_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")
    applied = _db().run_migrations(migrations_dir)
    print("Migraciones aplicadas ahora: " + (", ".join(applied) if applied else "ninguna (todo al dia)"))
    return 0


# --------------------------------------------------------------------------- set-admin-password
def cmd_set_admin_password(args=None) -> int:
    from .security import hash_password, password_is_strong
    password = os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")
    if not password:
        print("ERROR: defina ADMIN_BOOTSTRAP_PASSWORD en el entorno (no en .env).", file=sys.stderr)
        return 1
    if not password_is_strong(password):
        print("ERROR: la contrasena no cumple la politica (10+ caracteres con mayuscula, minuscula, "
              "numero y simbolo).", file=sys.stderr)
        return 1

    db = _db()
    hashed = hash_password(password)
    row = db.fetch_one("SELECT id FROM usuarios WHERE LOWER(email) = %s", (ADMIN_EMAIL,))
    if row:
        db.execute(
            "UPDATE usuarios SET password_hash = %s, rol = 'superadmin', activo = TRUE, "
            "require_password_change = TRUE WHERE id = %s",
            (hashed, row["id"]),
        )
        action = "actualizado"
    else:
        db.execute(
            "INSERT INTO usuarios (email, username, nombre_completo, password_hash, rol, activo, "
            "require_password_change) VALUES (%s, %s, %s, %s, 'superadmin', TRUE, TRUE)",
            (ADMIN_EMAIL, ADMIN_USERNAME, ADMIN_NAME, hashed),
        )
        action = "creado"
    _audit("cli_set_admin_password", f"{ADMIN_EMAIL} {action}")
    print(f"Superadmin {ADMIN_EMAIL} {action}. Se exigira cambio de contrasena en el primer acceso.")
    return 0


# --------------------------------------------------------------------------- disable-demo-users
def _is_demo(row: dict, extra: set) -> bool:
    if row["rol"] == "superadmin" or (row.get("email") or "").lower() == ADMIN_EMAIL:
        return False                      # nunca se desactiva a un superadmin desde aqui
    email = (row.get("email") or "").lower()
    username = (row.get("username") or "").lower()
    salt = (row.get("password_hash") or "").split(":")[0]
    return email in DEMO_EMAILS or email in extra or username in DEMO_USERNAMES or salt in DEMO_SALTS


def cmd_disable_demo_users(args) -> int:
    db = _db()
    extra = {e.strip().lower() for e in (args.email or [])}
    rows = db.fetch_all("SELECT id, email, username, rol, activo, password_hash FROM usuarios ORDER BY id")
    targets = [r for r in rows if _is_demo(r, extra)]
    if not targets:
        print("No hay usuarios de demostracion.")
        return 0
    changed = 0
    for r in targets:
        state = "ya inactivo" if not r["activo"] else ("se desactivaria" if args.dry_run else "desactivado")
        print(f"  {r['email']} ({r['rol']}): {state}")
        if r["activo"] and not args.dry_run:
            db.execute("UPDATE usuarios SET activo = FALSE WHERE id = %s", (r["id"],))
            changed += 1
    if not args.dry_run and changed:
        _audit("cli_disable_demo_users", f"{changed} usuario(s) desactivado(s)")
    print(("Simulacion: nada modificado." if args.dry_run else f"Desactivados: {changed}. No se borro ningun dato."))
    return 0


def _audit(action: str, detail: str) -> None:
    from . import audit
    audit.log("cli", action, detail, "cli")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check-env", help="comprueba variables obligatorias (sin mostrar valores)")
    sub.add_parser("migrate", help="aplica migraciones pendientes")
    sub.add_parser("set-admin-password", help="fija la contrasena del superadmin (ADMIN_BOOTSTRAP_PASSWORD)")
    d = sub.add_parser("disable-demo-users", help="desactiva usuarios de demostracion (no borra)")
    d.add_argument("--dry-run", action="store_true", help="solo muestra que se desactivaria")
    d.add_argument("--email", action="append", help="correo adicional a desactivar (repetible)")
    return p


COMMANDS = {
    "check-env": cmd_check_env,
    "migrate": cmd_migrate,
    "set-admin-password": cmd_set_admin_password,
    "disable-demo-users": cmd_disable_demo_users,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except Exception as e:                # nunca volcar trazas con datos: solo el tipo de error
        print(f"ERROR: {type(e).__name__} al ejecutar '{args.command}'.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
