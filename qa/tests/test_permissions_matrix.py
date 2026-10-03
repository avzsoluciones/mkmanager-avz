"""Matriz de permisos de la spec 3.2 (rol x capacidad) + rol `tecnico` (decisión 2 de 3.2). Cubre lo implementado y, como xfail
dinámico, lo que aún no tenga ruta. IDs: PM-<capacidad>-<rol>.

Criterio: deny = 403 exacto; allow = la puerta de rol deja pasar (ni 401 ni 403) y no hay 5xx (un 404/409/422 por recurso o cuerpo
de prueba cuenta como 'pasó la puerta'; el alcance sobre datos se prueba aparte en PM02/PM03 y test_authz.py)."""
import re

import pytest

from conftest import iter_routes, route_exists
from app.main import app as _app

TPL = {"params": {"dns": ["1.1.1.1"]}}
PROV = {"sede_id": 10, "nombre": "MK QA", "wan": {"interface": "ether1", "mode": "dhcp"},
        "lan": {"interface": "br", "cidr": "192.168.10.0/24"}}
R = ("superadmin", "soporte", "partner", "tecnico", "cliente")
ALL_ = dict(zip(R, "AAAAA"))
STAFF_ = dict(zip(R, "AADDD"))
SEDE_ADMIN = dict(zip(R, "AAADD"))
FIELD_ = dict(zip(R, "AAAAD"))
SUPER_ONLY = dict(zip(R, "ADDDD"))
# A = allow, D = deny

CAPS = {
    # ---- existentes en el backend al momento del snapshot
    "ver_sedes":          ("GET", "/api/v1/sedes", None, ALL_),
    "ver_equipos":        ("GET", "/api/v1/devices", None, ALL_),
    "ver_equipo":         ("GET", "/api/v1/devices/100", None, ALL_),
    "ver_panel":          ("GET", "/api/v1/dashboard", None, ALL_),
    "ver_estado":         ("GET", "/api/v1/devices/100/status", None, ALL_),
    "ver_historial":      ("GET", "/api/v1/devices/100/history", None, ALL_),
    "crear_sede":         ("POST", "/api/v1/sedes", {"nombre": "QA Matriz", "ciudad": "Cali"}, SEDE_ADMIN),
    "aprovisionar":       ("POST", "/api/v1/provision", PROV, FIELD_),
    "vista_previa":       ("POST", "/api/v1/provision/preview", PROV, FIELD_),
    "ver_script":         ("GET", "/api/v1/provision/100/script", None, FIELD_),
    "cambiar_clave":      ("POST", "/api/v1/auth/change-password", {"current_password": "x", "new_password": "y"}, ALL_),
    "baja_equipo":        ("DELETE", "/api/v1/devices/100?confirmar=true", None, dict(zip(R, "ADADD"))),
    "plantillas_listar":  ("GET", "/api/v1/templates", None, FIELD_),
    "plantilla_vista":    ("POST", "/api/v1/devices/100/templates/dns-ntp/preview", TPL, FIELD_),
    "plantilla_bajo":     ("POST", "/api/v1/devices/100/templates/dns-ntp/apply", TPL, FIELD_),
    "plantilla_alto":     ("POST", "/api/v1/devices/100/templates/firewall-seguro/apply", {"params": {}, "revertir_minutos": 5}, FIELD_),
    "aplicaciones":       ("GET", "/api/v1/devices/100/applications", None, FIELD_),
    "confirmar_app":      ("POST", "/api/v1/applications/1/confirm", {}, FIELD_),
    "revertir_app":       ("POST", "/api/v1/applications/1/revert", {}, FIELD_),
    "vpn_crear":          ("POST", "/api/v1/vpn/users", {"nombre": "Laura Gomez", "sede_id": 10}, FIELD_),
    "vpn_listar":         ("GET", "/api/v1/vpn/users", None, FIELD_),
    "vpn_revocar":        ("DELETE", "/api/v1/vpn/users/7001", None, FIELD_),
    "vpn_conf":           ("GET", "/api/v1/vpn/users/7001/conf", None, FIELD_),
    "vpn_qr":             ("GET", "/api/v1/vpn/users/7001/qr.png", None, FIELD_),
    "usuarios_listar":    ("GET", "/api/v1/users", None, dict(zip(R, "ADADD"))),
    "usuarios_crear":     ("POST", "/api/v1/users", {"email": "n@x.com", "rol": "cliente", "nombre_completo": "Nuevo Cliente"}, dict(zip(R, "ADADD"))),
    "usuarios_activar":   ("POST", "/api/v1/users/6/active", {"activo": False}, dict(zip(R, "ADADD"))),
    "usuarios_reset":     ("POST", "/api/v1/users/6/reset-password", None, dict(zip(R, "ADADD"))),
    "ver_auditoria":      ("GET", "/api/v1/audit", None, FIELD_),
    # ---- definidos en la spec 3.2 / PERMISSIONS pero SIN endpoint todavía
    "editar_sede":        ("PATCH", "/api/v1/sedes/10", {"ciudad": "Bogota"}, SEDE_ADMIN),
    "eliminar_sede":      ("DELETE", "/api/v1/sedes/10", None, SUPER_ONLY),
    "catalogo_gestionar": ("POST", "/api/v1/templates", {"id": "x", "nombre": "x"}, SUPER_ONLY),
    "ajustes_hub_ver":    ("GET", "/api/v1/hub", None, STAFF_),
    "ajustes_hub_cambiar": ("PUT", "/api/v1/hub", {}, SUPER_ONLY),
    "huerfanos_limpiar":  ("DELETE", "/api/v1/hub/orphans", None, SUPER_ONLY),
}
UID = {"superadmin": 1, "soporte": 2, "partner": 3, "tecnico": 7, "cliente": 5}


def _exists(cap):
    m, p, _b, _e = CAPS[cap]
    return route_exists(_app, m, p.split("?")[0])


def _cases():
    out = []
    for cap, (_m, _p, _b, exp) in CAPS.items():
        marks = [] if _exists(cap) else [pytest.mark.xfail(strict=False, reason="pendiente de implementar (sin endpoint)")]
        for role, want in exp.items():
            out.append(pytest.param(cap, role, want, marks=marks, id=f"{cap}-{role}"))
    return out


def _call(world, cap, uid):
    m, p, b, _ = CAPS[cap]
    if not route_exists(world.app, m, p.split("?")[0]):
        pytest.fail(f"ruta {m} {p} pendiente de implementar")
    return world.client.request(m, p, json=b, headers=world.auth(uid))


@pytest.mark.parametrize("cap,role,want", _cases())
def test_PM01_role_x_capability(world, cap, role, want):
    r = _call(world, cap, UID[role])
    if want == "D":
        assert r.status_code == 403, f"{cap}/{role}: esperado 403, obtuvo {r.status_code} {r.text[:150]}"
    else:
        assert r.status_code not in (401, 403) and r.status_code < 500, f"{cap}/{role}: {r.status_code} {r.text[:150]}"
    assert r.headers["content-type"].startswith(("application/json", "text/plain", "image/png"))


@pytest.mark.parametrize("cap", [c for c, v in CAPS.items() if v[1].startswith("/api/v1")])
def test_PM02_unauthenticated_is_401_everywhere(world, cap):
    m, p, b, _ = CAPS[cap]
    if not route_exists(world.app, m, p.split("?")[0]):
        pytest.xfail("pendiente de implementar")
    assert world.client.request(m, p, json=b).status_code == 401


# ------------------------------------------------------------------ aislamiento sobre recursos concretos
OWNED = ["ver_equipo", "ver_script", "ver_estado", "ver_historial", "plantilla_vista", "plantilla_bajo", "plantilla_alto",
         "aplicaciones", "vpn_revocar", "vpn_conf", "vpn_qr"]


@pytest.mark.parametrize("uid_name", ["partner_b", "tecnico_b", "cliente_b"])
@pytest.mark.parametrize("cap", OWNED + ["baja_equipo"])
def test_PM03_other_tenant_gets_404_or_403_and_no_side_effects(world, cap, uid_name):
    """A8: ni ver ni modificar recursos de otro aliado. Para el aliado B sobre recursos de A: 404 (no revelar existencia)."""
    from conftest import ROLES
    m, p, b, _ = CAPS[cap]
    if not route_exists(world.app, m, p.split("?")[0]):
        pytest.xfail("pendiente de implementar")
    if cap.startswith("vpn_"):                       # el peer 7001 pertenece a A; el de B es 7002
        pass
    r = world.client.request(m, p, json=b, headers=world.auth(ROLES[uid_name]))
    allowed_for_role = CAPS[cap][3][{"partner_b": "partner", "tecnico_b": "tecnico", "cliente_b": "cliente"}[uid_name]] == "A"
    assert r.status_code == (404 if allowed_for_role else 403), f"{cap}/{uid_name}: {r.status_code} {r.text[:120]}"
    assert world.api.added == [] and world.api.removed == [] and world.api.routes_removed == []
    assert not [a for a in world.db.audit if a["accion"] in ("equipo_baja", "vpn_revocada", "script_consultado", "vpn_config_consultada")]


def test_PM04_every_registered_api_route_has_a_row_in_the_matrix():
    """La spec 3.2 pide que una prueba verifique que matriz y endpoints coinciden."""
    norm = lambda s: re.sub(r"/\d+(?=/|$)", "/{p}", re.sub(r"\{[^}]+\}", "{p}", s.split("?")[0]))  # noqa: E731
    covered = {(m, norm(p)) for m, p, *_ in CAPS.values()}
    public = {("GET", "/api/v1/health"), ("POST", "/api/v1/auth/login"), ("GET", "/api/v1/auth/me")}
    missing = [(m, p) for m, p in iter_routes(_app) if p.startswith("/api/v1") and (m, norm(p)) not in covered and (m, p) not in public]
    assert missing == [], f"rutas sin fila en la matriz de permisos: {missing}"


def test_PM05_permissions_module_matches_spec_matrix():
    """Cruza app/permissions.py con la matriz esperada de este archivo (fuente: spec 3.2 + decisión 2)."""
    from app import permissions as perm
    expected = {
        "panel.ver": R, "sede.ver": R, "equipo.ver": R, "equipo.estado": R,
        "sede.crear": ("superadmin", "soporte", "partner"), "sede.editar": ("superadmin", "soporte", "partner"),
        "sede.eliminar": ("superadmin",),
        "equipo.aprovisionar": ("superadmin", "soporte", "partner", "tecnico"), "equipo.script": ("superadmin", "soporte", "partner", "tecnico"),
        "equipo.baja": ("superadmin", "partner"),
        "plantilla.bajo_medio": ("superadmin", "soporte", "partner", "tecnico"), "plantilla.alto": ("superadmin", "soporte", "partner", "tecnico"),
        "aplicacion.confirmar": ("superadmin", "soporte", "partner", "tecnico"), "vpn.gestionar": ("superadmin", "soporte", "partner", "tecnico"),
        "usuario.crear": ("superadmin", "partner"), "usuario.gestionar": ("superadmin", "partner"),
        "auditoria.ver": ("superadmin", "soporte", "partner", "tecnico"), "global.gestionar": ("superadmin",), "hub.ver": ("superadmin", "soporte"),
    }
    assert set(expected) == set(perm.PERMISSIONS), set(expected) ^ set(perm.PERMISSIONS)
    for cap, roles in expected.items():
        assert set(perm.PERMISSIONS[cap]) == set(roles), f"{cap}: backend {perm.PERMISSIONS[cap]} vs spec {roles}"
    assert "cliente" not in {r for roles in perm.PERMISSIONS.values() for r in roles if roles != R}, "cliente solo lectura"


def test_PM06_cliente_never_in_any_write_or_sensitive_capability():
    from app import permissions as perm
    sensitive = [c for c in perm.PERMISSIONS if c.split(".")[0] in ("equipo", "plantilla", "aplicacion", "vpn", "usuario", "auditoria", "global", "hub")
                 and c not in ("equipo.ver", "equipo.estado")]
    for cap in sensitive:
        assert "cliente" not in perm.PERMISSIONS[cap], cap


def test_PM07_require_rejects_unknown_capability_at_import_time():
    from app import permissions as perm
    with pytest.raises(KeyError):
        perm.require("no.existe")


def test_PM08_unknown_role_in_db_has_no_access(world):
    """Un rol inesperado en BD (p. ej. tras una migración) no debe heredar acceso: scope FALSE y 403 en permisos."""
    world.db.usuarios[3]["rol"] = "invitado"
    h = world.auth(3)
    assert world.client.get("/api/v1/sedes", headers=h).status_code == 403
    from app.security import sede_scope
    assert sede_scope({"role": "invitado", "id": 3})[0] == "FALSE"


@pytest.mark.parametrize("cap", [c for c in CAPS if CAPS[c][3]["cliente"] == "D"])
def test_PM09_denied_cliente_response_does_not_leak_resource_data(world, cap):
    m, p, b, _ = CAPS[cap]
    if not route_exists(world.app, m, p.split("?")[0]):
        pytest.xfail("pendiente de implementar")
    r = world.client.request(m, p, json=b, headers=world.auth(5))
    assert r.status_code == 403 and r.json() == {"detail": "Acceso denegado."}
