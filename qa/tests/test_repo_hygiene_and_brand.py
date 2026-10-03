"""A9 (sin credenciales en el repositorio), marca/confidencialidad de proveedores, migraciones y despliegue.
IDs: A9-*, BR-*, MG-*, DP-*. NO lee `.env` (solo `.env.example`)."""
import os
import re

import pytest

from conftest import BACKEND, FRONT_JS, ROOT

FRONT = os.path.join(ROOT, "frontend-new")
SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".pytest_cache", "server", "backups", ".git"}
TEXT_EXT = {".py", ".js", ".html", ".css", ".md", ".sql", ".yml", ".yaml", ".json", ".txt", ".example", ".conf", ".sh", ".toml", ".ini"}


def walk(base, exts=TEXT_EXT, skip_files=()):
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f == ".env":
                continue                       # nunca se lee
            if f in skip_files:
                continue
            ext = os.path.splitext(f)[1].lower()
            if ext in exts or f.endswith(".env.example") or f == ".env.example":
                yield os.path.join(root, f)


def read(p):
    return open(p, encoding="utf-8", errors="replace").read()


CODE_DIRS = [os.path.join(BACKEND, "app"), os.path.join(BACKEND, "migrations"), FRONT, os.path.join(ROOT, "deploy"),
             os.path.join(ROOT, "specs")]
DOCS = [os.path.join(ROOT, "CLAUDE.md"), os.path.join(ROOT, "PROGRESO.md"), os.path.join(ROOT, ".env.example")]


def all_files():
    for d in CODE_DIRS:
        if os.path.isdir(d):
            yield from walk(d)
    for f in DOCS:
        if os.path.exists(f):
            yield f


SECRET_PATTERNS = [
    ("clave privada PEM/OpenSSH", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")),
    ("clave Fernet", re.compile(r"\b[A-Za-z0-9_-]{43}=(?![A-Za-z0-9+/=])")),
    ("URL con credenciales", re.compile(r"[a-z]+://[^/\s:@]+:[^/\s@]{4,}@[^\s/]+")),
    ("AWS", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("asignación de secreto literal", re.compile(r"""(?i)\b(password|passwd|secret|token|api[_-]?key|private[_-]?key)\b\s*[:=]\s*['"][^'"\s<{$][^'"\s]{7,}['"]""")),
]


@pytest.mark.parametrize("label,rx", SECRET_PATTERNS, ids=[s[0] for s in SECRET_PATTERNS])
def test_A901_no_secret_patterns_in_repo(label, rx):
    hits = []
    for p in all_files():
        for i, line in enumerate(read(p).splitlines(), 1):
            if rx.search(line) and "placeholder" not in line.lower() and "ejemplo" not in line.lower() and "example" not in line.lower():
                hits.append(f"{os.path.relpath(p, ROOT)}:{i}")
    assert hits == [], f"posibles secretos ({label}): {hits[:10]}"


def test_A902_env_example_exists_with_empty_values():
    p = os.path.join(ROOT, ".env.example")
    assert os.path.exists(p), ".env.example ausente"
    for line in read(p).splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            if any(s in k for s in ("PASSWORD", "SECRET", "KEY", "TOKEN")):
                assert v.strip() in ("", '""', "''") or v.strip().lower().startswith(("cambiar", "<", "changeme")), f"{k} trae valor real"


def test_A903_gitignore_covers_env_keys_and_server_copy():
    g = read(os.path.join(ROOT, ".gitignore"))
    for needle in (".env", "*.pem", "*.key", ".venv/", "server/"):
        assert needle in g


def test_A904_env_vars_used_by_code_are_documented_in_env_example():
    cfg = read(os.path.join(BACKEND, "app", "config.py"))
    used = set(re.findall(r'_env\("([A-Z_]+)"', cfg))
    documented = set(re.findall(r"^([A-Z_]+)=", read(os.path.join(ROOT, ".env.example")), re.M))
    assert used <= documented, f"variables sin documentar: {sorted(used - documented)}"


def test_A905_env_example_does_not_list_unused_vars():
    """ADMIN_BOOTSTRAP_PASSWORD figura en .env.example pero ningún código la lee (el superadmin no se puede sembrar)."""
    documented = set(re.findall(r"^([A-Z_]+)=", read(os.path.join(ROOT, ".env.example")), re.M))
    code = "".join(read(p) for p in walk(os.path.join(BACKEND, "app")))
    unused = {v for v in documented if v not in code}
    assert unused == set(), f"HALLAZGO (bajo): variables documentadas pero sin uso: {sorted(unused)}"


def test_A906_logs_never_print_secrets():
    for p in walk(os.path.join(BACKEND, "app"), exts={".py"}):
        for i, line in enumerate(read(p).splitlines(), 1):
            if re.search(r"(print|log\.\w+|logging\.\w+)\(", line):
                assert not re.search(r"(?i)password|secret|private|token|vault", line.split("#")[0]) or "audit" in line.lower() or "no se pudo" in line.lower(), \
                    f"{os.path.relpath(p, ROOT)}:{i}: {line.strip()}"


def test_A907_compose_has_no_literal_credentials_and_binds_localhost_only():
    c = read(os.path.join(ROOT, "deploy", "docker-compose.yml"))
    for m in re.finditer(r"(?im)^\s*(POSTGRES_PASSWORD|SECRET_KEY|VAULT_KEY|\w*PASSWORD\w*)\s*:\s*(.+)$", c):
        assert m.group(2).strip().startswith("${"), f"credencial literal: {m.group(1)}"
    for m in re.finditer(r'-\s*"([^"]*:\d+:\d+)"', c):
        assert m.group(1).startswith("127.0.0.1:"), f"puerto publicado al exterior: {m.group(1)}"


def test_A908_postgres_not_published_publicly():
    c = read(os.path.join(ROOT, "deploy", "docker-compose.yml"))
    assert '"5432:5432"' not in c and "0.0.0.0:5432" not in c


# ------------------------------------------------------------------ marca / confidencialidad
PROVIDERS = ["3HC", "Yotta", "Wasabi", "Polaris", "Acronis", "Virtualizor", "OpenStack", "Proxmox", "Coolify", "1Panel", "OpenResty",
             "Imunify", "Softaculous", "Backuply", "WHMCS", "CloudLinux", "Hetzner"]


def _front_files():
    return list(walk(FRONT, exts={".js", ".html", ".css", ".json", ".md", ".svg"}))


def test_BR01_no_provider_names_in_frontend_sources():
    hits = []
    for p in _front_files():
        t = read(p)
        for name in PROVIDERS:
            if re.search(rf"\b{re.escape(name)}\b", t, re.I):
                hits.append(f"{os.path.relpath(p, ROOT)}: {name}")
    assert hits == [], hits


def test_BR02_frontend_filenames_are_brand_neutral():
    for p in _front_files():
        base = os.path.basename(p).lower()
        for name in PROVIDERS + ["mikrotik", "wireguard", "routeros"]:
            assert name.lower() not in base, p


def test_BR03_technology_names_not_shown_to_users_outside_mock():
    """Spec 3.1: la interfaz no expone la tecnología subyacente. 'Winbox' se admite solo en la guía de instalación
    (herramienta del técnico); WireGuard/MikroTik/RouterOS no deben aparecer en textos visibles."""
    hits = []
    for p in _front_files():
        if os.path.basename(p) == "mock.js" or p.endswith(".md"):
            continue
        for i, line in enumerate(read(p).splitlines(), 1):
            if re.search(r"(?i)wireguard|mikrotik|routeros|librouteros", line):
                hits.append(f"{os.path.relpath(p, ROOT)}:{i}: {line.strip()[:100]}")
    assert hits == [], hits


def test_BR04_alt_and_aria_texts_have_no_provider_names():
    for p in _front_files():
        for m in re.finditer(r"""(?:alt|aria-label|title)\s*[:=]\s*[`'"]([^`'"]+)""", read(p)):
            for name in PROVIDERS:
                assert name.lower() not in m.group(1).lower(), (p, m.group(1))


def test_BR05_api_user_visible_strings_have_no_provider_names():
    hits = []
    for p in walk(os.path.join(BACKEND, "app"), exts={".py"}):
        for i, line in enumerate(read(p).splitlines(), 1):
            if re.search(r"detail\s*=|HubError\(|raise \w+Error\(", line):
                for name in PROVIDERS + ["MikroTik"]:
                    if re.search(rf"\b{name}\b", line, re.I):
                        hits.append(f"{os.path.relpath(p, ROOT)}:{i}")
    assert hits == [], hits


def test_BR06_error_strings_in_backend_do_not_name_internal_infrastructure():
    """Mensajes que viajan al navegador sin nombres internos (variables de entorno, interfaz del hub, exception types)."""
    hits = []
    for p in walk(os.path.join(BACKEND, "app"), exts={".py"}):
        for i, line in enumerate(read(p).splitlines(), 1):
            if ("HubError(" in line or "detail=str(e)" in line or "detail=" in line) and re.search(r"HUB_\*|\.env|VAULT_KEY|type\(e\)\.__name__|str\(e\)", line):
                hits.append(f"{os.path.relpath(p, ROOT)}:{i}: {line.strip()[:110]}")
    assert hits == [], "HALLAZGO (bajo/medio): mensajes de error con detalles internos:\n" + "\n".join(hits)


# ------------------------------------------------------------------ migraciones
MIG = os.path.join(BACKEND, "migrations")


def _mig_text():
    return {f: read(os.path.join(MIG, f)) for f in sorted(os.listdir(MIG)) if f.endswith(".sql")}


def test_MG01_migrations_are_idempotent():
    for f, t in _mig_text().items():
        for stmt in re.findall(r"(?is)(CREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX)[^;]*|ALTER\s+TABLE[^;]*)", t):
            assert re.search(r"(?i)IF NOT EXISTS", stmt), f"{f}: {stmt.strip()[:70]}"


def test_MG02_unique_constraints_protect_tunnel_ip_and_keys():
    t = " ".join(_mig_text().values())
    assert "ux_dispositivos_tunnel_ip" in t and "tunnel_ip VARCHAR(45) NOT NULL UNIQUE" in t and "public_key VARCHAR(64) NOT NULL UNIQUE" in t


def test_MG03_spec_tables_exist():
    t = " ".join(_mig_text().values())
    for tbl in ("wg_peers", "plantillas_aplicadas"):
        assert f"CREATE TABLE IF NOT EXISTS {tbl}" in t


def test_MG04_columns_written_by_provision_exist_in_migrations_or_base_schema():
    prov = read(os.path.join(BACKEND, "app", "routers", "provision.py"))
    cols = re.search(r"INSERT INTO dispositivos \(([^)]+)\)", prov, re.S).group(1)
    cols = [c.strip() for c in cols.split(",")]
    mig = " ".join(_mig_text().values())
    base = read(os.path.join(ROOT, "server", "database.py")) if os.path.exists(os.path.join(ROOT, "server", "database.py")) else ""
    missing = [c for c in cols if c not in mig and c not in base]
    assert missing == [], missing


@pytest.mark.xfail(strict=False, reason="HALLAZGO (medio): no hay migración 001 con el esquema base (usuarios, sedes, dispositivos, "
                                        "audit_logs); en una BD vacía 002 falla en ALTER TABLE dispositivos. Solo funciona sobre la BD existente.")
def test_MG05_fresh_database_can_be_bootstrapped_by_migrations_alone():
    t = _mig_text()
    first = sorted(t)[0]
    assert first.startswith("001") or "CREATE TABLE IF NOT EXISTS usuarios" in " ".join(t.values())


def test_MG06_migration_runner_applies_in_order_once(monkeypatch, tmp_path):
    from app import db
    (tmp_path / "002_b.sql").write_text("SELECT 2;")
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "ignorar.txt").write_text("x")
    executed, done = [], {"001_a.sql"}

    class Cur:
        def execute(self, sql, params=None):
            executed.append(sql)
            self._last = sql

        def fetchall(self):
            return [{"name": n} for n in done]

    class Ctx:
        def __enter__(self):
            return Cur()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(db, "cursor", lambda: Ctx())
    assert db.run_migrations(str(tmp_path)) == ["002_b.sql"]
    assert "SELECT 1;" not in executed and "SELECT 2;" in executed


def test_MG07_wg_peers_private_key_is_not_plaintext_column():
    t = " ".join(_mig_text().values())
    assert "private_key_enc" in t and "private_key " not in t.replace("private_key_enc", "")


# ------------------------------------------------------------------ despliegue
def test_DP01_dockerfile_exists_and_does_not_run_as_root_or_copy_env():
    p = os.path.join(BACKEND, "Dockerfile")
    if not os.path.exists(p):
        pytest.skip("backend/Dockerfile no encontrado (no verificable)")
    t = read(p)
    assert ".env" not in t.replace(".env.example", "")
    if "USER " not in t:
        pytest.xfail("HALLAZGO (bajo): el contenedor de la API corre como root (sin USER)")


def test_DP02_requirements_pinned_lower_bounds_only_flagged():
    t = read(os.path.join(BACKEND, "requirements.txt"))
    unpinned = [l for l in t.splitlines() if l.strip() and "==" not in l]
    if unpinned:
        pytest.xfail(f"HALLAZGO (bajo): dependencias sin versión fija (>=): despliegues no reproducibles: {unpinned[:3]}")
