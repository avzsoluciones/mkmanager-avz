"""Configuración común de las pruebas QA.

Importa la app añadiendo `backend` al sys.path (igual que backend/tests/conftest.py). Todo con dobles:
sin red, sin BD, sin hub real.
"""
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
QA_DIR = os.path.dirname(HERE)
ROOT = os.path.dirname(QA_DIR)
BACKEND = os.path.join(ROOT, "backend")
FRONT_JS = os.path.join(ROOT, "frontend-new", "js")
sys.path.insert(0, BACKEND)
sys.path.insert(0, HERE)
os.environ.setdefault("SECRET_KEY", "k" * 40)

from cryptography.fernet import Fernet  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import fakes  # noqa: E402

PASSWORD = "Clave*Segura2026"

# id de usuario sembrado por rol
ROLES = {"superadmin": 1, "soporte": 2, "partner_a": 3, "partner_b": 4, "cliente_a": 5, "cliente_b": 6,
         "tecnico_a": 7, "tecnico_b": 8, "partner_a2": 9}


@pytest.fixture
def settings(monkeypatch):
    from app import config
    for k, v in dict(SECRET_KEY="k" * 40, HUB_HOST="10.10.10.3", HUB_API_USER="u", HUB_API_PASSWORD="Pw-secreto-QA-123",
                     HUB_WG_INTERFACE="wireguard1", HUB_PUBLIC_ENDPOINT="vpn.example.com", HUB_PUBLIC_PORT="51820",
                     VAULT_KEY=Fernet.generate_key().decode()).items():
        monkeypatch.setenv(k, v)
    config._settings = None
    yield config.get_settings()
    config._settings = None


@pytest.fixture(autouse=True)
def _clean_limiters():
    from app import security as sec
    from app.routers import vpn
    sec._FAILS.clear()
    vpn._CREATES.clear()
    yield
    sec._FAILS.clear()
    vpn._CREATES.clear()


@pytest.fixture
def world(monkeypatch, settings):
    """Mundo sembrado.

    Aliado A (tenant 3): partner 3 y 9, técnico 7, cliente 5 (sede 10). Aliado B (tenant 4): partner 4, técnico 8, cliente 6 (sede 20).
    Sedes: 10 (A, cliente 5), 11 (A), 20 (B, cliente 6). Equipos: 100 (sede 10), 101 (sede 11), 200 (sede 20).
    VPN: peer 7001 de A (sede 10) y 7002 de B (sede 20).
    """
    from app import db, deps, security as sec
    from app.main import app
    from app.services import wgkeys
    from app.services.hub import HubClient

    m = fakes.MemDb()
    ph = sec.hash_password(PASSWORD)
    m.add_user(id=1, email="admin@avz.test", username="admin", rol="superadmin", password_hash=ph)
    m.add_user(id=2, email="soporte@avz.test", username="soporte", rol="soporte", password_hash=ph)
    m.add_user(id=3, email="partner.a@avz.test", username="partner_a", rol="partner", tenant_id=3, password_hash=ph)
    m.add_user(id=4, email="partner.b@avz.test", username="partner_b", rol="partner", tenant_id=4, password_hash=ph)
    m.add_user(id=5, email="cliente.a@avz.test", username="cliente_a", rol="cliente", partner_id=3, password_hash=ph)
    m.add_user(id=6, email="cliente.b@avz.test", username="cliente_b", rol="cliente", partner_id=4, password_hash=ph)
    m.add_user(id=7, email="tecnico.a@avz.test", username="tecnico_a", rol="tecnico", tenant_id=3, password_hash=ph)
    m.add_user(id=8, email="tecnico.b@avz.test", username="tecnico_b", rol="tecnico", tenant_id=4, password_hash=ph)
    m.add_user(id=9, email="partner.a2@avz.test", username="partner_a2", rol="partner", tenant_id=3, password_hash=ph)
    m.add_sede(id=10, nombre="Sede A1", partner_id=3, cliente_id=5)
    m.add_sede(id=11, nombre="Sede A2", partner_id=3)
    m.add_sede(id=20, nombre="Sede B1", partner_id=4, cliente_id=6)

    def provisioned(dev_id, sede_id, name, tunnel, lan):
        priv, pub = wgkeys.generate_keypair()
        d = m.add_device(id=dev_id, nombre=name, sede_id=sede_id, tunnel_ip=tunnel, lan_cidr=lan, ip_local=lan.replace("0/24", "1"),
                         api_user="avz-api", puerto_gestion=8728,
                         api_password_enc=wgkeys.encrypt_secret("clave-api-" + str(dev_id)),
                         wan_config={"interface": "ether1", "mode": "dhcp", "address": None, "gateway": None},
                         lan_config={"interface": "bridge", "cidr": lan, "gateway": None, "dhcp_start": None,
                                     "dhcp_end": None, "dns": ["1.1.1.1", "8.8.8.8"]})
        peer_id = f"*P{dev_id}"
        m.add_peer(tipo="router", nombre=name, dispositivo_id=dev_id, tunnel_ip=tunnel, public_key=pub,
                   private_key_enc=wgkeys.encrypt_secret(priv), hub_peer_id=peer_id)
        return d, peer_id

    api = fakes.FakeApi()
    for dev_id, sede_id, name, tunnel, lan in ((100, 10, "Router A1", "10.254.0.100", "192.168.100.0/24"),
                                               (101, 11, "Router A2", "10.254.0.101", "192.168.101.0/24"),
                                               (200, 20, "Router B1", "10.254.0.102", "192.168.200.0/24")):
        _, pid = provisioned(dev_id, sede_id, name, tunnel, lan)
        api.peers.append({".id": pid, "interface": "wireguard1", "allowed-address": f"{tunnel}/32,{lan}"})
        api.routes.append({".id": f"*R{dev_id}", "dst-address": lan, "gateway": tunnel, "comment": f"AVZ {name}", "dynamic": False})
    for pid, sede, tenant, name, ip in ((7001, 10, 3, "Laura A", "10.254.0.200"), (7002, 20, 4, "Pedro B", "10.254.0.201")):
        priv, pub = wgkeys.generate_keypair()
        m.wg_peers.append({"id": pid, "tipo": "usuario", "nombre": name, "dispositivo_id": None, "tunnel_ip": ip, "public_key": pub,
                           "private_key_enc": wgkeys.encrypt_secret(priv), "preshared_key_enc": None, "hub_peer_id": f"*U{pid}",
                           "client_allowed": "10.254.0.1/32,192.168.100.0/24", "dns": "192.168.100.1", "sede_id": sede,
                           "tenant_id": tenant, "revocado_en": None, "tunnel_ip_historica": None,
                           "creado_en": fakes._now(), "allowed_extra": None, "creado_por": "x"})
        api.peers.append({".id": f"*U{pid}", "interface": "wireguard1", "public-key": pub, "allowed-address": f"{ip}/32"})
    hub = HubClient(settings, connect=api.connect)

    for name in ("fetch_one", "fetch_all", "execute", "cursor"):
        monkeypatch.setattr(db, name, getattr(m, name))
    app.dependency_overrides[deps.get_hub] = lambda: hub
    client = TestClient(app, raise_server_exceptions=False)

    def token(uid):
        u = m.usuarios[uid]
        return sec.create_token({"id": u["id"], "rol": u["rol"]})

    def auth(uid):
        return {"Authorization": f"Bearer {token(uid)}"}

    w = types.SimpleNamespace(db=m, api=api, hub=hub, client=client, app=app, token=token, auth=auth,
                              settings=settings, make_client=lambda: TestClient(app, raise_server_exceptions=False))
    yield w
    app.dependency_overrides.clear()


BODY = {"sede_id": 10, "nombre": "MK Oficina", "wan": {"interface": "ether1", "mode": "dhcp"},
        "lan": {"interface": "bridge-lan", "cidr": "192.168.10.0/24"}}


def body(**over):
    import copy
    b = copy.deepcopy(BODY)
    for k, v in over.items():
        b[k] = v
    return b


def iter_routes(app):
    """(MÉTODO, ruta) de todas las rutas de la API. Usa el esquema OpenAPI porque en FastAPI reciente
    app.routes contiene routers incluidos sin atributo `path`."""
    app.openapi_schema = None
    spec = app.openapi()
    for path, ops in spec["paths"].items():
        for method in ops:
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                yield method.upper(), path


def route_exists(app, method, path_template):
    """True si la app tiene una ruta con ese método y patrón (parámetros y ids numéricos normalizados a {p})."""
    import re
    norm = lambda s: re.sub(r"/\d+(?=/|$)", "/{p}", re.sub(r"\{[^}]+\}", "{p}", s.split("?")[0]))  # noqa: E731
    want = norm(path_template)
    return any(m == method.upper() and norm(p) == want for m, p in iter_routes(app))

