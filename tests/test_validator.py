import json

from gateway.validator import SequenceTracker, validate

NOW = 1_791_350_475_000


def msg(**kw):
    base = {"device_id": "esp32-01", "seq": 1, "ts": NOW - 120, "uptime_s": 10,
            "temperature": 25.0, "humidity": 50.0, "distance_cm": 100.0, "light_lux": 500.0, "rssi": -70}
    base.update(kw)
    return json.dumps(base).encode()


def test_valid():
    r = validate(msg(), NOW, "esp32-01")
    assert r.ok and r.record["ts_source"] == "device" and not r.warnings


def test_bad_json_and_missing_seq():
    assert validate(b"{bad", NOW).errors == ["invalid_json"]
    assert "bad_seq" in validate(msg(seq=None), NOW).errors
    assert "device_id_topic_mismatch" in validate(msg(), NOW, "other").errors


def test_out_of_range_field_dropped():
    r = validate(msg(temperature=999), NOW)
    assert r.ok and r.record["temperature"] is None and r.warnings


def test_null_and_wrong_type():
    r = validate(msg(humidity=None, light_lux="abc"), NOW)
    assert r.ok and r.record["humidity"] is None and r.record["light_lux"] is None


def test_all_invalid_rejected():
    r = validate(msg(temperature=None, humidity=None, distance_cm=None, light_lux=None), NOW)
    assert not r.ok and "no_valid_sensor_value" in r.errors


def test_bad_ts_replaced():
    r = validate(msg(ts=0), NOW)
    assert r.ok and r.record["ts_ms"] == NOW and r.record["ts_source"] == "gateway"


def test_sequence_tracker():
    t = SequenceTracker()
    assert t.check("d", 1, 5) == ("first", 0)
    assert t.check("d", 2, 10) == ("ok", 0)
    assert t.check("d", 2, 10) == ("duplicate", 0)
    assert t.check("d", 6, 30) == ("gap", 3)
    assert t.check("d", 1, 2) == ("restart", 0)      # seq & uptime quay về
    assert t.check("d", 2, 7) == ("ok", 0)
