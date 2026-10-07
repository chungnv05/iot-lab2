"""Khởi tạo InfluxDB 2.x:
  1. Nếu InfluxDB mới cài (chưa onboard) -> tạo user/org/token tự động và ghi vào .env
  2. Tạo (hoặc cập nhật) 2 bucket với retention policy:
       iot_raw       : dữ liệu thô       - giữ RETENTION_RAW_DAYS (mặc định 7 ngày)
       iot_processed : dữ liệu đã xử lý  - giữ RETENTION_PROCESSED_DAYS (mặc định 30 ngày)

Chạy:  python -m scripts.setup_influxdb [--username admin --password admin12345]
"""
from __future__ import annotations

import argparse
import re
import sys

import requests

import config


def write_env(key: str, value: str):
    env = config.ROOT / ".env"
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    if re.search(rf"^{key}=.*$", text, flags=re.M):
        text = re.sub(rf"^{key}=.*$", f"{key}={value}", text, flags=re.M)
    else:
        text += ("" if text.endswith("\n") or not text else "\n") + f"{key}={value}\n"
    env.write_text(text, encoding="utf-8")


def onboard(username: str, password: str) -> str | None:
    r = requests.get(f"{config.INFLUX_URL}/api/v2/setup", timeout=5)
    r.raise_for_status()
    if not r.json().get("allowed"):
        return None  # đã onboard trước đó
    body = {
        "username": username,
        "password": password,
        "org": config.INFLUX_ORG,
        "bucket": config.BUCKET_RAW,
        "retentionPeriodSeconds": config.RETENTION_RAW_DAYS * 86400,
    }
    r = requests.post(f"{config.INFLUX_URL}/api/v2/setup", json=body, timeout=10)
    r.raise_for_status()
    token = r.json()["auth"]["token"]
    write_env("INFLUX_TOKEN", token)
    write_env("INFLUX_ORG", config.INFLUX_ORG)
    print(f"[OK] Onboard xong: user={username} org={config.INFLUX_ORG}; token đã lưu vào .env")
    return token


def ensure_buckets(token: str):
    from influxdb_client import BucketRetentionRules, InfluxDBClient

    with InfluxDBClient(url=config.INFLUX_URL, token=token, org=config.INFLUX_ORG) as client:
        api = client.buckets_api()
        org_id = client.organizations_api().find_organizations(org=config.INFLUX_ORG)[0].id
        for name, days in [(config.BUCKET_RAW, config.RETENTION_RAW_DAYS),
                           (config.BUCKET_PROCESSED, config.RETENTION_PROCESSED_DAYS)]:
            rule = BucketRetentionRules(type="expire", every_seconds=days * 86400)
            b = api.find_bucket_by_name(name)
            if b is None:
                api.create_bucket(bucket_name=name, retention_rules=rule, org_id=org_id,
                                  description=f"Bai 2 IoT - retention {days} ngay")
                print(f"[OK] Tạo bucket {name} (retention {days} ngày)")
            else:
                b.retention_rules = [rule]
                api.update_bucket(b)
                print(f"[OK] Bucket {name} đã có -> cập nhật retention {days} ngày")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", default="admin")
    ap.add_argument("--password", default="admin12345")
    args = ap.parse_args()

    try:
        token = onboard(args.username, args.password)
    except requests.RequestException as exc:
        sys.exit(f"[ERR] Không kết nối được {config.INFLUX_URL}. InfluxDB (influxd.exe) đã chạy chưa? {exc}")

    token = token or config.INFLUX_TOKEN
    if not token:
        sys.exit("[ERR] InfluxDB đã được onboard từ trước. Vào http://localhost:8086 -> "
                 "Load Data -> API Tokens, tạo All Access token rồi dán vào INFLUX_TOKEN trong .env")
    ensure_buckets(token)
    print("[DONE] InfluxDB sẵn sàng.")


if __name__ == "__main__":
    main()
