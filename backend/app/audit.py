from . import db


def log(user_email: str, accion: str, detalles: str = "", ip: str = "") -> None:
    """Registra una acción. Nunca debe romper la operación principal."""
    try:
        db.execute(
            "INSERT INTO audit_logs (usuario_email, accion, detalles, ip_origen) VALUES (%s, %s, %s, %s)",
            (user_email, accion[:100], detalles[:2000], ip[:45]),
        )
    except Exception as e:
        print(f"[AUDIT] no se pudo registrar '{accion}': {e}")
