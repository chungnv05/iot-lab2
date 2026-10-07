"""Xóa toàn bộ dữ liệu trong 2 bucket (giữ nguyên bucket & retention) - dùng khi muốn đo lại từ đầu.

    python -m scripts.reset_data
"""
from datetime import datetime, timezone

from influxdb_client import InfluxDBClient

import config

with InfluxDBClient(url=config.INFLUX_URL, token=config.INFLUX_TOKEN, org=config.INFLUX_ORG) as c:
    api = c.delete_api()
    for b in (config.BUCKET_RAW, config.BUCKET_PROCESSED):
        api.delete("1970-01-01T00:00:00Z", datetime.now(timezone.utc) + __import__("datetime").timedelta(days=1),
                   "", bucket=b, org=config.INFLUX_ORG)
        print(f"[OK] Đã xóa dữ liệu bucket {b}")
