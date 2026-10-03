import pytest
from fastapi.testclient import TestClient

from app import permissions
from app.main import app
from app.security import current_user, sede_scope

R = ("superadmin", "soporte", "partner", "tecnico", "cliente")

# Matriz esperada: copia literal de specs/001 sección 3.2 (si cambia la spec, cambia aquí y en permissions.py).
EXPECTED = {
    "panel.ver": R,
    "empresa.ver": R,
    "empresa.crear": ("superadmin", "soporte", "partner"),
    "empresa.editar": ("superadmin", "soporte", "partner"),
    "empresa.eliminar": ("superadmin",),
    "sede.ver": R,
    "sede.crear": ("superadmin", "soporte", "partner"),
    "sede.editar": ("superadmin", "soporte", "partner"),
    "sede.eliminar": ("superadmin",),
    "equipo.ver": R,
    "equipo.estado": R,
    "equipo.aprovisionar": ("superadmin", "soporte", "partner", "tecnico"),
    "equipo.script": ("superadmin", "soporte", "partner", "tecnico"),
    "equipo.baja": ("superadmin", "partner"),
    "plantilla.bajo_medio": ("superadmin", "soporte", "partner", "tecnico"),
    "plantilla.alto": ("superadmin", "soporte", "partner", "tecnico"),
    "aplicacion.confirmar": ("superadmin", "soporte", "partner", "tecnico"),
    "vpn.gestionar": ("superadmin", "soporte", "partner", "tecnico"),
    "usuario.crear": ("superadmin", "partner"),
    "usuario.gestionar": ("superadmin", "partner"),
    "auditoria.ver": ("superadmin", "soporte", "partner", "tecnico"),
    "global.gestionar": ("superadmin",),
    "hub.ver": ("superadmin", "soporte"),
}


def test_matrix_matches_spec():
    assert set(permissions.PERMISSIONS) == set(EXPECTED)
    for cap, roles in EXPECTED.items():
        assert set(permissions.PERMISSIONS[cap]) == set(roles), cap


@pytest.mark.parametrize("cap", sorted(EXPECTED))
@pytest.mark.parametrize("role", R)
def test_allowed_function(cap, role):
    assert permissions.allowed(role, cap) is (role in EXPECTED[cap])


def test_cliente_never_gets_operational_capabilities():
    for cap in ("equipo.script", "equipo.aprovisionar", "vpn.gestionar", "plantilla.bajo_medio", "usuario.crear"):
        assert not permissions.allowed("cliente", cap)


def test_tecnico_cannot_administer():
    for cap in ("usuario.crear", "usuario.gestionar", "equipo.baja", "sede.crear", "sede.eliminar", "global.gestionar"):
        assert not permissions.allowed("tecnico", cap)


def test_unknown_capability_fails_loudly():
    with pytest.raises(KeyError):
        permissions.require("no.existe")


def test_tenant_roles_share_one_scope():
    partner = {"role": "partner", "id": 10, "tenant_id": 10}
    tecnico = {"role": "tecnico", "id": 11, "tenant_id": 10}      # Alirio, técnico del aliado 10
    assert sede_scope(partner) == sede_scope(tecnico) == ("s.partner_id = %s", (10,))


def test_tecnico_of_other_tenant_has_other_scope():
    assert sede_scope({"role": "tecnico", "id": 12, "tenant_id": 20}) == ("s.partner_id = %s", (20,))


def test_every_protected_route_requires_authentication():
    """Ninguna ruta de la API (salvo login y health) responde sin token. Se enumera por el esquema OpenAPI."""
    c = TestClient(app, raise_server_exceptions=False)
    public = {("post", "/api/v1/auth/login"), ("get", "/api/v1/health")}
    checked = 0
    for path, methods in app.openapi()["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for method in methods:
            if (method, path) in public:
                continue
            url = path.replace("{device_id}", "1").replace("{user_id}", "1").replace("{client_id}", "1")
            r = c.request(method.upper(), url, json={})
            assert r.status_code == 401, f"{method.upper()} {path} -> {r.status_code}"
            checked += 1
    assert checked >= 10


def test_unknown_role_sees_nothing():
    # SEG-019: un rol desconocido falla cerrado.
    assert sede_scope({"role": "invitado", "id": 9}) == ("FALSE", ())


def test_sede_rejects_control_characters():
    from pydantic import ValidationError
    from app.routers.sedes import SedeCreate
    with pytest.raises(ValidationError):
        SedeCreate(nombre="Sede\n/user add", ciudad="Cali", empresa_id=1)
    assert SedeCreate(nombre="Centro Comercial Chipichape", ciudad="Cali", empresa_id=1)
