"""Monitoreo: clasificación, ciclo de observación, incidentes, avisos por correo y retención. Sin red, BD ni SMTP real."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services import alerts, monitoring as mon
from app.services.monitoring import MonitorConfig, run_cycle

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
CFG = MonitorConfig()


def peer(key="K1", hs="1m", rx=1000, tx=500):
    p = {"public-key": key, "rx": str(rx), "tx": str(tx), "allowed-address": "10.254.0.2/32"}
    if hs is not None:
        p["last-handshake"] = hs
    return p


class MemStore:
    """Misma interfaz que PgStore, en memoria."""

    def __init__(self, devices=(), smtp=None, lock=True):
        self.devs = {d["id"]: dict(d) for d in devices}
        self.metrics, self.alerts, self.purged_before = [], [], None
        self.smtp, self.lock, self.next_id = smtp, lock, 1

    def try_lock(self, key):
        return self.lock

    def devices(self):
        out = []
        for d in self.devs.values():
            m = [x[1] for x in self.metrics if x[0] == d["id"]]
            out.append({**d, "ultima_muestra": max(m) if m else None})
        return out

    def update_device(self, did, estado, hb, desde, rx=None, rx_en=None, rx_activo_en=None):
        d = self.devs[did]
        d["estado"], d["estado_desde"], d["rx_activo_en"] = estado, desde, rx_activo_en
        if hb is not None:
            d["ultimo_heartbeat"] = hb
        if rx is not None:
            d["rx_ultimo"], d["rx_muestra_en"] = rx, rx_en

    def add_metric(self, did, ts, estado, rx, tx):
        self.metrics.append((did, ts, estado, rx, tx))

    def open_alerts(self):
        return {a["dispositivo_id"]: a for a in self.alerts if a["estado"] == "abierto"}

    def open_alert(self, did, inicio):
        if did in self.open_alerts():
            return
        self.alerts.append({"id": self.next_id, "dispositivo_id": did, "estado": "abierto", "inicio": inicio,
                            "cerrado_en": None, "notificado_caida_en": None, "notificado_recuperacion_en": None,
                            "intentos_caida": 0, "intentos_recuperacion": 0})
        self.next_id += 1

    def close_alert(self, aid, now):
        for a in self.alerts:
            if a["id"] == aid and a["estado"] == "abierto":
                a["estado"], a["cerrado_en"] = "cerrado", now

    def purge(self, cutoff):
        self.purged_before = cutoff
        n = len(self.metrics)
        self.metrics = [m for m in self.metrics if m[1] >= cutoff]
        return n - len(self.metrics)

    def smtp_config(self):
        return self.smtp

    def pending_notifications(self, max_attempts):
        out = []
        for a in self.alerts:
            d = self.devs[a["dispositivo_id"]]
            base = {**a, "equipo": d["nombre"], "sede": "Tienda Centro", "contacto_email": d.get("contacto"),
                    "partner_email": d.get("partner")}
            if a["estado"] == "abierto" and not a["notificado_caida_en"] and a["intentos_caida"] < max_attempts:
                out.append(base)
            elif (a["estado"] == "cerrado" and a["notificado_caida_en"] and not a["notificado_recuperacion_en"]
                  and a["intentos_recuperacion"] < max_attempts):
                out.append(base)
        return out

    def mark_attempt(self, aid, kind, ok, now):
        a = next(x for x in self.alerts if x["id"] == aid)
        a["intentos_" + kind] += 1
        if ok:
            a["notificado_" + ("caida" if kind == "caida" else "recuperacion") + "_en"] = now


def dev(did=1, estado="online", key="K1", **kw):
    return {"id": did, "nombre": f"Router {did}", "estado": estado, "public_key": key, "estado_desde": None,
            "ultimo_heartbeat": None, "rx_ultimo": None, "rx_muestra_en": None, "rx_activo_en": None,
            "contacto": "sede@cliente.co", "partner": "aliado@partner.co", **kw}


SMTP = {"smtp_host": "smtp.invalid", "smtp_port": 587, "smtp_user": "u", "smtp_password": "SECRETO-SMTP-123",
        "smtp_encryption": "TLS", "remitente_email": "alertas@avzcloud.com", "remitente_nombre": "AVZCloud Alertas"}


class Outbox:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def __call__(self, cfg, msg):
        if self.fail:
            raise OSError("smtp caído con SECRETO-SMTP-123")
        self.sent.append(msg)


# ---------------- duración y clasificación ----------------
@pytest.mark.parametrize("raw,secs", [("1m4s", 64), ("2m", 120), ("1d2h", 93600), ("00:01:04", 64),
                                      ("1d02:03:04", 93784), ("45s", 45), ("500ms", 0.5), (30, 30.0)])
def test_parse_ros_duration(raw, secs):
    assert mon.parse_ros_duration(raw) == pytest.approx(secs)


@pytest.mark.parametrize("raw", [None, "", "never"])
def test_parse_never(raw):
    assert mon.parse_ros_duration(raw) is None


def test_parse_garbage_raises():
    with pytest.raises(ValueError):
        mon.parse_ros_duration("hola")


def test_classify_by_handshake():
    assert mon.classify(30) == "online"
    assert mon.classify(179.9) == "online"
    assert mon.classify(180) == "offline"
    assert mon.classify(3600) == "offline"
    assert mon.classify(None) == "pendiente"
    assert mon.classify(30, online_seconds=20) == "offline"          # umbral configurable


def test_classify_established_without_handshake_is_not_pending():
    assert mon.classify(None, established=True) == "offline"
    assert mon.classify(None, rx_seen=True) == "offline"


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("MONITOR_ONLINE_SECONDS", "240")
    monkeypatch.setenv("MONITOR_ALERT_MINUTES", "basura")
    monkeypatch.setenv("MONITOR_RETENTION_DAYS", "30")
    monkeypatch.setenv("MONITOR_RX_WINDOW_SECONDS", "60")
    c = MonitorConfig.from_env()
    assert (c.online_seconds, c.alert_minutes, c.retention_days, c.rx_window_seconds) == (240, 5, 30, 60)


# ---------------- segunda señal: rx ----------------
def cycle(store, peers, now, cfg=CFG, **kw):
    return run_cycle(store, peers, cfg, now, **kw)


def test_first_sample_does_not_decide_by_rx():
    s = MemStore([dev(estado="online")])
    cycle(s, [peer(hs="10m", rx=5000)], T0)
    d = s.devs[1]
    assert d["estado"] == "offline"                     # sin muestra previa: solo cuenta el handshake
    assert d["rx_ultimo"] == 5000 and d["rx_activo_en"] is None


def test_growing_rx_with_old_handshake_is_online():
    s = MemStore([dev(estado="online", rx_ultimo=5000, rx_muestra_en=T0 - timedelta(seconds=60))])
    cycle(s, [peer(hs="2m50s", rx=5200)], T0)
    assert s.devs[1]["estado"] == "online"
    cycle(s, [peer(hs="6m", rx=5400)], T0 + timedelta(seconds=60))     # handshake ya vencido, rx sigue creciendo
    assert s.devs[1]["estado"] == "online"
    assert s.devs[1]["ultimo_heartbeat"] == T0 + timedelta(seconds=60)


def test_rx_growth_rescues_even_when_handshake_expired():
    s = MemStore([dev(estado="offline", rx_ultimo=100, rx_muestra_en=T0 - timedelta(seconds=60))])
    cycle(s, [peer(hs="5m", rx=400)], T0)
    assert s.devs[1]["estado"] == "online"


def test_quiet_rx_with_old_handshake_is_offline():
    s = MemStore([dev(estado="online", rx_ultimo=5000, rx_muestra_en=T0 - timedelta(seconds=60),
                      rx_activo_en=T0 - timedelta(seconds=100))])
    cycle(s, [peer(hs="4m", rx=5000)], T0)
    assert s.devs[1]["estado"] == "offline"


def test_quiet_rx_within_window_still_online():
    s = MemStore([dev(estado="online", rx_ultimo=5000, rx_muestra_en=T0 - timedelta(seconds=60),
                      rx_activo_en=T0 - timedelta(seconds=60))])
    cycle(s, [peer(hs="4m", rx=5000)], T0)
    assert s.devs[1]["estado"] == "online"


def test_counter_reset_is_not_an_outage():
    s = MemStore([dev(estado="online", rx_ultimo=9_000_000, rx_muestra_en=T0 - timedelta(seconds=60),
                      rx_activo_en=T0 - timedelta(seconds=300))])
    cycle(s, [peer(hs=None, rx=120)], T0)               # el hub reinició: rx bajó y el handshake se borró
    assert s.devs[1]["estado"] == "online"
    assert s.devs[1]["rx_ultimo"] == 120                # nueva línea base
    assert s.alerts == []


def test_stale_previous_sample_is_ignored():
    s = MemStore([dev(estado="offline", rx_ultimo=100, rx_muestra_en=T0 - timedelta(hours=3))])
    cycle(s, [peer(hs="1h", rx=900)], T0)               # muestra previa de hace horas: no sirve como evidencia
    assert s.devs[1]["estado"] == "offline"


def test_rx_signal_can_be_disabled():
    cfg = MonitorConfig(rx_window_seconds=0)
    s = MemStore([dev(estado="online", rx_ultimo=100, rx_muestra_en=T0 - timedelta(seconds=60))])
    cycle(s, [peer(hs="5m", rx=900)], T0, cfg)
    assert s.devs[1]["estado"] == "offline"


# ---------------- ciclo: estados, hub caído, pendiente ----------------
def test_never_handshake_is_pending():
    s = MemStore([dev(estado="pendiente")])
    cycle(s, [peer(hs=None, rx=0)], T0)
    assert s.devs[1]["estado"] == "pendiente"
    assert s.alerts == []


def test_first_handshake_turns_pending_online_and_stores_heartbeat():
    s = MemStore([dev(estado="pendiente")])
    cycle(s, [peer(hs="20s")], T0)
    d = s.devs[1]
    assert d["estado"] == "online" and d["ultimo_heartbeat"] == T0 - timedelta(seconds=20)
    assert s.metrics[0][2] == "online" and s.metrics[0][3:] == (1000, 500)


def test_established_device_losing_handshake_is_offline_not_pending():
    s = MemStore([dev(estado="online")])
    cycle(s, [peer(hs=None, rx=0)], T0)
    assert s.devs[1]["estado"] == "offline"


def test_hub_down_keeps_every_state():
    s = MemStore([dev(1, "online"), dev(2, "offline", key="K2"), dev(3, "pendiente", key="K3")])
    res = cycle(s, None, T0)
    assert res["hub"] is False
    assert [d["estado"] for d in s.devs.values()] == ["online", "offline", "pendiente"]
    assert s.metrics == [] and s.alerts == []


def test_hub_down_does_not_close_or_open_incidents():
    s = MemStore([dev(estado="offline", estado_desde=T0 - timedelta(hours=1))])
    cycle(s, None, T0)
    assert s.alerts == []


def test_peer_missing_in_hub_keeps_state():
    s = MemStore([dev(estado="online")])
    res = cycle(s, [peer(key="OTRO")], T0)
    assert res["sin_peer"] == 1 and s.devs[1]["estado"] == "online"


def test_unreadable_handshake_keeps_state():
    s = MemStore([dev(estado="online")])
    cycle(s, [peer(hs="???")], T0)
    assert s.devs[1]["estado"] == "online"


def test_other_replica_holding_lock_skips_cycle():
    s = MemStore([dev(estado="pendiente")], lock=False)
    res = cycle(s, [peer(hs="1s")], T0)
    assert res["omitido"] and s.devs[1]["estado"] == "pendiente"


def test_samples_on_change_or_after_interval():
    s = MemStore([dev(estado="online")])
    cycle(s, [peer(hs="10s")], T0)                                     # primera muestra
    cycle(s, [peer(hs="10s")], T0 + timedelta(seconds=60))             # sin cambio y dentro del intervalo
    assert len(s.metrics) == 1
    cycle(s, [peer(hs="10s")], T0 + timedelta(seconds=301))
    assert len(s.metrics) == 2
    cycle(s, [peer(hs="20m", rx=1000)], T0 + timedelta(seconds=330))   # cambio de estado: muestra inmediata
    assert len(s.metrics) == 3 and s.metrics[-1][2] == "offline"


# ---------------- incidentes y alertas ----------------
def go_offline(s, t=T0):
    cycle(s, [peer(hs="10m")], t)


def test_incident_opens_only_after_sustained_minutes():
    s = MemStore([dev(estado="online")])
    go_offline(s)
    assert s.alerts == []                                              # recién caído
    cycle(s, [peer(hs="14m")], T0 + timedelta(minutes=4, seconds=59))
    assert s.alerts == []
    res = cycle(s, [peer(hs="15m")], T0 + timedelta(minutes=5))
    assert res["alertas_abiertas"] == 1 and len(s.alerts) == 1
    assert s.alerts[0]["inicio"] == T0


def test_one_incident_per_outage_and_new_one_after_recovery():
    s = MemStore([dev(estado="online")])
    go_offline(s)
    for m in (5, 6, 7, 20):
        cycle(s, [peer(hs=f"{10 + m}m")], T0 + timedelta(minutes=m))
    assert len(s.alerts) == 1
    cycle(s, [peer(hs="5s")], T0 + timedelta(minutes=21))
    assert s.alerts[0]["estado"] == "cerrado" and s.alerts[0]["cerrado_en"] == T0 + timedelta(minutes=21)
    go_offline(s, T0 + timedelta(minutes=30))
    cycle(s, [peer(hs="16m")], T0 + timedelta(minutes=36))
    assert len(s.alerts) == 2 and s.alerts[1]["estado"] == "abierto"


def test_short_blip_never_alerts():
    s = MemStore([dev(estado="online")])
    go_offline(s)
    cycle(s, [peer(hs="3s")], T0 + timedelta(minutes=2))
    cycle(s, [peer(hs="3s")], T0 + timedelta(minutes=10))
    assert s.alerts == []


def sustained_outage(s):
    go_offline(s)
    cycle(s, [peer(hs="16m")], T0 + timedelta(minutes=6))


def test_email_sent_once_per_incident_then_recovery():
    s, box = MemStore([dev(estado="online")], smtp=SMTP), Outbox()
    sustained_outage(s)
    now = T0 + timedelta(minutes=6)
    assert alerts.notify_pending(s, box, now)["enviadas"] == 1
    assert alerts.notify_pending(s, box, now + timedelta(minutes=1))["enviadas"] == 0     # sin spam
    assert alerts.notify_pending(s, box, now + timedelta(minutes=2))["enviadas"] == 0
    assert len(box.sent) == 1 and "fuera de línea" in box.sent[0]["Subject"]
    cycle(s, [peer(hs="2s")], T0 + timedelta(minutes=9))
    assert alerts.notify_pending(s, box, T0 + timedelta(minutes=9))["enviadas"] == 1
    assert alerts.notify_pending(s, box, T0 + timedelta(minutes=10))["enviadas"] == 0
    assert len(box.sent) == 2 and "recuperado" in box.sent[1]["Subject"]


def test_recovery_not_sent_if_outage_was_never_notified():
    s, box = MemStore([dev(estado="online")], smtp=None), Outbox()
    sustained_outage(s)
    assert alerts.notify_pending(s, box, T0)["smtp"] is False
    cycle(s, [peer(hs="2s")], T0 + timedelta(minutes=9))
    s.smtp = SMTP
    assert alerts.notify_pending(s, box, T0 + timedelta(minutes=10))["enviadas"] == 0
    assert box.sent == []


def test_nothing_sent_without_smtp_config():
    s, box = MemStore([dev(estado="online")], smtp=None), Outbox()
    sustained_outage(s)
    res = alerts.notify_pending(s, box, T0)
    assert res == {"smtp": False, "enviadas": 0, "fallidas": 0, "sin_destinatarios": 0} and box.sent == []
    s.smtp = {**SMTP, "smtp_host": ""}
    assert alerts.notify_pending(s, box, T0)["smtp"] is False


def test_pending_alert_is_sent_once_smtp_gets_configured():
    s, box = MemStore([dev(estado="online")], smtp=None), Outbox()
    sustained_outage(s)
    alerts.notify_pending(s, box, T0)
    s.smtp = SMTP
    assert alerts.notify_pending(s, box, T0 + timedelta(minutes=7))["enviadas"] == 1


def test_failed_send_retries_limited_and_never_leaks_secret(caplog):
    s, box = MemStore([dev(estado="online")], smtp=SMTP), Outbox(fail=True)
    sustained_outage(s)
    with caplog.at_level("DEBUG"):
        for i in range(6):
            alerts.notify_pending(s, box, T0 + timedelta(minutes=7 + i))
    assert s.alerts[0]["intentos_caida"] == alerts.MAX_ATTEMPTS
    assert s.alerts[0]["notificado_caida_en"] is None
    assert "SECRETO-SMTP-123" not in caplog.text


def test_recipients_are_sede_contact_and_partner_deduplicated(monkeypatch):
    monkeypatch.delenv("ALERT_EXTRA_RECIPIENTS", raising=False)
    row = {"contacto_email": "Sede@Cliente.co", "partner_email": "sede@cliente.co"}
    assert alerts.recipients_for(row) == ["Sede@Cliente.co"]
    row = {"contacto_email": "mal formado", "partner_email": "aliado@partner.co"}
    assert alerts.recipients_for(row) == ["aliado@partner.co"]
    monkeypatch.setenv("ALERT_EXTRA_RECIPIENTS", "soporte@avzcloud.com, ,x")
    assert alerts.recipients_for(row) == ["aliado@partner.co", "soporte@avzcloud.com"]


def test_no_recipients_means_no_attempt():
    s, box = MemStore([dev(estado="online", contacto=None, partner=None)], smtp=SMTP), Outbox()
    sustained_outage(s)
    res = alerts.notify_pending(s, box, T0 + timedelta(minutes=7))
    assert res["sin_destinatarios"] == 1 and box.sent == [] and s.alerts[0]["intentos_caida"] == 0


def test_email_has_no_secrets_and_no_header_injection():
    s, box = MemStore([dev(estado="online", nombre="Router\r\nBcc: x@evil.co")], smtp=SMTP), Outbox()
    s.devs[1]["nombre"] = "Router\r\nBcc: x@evil.co"
    sustained_outage(s)
    alerts.notify_pending(s, box, T0 + timedelta(minutes=7))
    msg = box.sent[0]
    text = msg.as_string()
    assert "SECRETO-SMTP-123" not in text and "private" not in text.lower()
    assert "\n" not in msg["Subject"] and msg["Bcc"] is None
    assert msg["To"] == "sede@cliente.co, aliado@partner.co"


def test_smtp_sender_has_timeout():
    assert 0 < alerts.SMTP_TIMEOUT <= 30


# ---------------- retención ----------------
def test_retention_purges_old_samples_only_when_asked():
    s = MemStore([dev(estado="online")])
    s.metrics = [(1, T0 - timedelta(days=100), "online", 1, 1), (1, T0 - timedelta(days=89), "online", 2, 2)]
    cycle(s, [peer(hs="5s")], T0)
    assert s.purged_before is None and len(s.metrics) == 3
    res = cycle(s, [peer(hs="5s")], T0, purge=True)
    assert res["purgadas"] == 1 and s.purged_before == T0 - timedelta(days=90)
    cfg = MonitorConfig(retention_days=30)
    cycle(s, [peer(hs="5s")], T0, cfg, purge=True)
    assert s.purged_before == T0 - timedelta(days=30)


def test_no_purge_when_hub_down():
    s = MemStore([dev(estado="online")])
    cycle(s, None, T0, purge=True)
    assert s.purged_before is None


# ---------------- utilidades ----------------
def test_free_router_ips_union_and_range():
    assert mon.free_router_ips([]) == 198
    assert mon.free_router_ips(["10.254.0.2", "10.254.0.3"], ["10.254.0.3", "10.254.0.199"]) == 195
    assert mon.free_router_ips(["10.254.0.200", "10.254.0.1", "10.0.0.5", "basura"]) == 198   # fuera de rango/pool


def test_tunnel_ips_from_peers_ignores_lans():
    peers = [{"allowed-address": "10.254.0.7/32,192.168.7.0/24"}, {"allowed-address": "10.40.1.2/32"}]
    assert mon.tunnel_ips_from_peers(peers) == ["10.254.0.7"]


def test_downsample_limits_points_and_keeps_worst_state():
    rows = [{"instante": T0 + timedelta(minutes=i), "estado": "offline" if i == 500 else "online",
             "rx_bytes": i, "tx_bytes": i} for i in range(2000)]
    out = mon.downsample(rows, 100)
    assert len(out) <= 100
    assert any(r["estado"] == "offline" for r in out)
    assert out[-1]["rx_bytes"] == 1999
    assert mon.downsample(rows[:50], 100) == rows[:50]


def test_peer_cache_caches_success_and_failure():
    calls = []
    now = [0.0]

    class H:
        def __init__(self, fail): self.fail = fail

        def list_peers(self):
            calls.append(1)
            if self.fail:
                raise RuntimeError("caído")
            return [{"public-key": "K"}]

    c = mon.PeerCache(clock=lambda: now[0])
    assert c.get(H(True)) is None and c.get(H(True)) is None and len(calls) == 1
    now[0] = 31
    assert c.get(H(False)) == [{"public-key": "K"}] and c.get(H(False)) is not None and len(calls) == 2
    now[0] = 45
    c.get(H(False))
    assert len(calls) == 3


def test_task_cycle_survives_hub_exception(monkeypatch):
    import asyncio
    from app.tasks import monitor as task

    class BadHub:
        def __init__(self, *_): pass

        def list_peers(self): raise RuntimeError("sin ruta")

    seen = {}
    monkeypatch.setattr(task, "HubClient", BadHub)
    monkeypatch.setattr(task, "get_settings", lambda: None)
    monkeypatch.setattr(task.monitoring, "run_db_cycle", lambda peers, cfg: seen.setdefault("peers", peers) or {})
    monkeypatch.setattr(task.alerts, "run_notify", lambda: {})
    asyncio.run(task.cycle(CFG))
    assert seen["peers"] is None
