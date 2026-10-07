"""Thiết bị giả lập (khi không chạy Wokwi, hoặc để thử tải nhiều thiết bị).

Gửi đúng định dạng JSON như firmware ESP32, có thể chèn lỗi để thử validate/tiền xử lý.

    python -m tools.simulator                       # 1 thiết bị, 5 s/bản tin
    python -m tools.simulator --devices 3 --interval 1 --fault-rate 0.1
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time

import paho.mqtt.client as mqtt

import config


class FakeDevice:
    def __init__(self, device_id: str, fault_rate: float):
        self.id = device_id
        self.seq = 0
        self.start = time.time()
        self.fault_rate = fault_rate
        self.phase = random.random() * 6

    def reading(self) -> list[dict]:
        t = time.time()
        self.seq += 1
        day = math.sin((t - self.start) / 300 + self.phase)   # dao động chậm chu kỳ ~30 phút
        msg = {
            "device_id": self.id,
            "seq": self.seq,
            "ts": int(t * 1000),
            "uptime_s": int(t - self.start),
            "temperature": round(27 + 3 * day + random.gauss(0, 0.2), 2),
            "humidity": round(60 - 8 * day + random.gauss(0, 0.5), 2),
            "distance_cm": round(100 + 40 * math.sin((t - self.start) / 90) + random.gauss(0, 1), 2),
            "light_lux": round(max(0, 500 + 300 * day + random.gauss(0, 15)), 1),
            "rssi": random.randint(-80, -60),
        }
        out = [msg]
        r = random.random()
        f = self.fault_rate
        if r < f * 0.35:
            msg["temperature"] += random.choice([-15, 15])          # spike
        elif r < f * 0.6:
            msg["humidity"] = None                                    # thiếu giá trị
        elif r < f * 0.75:
            msg["temperature"] = 999                                  # vô lý
        elif r < f * 0.85:
            out.append(dict(msg))                                     # trùng gói
        elif r < f * 0.95:
            self.seq += random.randint(1, 3)                          # mất gói
        elif r < f:
            return [{"raw": "not-json"}]                              # bản tin hỏng
        return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", type=int, default=1)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--fault-rate", type=float, default=0.1)
    ap.add_argument("--count", type=int, default=0, help="số chu kỳ (0 = chạy mãi)")
    ap.add_argument("--prefix", default="sim")
    args = ap.parse_args()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{random.randint(0, 1 << 30)}")
    if config.MQTT_USERNAME:
        client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)
    if config.MQTT_PORT == 8883:
        client.tls_set()
    def on_message(c, userdata, msg):   # trả pong giống firmware ESP32
        c.publish(msg.topic[:-len("ping")] + "pong", msg.payload)

    client.on_message = on_message
    client.on_connect = lambda c, *a: c.subscribe(f"ptit/iot/{config.MQTT_GROUP}/+/ping")
    client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
    client.loop_start()

    devices = [FakeDevice(f"{args.prefix}-{i + 1:02d}", args.fault_rate) for i in range(args.devices)]
    for d in devices:
        client.publish(f"ptit/iot/{config.MQTT_GROUP}/{d.id}/status", "online", qos=1, retain=True)
    print(f"Publishing to {config.MQTT_HOST} topic ptit/iot/{config.MQTT_GROUP}/<id>/telemetry")

    n = 0
    try:
        while args.count == 0 or n < args.count:
            for d in devices:
                topic = f"ptit/iot/{config.MQTT_GROUP}/{d.id}/telemetry"
                for m in d.reading():
                    payload = "{bad json" if "raw" in m else json.dumps(m)
                    client.publish(topic, payload, qos=1)
            n += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        for d in devices:
            client.publish(f"ptit/iot/{config.MQTT_GROUP}/{d.id}/status", "offline", qos=1, retain=True)
        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
