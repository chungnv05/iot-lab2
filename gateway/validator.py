"""Kiểm tra tính hợp lệ của bản tin telemetry nhận từ MQTT.

Chính sách:
  * Lỗi cấu trúc (không phải JSON, thiếu device_id/seq, sai kiểu) -> LOẠI cả bản tin.
  * Một trường cảm biến nằm ngoài miền đo vật lý -> bỏ riêng trường đó (None), giữ bản tin.
  * Không còn trường cảm biến nào hợp lệ -> LOẠI.
  * ts thiết bị không hợp lệ (0, NTP lỗi, lệch > 30 s) -> dùng thời gian nhận của gateway.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from config import SENSOR_FIELDS, VALID_RANGES

DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
# Lệch đồng hồ thiết bị/gateway quá 30 s -> coi ts thiết bị không tin cậy (vd. mô phỏng Wokwi chạy
# chậm hơn thời gian thực làm đồng hồ ESP32 trôi) -> gắn thời gian nhận của gateway.
MAX_CLOCK_SKEW_MS = 30 * 1000


@dataclass
class ValidationResult:
    ok: bool
    record: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)      # lý do loại bản tin
    warnings: list[str] = field(default_factory=list)    # trường bị bỏ / sửa


def _as_number(v: Any) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    raise TypeError


def validate(payload: bytes | str, recv_ms: int, topic_device: str | None = None) -> ValidationResult:
    res = ValidationResult(ok=False)
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        res.errors.append("invalid_json")
        return res
    if not isinstance(data, dict):
        res.errors.append("not_object")
        return res

    device_id = data.get("device_id")
    if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
        res.errors.append("bad_device_id")
    elif topic_device and topic_device != device_id:
        res.errors.append("device_id_topic_mismatch")

    seq = data.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        res.errors.append("bad_seq")

    uptime = data.get("uptime_s", 0)
    if not isinstance(uptime, int) or isinstance(uptime, bool) or uptime < 0:
        res.warnings.append("bad_uptime")
        uptime = None

    if res.errors:
        return res

    rec: dict[str, Any] = {"device_id": device_id, "seq": seq, "uptime_s": uptime}

    # --- timestamp ---
    ts = data.get("ts")
    if isinstance(ts, int) and not isinstance(ts, bool) and abs(recv_ms - ts) <= MAX_CLOCK_SKEW_MS:
        rec["ts_ms"] = ts
        rec["ts_source"] = "device"
    else:
        rec["ts_ms"] = recv_ms
        rec["ts_source"] = "gateway"
        if isinstance(ts, int) and ts > 0:
            rec["clock_skew_ms"] = recv_ms - ts
        res.warnings.append("ts_replaced_by_gateway")

    # --- các trường số ---
    valid_sensor = 0
    for name in SENSOR_FIELDS + ["rssi"]:
        try:
            val = _as_number(data.get(name))
        except TypeError:
            res.warnings.append(f"{name}:wrong_type")
            val = None
        if val is not None:
            lo, hi = VALID_RANGES[name]
            if not lo <= val <= hi:
                res.warnings.append(f"{name}:out_of_range({val})")
                val = None
        rec[name] = val
        if name in SENSOR_FIELDS and val is not None:
            valid_sensor += 1

    if valid_sensor == 0:
        res.errors.append("no_valid_sensor_value")
        return res

    res.ok = True
    res.record = rec
    return res


class SequenceTracker:
    """Theo dõi seq theo từng thiết bị: phát hiện trùng gói, mất gói, khởi động lại."""

    def __init__(self, window: int = 2000):
        self.window = window
        self.last_seq: dict[str, int] = {}
        self.seen: dict[str, set[int]] = {}
        self.last_uptime: dict[str, int | None] = {}

    def check(self, device_id: str, seq: int, uptime_s: int | None) -> tuple[str, int]:
        """Trả về (trạng thái, số gói mất). Trạng thái: first|ok|duplicate|gap|restart|out_of_order."""
        last = self.last_seq.get(device_id)
        seen = self.seen.setdefault(device_id, set())
        prev_up = self.last_uptime.get(device_id)

        if last is None:
            status, lost = "first", 0
        elif seq < last and uptime_s is not None and prev_up is not None and uptime_s < prev_up:
            # seq quay về nhỏ + uptime giảm -> thiết bị khởi động lại
            seen.clear()
            status, lost = "restart", 0
        elif seq in seen:
            return "duplicate", 0
        elif seq < last:
            status, lost = "out_of_order", 0
        elif seq == last + 1:
            status, lost = "ok", 0
        else:
            status, lost = "gap", seq - last - 1

        seen.add(seq)
        if len(seen) > self.window:
            for s in sorted(seen)[: len(seen) - self.window]:
                seen.discard(s)
        if status != "out_of_order":
            self.last_seq[device_id] = seq
            self.last_uptime[device_id] = uptime_s
        return status, lost
