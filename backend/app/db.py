import os
from contextlib import contextmanager

from .config import get_settings

# psycopg2 se importa al abrir la primera conexión, no al cargar el módulo: así las pruebas que
# simulan la base de datos (y la carga de la aplicación) no dependen de la librería nativa.

_pool = None


def init_pool() -> None:
    global _pool
    if _pool is not None:
        return
    from psycopg2.pool import ThreadedConnectionPool
    s = get_settings()
    _pool = ThreadedConnectionPool(
        1, 10, host=s.pg_host, port=s.pg_port, dbname=s.pg_db,
        user=s.pg_user, password=s.pg_password, connect_timeout=5,
    )


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def cursor():
    """Cursor con commit al salir y rollback ante cualquier excepción."""
    init_pool()
    import psycopg2.extras
    conn = _pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)


def fetch_all(sql: str, params=()) -> list:
    with cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def fetch_one(sql: str, params=()):
    with cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def execute(sql: str, params=()) -> None:
    with cursor() as cur:
        cur.execute(sql, params)


def run_migrations(directory: str) -> list:
    """Aplica en orden los .sql de `directory` que no consten en schema_migrations."""
    applied_now = []
    with cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, aplicada_en TIMESTAMPTZ DEFAULT now())")
        cur.execute("SELECT name FROM schema_migrations")
        done = {r["name"] for r in cur.fetchall()}
        for fname in sorted(f for f in os.listdir(directory) if f.endswith(".sql")):
            if fname in done:
                continue
            with open(os.path.join(directory, fname), encoding="utf-8") as fh:
                cur.execute(fh.read())
            cur.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (fname,))
            applied_now.append(fname)
    return applied_now
