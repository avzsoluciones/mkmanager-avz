from fastapi.testclient import TestClient

from app.main import app


def test_me_without_token_is_json_401():
    c = TestClient(app)
    r = c.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")


def test_protected_routes_require_auth():
    c = TestClient(app)
    for path in ("/api/v1/sedes", "/api/v1/devices", "/api/v1/devices/1"):
        assert c.get(path).status_code == 401


def test_unhandled_error_returns_json_not_text():
    @app.get("/__boom")
    def boom():
        raise RuntimeError("fallo interno con datos sensibles")

    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/__boom")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    assert "sensibles" not in r.text
