"""PATCH /api/v1/sedes/{id}: editar sede (spec 001, M8)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers import empresas as empresas_router
from app.routers import sedes as sedes_router

from test_users_and_baja import ScriptedDb, as_user


@pytest.fixture(autouse=True)
def _clean():
    yield
    app.dependency_overrides.clear()


LOGS = []


def _client(rules):
    fake = ScriptedDb(rules)
    LOGS.clear()
    for mod in (empresas_router, sedes_router):
        mod.db = fake
        mod.audit = type("A", (), {"log": staticmethod(lambda *a, **k: LOGS.append(a))})
    return TestClient(app), fake


SEDE_DB = ("FROM sedes s WHERE s.id", {"id": 3, "nombre": "Centro", "ciudad": "Cali", "empresa_id": 5, "partner_id": 9})
FINAL = ("FROM sedes s JOIN empresas e", {"id": 3, "nombre": "Centro", "ciudad": "Bogotá", "empresa_id": 5,
                                          "empresa": "AFS", "direccion": None, "contacto_email": None,
                                          "estado": "activa", "partner_id": 9})
EMPRESA6 = ("FROM empresas e WHERE", {"id": 6, "nombre": "Otra", "partner_id": 9, "contacto_email": None})


def test_editar_campos_basicos():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])
    r = c.patch("/api/v1/sedes/3", json={"ciudad": "Bogotá"})
    assert r.status_code == 200 and r.json()["empresa"] == "AFS"
    assert len(fake.executed) == 1 and LOGS[0][1] == "sede_editada"


def test_update_usa_parametros():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])
    c.patch("/api/v1/sedes/3", json={"ciudad": "x'; DROP TABLE sedes;--"})
    sql, params = fake.executed[0]
    assert "DROP" not in sql and "ciudad = %s" in sql
    assert params == ("x'; DROP TABLE sedes;--", 3)


def test_sede_inexistente_o_ajena_404():
    as_user("partner", tenant=1)
    c, fake = _client([])
    assert c.patch("/api/v1/sedes/3", json={"ciudad": "Bogotá"}).status_code == 404
    assert not fake.executed


def test_cambio_de_empresa_valido():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, EMPRESA6, FINAL])
    assert c.patch("/api/v1/sedes/3", json={"empresa_id": 6}).status_code == 200
    sql, params = fake.executed[0]
    assert "empresa_id = %s" in sql and params == (6, 3)


def test_empresa_destino_de_otro_aliado_422():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, ("FROM empresas e WHERE", {"id": 6, "nombre": "X", "partner_id": 10, "contacto_email": None})])
    r = c.patch("/api/v1/sedes/3", json={"empresa_id": 6})
    assert r.status_code == 422 and r.json()["detail"] == "La empresa pertenece a otro aliado."
    assert not fake.executed


def test_empresa_destino_no_visible_404():
    as_user("partner", tenant=9)
    c, fake = _client([SEDE_DB])
    assert c.patch("/api/v1/sedes/3", json={"empresa_id": 6}).status_code == 404
    assert not fake.executed


def test_nombre_duplicado_en_empresa_destino_409():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, EMPRESA6, ("LOWER(nombre) = LOWER(%s)", {"x": 1})])
    assert c.patch("/api/v1/sedes/3", json={"empresa_id": 6}).status_code == 409
    assert not fake.executed


def test_body_vacio_200_sin_update():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])
    assert c.patch("/api/v1/sedes/3", json={}).status_code == 200
    assert not fake.executed


@pytest.mark.parametrize("rol", ["cliente", "tecnico"])
def test_roles_sin_permiso_403(rol):
    as_user(rol, tenant=9)
    c, fake = _client([SEDE_DB, FINAL])
    assert c.patch("/api/v1/sedes/3", json={"ciudad": "Bogotá"}).status_code == 403
    assert not fake.executed
