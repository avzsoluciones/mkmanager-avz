"""Plantillas de servicio con reversión automática (M4/M5).

Contrato consumido por la interfaz (ver frontend-new/js/mock.js):
  GET  /api/v1/templates
  POST /api/v1/devices/{id}/templates/{template_id}/preview   {params}                      -> {script, avisos}
  POST /api/v1/devices/{id}/templates/{template_id}/apply     {params, revertir_minutos?}   -> {aplicacion_id, estado, revertir_en}
  POST /api/v1/applications/{id}/confirm
  POST /api/v1/applications/{id}/revert                       (adelanta la reversión de una aplicación pendiente)
  GET  /api/v1/devices/{id}/applications
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictInt

from .. import audit
from ..permissions import allowed, require
from ..security import client_ip, sede_scope
from ..services import edge
from ..services import templates as tpls
from ..services.ops import PREVIEW_TAG, OpError, assert_safe, render_cli, stamp

router = APIRouter(prefix="/api/v1", tags=["templates"])

_base = require("plantilla.bajo_medio")
_confirm = require("aplicacion.confirmar")


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    params: Dict[str, Any] = {}


class ApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    params: Dict[str, Any] = {}
    revertir_minutos: Optional[StrictInt] = None


def _http422(errors: List[tuple]) -> HTTPException:
    general = [m for f, m in errors if f is None]
    if general and len(general) == len(errors):
        return HTTPException(status_code=422, detail=" ".join(general))
    detail = []
    for f, m in errors:
        loc = ["body", "params"] if f is None else (["body", f] if f == "revertir_minutos" else ["body", "params", f])
        detail.append({"loc": loc, "msg": m, "type": "value_error"})
    return HTTPException(status_code=422, detail=detail)


def _template(user: dict, template_id: str) -> tpls.Template:
    tpl = tpls.CATALOG.get(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada.")
    if not allowed(user["role"], tpl.capability):
        raise HTTPException(status_code=403, detail="Acceso denegado.")
    return tpl


def _device(user: dict, device_id: int) -> dict:
    where, params = sede_scope(user)
    dev = edge.store.get_device(device_id, where, params)
    if not dev:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado.")
    return dev


def _managed(dev: dict) -> dict:
    ok = (str(dev.get("marca") or "").lower() == "mikrotik" and dev.get("api_user")
          and dev.get("api_password_enc") and dev.get("tunnel_ip"))
    if not ok:
        raise HTTPException(status_code=409, detail="Este equipo no admite servicios: requiere un MikroTik con gestión completa.")
    return dev


@router.get("/templates")
def list_templates(user: dict = Depends(_base)):
    return [t.public() for t in tpls.CATALOG.values()]


@router.post("/devices/{device_id}/templates/{template_id}/preview")
def preview(device_id: int, template_id: str, body: PreviewBody, user: dict = Depends(_base)):
    """Vista previa: valida y renderiza. No toca el router ni escribe en la BD."""
    tpl = _template(user, template_id)
    dev = _managed(_device(user, device_id))
    try:
        _params, ops, avisos = tpls.plan(tpl, body.params, tpls.DeviceCtx.from_row(dev))
        stamped = stamp(ops, PREVIEW_TAG)
        assert_safe(stamped)
    except tpls.TemplateError as e:
        raise _http422(e.errors)
    except OpError as e:
        raise HTTPException(status_code=422, detail=f"La plantilla generó una operación no permitida: {e}")
    return {"script": f"# Servicio: {tpl.nombre}\n{render_cli(stamped, PREVIEW_TAG)}", "avisos": avisos}


@router.post("/devices/{device_id}/templates/{template_id}/apply")
def apply(device_id: int, template_id: str, body: ApplyBody, request: Request, user: dict = Depends(_base)):
    tpl = _template(user, template_id)
    dev = _managed(_device(user, device_id))
    ctx = tpls.DeviceCtx.from_row(dev)
    errors: List[tuple] = []
    params, ops, minutes = {}, [], None
    try:
        params, ops, _avisos = tpls.plan(tpl, body.params, ctx)
    except tpls.TemplateError as e:
        errors += e.errors
    try:
        minutes = tpls.reversal_minutes(user["role"], tpl, body.revertir_minutos)
    except tpls.TemplateError as e:
        errors += e.errors
    if errors:
        raise _http422(errors)

    ip = client_ip(request)
    try:
        app_id = edge.store.begin(dev["id"], tpl.id, params, edge.params_hash(params), user["email"])
    except edge.Conflict as e:
        raise HTTPException(status_code=409, detail=str(e))

    try:
        edge.run_apply(edge.store, dev, app_id, ops)
    except edge.ApplyFailed as e:
        if e.rolled_back:
            edge.store.mark_error(app_id, str(e))
            extra = " Se deshizo lo que se alcanzó a cambiar."
        else:
            edge.store.schedule_revert_now(app_id, f"Aplicación fallida y reversión inmediata incompleta: {e}")
            extra = " No se pudo deshacer todo al instante; el sistema lo reintentará solo."
        audit.log(user["email"], "plantilla_error", f"device={dev['id']} plantilla={tpl.id} app={app_id} {str(e)[:300]}", ip)
        raise HTTPException(status_code=409, detail=f"No se pudo aplicar el cambio: {e}{extra}")
    except edge.EdgeError as e:                       # no se alcanzó a cambiar nada (conexión, credenciales, lectura)
        edge.store.mark_error(app_id, str(e))
        audit.log(user["email"], "plantilla_error", f"device={dev['id']} plantilla={tpl.id} app={app_id} {str(e)[:300]}", ip)
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        edge.store.mark_error(app_id, "Error interno al aplicar.")
        raise

    revertir_en = edge.store.finish_applied(app_id, minutes)
    audit.log(user["email"], "plantilla_aplicada",
              f"device={dev['id']} plantilla={tpl.id} app={app_id} revertir_min={minutes} params={params}", ip)
    return {"aplicacion_id": app_id, "estado": "aplicada", "revertir_en": edge.iso(revertir_en)}


def _application(user: dict, app_id: int) -> dict:
    where, params = sede_scope(user)
    a = edge.store.get_application(app_id, where, params)
    if not a:
        raise HTTPException(status_code=404, detail="Aplicación no encontrada.")
    return a


@router.post("/applications/{app_id}/confirm")
def confirm(app_id: int, request: Request, user: dict = Depends(_confirm)):
    a = _application(user, app_id)
    if a["estado"] == "confirmada":                   # idempotente ante doble clic
        return {"estado": "confirmada"}
    if edge.store.confirm(app_id):
        audit.log(user["email"], "plantilla_confirmada", f"app={app_id} device={a['dispositivo_id']} plantilla={a['plantilla']}",
                  client_ip(request))
        return {"estado": "confirmada"}
    estado = (_application(user, app_id))["estado"]
    if estado == "confirmada":
        return {"estado": "confirmada"}
    if estado == "revertida":
        raise HTTPException(status_code=409, detail="Este cambio ya se revirtió porque no se confirmó a tiempo.")
    if estado == "aplicada":
        raise HTTPException(status_code=409, detail="El plazo de confirmación venció; el cambio se está revirtiendo.")
    raise HTTPException(status_code=409, detail="Esta aplicación no se puede confirmar en su estado actual.")


@router.post("/applications/{app_id}/revert")
def revert(app_id: int, request: Request, user: dict = Depends(_confirm)):
    a = _application(user, app_id)
    if not edge.store.request_revert(app_id):
        raise HTTPException(status_code=409, detail="Solo se puede revertir una aplicación vigente (no confirmada ni revertida).")
    audit.log(user["email"], "plantilla_reversion_solicitada",
              f"app={app_id} device={a['dispositivo_id']} plantilla={a['plantilla']}", client_ip(request))
    return {"estado": "aplicada", "detalle": "Se revertirá en unos segundos."}


@router.get("/devices/{device_id}/applications")
def list_applications(device_id: int, user: dict = Depends(_base)):
    _device(user, device_id)
    out = []
    for r in edge.store.list_for_device(device_id):
        tpl = tpls.CATALOG.get(r["plantilla"])
        out.append({"id": r["id"], "plantilla": tpl.nombre if tpl else r["plantilla"], "plantilla_id": r["plantilla"],
                    "estado": r["estado"], "creado_en": edge.iso(r["creado_en"]), "revertir_en": edge.iso(r["revertir_en"]),
                    "usuario": r["usuario_email"], "resultado": r.get("resultado")})
    return out
