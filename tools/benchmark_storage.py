"""Đánh giá hiệu năng lưu trữ InfluxDB: thông lượng ghi theo kích thước batch + thời gian truy vấn.

    python -m tools.benchmark_storage --points 5000

Dùng bucket tạm `bench_tmp` (tự xóa khi xong). Kết quả: output/benchmark_storage.csv
"""
from __future__ import annotations

import argparse
import random
import time

import pandas as pd
from influxdb_client import BucketRetentionRules, InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

import config

BUCKET = "bench_tmp"


def make_points(n: int, t0_ms: int, tag: str):
    return [Point("bench").tag("device_id", f"bench-{i % 5}").tag("run", tag)
            .field("temperature", 25 + random.random()).field("humidity", 50 + random.random())
            .field("distance_cm", 100 + random.random()).field("light_lux", 500 + random.random())
            .field("seq", i).time(t0_ms + i * 10, WritePrecision.MS) for i in range(n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--points", type=int, default=5000)
    ap.add_argument("--batches", default="1,10,100,1000,5000")
    args = ap.parse_args()

    client = InfluxDBClient(url=config.INFLUX_URL, token=config.INFLUX_TOKEN, org=config.INFLUX_ORG,
                            timeout=60000)
    bapi = client.buckets_api()
    old = bapi.find_bucket_by_name(BUCKET)
    if old:
        bapi.delete_bucket(old)
    org_id = client.organizations_api().find_organizations(org=config.INFLUX_ORG)[0].id
    bucket = bapi.create_bucket(bucket_name=BUCKET, org_id=org_id,
                                retention_rules=BucketRetentionRules(type="expire", every_seconds=3600))
    w = client.write_api(write_options=SYNCHRONOUS)
    rows = []
    try:
        base = int(time.time() * 1000) - 3_000_000
        for k, bs in enumerate(int(x) for x in args.batches.split(",")):
            n = min(args.points, 500) if bs == 1 else args.points   # batch=1 rất chậm -> 500 điểm
            pts = make_points(n, base + k * 100_000, f"b{bs}")
            t0 = time.perf_counter()
            for i in range(0, n, bs):
                w.write(bucket=BUCKET, record=pts[i:i + bs])
            dt = time.perf_counter() - t0
            rows.append({"batch_size": bs, "points": n, "seconds": round(dt, 3),
                         "points_per_s": round(n / dt, 1), "ms_per_request": round(1000 * dt / -(-n // bs), 2)})
            print(f"batch={bs:5d}: {n} điểm trong {dt:.2f}s -> {n / dt:,.0f} điểm/s")

        q = client.query_api()
        for label, flux in [
            ("Đọc thô 1 thiết bị", f'from(bucket:"{BUCKET}") |> range(start:-1h) '
                                   f'|> filter(fn:(r)=>r.device_id=="bench-0")'),
            ("Aggregate mean 10s", f'from(bucket:"{BUCKET}") |> range(start:-1h) '
                                   f'|> aggregateWindow(every:10s, fn:mean)'),
            ("Pivot toàn bộ", f'from(bucket:"{BUCKET}") |> range(start:-1h) '
                              f'|> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")'),
        ]:
            t0 = time.perf_counter()
            tables = q.query(flux)
            nrec = sum(len(t.records) for t in tables)
            dt = (time.perf_counter() - t0) * 1000
            rows.append({"query": label, "records": nrec, "ms": round(dt, 1)})
            print(f"Query '{label}': {nrec} bản ghi trong {dt:.0f} ms")
    finally:
        bapi.delete_bucket(bucket)
        client.close()
    pd.DataFrame(rows).to_csv(config.OUTPUT_DIR / "benchmark_storage.csv", index=False, encoding="utf-8-sig")
    print("Đã lưu output/benchmark_storage.csv")


if __name__ == "__main__":
    main()
