"""Lớp truy vấn dữ liệu cho dashboard / công cụ báo cáo.

InfluxSource : đọc từ InfluxDB (chế độ chính).
DemoSource   : đọc từ logs/dryrun_points.jsonl (khi collector chạy --dry-run) và
               output/processed_*.csv -> xem thử app khi chưa cài InfluxDB.
Mọi hàm trả về pandas.DataFrame có index thời gian (UTC).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd

import config

SENSOR_LAT_FIELDS = config.SENSOR_FIELDS + ["rssi", "seq", "uptime_s", "net_latency_ms", "proc_latency_ms"]


def _empty() -> pd.DataFrame:
    return pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC"))


class InfluxSource:
    name = "InfluxDB"

    def __init__(self):
        from influxdb_client import InfluxDBClient

        self.client = InfluxDBClient(url=config.INFLUX_URL, token=config.INFLUX_TOKEN,
                                     org=config.INFLUX_ORG, timeout=15000)
        self.q = self.client.query_api()

    def ok(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def _frame(self, flux: str) -> pd.DataFrame:
        df = self.q.query_data_frame(flux)
        if isinstance(df, list):
            df = pd.concat(df, ignore_index=True) if df else pd.DataFrame()
        if df.empty:
            return _empty()
        df = df.drop(columns=[c for c in ("result", "table", "_start", "_stop", "_measurement") if c in df])
        df["_time"] = pd.to_datetime(df["_time"], utc=True)
        return df.set_index("_time").sort_index()

    def _pivot(self, bucket, measurement, minutes, device=None, extra=""):
        dev = f'|> filter(fn: (r) => r.device_id == "{device}")' if device else ""
        return self._frame(f'''
from(bucket: "{bucket}")
  |> range(start: -{int(minutes)}m)
  |> filter(fn: (r) => r._measurement == "{measurement}")
  {dev}
  {extra}
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()
''')

    def devices(self, minutes=1440) -> list[str]:
        raw = self._frame(f'''
from(bucket: "{config.BUCKET_RAW}")
  |> range(start: -{int(minutes)}m)
  |> filter(fn: (r) => r._measurement == "{config.M_RAW}" and r._field == "seq")
  |> last()
  |> group()''')
        return sorted(raw["device_id"].dropna().unique().tolist()) if "device_id" in raw else []

    def raw(self, device, minutes):
        return self._pivot(config.BUCKET_RAW, config.M_RAW, minutes, device)

    def processed(self, device, minutes):
        return self._pivot(config.BUCKET_PROCESSED, config.M_PROCESSED, minutes, device)

    def pipeline(self, minutes, device=None):
        return self._pivot(config.BUCKET_RAW, config.M_PIPELINE, minutes, device)

    def events(self, minutes):
        """Sự kiện chất lượng + bản tin bị loại: cột kind, device_id, count, measurement."""
        out = []
        for m in (config.M_QUALITY, config.M_REJECTED):
            df = self._frame(f'''
from(bucket: "{config.BUCKET_RAW}")
  |> range(start: -{int(minutes)}m)
  |> filter(fn: (r) => r._measurement == "{m}" and (r._field == "count" or r._field == "lost"))
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> group()''')
            if not df.empty:
                df["measurement"] = m
                out.append(df)
        return pd.concat(out).sort_index() if out else _empty()

    def storage_stats(self) -> pd.DataFrame:
        """Số điểm dữ liệu, retention và dung lượng đĩa (nếu đọc được /metrics) mỗi bucket."""
        import requests

        rows = []
        buckets = {b.name: b for b in self.client.buckets_api().find_buckets().buckets}
        sizes = {}
        try:
            txt = requests.get(f"{config.INFLUX_URL}/metrics", timeout=5).text
            for line in txt.splitlines():
                if line.startswith("storage_shard_disk_size{"):
                    lbl, val = line.rsplit(" ", 1)
                    bid = lbl.split('bucket="')[1].split('"')[0]
                    sizes[bid] = sizes.get(bid, 0) + float(val)
        except Exception:
            pass
        for name in (config.BUCKET_RAW, config.BUCKET_PROCESSED):
            b = buckets.get(name)
            if b is None:
                continue
            ret = b.retention_rules[0].every_seconds if b.retention_rules else 0
            cnt = self._frame(f'''
from(bucket: "{name}")
  |> range(start: -{max(ret, 86400)}s)
  |> filter(fn: (r) => r._field != "")
  |> group(columns: ["_measurement"])
  |> count()
  |> map(fn: (r) => ({{r with _time: now()}}))''')
            if not cnt.empty and "_measurement" not in cnt:
                cnt["_measurement"] = "?"
            for _, r in (cnt.reset_index().iterrows() if not cnt.empty else []):
                rows.append({"bucket": name, "measurement": r.get("_measurement", "?"),
                             "field_values": int(r["_value"]),
                             "retention_days": ret / 86400 if ret else "vô hạn",
                             "disk_MB": round(sizes.get(b.id, 0) / 1e6, 3)})
        return pd.DataFrame(rows)

    def close(self):
        self.client.close()


class DemoSource:
    name = "Demo (dry-run file)"

    def __init__(self):
        self.path = config.LOG_DIR / "dryrun_points.jsonl"

    def ok(self) -> bool:
        return self.path.exists()

    def _load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame()
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            rows.append({"measurement": d["measurement"], "time": d["time_ms"], **d["tags"], **d["fields"]})
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["time"] = pd.to_datetime(df["time"], unit="ms", utc=True)
        return df.set_index("time").sort_index()

    def _sel(self, measurement, minutes, device=None):
        df = self._load()
        if df.empty:
            return _empty()
        df = df[df["measurement"] == measurement]
        df = df[df.index >= datetime.now(timezone.utc) - timedelta(minutes=minutes)]
        if device:
            df = df[df["device_id"] == device]
        return df.dropna(axis=1, how="all")

    def devices(self, minutes=1440):
        df = self._sel(config.M_RAW, minutes)
        return sorted(df["device_id"].unique().tolist()) if "device_id" in df else []

    def raw(self, device, minutes):
        return self._sel(config.M_RAW, minutes, device)

    def processed(self, device, minutes):
        f = config.OUTPUT_DIR / f"processed_{device}.csv"
        if not f.exists():
            return _empty()
        df = pd.read_csv(f, index_col=0)
        df.index = pd.to_datetime(df.index, utc=True)
        return df[df.index >= datetime.now(timezone.utc) - timedelta(minutes=minutes)]

    def pipeline(self, minutes, device=None):
        return self._sel(config.M_PIPELINE, minutes, device)

    def events(self, minutes):
        q = self._sel(config.M_QUALITY, minutes)
        r = self._sel(config.M_REJECTED, minutes)
        return pd.concat([q, r]).sort_index() if len(q) + len(r) else _empty()

    def storage_stats(self):
        df = self._load()
        if df.empty:
            return pd.DataFrame()
        g = df.groupby("measurement").size().reset_index(name="points")
        g["bucket"] = "dry-run file"
        g["file_MB"] = round(self.path.stat().st_size / 1e6, 3)
        return g

    def close(self):
        pass


def get_source(demo: bool = False):
    if demo or not config.INFLUX_TOKEN:
        return DemoSource()
    return InfluxSource()
