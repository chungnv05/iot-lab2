"""Ứng dụng giám sát IoT (Streamlit) - dashboard real-time + dữ liệu đã xử lý + độ trễ + lưu trữ.

    streamlit run app/dashboard.py              # đọc InfluxDB (cần INFLUX_TOKEN trong .env)
    streamlit run app/dashboard.py -- --demo    # đọc file dry-run, không cần InfluxDB
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

import config  # noqa: E402
from app.data import get_source  # noqa: E402

# Bảng màu phân loại (thứ tự cố định) + màu trạng thái cho outlier
C_RAW, C_PROC, C_ROLL, C_ALERT = "#2a78d6", "#eb6834", "#1baf7a", "#e34948"
LABELS = {
    "temperature": ("Nhiệt độ", "°C"),
    "humidity": ("Độ ẩm", "%RH"),
    "distance_cm": ("Khoảng cách", "cm"),
    "light_lux": ("Ánh sáng", "lux"),
}

st.set_page_config(page_title="IoT Monitor - Bài 2", page_icon="📡", layout="wide")
DEMO = "--demo" in sys.argv
TZ = "Asia/Ho_Chi_Minh"   # hiển thị giờ Việt Nam (InfluxDB lưu UTC)


@st.cache_resource
def source(demo: bool):
    return get_source(demo)


src = source(DEMO)


def local(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.index, pd.DatetimeIndex) and df.index.tz is not None:
        df = df.copy()
        df.index = df.index.tz_convert(TZ)
    return df

# ------------------------------------------------------------------ sidebar
st.sidebar.title("📡 IoT Monitor")
st.sidebar.caption(f"Nguồn dữ liệu: **{src.name}**")
if not src.ok():
    st.sidebar.error("Không kết nối được nguồn dữ liệu")
devices = src.devices()
if not devices:
    st.warning("Chưa có dữ liệu. Hãy chạy Wokwi (hoặc `python -m tools.simulator`) và "
               "`python -m gateway.collector` trước.")
    st.stop()
device = st.sidebar.selectbox("Thiết bị", devices)
minutes = st.sidebar.select_slider("Khoảng thời gian", [5, 15, 30, 60, 180, 360, 1440], value=30,
                                   format_func=lambda m: f"{m} phút" if m < 60 else f"{m // 60} giờ")
refresh = st.sidebar.select_slider("Tự làm mới (giây)", [2, 5, 10, 30, 60], value=5)
st.sidebar.subheader("Ngưỡng cảnh báo")
th_temp = st.sidebar.number_input("Nhiệt độ cao (°C)", value=35.0, step=0.5)
th_hum = st.sidebar.number_input("Độ ẩm thấp (%RH)", value=30.0, step=1.0)
th_dist = st.sidebar.number_input("Vật cản gần (cm)", value=20.0, step=1.0)


def line_chart(series: list[tuple[str, pd.Series, str, str]], unit: str, height=260,
               markers: pd.Series | None = None):
    fig = go.Figure()
    for name, s, color, dash in series:
        s = s.dropna()
        if isinstance(s.index, pd.DatetimeIndex) and s.index.tz is not None:
            s.index = s.index.tz_convert(TZ)
        fig.add_trace(go.Scatter(x=s.index, y=s.values, name=name, mode="lines",
                                 line=dict(color=color, width=2, dash=dash),
                                 hovertemplate=f"%{{y:.2f}} {unit}<extra>{name}</extra>"))
    if markers is not None and len(markers):
        markers = markers.copy()
        markers.index = markers.index.tz_convert(TZ)
        fig.add_trace(go.Scatter(x=markers.index, y=markers.values, name="Outlier đã loại",
                                 mode="markers", marker=dict(color=C_ALERT, size=9, symbol="x"),
                                 hovertemplate=f"%{{y:.2f}} {unit}<extra>outlier</extra>"))
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10), hovermode="x unified",
                      legend=dict(orientation="h", y=1.12, x=0), yaxis_title=unit,
                      showlegend=len(fig.data) > 1)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)")
    return fig


def fmt_age(ts: pd.Timestamp) -> str:
    sec = (datetime.now(timezone.utc) - ts).total_seconds()
    return f"{sec:.0f} s trước" if sec < 120 else f"{sec / 60:.0f} phút trước"


# ================================================================== TAB 1
@st.fragment(run_every=refresh)
def realtime_tab():
    raw = local(src.raw(device, minutes))
    if raw.empty:
        st.info("Không có dữ liệu trong khoảng thời gian này.")
        return
    last = raw.iloc[-1]
    age = (datetime.now(timezone.utc) - raw.index[-1]).total_seconds()
    online = age < 30
    st.markdown(f"**Trạng thái:** {'🟢 Online' if online else '🔴 Offline / không có dữ liệu mới'} · "
                f"bản tin cuối {fmt_age(raw.index[-1])} · seq={int(last.get('seq', 0))} · "
                f"RSSI={last.get('rssi', float('nan')):.0f} dBm")

    cols = st.columns(4)
    for col, f in zip(cols, config.SENSOR_FIELDS):
        name, unit = LABELS[f]
        s = raw[f].dropna() if f in raw else pd.Series(dtype=float)
        if s.empty:
            col.metric(name, "—")
            continue
        delta = s.iloc[-1] - s.iloc[-2] if len(s) > 1 else 0
        col.metric(name, f"{s.iloc[-1]:.1f} {unit}", f"{delta:+.2f}")

    alerts = []
    if "temperature" in raw and raw["temperature"].dropna().size and raw["temperature"].dropna().iloc[-1] > th_temp:
        alerts.append(f"🌡️ Nhiệt độ vượt {th_temp} °C")
    if "humidity" in raw and raw["humidity"].dropna().size and raw["humidity"].dropna().iloc[-1] < th_hum:
        alerts.append(f"💧 Độ ẩm dưới {th_hum} %RH")
    if "distance_cm" in raw and raw["distance_cm"].dropna().size and raw["distance_cm"].dropna().iloc[-1] < th_dist:
        alerts.append(f"🚧 Có vật cản gần hơn {th_dist} cm")
    for a in alerts:
        st.error(a)

    c1, c2 = st.columns(2)
    for i, f in enumerate(config.SENSOR_FIELDS):
        if f not in raw:
            continue
        name, unit = LABELS[f]
        with (c1 if i % 2 == 0 else c2):
            st.markdown(f"**{name}** ({unit})")
            st.plotly_chart(line_chart([(name, raw[f], C_RAW, "solid")], unit),
                            width="stretch", key=f"rt_{f}")
    with st.expander("Bảng dữ liệu thô (20 bản ghi mới nhất)"):
        show = [c for c in ["seq"] + config.SENSOR_FIELDS + ["rssi", "net_latency_ms"] if c in raw]
        st.dataframe(raw[show].tail(20).iloc[::-1], width="stretch")


# ================================================================== TAB 2
@st.fragment(run_every=max(refresh, 10))
def processed_tab():
    raw = local(src.raw(device, minutes))
    proc = local(src.processed(device, minutes))
    if proc.empty:
        st.info("Chưa có dữ liệu đã xử lý. Chạy: `python -m preprocessing.preprocess --start -1h --loop 60`")
        return
    st.caption(f"{len(proc)} cửa sổ đã resample · "
               f"{int(proc['n_samples'].sum()) if 'n_samples' in proc else '?'} mẫu thô · "
               f"{int(proc['n_outliers'].sum()) if 'n_outliers' in proc else 0} outlier bị loại")
    f = st.radio("Đại lượng", config.SENSOR_FIELDS, horizontal=True,
                 format_func=lambda x: LABELS[x][0], key="proc_field")
    name, unit = LABELS[f]
    if f not in proc:
        st.info("Không có dữ liệu cho đại lượng này.")
        return
    # outlier = điểm thô lệch xa giá trị đã làm sạch của cửa sổ tương ứng
    markers = None
    if f in raw and len(raw):
        ref = proc[f].reindex(raw.index, method="nearest", tolerance=pd.Timedelta("5min"))
        spread = proc[f].std() if proc[f].std() > 0 else 1
        markers = raw[f][(raw[f] - ref).abs() > 3 * spread + 1]
    st.plotly_chart(line_chart([
        ("Thô", raw[f] if f in raw else pd.Series(dtype=float), C_RAW, "dot"),
        ("Đã xử lý (resample)", proc[f], C_PROC, "solid"),
        ("Rolling mean", proc.get(f"{f}_rollmean", pd.Series(dtype=float)), C_ROLL, "dash"),
    ], unit, height=340, markers=markers), width="stretch", key="proc_main")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"**Delta (thay đổi giữa 2 cửa sổ)** ({unit})")
        st.plotly_chart(line_chart([("Delta", proc.get(f"{f}_delta", pd.Series(dtype=float)),
                                     C_RAW, "solid")], unit, 220),
                        width="stretch", key="proc_delta")
    with c2:
        st.markdown("**Giá trị chuẩn hóa Min-Max** (0–1)")
        st.plotly_chart(line_chart([("Min-Max", proc.get(f"{f}_minmax", pd.Series(dtype=float)),
                                     C_RAW, "solid")], "0-1", 220),
                        width="stretch", key="proc_norm")
    with st.expander("Bảng dữ liệu đã xử lý"):
        st.dataframe(proc.tail(50).iloc[::-1], width="stretch")


# ================================================================== TAB 3
@st.fragment(run_every=max(refresh, 10))
def latency_tab():
    pm = local(src.pipeline(minutes, device))
    ev = src.events(minutes)
    raw = local(src.raw(device, minutes))

    if not pm.empty and "e2e_ms" in pm:
        e2e = pm["e2e_ms"].dropna()
        cols = st.columns(5)
        for col, (lbl, val) in zip(cols, [("Số mẫu", f"{len(e2e)}"),
                                           ("Trung bình", f"{e2e.mean():.0f} ms"),
                                           ("P50", f"{e2e.quantile(.5):.0f} ms"),
                                           ("P95", f"{e2e.quantile(.95):.0f} ms"),
                                           ("Max", f"{e2e.max():.0f} ms")]):
            col.metric(lbl, val)
        parts = pd.DataFrame({
            "RTT ping/pong": pm.get("rtt_ms", pd.Series(dtype=float)).describe(),
            "Mạng 1 chiều (≈RTT/2)": pm.get("net_ms", pd.Series(dtype=float)).describe(),
            "Xử lý (validate)": pm.get("proc_ms", pd.Series(dtype=float)).describe(),
            "Ghi DB": pm.get("write_ms", pd.Series(dtype=float)).describe(),
            "End-to-end": e2e.describe(),
        }).T[["mean", "50%", "max"]].round(2)
        parts.columns = ["Trung bình (ms)", "P50 (ms)", "Max (ms)"]
        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown("**Độ trễ end-to-end theo thời gian** (ms)")
            st.plotly_chart(line_chart([("E2E", e2e, C_RAW, "solid")], "ms", 260),
                            width="stretch", key="lat_line")
        with c2:
            st.markdown("**Phân rã độ trễ**")
            st.dataframe(parts, width="stretch")
            fig = go.Figure(go.Histogram(x=e2e.clip(upper=e2e.quantile(.99)), nbinsx=30,
                                         marker=dict(color=C_RAW, line=dict(width=1, color="white")),
                                         hovertemplate="%{x} ms: %{y} bản tin<extra></extra>"))
            fig.update_layout(height=200, margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_title="ms", yaxis_title="số bản tin")
            st.plotly_chart(fig, width="stretch", key="lat_hist")
        if "clock_skew_ms" in pm and pm["clock_skew_ms"].notna().any():
            sk = pm["clock_skew_ms"].dropna().iloc[-1] / 1000
            st.caption(f"ℹ️ Đồng hồ thiết bị lệch {sk:.0f} s so với gateway (mô phỏng chạy chậm hơn thời gian "
                       "thực) → gateway dùng thời điểm nhận làm timestamp và đo độ trễ mạng bằng ping/pong.")
    else:
        st.info("Chưa có số liệu độ trễ (thiết bị cần đồng bộ NTP để có ts).")

    st.markdown("**Chất lượng dữ liệu**")
    if ev.empty:
        st.success("Chưa ghi nhận sự kiện bất thường.")
    else:
        ev_dev = ev[ev["device_id"].isin([device, "unknown"])].copy() if "device_id" in ev else ev.copy()
        ev_dev["lost"] = pd.to_numeric(ev_dev["lost"], errors="coerce").fillna(0) if "lost" in ev_dev else 0
        summary = ev_dev.groupby("kind").agg(so_lan=("kind", "size"), goi_mat=("lost", "sum"))
        expected = len(raw) + int(summary["goi_mat"].sum())
        cols = st.columns(4)
        cols[0].metric("Bản tin đã lưu", len(raw))
        cols[1].metric("Gói mất (theo seq)", int(summary["goi_mat"].sum()),
                       f"{100 * summary['goi_mat'].sum() / max(expected, 1):.1f}%", delta_color="inverse")
        cols[2].metric("Gói trùng bị bỏ", int(summary.loc["duplicate", "so_lan"]) if "duplicate" in summary.index else 0)
        rej = summary[~summary.index.isin(["duplicate", "gap", "restart", "out_of_order",
                                           "field_dropped", "status_online", "status_offline"])]
        cols[3].metric("Bản tin bị loại", int(rej["so_lan"].sum()))
        st.dataframe(summary.rename(columns={"so_lan": "Số lần", "goi_mat": "Gói mất"}),
                     width="stretch")


# ================================================================== TAB 4
def storage_tab():
    st.markdown("**Thống kê lưu trữ theo bucket / measurement**")
    if st.button("Làm mới thống kê"):
        st.cache_data.clear()
    try:
        stats = src.storage_stats()
        st.dataframe(stats, width="stretch")
    except Exception as exc:
        st.error(f"Không lấy được thống kê: {exc}")
    raw = local(src.raw(device, minutes))
    if len(raw) > 1:
        rate = len(raw) / max((raw.index[-1] - raw.index[0]).total_seconds(), 1)
        per_day = rate * 86400
        st.markdown(
            f"- Tốc độ ghi hiện tại (thiết bị `{device}`): **{rate:.3f} bản ghi/s** "
            f"≈ **{per_day:,.0f} bản ghi/ngày**\n"
            f"- Với retention {config.RETENTION_RAW_DAYS} ngày cho dữ liệu thô: "
            f"≈ **{per_day * config.RETENTION_RAW_DAYS:,.0f} bản ghi** / thiết bị "
            f"({per_day * config.RETENTION_RAW_DAYS * 10:,.0f} giá trị field)\n"
            f"- Dữ liệu đã xử lý (resample) giữ {config.RETENTION_PROCESSED_DAYS} ngày, "
            f"nhỏ hơn nhiều lần vì mỗi cửa sổ gộp nhiều mẫu.")


st.title("Giám sát dữ liệu IoT")
t1, t2, t3, t4 = st.tabs(["📈 Real-time", "🧹 Đã tiền xử lý", "⏱️ Độ trễ & chất lượng", "💾 Lưu trữ"])
with t1:
    realtime_tab()
with t2:
    processed_tab()
with t3:
    latency_tab()
with t4:
    storage_tab()
