"""COV replay, polling replay, metrics and the analytic check."""
import random

import pytest

from bacgw import covstudy as C
from bacgw import knx


EV = [(0.0, 20.0), (10.0, 20.1), (20.0, 20.2), (30.0, 20.3), (40.0, 20.6), (50.0, 20.5)]


def test_cov_replay_follows_server_rule():
    shown = C.cov_replay(EV, 0.3)
    assert shown == [(0.0, 20.0), (30.0, 20.3), (40.0, 20.6)]
    assert C.count_notifications(EV, 0.3) == 2
    assert C.cov_replay(EV, 0.1) == EV


def test_binary_cov_replay_reports_every_change():
    ev = [(0.0, 0.0), (5.0, 1.0), (6.0, 0.0), (9.0, 1.0)]
    assert C.cov_replay(ev, None) == ev


def test_renewal_sends_current_value():
    shown = C.cov_replay(EV, 1.0, renew=25.0, t_end=60.0)
    assert (25.0, 20.2) in shown and (50.0, 20.6) in shown


def test_compare_metrics():
    m = C.compare(EV, C.cov_replay(EV, 0.3), 60.0)
    assert m["changes"] == 5
    assert m["missed"] == 3                       # 20.1, 20.2 and 20.5 never shown
    assert m["err_max"] == pytest.approx(0.2)
    assert m["stale_max_s"] == pytest.approx(20.0)


def test_poll_replay_samples_current_value():
    shown = C.poll_replay(EV, 25.0, 60.0, phase=5.0)
    assert shown == [(5.0, 20.0), (30.0, 20.3), (55.0, 20.5)]
    m = C.compare(EV, shown, 60.0)
    assert m["missed"] == 3                       # 20.1, 20.2, 20.6


def test_weighted_quantile():
    assert C.weighted_quantile([1, 98, 1], [0.0, 0.1, 5.0], 0.5) == 0.1
    assert C.weighted_quantile([1, 1], [0.0, 1.0], 0.99) == 1.0


def test_random_walk_rate_matches_first_exit_time():
    for r in C.brownian_check(t_end=4 * C.DAY):
        assert abs(r["relative_error"]) < 0.25


def test_temperature_sensor_sends_on_delta():
    ev = knx.temperature(random.Random(1), 3600 * 6, knx.SignalParams())
    assert all(abs(b[1] - a[1]) >= 0.095 for a, b in zip(ev, ev[1:]))


def test_save_study_stale_time_is_bounded_by_renewal():
    rows = C.save_study(renew_options=(600.0,), n_objects=20, runs=3)
    assert rows[0]["stale_max_s"] <= 600.0 + 1
    assert rows[0]["renewal_msg_per_s"] == pytest.approx(2 * 20 / 600)


def test_traffic_scales_with_object_count():
    sweep = C.increment_sweep(seeds=2)
    t = C.traffic(sweep)
    for k in ("fine (0.1 K, 10 W)",):
        assert t[k]["2000"]["unconfirmed_msg_per_s"] == pytest.approx(
            t[k]["150"]["unconfirmed_msg_per_s"] * 2000 / 150)
        assert t[k]["500"]["confirmed_msg_per_s"] == pytest.approx(2 * t[k]["500"]["unconfirmed_msg_per_s"])
