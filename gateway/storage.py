"""Lớp lưu trữ: InfluxDB (thật) hoặc JSONL (chế độ --dry-run để thử khi chưa cài InfluxDB)."""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Any

import config


def make_raw_point(rec: dict[str, Any], recv_ms: int, proc_ms: float):
    """Chuyển bản ghi đã validate thành Point của InfluxDB.

    Schema measurement `sensor_raw`:
      tags   : device_id, group, ts_source
      fields : temperature, humidity, distance_cm, light_lux, rssi (float)
               seq, uptime_s (int), net_latency_ms, proc_latency_ms (float)
      time   : thời điểm đo trên thiết bị (ms)
    """
    from influxdb_client import Point, WritePrecision

    p = (
        Point(config.M_RAW)
        .tag("device_id", rec["device_id"])
        .tag("group", config.MQTT_GROUP)
        .tag("ts_source", rec["ts_source"])
        .field("seq", int(rec["seq"]))
        .time(int(rec["ts_ms"]), WritePrecision.MS)
    )
    if rec.get("uptime_s") is not None:
        p.field("uptime_s", int(rec["uptime_s"]))
    for f in config.SENSOR_FIELDS + ["rssi"]:
        if rec.get(f) is not None:
            p.field(f, float(rec[f]))
    if rec["ts_source"] == "device":
        p.field("net_latency_ms", float(recv_ms - rec["ts_ms"]))
    p.field("proc_latency_ms", float(proc_ms))
    return p


def make_event_point(measurement: str, device_id: str, kind: str, ts_ms: int, **fields):
    from influxdb_client import Point, WritePrecision

    p = Point(measurement).tag("device_id", device_id or "unknown").tag("kind", kind)
    for k, v in fields.items():
        if v is None:
            continue
        p.field(k, v if isinstance(v, (int, float, bool)) else str(v))
    if not fields:
        p.field("count", 1)
    return p.time(int(ts_ms), WritePrecision.MS)


class InfluxStorage:
    """Ghi đồng bộ (để đo được thời gian ghi). Nếu InfluxDB tạm lỗi -> đệm lại trong RAM
    và file logs/buffer.jsonl, tự ghi bù khi kết nối lại (chống mất dữ liệu)."""

    def __init__(self, max_buffer: int = 50000):
        from influxdb_client import InfluxDBClient
        from influxdb_client.client.write_api import SYNCHRONOUS

        self.client = InfluxDBClient(url=config.INFLUX_URL, token=config.INFLUX_TOKEN,
                                     org=config.INFLUX_ORG, timeout=5000)
        self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
        from influxdb_client import WritePrecision
        self.precision = WritePrecision.MS   # mọi point của collector dùng timestamp mili-giây
        self.buffer: deque[str] = deque(maxlen=max_buffer)   # line protocol
        self.buffer_file = config.LOG_DIR / "buffer.jsonl"
        self._load_buffer_file()

    def _load_buffer_file(self):
        if self.buffer_file.exists():
            for line in self.buffer_file.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.buffer.append(json.loads(line))
            self.buffer_file.unlink()

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def write(self, points: list, bucket: str | None = None) -> float:
        """Ghi danh sách point. Trả về thời gian ghi (ms) hoặc -1 nếu phải đệm."""
        bucket = bucket or config.BUCKET_RAW
        lines = [p.to_line_protocol() for p in points]
        try:
            self.flush_buffer(bucket)
            t0 = time.perf_counter()
            self.write_api.write(bucket=bucket, record=lines, write_precision=self.precision)
            return (time.perf_counter() - t0) * 1000
        except Exception as exc:  # mất kết nối DB
            self.buffer.extend(lines)
            with self.buffer_file.open("a", encoding="utf-8") as fh:
                for ln in lines:
                    fh.write(json.dumps(ln) + "\n")
            detail = getattr(exc, "message", None) or getattr(exc, "body", None) or str(exc)
            print(f"[WARN] InfluxDB lỗi ({exc.__class__.__name__}: {str(detail)[:200]}), "
                  f"đệm {len(self.buffer)} điểm")
            return -1.0

    def flush_buffer(self, bucket: str):
        if not self.buffer:
            return
        pending = list(self.buffer)
        self.write_api.write(bucket=bucket, record=pending, write_precision=self.precision)  # lỗi nếu DB vẫn chết
        self.buffer.clear()
        if self.buffer_file.exists():
            self.buffer_file.unlink()
        print(f"[INFO] Đã ghi bù {len(pending)} điểm từ bộ đệm")

    def close(self):
        self.client.close()


class JsonlStorage:
    """Chế độ dry-run: ghi điểm dữ liệu ra file JSONL khi chưa có InfluxDB.
    App Streamlit đọc được file này ở chế độ demo."""

    def __init__(self, path: Path | None = None):
        self.path = path or (config.LOG_DIR / "dryrun_points.jsonl")

    def ping(self) -> bool:
        return True

    def write(self, points: list, bucket: str | None = None) -> float:
        t0 = time.perf_counter()
        with self.path.open("a", encoding="utf-8") as fh:
            for p in points:
                fh.write(json.dumps({"bucket": bucket or config.BUCKET_RAW, "measurement": p._name,
                                     "tags": p._tags, "fields": p._fields, "time_ms": int(p._time),
                                     "lp": p.to_line_protocol()}, default=str) + "\n")
        return (time.perf_counter() - t0) * 1000

    def close(self):
        pass
