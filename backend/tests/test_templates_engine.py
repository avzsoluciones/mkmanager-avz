"""Motor: aplicar, verificar, deshacer y revertir contra un router en memoria (sin red)."""
from datetime import timedelta

import pytest

from app import audit
from app.services import edge
from app.services import templates as T
from app.services.ops import FILTER, Op, ops_from_json, tag_for
from app.tasks import reverter

from test_templates_fakes import DEV, T0, FakeRouter, MemStore

CTX = T.DeviceCtx.from_row(DEV)
FW = {"ips_admin": ["203.0.113.0/24"]}


@pytest.fixture
def env(monkeypatch):
    router, store = FakeRouter(), MemStore()
    events = []
    monkeypatch.setattr(edge, "session_factory", router.factory)
    monkeypatch.setattr(edge, "store", store)
    monkeypatch.setattr(audit, "log", lambda u, a, d="", ip="": events.append((u, a, d)))
    return router, store, events


def ops_for(tid, params):
    return T.plan(T.CATALOG[tid], params, CTX)[1]


def begin(store, plantilla="firewall-seguro", params=None):
    return store.begin(DEV["id"], plantilla, params or {}, edge.params_hash(params or {}), "u@x")


def apply_fw(env, minutes=5):
    router, store, _ = env
    app_id = begin(store)
    edge.run_apply(store, DEV, app_id, ops_for("firewall-seguro", FW))
    store.finish_applied(app_id, minutes)
    return app_id


# ---------------------------------------------------------------- aplicar
def test_firewall_goes_right_after_the_management_rule_and_keeps_order(env):
    router, store, _ = env
    app_id = apply_fw(env)
    rules = router.tables[FILTER]
    assert rules[0]["comment"] == "AVZCloud mgmt" and rules[-1]["comment"] == "regla previa"
    tag = tag_for(app_id)
    new = [r for r in rules if r.get("comment") == tag]
    assert [r["comment"] for r in rules[1:1 + len(new)]] == [tag] * len(new)            # contiguas, justo después de la gestión
    assert new[0]["connection-state"] == "established,related" and new[-1]["chain"] == "forward"
    assert router.comments(("ip", "firewall", "address-list")) == [tag]
    a = store.apps[app_id]
    assert a["ops"] and a["inversas"] and a["script"].startswith("# AVZCloud")           # inversas guardadas ANTES de aplicar


def test_inverses_of_add_remove_by_tag_and_of_set_restore_previous_values(env):
    router, store, _ = env
    sess = router.factory(DEV)
    inv = edge.plan_inverses(sess, [Op(("ip", "dns"), "set", {"servers": "1.1.1.1"}),
                                    Op(("system", "ntp", "client"), "set", {"enabled": "yes", "servers": "pool.ntp.org"})], "AVZ-APP-1")
    assert [o.path for o in inv] == [("system", "ntp", "client"), ("ip", "dns")]          # orden inverso
    assert inv[1].attrs == {"servers": "9.9.9.9"} and inv[0].attrs == {"enabled": "no", "servers": ""}
    adds = edge.plan_inverses(sess, [Op(("queue", "simple"), "add", {"name": "q", "target": "1.1.1.1/32", "comment": "AVZ-APP-1"}),
                                     Op(("queue", "simple"), "add", {"name": "r", "target": "1.1.1.2/32", "comment": "AVZ-APP-1"})], "AVZ-APP-1")
    assert adds == [Op(("queue", "simple"), "remove", {}, {"comment": "AVZ-APP-1"})]       # una sola inversa por ruta
    assert router.mutating_calls() == []                                                   # planificar no escribe


def test_dns_ntp_applies_and_rolls_back_exactly(env):
    router, store, _ = env
    app_id = begin(store, "dns-ntp", {"dns": ["1.1.1.1"]})
    edge.run_apply(store, DEV, app_id, ops_for("dns-ntp", {"dns": ["1.1.1.1"], "ntp": "pool.ntp.org"}))
    assert router.tables[("ip", "dns")][0]["servers"] == "1.1.1.1"
    assert router.tables[("ip", "dns")][0]["allow-remote-requests"] is False               # intacto
    assert edge.rollback(router, ops_from_json(store.apps[app_id]["inversas"])) == []
    assert router.tables[("ip", "dns")][0]["servers"] == "9.9.9.9"
    assert router.tables[("system", "ntp", "client")][0]["enabled"] == "no"


def test_missing_or_disabled_management_rule_refuses_to_apply(env):
    router, store, _ = env
    router.tables[FILTER][0]["comment"] = "otra"
    app_id = begin(store)
    with pytest.raises(edge.EdgeError, match="regla de gestión"):
        edge.run_apply(store, DEV, app_id, ops_for("firewall-seguro", FW))
    assert router.mutating_calls() == []
    store.mark_error(app_id, "x")
    router.tables[FILTER][0].update(comment="AVZCloud mgmt", disabled=True)
    with pytest.raises(edge.EdgeError, match="deshabilitada"):
        edge.run_apply(store, DEV, begin(store, params={"x": 1}), ops_for("firewall-seguro", FW))
    assert router.mutating_calls() == []


def test_router_rejection_midway_rolls_everything_back(env):
    router, store, _ = env
    before = router.tables[FILTER].copy()
    n = {"c": 0}

    def reject(path, attrs):
        n["c"] += 1
        return n["c"] == 6
    router.reject = reject
    app_id = begin(store)
    with pytest.raises(edge.ApplyFailed) as e:
        edge.run_apply(store, DEV, app_id, ops_for("firewall-seguro", FW))
    assert e.value.rolled_back and "rechazó la operación 6" in str(e.value)
    assert router.tables[FILTER] == before and router.tables[("ip", "firewall", "address-list")] == []


def test_new_connection_failing_after_apply_triggers_rollback(env):
    router, store, _ = env
    router.fail_connect_from = 2                       # la 1.ª conexión (aplicar) entra; la 2.ª (chequeo) ya no
    before = router.tables[FILTER].copy()
    with pytest.raises(edge.ApplyFailed) as e:
        edge.run_apply(store, DEV, begin(store), ops_for("firewall-seguro", FW))
    assert e.value.rolled_back and router.tables[FILTER] == before


def test_failed_rollback_is_reported_so_the_reverter_can_retry(env):
    router, store, _ = env
    router.reject = lambda p, a: p == FILTER and a["chain"] == "forward"
    router.fail_remove = True
    with pytest.raises(edge.ApplyFailed) as e:
        edge.run_apply(store, DEV, begin(store), ops_for("firewall-seguro", FW))
    assert e.value.rolled_back is False


def test_tunnel_host_must_be_a_device_address_inside_the_pool():
    for bad in (None, "", "8.8.8.8", "10.254.0.1", "10.254.0.0", "10.10.10.3", "10.254.0.256", "10.254.0.5\n"):
        with pytest.raises(edge.EdgeError):
            edge._tunnel_host({"tunnel_ip": bad})
    assert edge._tunnel_host({"tunnel_ip": "10.254.0.10"}) == "10.254.0.10"


def test_libsession_maps_to_librouteros_calls():
    calls = []

    class Path:
        def __init__(self, parts): self.parts = parts
        def __iter__(self): return iter([{".id": "*1", "a": "b"}])
        def add(self, **kw): calls.append(("add", self.parts, kw)); return "*9"
        def update(self, **kw): calls.append(("update", self.parts, kw))
        def remove(self, *ids): calls.append(("remove", self.parts, ids))

    class Api:
        def path(self, *parts): return Path(parts)
        def close(self): calls.append("close")

    s = edge.LibSession(Api())
    assert s.rows(("ip", "dns")) == [{".id": "*1", "a": "b"}]
    assert s.add(("queue", "simple"), {"name": "x"}) == "*9"
    s.set(("ip", "dns"), {"servers": "1.1.1.1"})
    s.set(("ip", "address"), {"comment": "c"}, "*5")
    s.remove(("ip", "address"), "*5")
    s.close()
    assert calls == [("add", ("queue", "simple"), {"name": "x"}), ("update", ("ip", "dns"), {"servers": "1.1.1.1"}),
                     ("update", ("ip", "address"), {"comment": "c", ".id": "*5"}), ("remove", ("ip", "address"), ("*5",)), "close"]


# ---------------------------------------------------------------- reversión
def test_rollback_is_idempotent_and_removes_only_own_items(env):
    router, store, _ = env
    app_id = apply_fw(env)
    inv = ops_from_json(store.apps[app_id]["inversas"])
    assert edge.rollback(router, inv) == [] and edge.rollback(router, inv) == []
    assert [r["comment"] for r in router.tables[FILTER]] == ["AVZCloud mgmt", "regla previa"]


def test_rollback_refuses_tampered_inverses(env):
    router, _, _ = env
    bad = [Op(FILTER, "remove", {}, {"comment": "AVZCloud mgmt"})]
    assert edge.rollback(router, bad)[0].startswith("Inversas no válidas")
    assert router.mutating_calls() == []


def test_tick_reverts_expired_unconfirmed_and_audits(env):
    router, store, events = env
    app_id = apply_fw(env, minutes=2)
    store.now = T0 + timedelta(minutes=1)
    assert reverter.tick() == 0 and len(router.tables[FILTER]) > 2                         # aún no vence
    store.now = T0 + timedelta(minutes=2, seconds=1)
    assert reverter.tick() == 1
    a = store.apps[app_id]
    assert a["estado"] == "revertida" and a["revertida_en"] and a["intentos_reversion"] == 1
    assert [r["comment"] for r in router.tables[FILTER]] == ["AVZCloud mgmt", "regla previa"]
    assert ("sistema", "plantilla_revertida") == events[-1][:2] and f"app={app_id}" in events[-1][2]
    assert reverter.tick() == 0                                                              # segunda pasada: nada que hacer


def test_tick_does_not_touch_confirmed_or_non_reversible(env):
    router, store, _ = env
    a1 = apply_fw(env, minutes=2)
    assert store.confirm(a1)
    app2 = begin(store, "dns-ntp", {"dns": ["1.1.1.1"]})
    edge.run_apply(store, DEV, app2, ops_for("dns-ntp", {"dns": ["1.1.1.1"]}))
    store.finish_applied(app2, None)
    store.now = T0 + timedelta(hours=5)
    n = len(router.tables[FILTER])
    assert reverter.tick() == 0
    assert len(router.tables[FILTER]) == n and router.tables[("ip", "dns")][0]["servers"] == "1.1.1.1"
    assert store.apps[a1]["estado"] == "confirmada" and store.apps[app2]["estado"] == "aplicada"


def test_unreachable_device_is_retried_then_ends_in_error_reversion(env):
    router, store, events = env
    app_id = apply_fw(env, minutes=2)
    router.fail_connect_from = 1
    store.now = T0 + timedelta(minutes=3)
    seen = []
    for _ in range(edge.MAX_REVERT_ATTEMPTS):
        reverter.tick()
        a = store.apps[app_id]
        seen.append((a["estado"], a["intentos_reversion"]))
        store.now += timedelta(minutes=10)                          # pasa la espera del reintento
    assert seen[0] == ("aplicada", 1) and seen[-1] == ("error_reversion", edge.MAX_REVERT_ATTEMPTS)
    assert store.apps[app_id]["reintentar_en"] is None
    assert [e[1] for e in events].count("reversion_reintento") == edge.MAX_REVERT_ATTEMPTS - 1
    assert events[-1][1] == "reversion_fallida"
    assert reverter.tick() == 0                                     # estado final: no se vuelve a intentar


def test_retry_waits_before_the_next_attempt_and_recovers(env):
    router, store, _ = env
    app_id = apply_fw(env, minutes=2)
    router.fail_connect_from = 1
    store.now = T0 + timedelta(minutes=3)
    assert reverter.tick() == 1 and store.apps[app_id]["reintentar_en"] == store.now + timedelta(seconds=edge.RETRY_BASE_SECONDS)
    assert reverter.tick() == 0                                     # todavía en espera
    router.fail_connect_from = None
    store.now += timedelta(seconds=edge.RETRY_BASE_SECONDS + 1)
    assert reverter.tick() == 1 and store.apps[app_id]["estado"] == "revertida"


def test_stale_applying_without_plan_is_closed_and_with_plan_is_reverted(env):
    router, store, _ = env
    lost = begin(store, "dns-ntp", {"dns": ["8.8.8.8"]})              # el proceso murió antes de guardar el plan
    store.now = T0 + timedelta(minutes=edge.STALE_APPLYING_MINUTES, seconds=1)
    assert reverter.tick() == 1 and store.apps[lost]["estado"] == "error"
    # murió a mitad de aplicar, con plan guardado
    store.now = T0 + timedelta(minutes=10)
    half = begin(store, "dns-ntp", {"dns": ["1.1.1.1"]})
    ops = edge.stamp(ops_for("dns-ntp", {"dns": ["1.1.1.1"]}), tag_for(half))
    store.save_plan(half, ops, edge.plan_inverses(router, ops, tag_for(half)), "x")
    edge.execute(router, ops)
    store.now += timedelta(minutes=4)
    assert reverter.tick() == 1 and store.apps[half]["estado"] == "revertida"
    assert router.tables[("ip", "dns")][0]["servers"] == "9.9.9.9"


def test_reverter_survives_errors_and_loops(monkeypatch):
    import asyncio
    calls = {"tick": 0, "sleep": 0}

    def flaky():
        calls["tick"] += 1
        if calls["tick"] == 1:
            raise RuntimeError("BD caída")
        return 0

    async def fake_sleep(secs):
        calls["sleep"] += 1
        assert secs == reverter.INTERVAL_SECONDS == 30
        if calls["sleep"] == 3:
            raise asyncio.CancelledError()

    monkeypatch.setattr(reverter, "tick", flaky)
    monkeypatch.setattr(reverter.asyncio, "sleep", fake_sleep)

    async def go():
        with pytest.raises(asyncio.CancelledError):
            await reverter.run()
    asyncio.run(go())
    assert calls["tick"] == 3                                       # el primer fallo no mató el bucle
