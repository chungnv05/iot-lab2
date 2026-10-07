"""Gateway thu thập dữ liệu: MQTT subscribe -> validate -> phát hiện trùng/mất gói -> ghi InfluxDB.

Chạy:
    python -m gateway.collector            # ghi vào InfluxDB
    python -m gateway.collector --dry-run  # chưa có InfluxDB: ghi ra logs/dryrun_points.jsonl
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from collections import Counter
from datetime import datetime

import paho.mqtt.client as mqtt

import config
from gateway.storage import InfluxStorage, JsonlStorage, make_event_point, make_raw_point
from gateway.validator import SequenceTracker, validate


def now_ms() -> int:
    return int(time.time() * 1000)


class Collector:
    def __init__(self, storage, verbose: bool = True):
        self.storage = storage
        self.tracker = SequenceTracker()
        self.stats = Counter()
        self.verbose = verbose
        self.rejected_log = config.LOG_DIR / "rejected.jsonl"
        self.last_rtt: dict[str, float] = {}     # RTT ping/pong gần nhất theo thiết bị
        self.ping_no = 0

    # ------------------------------------------------------------------
    def send_pings(self, client):
        """Gửi ping tới mọi thiết bị đã thấy; thiết bị trả pong -> đo RTT (không phụ thuộc đồng hồ thiết bị)."""
        for dev in list(self.tracker.last_seq):
            self.ping_no += 1
            body = json.dumps({"n": self.ping_no, "t": now_ms()})
            client.publish(f"ptit/iot/{config.MQTT_GROUP}/{dev}/ping", body, qos=0)

    def handle_pong(self, topic: str, payload: bytes):
        dev = topic.split("/")[3]
        try:
            t = int(json.loads(payload)["t"])
        except (ValueError, KeyError, TypeError):
            return
        rtt = now_ms() - t
        if not 0 <= rtt < 60000:
            return
        self.last_rtt[dev] = float(rtt)
        pt = make_event_point(config.M_PIPELINE, dev, "rtt", now_ms(), rtt_ms=float(rtt))
        self.storage.write([pt])
        self._log(f"PONG   {dev} RTT={rtt} ms")

    # ------------------------------------------------------------------
    def handle_telemetry(self, topic: str, payload: bytes, recv_ms: int | None = None):
        recv_ms = recv_ms or now_ms()
        t0 = time.perf_counter()
        self.stats["received"] += 1
        parts = topic.split("/")
        topic_device = parts[3] if len(parts) >= 5 else None

        res = validate(payload, recv_ms, topic_device)
        if not res.ok:
            self.stats["rejected"] += 1
            reason = ",".join(res.errors)
            with self.rejected_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"recv_ms": recv_ms, "topic": topic, "reason": reason,
                                     "payload": payload.decode("utf-8", "replace")}) + "\n")
            pt = make_event_point(config.M_REJECTED, topic_device, reason, recv_ms, count=1)
            self.storage.write([pt])
            self._log(f"REJECT {topic_device}: {reason}")
            return

        rec = res.record
        status, lost = self.tracker.check(rec["device_id"], rec["seq"], rec["uptime_s"])
        if status == "duplicate":
            self.stats["duplicate"] += 1
            pt = make_event_point(config.M_QUALITY, rec["device_id"], "duplicate", recv_ms,
                                  seq=rec["seq"], count=1)
            self.storage.write([pt])
            self._log(f"DUP    {rec['device_id']} seq={rec['seq']} -> bỏ qua")
            return

        points = []
        if status in ("gap", "restart", "out_of_order"):
            self.stats[status] += 1
            self.stats["lost_packets"] += lost
            points.append(make_event_point(config.M_QUALITY, rec["device_id"], status, recv_ms,
                                           seq=rec["seq"], lost=lost, count=1))
            self._log(f"{status.upper():6} {rec['device_id']} seq={rec['seq']} lost={lost}")
        field_warn = [w for w in res.warnings if w != "ts_replaced_by_gateway"]
        if field_warn:
            self.stats["field_warnings"] += len(field_warn)
            points.append(make_event_point(config.M_QUALITY, rec["device_id"], "field_dropped",
                                           recv_ms, detail=";".join(field_warn), count=len(field_warn)))

        proc_ms = (time.perf_counter() - t0) * 1000
        points.insert(0, make_raw_point(rec, recv_ms, proc_ms))
        write_ms = self.storage.write(points)
        self.stats["stored"] += 1

        # Độ trễ end-to-end = mạng (thiết bị -> broker -> gateway) + xử lý + ghi DB.
        # Chặng mạng ước lượng = RTT/2 (ping/pong). Nếu chưa có RTT mà đồng hồ thiết bị tin cậy
        # thì dùng (thời điểm nhận - ts thiết bị).
        if write_ms >= 0:
            clock_net = (recv_ms - rec["ts_ms"]) if rec["ts_source"] == "device" else None
            rtt = self.last_rtt.get(rec["device_id"])
            net = rtt / 2 if rtt is not None else clock_net
            e2e = (net + proc_ms + write_ms) if net is not None else None
            mp = make_event_point(config.M_PIPELINE, rec["device_id"], "write", now_ms(),
                                  seq=rec["seq"], write_ms=float(write_ms), proc_ms=float(proc_ms),
                                  net_ms=float(net) if net is not None else None,
                                  clock_net_ms=float(clock_net) if clock_net is not None else None,
                                  clock_skew_ms=float(rec["clock_skew_ms"]) if "clock_skew_ms" in rec else None,
                                  e2e_ms=float(e2e) if e2e is not None else None)
            self.storage.write([mp])
            lat = f"net≈{net:.0f}ms e2e={e2e:.1f}ms" if e2e is not None else "chờ RTT"
        else:
            lat = "buffered"
        vals = " ".join(f"{k}={rec[k]}" for k in config.SENSOR_FIELDS)
        shown = [w for w in res.warnings if w != "ts_replaced_by_gateway"]
        warn = f" WARN[{';'.join(shown)}]" if shown else ""
        self._log(f"OK     {rec['device_id']} seq={rec['seq']} {vals} | {lat}{warn}")

    def handle_status(self, topic: str, payload: bytes):
        device = topic.split("/")[3]
        state = payload.decode("utf-8", "replace")
        pt = make_event_point(config.M_QUALITY, device, f"status_{state}", now_ms(), count=1)
        self.storage.write([pt])
        self._log(f"STATUS {device} -> {state}")

    def _log(self, msg: str):
        if self.verbose:
            print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="MQTT -> InfluxDB collector")
    ap.add_argument("--dry-run", action="store_true", help="không dùng InfluxDB, ghi ra file")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        storage = JsonlStorage()
        print(f"[DRY-RUN] ghi line protocol vào {storage.path}")
    else:
        if not config.INFLUX_TOKEN:
            sys.exit("Thiếu INFLUX_TOKEN trong .env - chạy: python -m scripts.setup_influxdb")
        storage = InfluxStorage()
        if not storage.ping():
            print(f"[WARN] Không kết nối được InfluxDB tại {config.INFLUX_URL}; dữ liệu sẽ được đệm.")

    col = Collector(storage, verbose=not args.quiet)

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            print(f"[ERR] MQTT connect failed: {reason_code}")
            return
        print(f"[MQTT] connected {config.MQTT_HOST}:{config.MQTT_PORT}")
        client.subscribe([(config.MQTT_TOPIC_TELEMETRY, 1), (config.MQTT_TOPIC_STATUS, 1),
                          (config.MQTT_TOPIC_PONG, 0)])
        print(f"[MQTT] subscribed {config.MQTT_TOPIC_TELEMETRY}")

    def on_disconnect(client, userdata, flags, reason_code, properties):
        print(f"[MQTT] disconnected ({reason_code}) - paho sẽ tự kết nối lại")

    def on_message(client, userdata, msg):
        try:
            if msg.topic.endswith("/telemetry"):
                col.handle_telemetry(msg.topic, msg.payload)
            elif msg.topic.endswith("/status"):
                col.handle_status(msg.topic, msg.payload)
            elif msg.topic.endswith("/pong"):
                col.handle_pong(msg.topic, msg.payload)
        except Exception as exc:  # không để 1 bản tin lỗi làm chết gateway
            print(f"[ERR] {exc!r}")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"gateway-{config.MQTT_GROUP}-{int(time.time())}")
    if config.MQTT_USERNAME:
        client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)
    if config.MQTT_PORT == 8883:
        client.tls_set()
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    def stop(*_):
        print("\n[STOP] Thống kê:", dict(col.stats))
        client.disconnect()
        storage.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    def pinger():
        while True:
            time.sleep(config.PING_INTERVAL_S)
            if client.is_connected():
                try:
                    col.send_pings(client)
                except Exception as exc:
                    print(f"[ERR] ping {exc!r}")

    threading.Thread(target=pinger, daemon=True).start()
    client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
