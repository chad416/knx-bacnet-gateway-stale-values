"""Export rules, instance mapping, services, priority array, COV and subscriptions."""
import pytest

from bacgw import knx
from bacgw.server import (AV, BV, DEVICE, MAX_SUBSCRIPTIONS, NO_SPACE, UNKNOWN_OBJECT, UNKNOWN_PROPERTY,
                          WRITE_ACCESS_DENIED, BacnetServer, KnxObject)


def server(objs, limit=150):
    return BacnetServer(1234, "a", objs, limit)


# ------------------------------------------------------------ instance number
@pytest.mark.parametrize("ga,inst", [("0/0/1", 1), ("1/1/1", 2305), ("31/7/255", 65535), ("3/2/14", 6670)])
def test_instance_is_group_address_as_16_bit_number(ga, inst):
    assert knx.ga_to_instance(ga) == inst
    assert knx.instance_to_ga(inst) == ga


@pytest.mark.parametrize("bad", ["32/0/0", "0/8/0", "0/0/256", "1/2", "a/b/c"])
def test_group_address_range_checked(bad):
    with pytest.raises(ValueError):
        knx.ga_to_instance(bad)


def test_every_instance_maps_back():
    for inst in range(0, 65536, 97):
        assert knx.ga_to_instance(knx.instance_to_ga(inst)) == inst


# ------------------------------------------------------------ export rules
def test_binary_becomes_bv_numeric_becomes_av_others_not_exported():
    s = server([KnxObject("sw", "1/0/1", "1.001"), KnxObject("t", "3/0/1", "9.001"),
                KnxObject("p", "4/0/1", "14.056"), KnxObject("e", "4/0/2", "13.010"),
                KnxObject("b", "2/0/1", "5.001"), KnxObject("d", "5/0/1", "11.001"),
                KnxObject("txt", "5/0/2", "16.000"), KnxObject("time", "5/0/3", "10.001")])
    ol = s.object_list()
    assert ol[0] == (DEVICE, 1234)
    assert (BV, knx.ga_to_instance("1/0/1")) in ol
    for ga in ("3/0/1", "4/0/1", "4/0/2", "2/0/1"):
        assert (AV, knx.ga_to_instance(ga)) in ol
    assert len(ol) == 1 + 5


def test_export_flag_required():
    s = server([KnxObject("t", "3/0/1", "9.001", export=False)])
    assert s.object_list() == [(DEVICE, 1234)]
    assert s.read_property((AV, 6145), "present-value") == UNKNOWN_OBJECT


def test_export_limit_keeps_lowest_instances():
    objs = [KnxObject(f"o{i}", knx.instance_to_ga(i + 1), "1.001") for i in range(160)]
    s = server(objs, limit=150)
    keys = [k for k in s.object_list() if k[0] != DEVICE]
    assert len(keys) == 150
    assert max(k[1] for k in keys) == 150
    assert len(server(objs, limit=None).object_list()) == 161


def test_object_disappears_after_config_change_and_subscription_dropped():
    o = KnxObject("t", "3/0/1", "9.001")
    s = server([o])
    key = (AV, 6145)
    assert s.subscribe_cov(0, "c", 1, key)[0] == "ack"
    o.export = False
    s.apply_config()
    assert s.active_subscriptions(1) == 0
    assert s.read_property(key, "present-value") == UNKNOWN_OBJECT


def test_group_address_change_moves_the_instance():
    o = KnxObject("t", "3/0/1", "9.001")
    s = server([o])
    s.knx_telegram(0, "3/0/1", 21.5)
    o.ga = "3/1/1"
    s.knx_value["3/1/1"] = 21.5
    s.apply_config()
    assert s.read_property((AV, 6145), "present-value") == UNKNOWN_OBJECT
    assert s.read_property((AV, 6401), "present-value") == 21.5
    assert s.read_property((AV, 6401), "object-name") == "t"


# ------------------------------------------------------------ services and errors
def test_who_is_range_and_i_am():
    s = server([])
    assert s.who_is() == (1234, "a")
    assert s.who_is(1000, 2000) == (1234, "a")
    assert s.who_is(2000, 3000) is None


def test_device_instance_range():
    with pytest.raises(ValueError):
        BacnetServer(4194303, "a", [])


def test_read_errors():
    s = server([KnxObject("sw", "1/0/1", "1.001")])
    assert s.read_property((AV, 999), "present-value") == UNKNOWN_OBJECT
    assert s.read_property((BV, 2049), "cov-increment") == UNKNOWN_PROPERTY
    assert s.read_property((BV, 2049), "no-such-property") == UNKNOWN_PROPERTY
    assert str(UNKNOWN_OBJECT) == "OBJECT/UNKNOWN_OBJECT"


def test_read_property_multiple_counts_once():
    s = server([KnxObject("sw", "1/0/1", "1.001")])
    r = s.read_property_multiple([((DEVICE, 1234), "object-list"), ((BV, 2049), "present-value")])
    assert r[0][1] == (BV, 2049) and r[1] == 0.0
    assert s.count["rpm"] == 1 and s.count["rp"] == 0


def test_write_errors():
    s = server([KnxObject("t", "3/0/1", "9.001")])
    assert s.write_property(0, (AV, 1), "present-value", 1.0, 8)[0] == UNKNOWN_OBJECT
    assert s.write_property(0, (AV, 6145), "priority-array", 1.0, 8)[0] == WRITE_ACCESS_DENIED
    with pytest.raises(ValueError):
        s.write_property(0, (AV, 6145), "present-value", 1.0, 17)


# ------------------------------------------------------------ priority array
def test_priority_array_highest_non_null_wins_and_relinquish_default():
    s = server([KnxObject("sp", "3/0/1", "9.001")])
    key = (AV, 6145)
    s.knx_telegram(0, "3/0/1", 21.0)
    assert s.read_property(key, "present-value") == 21.0
    s.write_property(1, key, "present-value", 24.0, 8)
    s.write_property(2, key, "present-value", 18.0, 16)
    assert s.read_property(key, "present-value") == 24.0
    s.write_property(3, key, "present-value", 19.0, 5)
    assert s.read_property(key, "present-value") == 19.0
    s.write_property(4, key, "present-value", None, 5)
    s.write_property(5, key, "present-value", None, 8)
    assert s.read_property(key, "present-value") == 18.0
    pa = s.read_property(key, "priority-array")
    assert len(pa) == 16 and pa[15] == 18.0 and pa[7] is None


def test_knx_change_is_masked_while_a_command_is_held():
    s = server([KnxObject("sp", "3/0/1", "9.001")])
    key = (AV, 6145)
    s.knx_telegram(0, "3/0/1", 21.0)
    _, telegram, _ = s.write_property(10, key, "present-value", 24.0, 8)
    assert telegram == (10, "3/0/1", 24.0)              # the gateway writes the KNX group address
    s.knx_telegram(20, "3/0/1", 22.5)                   # room panel
    assert s.read_property(key, "present-value") == 24.0
    assert s.read_property(key, "relinquish-default") == 22.5
    s.write_property(30, key, "present-value", None, 8)
    assert s.read_property(key, "present-value") == 22.5


# ------------------------------------------------------------ COV
def test_default_cov_increment_is_one():
    assert KnxObject("t", "3/0/1", "9.001").cov_increment == 1.0


def test_cov_increment_rule_analog():
    s = server([KnxObject("t", "3/0/1", "9.001", cov_increment=0.5)])
    key = (AV, 6145)
    s.knx_telegram(0, "3/0/1", 20.0)
    res, notes = s.subscribe_cov(1, "c", 1, key)
    assert res == "ack" and notes[0].value == 20.0          # initial notification
    assert s.knx_telegram(2, "3/0/1", 20.4) == []           # 0.4 < 0.5
    n = s.knx_telegram(3, "3/0/1", 20.5)                    # >= increment from last report
    assert len(n) == 1 and n[0].value == 20.5
    assert s.knx_telegram(4, "3/0/1", 20.1) == []           # 0.4 below the last report
    assert len(s.knx_telegram(5, "3/0/1", 20.0)) == 1


def test_default_increment_hides_tenths():
    s = server([KnxObject("t", "3/0/1", "9.001")])
    key = (AV, 6145)
    s.knx_telegram(0, "3/0/1", 21.0)
    s.subscribe_cov(1, "c", 1, key)
    sent = [s.knx_telegram(2 + i, "3/0/1", round(21.0 + 0.1 * (i + 1), 2)) for i in range(9)]
    assert all(x == [] for x in sent)                        # 21.1 .. 21.9 never reported
    assert s.read_property(key, "present-value") == 21.9     # a direct read is right


def test_binary_reports_every_change():
    s = server([KnxObject("sw", "1/0/1", "1.001")])
    key = (BV, 2049)
    s.subscribe_cov(0, "c", 1, key, confirmed=True)
    n = s.knx_telegram(1, "1/0/1", 1.0)
    assert len(n) == 1 and n[0].confirmed
    assert s.knx_telegram(2, "1/0/1", 1.0) == []
    assert len(s.knx_telegram(3, "1/0/1", 0.0)) == 1
    assert s.count["cov_confirmed"] == 3


def test_subscription_lifetime_expires_and_zero_is_indefinite():
    s = server([KnxObject("sw", "1/0/1", "1.001"), KnxObject("sw2", "1/0/2", "1.001")])
    s.subscribe_cov(0, "c", 1, (BV, 2049), lifetime=100)
    s.subscribe_cov(0, "c", 1, (BV, 2050), lifetime=0)
    assert len(s.knx_telegram(99, "1/0/1", 1.0)) == 1
    assert s.knx_telegram(101, "1/0/1", 0.0) == []
    assert len(s.knx_telegram(10_000_000, "1/0/2", 1.0)) == 1


def test_renewal_refreshes_and_sends_current_value():
    s = server([KnxObject("t", "3/0/1", "9.001")])
    key = (AV, 6145)
    s.knx_telegram(0, "3/0/1", 21.0)
    s.subscribe_cov(0, "c", 1, key, lifetime=100)
    s.knx_telegram(50, "3/0/1", 21.4)
    res, notes = s.subscribe_cov(90, "c", 1, key, lifetime=100)
    assert notes[0].value == 21.4
    assert len(s.subs) == 1
    assert s.active_subscriptions(150) == 1 and s.active_subscriptions(191) == 0


def test_subscribe_unknown_object_and_no_space():
    objs = [KnxObject(f"o{i}", knx.instance_to_ga(i + 1), "1.001") for i in range(MAX_SUBSCRIPTIONS + 1)]
    s = server(objs, limit=None)
    assert s.subscribe_cov(0, "c", 1, (AV, 1))[0] == UNKNOWN_OBJECT
    for i in range(MAX_SUBSCRIPTIONS):
        assert s.subscribe_cov(0, "c", 1, (BV, i + 1))[0] == "ack"
    assert s.subscribe_cov(0, "c", 1, (BV, MAX_SUBSCRIPTIONS + 1))[0] == NO_SPACE


def test_cancel_subscription():
    s = server([KnxObject("sw", "1/0/1", "1.001")])
    s.subscribe_cov(0, "c", 1, (BV, 2049))
    s.cancel_cov("c", 1, (BV, 2049))
    assert s.knx_telegram(1, "1/0/1", 1.0) == []


def test_saving_cov_settings_cancels_subscriptions_and_resets_priority_arrays():
    s = server([KnxObject("sp", "3/0/1", "9.001", cov_increment=0.1), KnxObject("sw", "1/0/1", "1.001")])
    sp, sw = (AV, 6145), (BV, 2049)
    s.knx_telegram(0, "3/0/1", 21.0)
    s.subscribe_cov(1, "c", 1, sp)
    s.subscribe_cov(1, "c", 1, sw)
    s.write_property(2, sp, "present-value", 24.0, 8)
    assert s.read_property(sp, "present-value") == 24.0
    s.save_cov_settings({"3/0/1": 0.5})
    assert s.active_subscriptions(3) == 0
    assert s.read_property(sp, "priority-array") == [None] * 16
    assert s.read_property(sp, "cov-increment") == 0.5
    assert s.knx_telegram(4, "1/0/1", 1.0) == []            # nobody hears it
    assert s.knx_telegram(5, "3/0/1", 23.0) == []
