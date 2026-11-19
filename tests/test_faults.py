"""Every fault leaves its signature in the evidence, and the engine reads it."""
import pytest

from bacgw import knx
from bacgw.diagnose import LEVELS, diagnose, report
from bacgw.evaluate import evaluate, seed_of
from bacgw.server import AV, DEVICE, UNKNOWN_OBJECT, Error
from bacgw.sim import FAULTS, simulate

SEEDS = range(4)


def runs(fault):
    return [simulate(fault, seed_of(fault, i, "t")) for i in SEEDS]


def target_key(ev):
    p = ev.points[ev.target]
    return (p["type"], p["instance"])


def stale(ev):
    return ev.history[ev.target].updates[-1][1]


def test_cov_increment_too_coarse_signature():
    for ev in runs("COV_INC_COARSE"):
        cfg = next(o for o in ev.config if o.name == ev.target)
        assert cfg.cov_increment == 1.0 and ev.points[ev.target]["kind"] == "temperature"
        assert isinstance(ev.direct_read, float) and abs(ev.direct_read - stale(ev)) < 1.0
        assert target_key(ev) in next(iter(ev.object_lists.values()))


def test_subscriptions_lost_after_save_signature():
    for ev in runs("COV_SUB_LOST_SAVE"):
        t_save = ev.truth["t_fault"]
        assert ev.client.mode == "cov"
        for name, lg in ev.history.items():
            assert not lg.updates or lg.updates[-1][0] <= t_save + 1e-6
        rec = ev.sub_records[ev.target]
        assert rec.lifetime == 0 or rec.last_renewal + rec.lifetime > ev.t_end


def test_lifetime_expired_signature():
    for ev in runs("COV_LIFETIME_EXPIRED"):
        rec = ev.sub_records[ev.target]
        assert rec.renew_interval is None and 0 < rec.lifetime and rec.first + rec.lifetime < ev.t_end
        for lg in ev.history.values():
            assert not lg.updates or lg.updates[-1][0] <= rec.first + rec.lifetime + 1e-6


@pytest.mark.parametrize("fault", ["NOT_EXPORTED", "TYPE_NOT_EXPORTABLE", "OVER_LIMIT", "GA_CHANGED"])
def test_missing_objects_answer_unknown_object(fault):
    for ev in runs(fault):
        assert ev.direct_read == UNKNOWN_OBJECT
        assert target_key(ev) not in next(iter(ev.object_lists.values()))


def test_not_exported_signature():
    for ev in runs("NOT_EXPORTED"):
        assert not next(o for o in ev.config if o.name == ev.target).export


def test_type_not_exportable_signature():
    for ev in runs("TYPE_NOT_EXPORTABLE"):
        o = next(o for o in ev.config if o.name == ev.target)
        assert o.export and knx.DPTS[o.dpt].kind == "other"
        assert not ev.history[ev.target].updates


def test_over_limit_signature():
    for ev in runs("OVER_LIMIT"):
        objs = [k for k in next(iter(ev.object_lists.values())) if k[0] != DEVICE]
        assert ev.model_name == "small" and len(objs) == 150
        assert sum(1 for o in ev.config if o.export and o.kind != "other") > 150


def test_ga_changed_signature():
    for ev in runs("GA_CHANGED"):
        moved = [k for k, n in ev.new_object_names.items() if n == ev.target]
        assert len(moved) == 1
        assert moved[0][1] == knx.ga_to_instance(ev.truth["new_ga"]) != target_key(ev)[1]


def target_commands(ev):
    return [c for c in ev.commands if c[1] == ev.target]


def test_command_masks_knx_signature():
    for ev in runs("CMD_MASKS_KNX"):
        t, _, v, p, _ = target_commands(ev)[-1]
        assert v is not None and p < ev.knx_priority
        assert ev.priority_array[p - 1] == v and all(x is None for x in ev.priority_array[:p - 1])
        assert ev.direct_read == v == stale(ev)
        ga = next(o for o in ev.config if o.name == ev.target).ga
        knx_last = [v2 for t2, g, v2 in ev.knx_monitor if g == ga][-1]
        assert knx_last != ev.direct_read                # KNX moved on, BACnet did not
        assert ev.priority_array[ev.knx_priority - 1] == knx_last   # waiting in the controller's slot


def test_command_no_effect_signature():
    for ev in runs("CMD_NO_EFFECT"):
        t, _, v, p, res = target_commands(ev)[-1]
        assert res == "ack" and v is not None and p > ev.knx_priority
        assert ev.priority_array[p - 1] == v
        assert ev.priority_array[ev.knx_priority - 1] == ev.direct_read != v
        # the command value never appears in the history after it was written
        assert all(v2 != v for t2, v2 in ev.history[ev.target].updates if t2 >= t)


def test_command_same_slot_signature():
    for ev in runs("CMD_SAME_SLOT"):
        cmds = target_commands(ev)
        (t_w, _, v_w, p, _), (t_r, _, v_r, p_r, _) = cmds[-2], cmds[-1]
        assert p == p_r == ev.knx_priority and v_w is not None and v_r is None
        ups = ev.history[ev.target].updates
        # after the relinquish the point shows the frozen Relinquish_Default, its commissioning value
        assert ev.direct_read == ev.relinquish_default == ups[0][1] == stale(ev) != v_w


def test_duplicate_device_signature():
    for ev in runs("DUP_DEVICE"):
        assert len(ev.i_am) == 2 and ev.i_am[0][0] == ev.i_am[1][0] and ev.i_am[0][1] != ev.i_am[1][1]


def test_poll_too_slow_signature():
    for ev in runs("POLL_TOO_SLOW"):
        assert ev.client.mode == "poll" and ev.client.poll_interval >= 900
        ts = [t for t, _ in ev.history[ev.target].updates]
        gaps = {round(b - a, 6) for a, b in zip(ts, ts[1:])}
        assert gaps == {ev.client.poll_interval}


@pytest.mark.parametrize("fault", FAULTS)
def test_engine_finds_every_fault_with_all_evidence(fault):
    for ev in runs(fault)[:3]:
        r = diagnose(ev, "E4")
        assert r[0].cls == fault, report(ev, "E4")


def test_save_and_expiry_look_identical_in_the_history():
    for f in ("COV_SUB_LOST_SAVE", "COV_LIFETIME_EXPIRED"):
        for ev in runs(f):
            top2 = {h.cls for h in diagnose(ev, "E1")[:2]}
            assert top2 == {"COV_SUB_LOST_SAVE", "COV_LIFETIME_EXPIRED"}


def test_certain_verdicts_name_their_evidence():
    ev = runs("DUP_DEVICE")[0]
    h = diagnose(ev, "E3")[0]
    assert h.certainty == "certain" and "Who-Is" in h.why[-1]


def test_no_wrong_certain_verdict_small_run():
    d = evaluate(6, salt="test")
    for lv in LEVELS:
        assert d["certain_verdicts"][lv]["wrong"] == 0
    assert d["recall"]["E4"]["HEALTHY"] >= 0.8
