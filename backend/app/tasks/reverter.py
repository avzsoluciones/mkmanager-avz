"""Tarea periódica: revierte las aplicaciones de plantillas vencidas y no confirmadas.

Todo el estado vive en la BD (`plantillas_aplicadas`), así que sobrevive a reinicios; el primer ciclo se ejecuta
al arrancar y retoma lo pendiente. Con varias réplicas, `FOR UPDATE SKIP LOCKED` evita que dos procesos
reviertan la misma aplicación. Lo bloqueante (BD y API del router) corre en un hilo con `asyncio.to_thread`.
"""
import asyncio
import logging

from .. import audit
from ..services import edge

log = logging.getLogger("avz.reverter")
INTERVAL_SECONDS = 30
SYSTEM_USER = "sistema"


def tick() -> int:
    """Un ciclo: procesa lo vencido y deja registro de auditoría. Devuelve cuántas aplicaciones se procesaron."""
    done = edge.store.process_due(edge.revert_row)
    for row, out in done:
        info = f"app={row['id']} device={row['dispositivo_id']} plantilla={row['plantilla']} aplicada_por={row.get('usuario_email')}"
        estado = out["estado"]
        if estado == "revertida":
            audit.log(SYSTEM_USER, "plantilla_revertida", f"{info} intentos={out['intentos']}")
        elif estado == "error_reversion":
            audit.log(SYSTEM_USER, "reversion_fallida", f"{info} intentos={out['intentos']} {out['resultado'][:300]}")
        elif estado == "error":
            audit.log(SYSTEM_USER, "plantilla_error", f"{info} {out['resultado'][:300]}")
        else:
            audit.log(SYSTEM_USER, "reversion_reintento", f"{info} intento={out['intentos']} en {out['reintentar_en']}s")
    return len(done)


async def run() -> None:
    while True:
        try:
            await asyncio.to_thread(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Fallo en el ciclo de reversión; se reintenta en %s s", INTERVAL_SECONDS)
        await asyncio.sleep(INTERVAL_SECONDS)
