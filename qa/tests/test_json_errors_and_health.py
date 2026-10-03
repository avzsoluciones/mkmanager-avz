"""A1: errores siempre en JSON. Health, rutas inexistentes, métodos, cuerpos raros, OpenAPI. IDs: JS-*."""
import re

import pytest

from conftest import iter_routes


def is_json(r):
    return r.headers.get("content-type", "").startswith("application/json")


def test_JS01_A1_unknown_route_404_json(world):
    r = world.client.get("/api/v1/no-existe")
    assert r.status_code == 404 and is_json(r)


@pytest.mark.parametrize("method,path", [("DELETE", "/api/v1/sedes"), ("PUT", "/api/v1/devices"), ("GET", "/api/v1/auth/login"),
                                         ("PATCH", "/api/v1/provision")])
def test_JS02_method_not_allowed_json(world, method, path):
    r = world.client.request(method, path, headers=world.auth(1))
    assert r.status_code in (404, 405) and is_json(r)


def test_JS03_every_route_without_token_returns_json_401_or_422(world):
    skip = {"/api/v1/health", "/api/v1/auth/login", "/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
    for m, path in iter_routes(world.app):
        if path in skip or not path.startswith("/api/v1"):
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        r = world.client.request(m, url, json={})
        assert r.status_code == 401 and is_json(r), (m, path, r.status_code)


def test_JS04_health_ok_and_degraded_are_json(world):
    r = world.client.get("/api/v1/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "db": "ok"}
    world.db.fail_health = True
    r = world.client.get("/api/v1/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded" and "db down" not in r.text


def test_JS05_health_needs_no_auth_and_leaks_no_config(world):
    t = world.client.get("/api/v1/health").text
    for leak in ("HUB", "SECRET", "VAULT", "password", "postgres"):
        assert leak.lower() not in t.lower()


def test_JS06_unhandled_exception_in_any_layer_is_json_without_details(world):
    from fastapi import APIRouter
    r = APIRouter()

    @r.get("/api/v1/__qa_boom")
    def boom():
        raise ValueError("detalle sensible con 10.10.10.3 y clave")

    world.app.include_router(r)
    try:
        resp = world.client.get("/api/v1/__qa_boom")
    finally:
        world.app.router.routes[:] = [x for x in world.app.router.routes if getattr(x, "path", "") != "/api/v1/__qa_boom"]
    assert resp.status_code == 500 and is_json(resp) and resp.json() == {"detail": "Error interno del servidor."}


def test_JS07_oversized_and_binary_bodies_do_not_500(world):
    r = world.client.post("/api/v1/auth/login", content=b"\xff\xfe\x00" * 1000, headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and is_json(r)
    r = world.client.post("/api/v1/auth/login", json={"email": "a" * 200000, "password": "b"})
    assert r.status_code in (401, 413, 422) and is_json(r)


def test_JS08_wrong_content_type_is_json_error(world):
    r = world.client.post("/api/v1/auth/login", content="email=a&password=b", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 422 and is_json(r)


def test_JS09_validation_errors_are_serializable_for_all_validators(world):
    """Los validadores lanzan ValueError dentro de pydantic: la respuesta 422 debe poder serializarse (no 500)."""
    cases = [{"nombre": "x;y"}, {"wan": {"interface": "e t", "mode": "dhcp"}}, {"wan": {"interface": "e", "mode": "static"}},
             {"lan": {"interface": "b", "cidr": "192.168.1.0/24", "dns": ["abc"]}}, {"tunnel_ip": "10.0.0.1"}]
    base = {"sede_id": 10, "nombre": "MK", "wan": {"interface": "ether1", "mode": "dhcp"}, "lan": {"interface": "br", "cidr": "192.168.10.0/24"}}
    for c in cases:
        r = world.client.post("/api/v1/provision/preview", json={**base, **c}, headers=world.auth(1))
        assert r.status_code == 422 and is_json(r), (c, r.status_code, r.text[:200])


def test_JS10_cors_not_open_by_default(world):
    r = world.client.options("/api/v1/sedes", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_JS11_no_stack_trace_or_path_in_any_error_body(world):
    for r in (world.client.get("/api/v1/zzz"), world.client.post("/api/v1/auth/login", json={}), world.client.get("/api/v1/sedes")):
        assert "Traceback" not in r.text and "site-packages" not in r.text and "\\app\\" not in r.text and "/app/" not in r.text


def test_JS12_openapi_and_docs_exposed_by_app_but_must_be_blocked_at_proxy(world):
    """Informativo: la app sirve /docs y /openapi.json. deploy/CAMBIOS-srv2.md exige bloquearlos en OpenResty.
    Esta prueba verifica que lo que se expone no contiene nombres de proveedores."""
    spec = world.client.get("/openapi.json").text
    for bad in ("3HC", "Yotta", "Wasabi", "Polaris", "Acronis", "SECRET_KEY", "VAULT_KEY"):
        assert bad.lower() not in spec.lower()


