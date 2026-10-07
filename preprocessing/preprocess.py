"""Đọc dữ liệu thô từ InfluxDB theo khoảng thời gian -> tiền xử lý -> ghi measurement mới.

    python -m preprocessing.preprocess --start 1h                   # 1 giờ gần nhất
    python -m preprocessing.preprocess --start 6h --rule 1min --outlier zscore
    python -m preprocessing.preprocess --start 30m --loop 60       # chạy lặp mỗi 60 s (cho dashboard)
    python -m preprocessing.preprocess --csv output/raw_sample.csv  # chạy offline trên file CSV
    python -m preprocessing.preprocess --demo --start 1h             # dùng dữ liệu collector --dry-run

Kết quả:
  * InfluxDB bucket `iot_processed`, measurement `sensor_processed`
  * output/processed_<device>.csv, output/report_<device>.json
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

import pandas as pd

import config
from preprocessing.pipeline import run_pipeline

RAW_COLUMNS = config.SENSOR_FIELDS + ["rssi", "seq"]


def query_raw(client, start: str, stop: str | None, device: str | None) -> pd.DataFrame:
    dev = f'|> filter(fn: (r) => r.device_id == "{device}")' if device else ""
    rng = f"start: {start}" + (f", stop: {stop}" if stop else "")
    fields = " or ".join(f'r._field == "{f}"' for f in RAW_COLUMNS)
    flux = f'''
from(bucket: "{config.BUCKET_RAW}")
  |> range({rng})
  |> filter(fn: (r) => r._measurement == "{config.M_RAW}")
  {dev}
  |> filter(fn: (r) => {fields})
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> drop(columns: ["_start", "_stop", "_measurement", "group", "ts_source"])
'''
    df = client.query_api().query_data_frame(flux)
    if isinstance(df, list):
        df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
    if df.empty:
        return df
    df = df.drop(columns=[c for c in ("result", "table") if c in df])
    df["_time"] = pd.to_datetime(df["_time"], utc=True)
    return df.set_index("_time").sort_index()


def write_processed(client, df: pd.DataFrame, device: str, rule: str, method: str):
    from influxdb_client import Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS

    points = []
    for ts, row in df.iterrows():
        p = (Point(config.M_PROCESSED).tag("device_id", device).tag("rule", rule)
             .tag("outlier_method", method).time(ts.to_pydatetime(), WritePrecision.S))
        for col, val in row.items():
            if pd.isna(val):
                continue
            if isinstance(val, (bool,)) or str(df[col].dtype) == "bool":
                p.field(col, bool(val))
            elif col in ("n_samples", "n_outliers"):
                p.field(col, int(val))
            else:
                p.field(col, float(val))
        points.append(p)
    client.write_api(write_options=SYNCHRONOUS).write(bucket=config.BUCKET_PROCESSED, record=points)
    return len(points)


def process_device(raw: pd.DataFrame, device: str, args, client=None):
    df, rep = run_pipeline(raw, rule=args.rule, outlier=args.outlier,
                           outlier_window=args.outlier_window, fill_limit=args.fill_limit)
    out_csv = config.OUTPUT_DIR / f"processed_{device}.csv"
    df.to_csv(out_csv)
    raw.to_csv(config.OUTPUT_DIR / f"raw_{device}.csv")
    with (config.OUTPUT_DIR / f"report_{device}.json").open("w", encoding="utf-8") as fh:
        json.dump(rep.to_dict(), fh, indent=2, ensure_ascii=False)
    n = write_processed(client, df, device, args.rule, args.outlier) if client and len(df) else 0

    print(f"\n=== {device} | {datetime.now():%H:%M:%S} ===")
    print(f"  Bản ghi thô           : {rep.rows_in}  (trùng timestamp bỏ: {rep.duplicates_removed})")
    print(f"  Ngoài miền đo         : {rep.out_of_range}")
    print(f"  Thiếu (trước xử lý)   : {rep.missing_before}")
    print(f"  Outlier ({args.outlier:6})      : {rep.outliers}")
    print(f"  Cửa sổ {args.rule:>5}          : {rep.windows} (rỗng: {rep.empty_windows})")
    print(f"  Đã nội suy            : {rep.imputed}")
    print(f"  Bản ghi đầu ra        : {rep.rows_out}  -> {out_csv.name}"
          + (f", ghi {n} điểm vào {config.BUCKET_PROCESSED}" if client else ""))
    return df, rep


def _minutes(start: str) -> int:
    """'-1h' -> 60, '-30m' -> 30, '-2d' -> 2880 (chỉ dùng cho chế độ --demo)."""
    unit = {"m": 1, "h": 60, "d": 1440}
    try:
        return int(start.lstrip("-")[:-1]) * unit[start[-1]]
    except (KeyError, ValueError):
        return 60


def run_once(args, client):
    if args.demo:
        from app.data import DemoSource
        src, mins = DemoSource(), _minutes(args.start)
        devices = {}
        for d in src.devices(mins):
            if args.device and d != args.device:
                continue
            r = src.raw(d, mins)
            devices[d] = r[[c for c in RAW_COLUMNS if c in r]]
        if not devices:
            print("[WARN] Không có dữ liệu demo (chạy collector với --dry-run trước)")
    elif args.csv:
        raw = pd.read_csv(args.csv, index_col=0, parse_dates=True)
        devices = {args.device or "csv": raw}
    else:
        raw = query_raw(client, args.start, args.stop, args.device)
        if raw.empty:
            print(f"[WARN] Không có dữ liệu trong {config.BUCKET_RAW} từ {args.start}")
            return
        devices = {d: g.drop(columns="device_id") for d, g in raw.groupby("device_id")}
    for dev, g in devices.items():
        if len(g) < 3:
            print(f"[SKIP] {dev}: quá ít dữ liệu ({len(g)})")
            continue
        process_device(g, dev, args, client)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="1h", help="khoảng lùi: 30m, 1h, 6h, 2d hoặc mốc RFC3339 2026-10-07T00:00:00Z")
    ap.add_argument("--stop", default=None)
    ap.add_argument("--device", default=None)
    ap.add_argument("--rule", default="30s", help="cửa sổ resample: 10s, 30s, 1min, 5min...")
    ap.add_argument("--outlier", choices=["iqr", "zscore"], default="iqr")
    ap.add_argument("--outlier-window", type=int, default=15,
                    help="số mẫu của cửa sổ trượt khi tìm outlier (0 = toàn chuỗi)")
    ap.add_argument("--fill-limit", type=int, default=3, help="số cửa sổ tối đa được nội suy liên tiếp")
    ap.add_argument("--loop", type=int, default=0, help="chạy lặp mỗi N giây")
    ap.add_argument("--csv", default=None, help="đọc dữ liệu thô từ CSV thay vì InfluxDB")
    ap.add_argument("--demo", action="store_true", help="đọc file dry-run của collector (không cần InfluxDB)")
    args = ap.parse_args()
    if "T" not in args.start and not args.start.startswith("-"):
        args.start = "-" + args.start      # 1h -> -1h (cú pháp Flux)

    client = None
    if not (args.csv or args.demo):
        from influxdb_client import InfluxDBClient
        client = InfluxDBClient(url=config.INFLUX_URL, token=config.INFLUX_TOKEN,
                                org=config.INFLUX_ORG, timeout=30000)
    try:
        while True:
            run_once(args, client)
            if not args.loop:
                break
            time.sleep(args.loop)
    except KeyboardInterrupt:
        pass
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    main()
