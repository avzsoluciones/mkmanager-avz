"""Pruebas de app.cli con una base de datos simulada en memoria (sin PostgreSQL)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SECRET_KEY", "k" * 40)

from app import cli, security  # noqa: E402

GOOD = "Clave-Segura-2026*"


class FakeDB:
    """Interpreta solo las consultas que usa cli.py."""

    def __init__(self, users=None):
        self.users = users or []
        self.audit = []
        self.migrations_called_with = None

    def fetch_one(self, sql, params=()):
        assert "FROM usuarios WHERE LOWER(email)" in sql
        for u in self.users:
            if u["email"].lower() == params[0]:
                return {"id": u["id"]}
        return None

    def fetch_all(self, sql, params=()):
        assert sql.startswith("SELECT id, email, username, rol, activo, password_hash FROM usuarios")
        return [dict(u) for u in self.users]

    def execute(self, sql, params=()):
        if sql.startswith("INSERT INTO audit_logs"):
            self.audit.append(params)
        elif sql.startswith("INSERT INTO usuarios"):
            email, username, name, hashed = params
            self.users.append({"id": len(self.users) + 1, "email": email, "username": username,
                               "rol": "superadmin", "activo": True, "password_hash": hashed,
                               "require_password_change": True})
        elif sql.startswith("UPDATE usuarios SET password_hash"):
            hashed, uid = params
            u = next(x for x in self.users if x["id"] == uid)
            u.update(password_hash=hashed, rol="superadmin", activo=True, require_password_change=True)
        elif sql.startswith("UPDATE usuarios SET activo = FALSE"):
            next(x for x in self.users if x["id"] == params[0])["activo"] = False
        else:
            raise AssertionError(f"SQL inesperado: {sql}")

    def run_migrations(self, directory):
        self.migrations_called_with = directory
        return ["001_base.sql"]


def user(i, email, rol="partner", username=None, salt="f" * 32, activo=True):
    return {"id": i, "email": email, "username": username, "rol": rol, "activo": activo,
            "password_hash": f"{salt}:{'0' * 64}"}


@pytest.fixture
def fake(monkeypatch):
    f = FakeDB()
    # cli y audit comparten el mismo modulo db: se parchean sus funciones.
    from app import db
    for name in ("fetch_one", "fetch_all", "execute", "run_migrations"):
        monkeypatch.setattr(db, name, getattr(f, name))
    return f


# ---------------------------------------------------------------- set-admin-password
def test_set_admin_creates_superadmin_with_forced_change_and_never_prints_password(fake, monkeypatch, capsys):
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", GOOD)
    assert cli.main(["set-admin-password"]) == 0
    out = capsys.readouterr()
    assert GOOD not in out.out + out.err
    u = fake.users[0]
    assert u["email"] == "admin@avzcloud.com" and u["rol"] == "superadmin"
    assert u["require_password_change"] is True and u["activo"] is True
    assert security.verify_password(u["password_hash"], GOOD)
    assert GOOD not in repr(fake.audit)
    assert fake.audit and fake.audit[0][0] == "cli"


def test_set_admin_updates_existing_and_reactivates(fake, monkeypatch):
    fake.users.append(user(1, "admin@avzcloud.com", rol="soporte", username="adminavz", activo=False))
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", GOOD)
    assert cli.main(["set-admin-password"]) == 0
    assert len(fake.users) == 1
    u = fake.users[0]
    assert u["rol"] == "superadmin" and u["activo"] and u["require_password_change"]
    assert u["username"] == "adminavz"
    assert security.verify_password(u["password_hash"], GOOD)


@pytest.mark.parametrize("bad", ["", "corta1*A", "sinsimbolo12345A", "SINMINUSCULA1*ABC"])
def test_set_admin_rejects_weak_or_missing_without_touching_db(fake, monkeypatch, capsys, bad):
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", bad)
    assert cli.main(["set-admin-password"]) == 1
    out = capsys.readouterr()
    if bad:
        assert bad not in out.out + out.err
    assert fake.users == [] and fake.audit == []


# ---------------------------------------------------------------- disable-demo-users
def test_disable_demo_users_deactivates_only_demo_and_keeps_data(fake, capsys):
    fake.users += [
        user(1, "admin@avzcloud.com", rol="superadmin", username="adminavz"),
        user(2, "victor@partner.avzcloud.com", username="victor_partner"),
        user(3, "admin@chipichape.com", rol="cliente", username="cc_chipichape"),
        user(4, "otro@demo.test", rol="cliente", salt="a1b2c3d4e5f60718293a4b5c6d7e8f90"),   # sal de relleno
        user(5, "real@aliado.com"),
    ]
    assert cli.main(["disable-demo-users"]) == 0
    state = {u["id"]: u["activo"] for u in fake.users}
    assert state == {1: True, 2: False, 3: False, 4: False, 5: True}
    assert len(fake.users) == 5                               # no se borra nada
    assert "Desactivados: 3" in capsys.readouterr().out


def test_disable_demo_users_dry_run_changes_nothing(fake, capsys):
    fake.users.append(user(2, "victor@partner.avzcloud.com"))
    assert cli.main(["disable-demo-users", "--dry-run"]) == 0
    assert fake.users[0]["activo"] is True and fake.audit == []
    assert "se desactivaria" in capsys.readouterr().out


def test_disable_demo_users_never_touches_superadmin_even_if_listed(fake):
    fake.users.append(user(1, "admin@avzcloud.com", rol="superadmin"))
    assert cli.main(["disable-demo-users", "--email", "admin@avzcloud.com"]) == 0
    assert fake.users[0]["activo"] is True


def test_disable_demo_users_extra_email_and_idempotent(fake):
    fake.users.append(user(7, "prueba@x.com"))
    assert cli.main(["disable-demo-users", "--email", "Prueba@X.com"]) == 0
    assert fake.users[0]["activo"] is False
    assert cli.main(["disable-demo-users", "--email", "prueba@x.com"]) == 0     # segunda vez: sin cambios


# ---------------------------------------------------------------- check-env
ALL = {"SECRET_KEY": "s" * 10 + "abcdefghijklmnopqrstuvwxyz", "POSTGRES_DB": "d", "POSTGRES_USER": "u",
       "POSTGRES_PASSWORD": "p-secreto-xyz", "HUB_HOST": "h", "HUB_API_USER": "u",
       "HUB_API_PASSWORD": "hub-secreto-xyz", "HUB_WG_INTERFACE": "w", "HUB_PUBLIC_ENDPOINT": "e"}


def _env(monkeypatch, **extra):
    for k in [n for n, _ in cli.REQUIRED + cli.REQUIRED_HUB] + list(cli.OPTIONAL) + ["ADMIN_BOOTSTRAP_PASSWORD"]:
        monkeypatch.delenv(k, raising=False)
    from cryptography.fernet import Fernet
    values = dict(ALL, VAULT_KEY=Fernet.generate_key().decode())
    values.update(extra)
    for k, v in values.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def test_check_env_ok(monkeypatch, capsys):
    _env(monkeypatch)
    assert cli.main(["check-env"]) == 0
    assert "OK" in capsys.readouterr().out


def test_check_env_lists_missing_names_without_values(monkeypatch, capsys):
    _env(monkeypatch, VAULT_KEY=None, HUB_API_PASSWORD=None)
    assert cli.main(["check-env"]) == 1
    out = capsys.readouterr().out
    assert "FALTA  VAULT_KEY" in out and "FALTA  HUB_API_PASSWORD" in out
    for secret in ("p-secreto-xyz", ALL["SECRET_KEY"]):
        assert secret not in out


def test_check_env_flags_short_secret_and_bad_vault(monkeypatch, capsys):
    _env(monkeypatch, SECRET_KEY="corta", VAULT_KEY="no-es-fernet")
    assert cli.main(["check-env"]) == 1
    out = capsys.readouterr().out
    assert "SECRET_KEY tiene menos de 32" in out and "Fernet" in out
    assert "no-es-fernet" not in out


def test_check_env_warns_if_bootstrap_password_in_env_but_never_prints_it(monkeypatch, capsys):
    _env(monkeypatch, ADMIN_BOOTSTRAP_PASSWORD=GOOD)
    cli.main(["check-env"])
    out = capsys.readouterr().out
    assert "ADMIN_BOOTSTRAP_PASSWORD" in out and GOOD not in out


# ---------------------------------------------------------------- migrate / errores
def test_migrate_uses_migrations_dir(fake, capsys):
    assert cli.main(["migrate"]) == 0
    assert fake.migrations_called_with.endswith("migrations")
    assert os.path.exists(os.path.join(fake.migrations_called_with, "001_base.sql"))


def test_unexpected_error_does_not_leak_details(monkeypatch, capsys):
    monkeypatch.setenv("ADMIN_BOOTSTRAP_PASSWORD", GOOD)
    from app import db

    def boom(*a, **k):
        raise RuntimeError("host=10.1.1.1 password=SECRETO")
    monkeypatch.setattr(db, "fetch_one", boom)
    assert cli.main(["set-admin-password"]) == 2
    out = capsys.readouterr()
    assert "SECRETO" not in out.out + out.err and "RuntimeError" in out.err


def test_001_base_has_no_seed_users_and_is_idempotent():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations", "001_base.sql")
    sql = open(path, encoding="utf-8").read()
    low = sql.lower()
    assert "insert into usuarios" not in low and "insert into sedes" not in low
    assert "chipichape" not in low and "victor" not in low
    assert low.count("create table") == low.count("create table if not exists") == 9
    import re
    statements = chr(10).join(l.split("--")[0] for l in low.splitlines())
    assert not re.search("(^|;)[ ]*(drop|delete|truncate|update|alter) ", statements.replace(chr(10), " ;"))
    assert "on conflict (id) do nothing" in low
