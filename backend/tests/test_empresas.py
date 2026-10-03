"""Jerarquía obligatoria Empresa -> Sede (spec 001, M8)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import empresas as empresas_router
from app.routers import sedes as sedes_router
from app.security import current_user

from test_users_and_baja import ScriptedDb, as_user


@pytest.fixture(autouse=True)
def _clean():
    yield
    app.dependency_overrides.clear()


def _client(rules):
    fake = ScriptedDb(rules)
    for mod in (empresas_router, sedes_router):
        mod.db = fake
        mod.audit = type("A", (), {"log": staticmethod(lambda *a, **k: None)})
    return TestClient(app), fake


SEDE = {"nombre": "Tienda Centro", "ciudad": "Medellín"}


def test_crear_sede_sin_empresa_da_422():
    as_user("superadmin")
    c, fake = _client([])
    assert c.post("/api/v1/sedes", json=SEDE).status_code == 422
    assert not fake.writes


def test_crear_sede_con_empresa_inexistente_o_ajena_da_404():
    as_user("partner", tenant=9)
    c, fake = _client([])                       # la empresa no es visible para este aliado
    assert c.post("/api/v1/sedes", json={**SEDE, "empresa_id": 5}).status_code == 404


def test_crear_sede_hereda_el_aliado_de_la_empresa():
    as_user("superadmin")
    c, fake = _client([("FROM empresas e WHERE", {"id": 5, "nombre": "AFS", "partner_id": 9, "contacto_email": None}),
                       ("INSERT INTO sedes", {"id": 1, "nombre": "Tienda Centro", "ciudad": "Medellín", "empresa_id": 5, "estado": "activa"})])
    r = c.post("/api/v1/sedes", json={**SEDE, "empresa_id": 5})
    assert r.status_code == 201 and r.json()["empresa"] == "AFS"


def test_empresa_duplicada_da_409():
    as_user("partner", tenant=9)
    c, _ = _client([("FROM empresas WHERE LOWER(nombre)", {"x": 1})])
    assert c.post("/api/v1/empresas", json={"nombre": "AFS"}).status_code == 409


def test_eliminar_empresa_con_sedes_da_409_y_solo_superadmin():
    as_user("superadmin")
    c, fake = _client([("FROM empresas e WHERE", {"id": 5, "nombre": "AFS", "partner_id": 9, "contacto_email": None}),
                       ("FROM sedes WHERE empresa_id", {"n": 2})])
    assert c.delete("/api/v1/empresas/5").status_code == 409
    assert not fake.executed
    as_user("partner", tenant=9)
    c, _ = _client([])
    assert c.delete("/api/v1/empresas/5").status_code == 403


def test_eliminar_empresa_vacia():
    as_user("superadmin")
    c, fake = _client([("FROM empresas e WHERE", {"id": 5, "nombre": "AFS", "partner_id": 9, "contacto_email": None}),
                       ("FROM sedes WHERE empresa_id", {"n": 0})])
    assert c.delete("/api/v1/empresas/5").status_code == 200
    assert any("DELETE FROM empresas" in s for s, _ in fake.executed)
