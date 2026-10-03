import asyncio
import importlib
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db
from .config import get_settings
from .routers import auth, devices, empresas, monitor, provision, sedes, templates, users, vpn

log = logging.getLogger("avz")
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    db.init_pool()
    applied = db.run_migrations(MIGRATIONS_DIR)
    if applied:
        log.warning("Migraciones aplicadas: %s", applied)
    tasks = []
    for name in ("reverter", "monitor"):                 # tareas periódicas: app/tasks/<name>.py con `async def run()`
        mod = importlib.import_module(f"app.tasks.{name}")
        tasks.append(asyncio.create_task(mod.run(), name=f"avz-{name}"))
    yield
    for t in tasks:
        t.cancel()
    db.close_pool()


app = FastAPI(title="AVZCloud Central Manager API", version="3.0.0", lifespan=lifespan)

_origins = os.environ.get("CORS_ORIGINS", "")
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o for o in _origins.split(",") if o],
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """Toda falla no prevista responde JSON, nunca texto plano."""
    log.exception("Error no controlado en %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor."})


@app.get("/api/v1/health")
def health():
    try:
        db.fetch_one("SELECT 1 AS ok")
        return {"status": "ok", "db": "ok"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "error"})


app.include_router(auth.router)
app.include_router(empresas.router)
app.include_router(sedes.router)
app.include_router(devices.router)
app.include_router(provision.router)
app.include_router(users.router)
app.include_router(templates.router)
app.include_router(vpn.router)
app.include_router(monitor.router)
