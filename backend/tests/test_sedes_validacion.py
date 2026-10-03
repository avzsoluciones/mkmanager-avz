"""Validación y endurecimiento de sedes (crear/editar/listar)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app

from test_sedes_edit import EMPRESA6, FINAL, SEDE_DB, LOGS, _client
from test_users_and_baja import as_user

EMPRESA5 = ("FROM empresas e WHERE", {"id": 5, "nombre": "AFS", "partner_id": 9, "contacto_email": None})
INSERTED = ("INSERT INTO sedes", {"id": 8, "nombre": "Norte", "ciudad": "Cali", "empresa_id": 5, "direccion": None,
                                   "contacto_nombre": None, "contacto_telefono": None, "contacto_email": None,
                                   "estado": "activa"})


class PgError(Exception):
    def __init__(self, pgcode):
        super().__init__("db")
        self.pgcode = pgcode


def _post(c, **over):
    body = {"empresa_id": 5, "nombre": "Norte", "ciudad": "Cali"}
    body.update(over)
    return c.post("/api/v1/sedes", json=body)


# ---------- GET ----------
def test_get_incluye_contactos():
    as_user("superadmin")
    c, fake = _client([("FROM sedes s JOIN empresas e", [{"id": 1}])])
    fake.sqls = []
    orig = fake.fetch_all
    fake.fetch_all = lambda sql, params=(): (fake.sqls.append(sql), orig(sql, params))[1]
    assert c.get("/api/v1/sedes").status_code == 200
    assert "s.contacto_nombre" in fake.sqls[0] and "s.contacto_telefono" in fake.sqls[0]
    assert "s.contacto_email" in fake.sqls[0]


def test_respuesta_de_editar_incluye_contactos():
    as_user("superadmin")
    final = ("FROM sedes s JOIN empresas e", {**FINAL[1], "contacto_nombre": "Ana", "contacto_telefono": "300"})
    c, fake = _client([SEDE_DB, final])
    j = c.patch("/api/v1/sedes/3", json={"ciudad": "Bogotá"}).json()
    assert j["contacto_nombre"] == "Ana" and j["contacto_telefono"] == "300"


def test_respuesta_de_crear_incluye_contactos():
    as_user("superadmin")
    c, fake = _client([EMPRESA5, INSERTED])
    j = _post(c).json()
    assert {"contacto_nombre", "contacto_telefono", "contacto_email"} <= set(j)


# ---------- largos máximos ----------
@pytest.mark.parametrize("campo,max_", [("direccion", 255), ("contacto_nombre", 150),
                                         ("contacto_telefono", 50)])
def test_largo_maximo_patch_y_post(campo, max_):
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL, EMPRESA5, INSERTED])
    assert c.patch("/api/v1/sedes/3", json={campo: "a" * (max_ + 1)}).status_code == 422
    assert _post(c, **{campo: "a" * (max_ + 1)}).status_code == 422
    assert c.patch("/api/v1/sedes/3", json={campo: "a" * max_}).status_code == 200
    assert not [w for w in fake.executed if "INSERT" in w[0]]


def test_largo_maximo_email():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL, EMPRESA5, INSERTED])
    largo = "a" * 140 + "@x.com"                         # 146 -> ok
    exceso = "a" * 145 + "@x.com"                        # 151 -> excede
    assert c.patch("/api/v1/sedes/3", json={"contacto_email": exceso}).status_code == 422
    assert _post(c, contacto_email=exceso).status_code == 422
    assert c.patch("/api/v1/sedes/3", json={"contacto_email": largo}).status_code == 200


# ---------- null explícito ----------
@pytest.mark.parametrize("campo", ["nombre", "ciudad", "empresa_id"])
def test_null_explicito_422(campo):
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])
    r = c.patch("/api/v1/sedes/3", json={campo: None})
    assert r.status_code == 422 and r.json()["detail"] == f"El campo {campo} no puede ser nulo."
    assert not fake.executed


def test_null_en_opcional_vacia_columna():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])
    assert c.patch("/api/v1/sedes/3", json={"direccion": None}).status_code == 200
    sql, params = fake.executed[0]
    assert "direccion = %s" in sql and params[0] is None


# ---------- vacío => NULL ----------
@pytest.mark.parametrize("campo", ["direccion", "contacto_nombre", "contacto_telefono", "contacto_email"])
@pytest.mark.parametrize("valor", ["", "   "])
def test_vacio_guarda_null_en_patch(campo, valor):
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])
    assert c.patch("/api/v1/sedes/3", json={campo: valor}).status_code == 200
    assert fake.executed[0][1][0] is None


def test_vacio_guarda_null_en_post():
    as_user("superadmin")
    c, fake = _client([EMPRESA5, INSERTED])
    captured = []
    orig = fake.fetch_one
    fake.fetch_one = lambda sql, params=(): (captured.append((sql, params)), orig(sql, params))[1]
    assert _post(c, direccion="  ", contacto_nombre="", contacto_telefono=" ", contacto_email="").status_code == 201
    params = [p for s, p in captured if "INSERT" in s][0]
    # (nombre, ciudad, empresa_id, direccion, partner_id, c_nombre, c_tel, c_email)
    assert params[3] is None and params[5] is None and params[6] is None and params[7] is None


def test_nombre_solo_espacios_422_y_recorte_antes_de_largo():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])
    assert c.patch("/api/v1/sedes/3", json={"nombre": "     "}).status_code == 422
    assert c.patch("/api/v1/sedes/3", json={"ciudad": "   "}).status_code == 422
    assert c.patch("/api/v1/sedes/3", json={"nombre": " " + "a" * 150}).status_code == 200
    assert fake.executed[0][1][0] == "a" * 150


# ---------- correo ----------
@pytest.mark.parametrize("mail", ["sin-arroba", "a@b", "a b@c.com", "a@b.com,c@d.com", "<a>@b.com"])
def test_correo_invalido_422(mail):
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL, EMPRESA5, INSERTED])
    assert c.patch("/api/v1/sedes/3", json={"contacto_email": mail}).status_code == 422
    assert _post(c, contacto_email=mail).status_code == 422
    assert not fake.executed


def test_correo_valido():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])
    assert c.patch("/api/v1/sedes/3", json={"contacto_email": "ana@ejemplo.com"}).status_code == 200


# ---------- caracteres no permitidos ----------
@pytest.mark.parametrize("ch", [" ", "\u0085", "​", "‮", "\n", "\t", "\x00", " "])
@pytest.mark.parametrize("campo", ["nombre", "ciudad", "direccion", "contacto_nombre", "contacto_telefono"])
def test_caracteres_prohibidos_422(campo, ch):
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL, EMPRESA5, INSERTED])
    assert c.patch("/api/v1/sedes/3", json={campo: f"ab{ch}cd"}).status_code == 422
    assert c.patch("/api/v1/sedes/3", json={campo: f"abcd{ch}"}).status_code == 422   # tampoco al borde
    assert _post(c, **{campo: f"ab{ch}cd"}).status_code == 422
    assert not fake.executed


def test_tildes_y_enie_permitidas():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])
    assert c.patch("/api/v1/sedes/3", json={"nombre": "Sede Peña Ñandú Ávila"}).status_code == 200


# ---------- empresa_id estricto ----------
@pytest.mark.parametrize("val", [True, "6", 6.5, 0, -1])
def test_empresa_id_estricto_422(val):
    as_user("superadmin")
    c, fake = _client([SEDE_DB, EMPRESA6, FINAL, EMPRESA5, INSERTED])
    assert c.patch("/api/v1/sedes/3", json={"empresa_id": val}).status_code == 422
    assert _post(c, empresa_id=val).status_code == 422
    assert not fake.executed


# ---------- aislamiento en el UPDATE ----------
def test_update_repite_filtro_de_aislamiento():
    as_user("partner", tenant=9)
    c, fake = _client([SEDE_DB, FINAL])
    assert c.patch("/api/v1/sedes/3", json={"ciudad": "Bogotá"}).status_code == 200
    sql, params = fake.executed[0]
    assert "s.id = %s AND s.partner_id = %s" in sql
    assert params == ("Bogotá", 3, 9)


# ---------- auditoría ----------
def test_auditoria_con_empresa_anterior_al_mover():
    as_user("superadmin")
    sede = ("FROM sedes s WHERE s.id", {**SEDE_DB[1], "empresa_nombre": "AFS"})
    final = ("FROM sedes s JOIN empresas e", {**FINAL[1], "empresa_id": 6, "empresa": "Otra"})
    c, fake = _client([sede, EMPRESA6, final])
    assert c.patch("/api/v1/sedes/3", json={"empresa_id": 6}).status_code == 200
    assert "empresa_anterior=AFS empresa=Otra" in LOGS[0][2]


def test_auditoria_sin_mover_no_incluye_anterior():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])
    c.patch("/api/v1/sedes/3", json={"ciudad": "Bogotá"})
    assert "empresa=AFS" in LOGS[0][2] and "empresa_anterior" not in LOGS[0][2]


# ---------- concurrencia (unicidad) ----------
def test_unicidad_en_post_409():
    as_user("superadmin")
    c, fake = _client([EMPRESA5])

    def boom(sql, params=()):
        if "INSERT" in sql:
            raise PgError("23505")
        return EMPRESA5[1] if "FROM empresas e" in sql else None
    fake.fetch_one = boom
    r = _post(c)
    assert r.status_code == 409 and r.json()["detail"] == "Ya existe una sede con ese nombre en esa empresa."


def test_unicidad_en_patch_409():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])

    def boom(sql, params=()):
        raise PgError("23505")
    fake.execute = boom
    r = c.patch("/api/v1/sedes/3", json={"ciudad": "Bogotá"})
    assert r.status_code == 409 and r.json()["detail"] == "Ya existe una sede con ese nombre en esa empresa."


def test_otra_excepcion_no_se_captura():
    as_user("superadmin")
    c, fake = _client([SEDE_DB, FINAL])

    def boom(sql, params=()):
        raise PgError("40001")
    fake.execute = boom
    with pytest.raises(PgError):
        TestClient(app, raise_server_exceptions=True).patch("/api/v1/sedes/3", json={"ciudad": "Bogotá"})
