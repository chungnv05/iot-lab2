"""Cấu hình chung, đọc từ file .env (xem .env.example)."""
import os
import sys
from pathlib import Path

# Windows: tránh UnicodeEncodeError khi in tiếng Việt ra console/file log (cp1252)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


# ---------- MQTT ----------
MQTT_HOST = _get("MQTT_HOST", "broker.hivemq.com")
MQTT_PORT = int(_get("MQTT_PORT", "1883"))
MQTT_USERNAME = _get("MQTT_USERNAME") or None
MQTT_PASSWORD = _get("MQTT_PASSWORD") or None
MQTT_GROUP = _get("MQTT_GROUP", "nhom15")
# '+' = mọi device_id trong nhóm
MQTT_TOPIC_TELEMETRY = f"ptit/iot/{MQTT_GROUP}/+/telemetry"
MQTT_TOPIC_STATUS = f"ptit/iot/{MQTT_GROUP}/+/status"
MQTT_TOPIC_PONG = f"ptit/iot/{MQTT_GROUP}/+/pong"
PING_INTERVAL_S = 10   # gateway gửi ping đo RTT mỗi 10 s

# ---------- InfluxDB ----------
INFLUX_URL = _get("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = _get("INFLUX_TOKEN")
INFLUX_ORG = _get("INFLUX_ORG", "ptit")
BUCKET_RAW = _get("BUCKET_RAW", "iot_raw")
BUCKET_PROCESSED = _get("BUCKET_PROCESSED", "iot_processed")
RETENTION_RAW_DAYS = int(_get("RETENTION_RAW_DAYS", "7"))
RETENTION_PROCESSED_DAYS = int(_get("RETENTION_PROCESSED_DAYS", "30"))

# ---------- Tên measurement ----------
M_RAW = "sensor_raw"            # dữ liệu thô đã validate
M_REJECTED = "rejected"         # bản tin bị loại (lý do)
M_QUALITY = "quality_event"     # sự kiện mất gói / trùng / khởi động lại
M_PIPELINE = "pipeline_metrics" # thời gian ghi DB, độ trễ end-to-end
M_PROCESSED = "sensor_processed"

SENSOR_FIELDS = ["temperature", "humidity", "distance_cm", "light_lux"]

# Miền đo vật lý hợp lệ (theo datasheet / miền mô phỏng Wokwi)
VALID_RANGES = {
    "temperature": (-40.0, 80.0),   # DHT22
    "humidity": (0.0, 100.0),       # DHT22
    "distance_cm": (2.0, 400.0),    # HC-SR04
    "light_lux": (0.0, 100000.0),   # LDR
    "rssi": (-120.0, 0.0),
}

LOG_DIR = ROOT / "logs"
OUTPUT_DIR = ROOT / "output"
LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
