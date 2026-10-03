"""Tarea periódica: monitoreo de estado de túneles y equipos (cada 60 s por defecto) y avisos por correo."""
import asyncio
import logging

from ..config import get_settings
from ..services import alerts, monitoring
from ..services.hub import HubClient

log = logging.getLogger("avz.monitor")


async def cycle(cfg: monitoring.MonitorConfig) -> dict:
    hub = HubClient(get_settings())
    try:
        peers = await asyncio.to_thread(hub.list_peers)
    except Exception as e:                              # hub caído: se conserva el último estado
        log.warning("hub inalcanzable: %s", type(e).__name__)
        peers = None
    res = await asyncio.to_thread(monitoring.run_db_cycle, peers, cfg)
    res["avisos"] = await asyncio.to_thread(alerts.run_notify)
    return res


async def run() -> None:
    cfg = monitoring.MonitorConfig.from_env()
    if not cfg.enabled:
        log.warning("monitoreo desactivado (MONITOR_ENABLED=0)")
        return
    await asyncio.sleep(5)                               # deja terminar el arranque y las migraciones
    while True:
        try:
            await cycle(cfg)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("falló un ciclo de monitoreo")
        await asyncio.sleep(cfg.interval)
