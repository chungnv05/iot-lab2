"""Báo cáo độ trễ end-to-end & chất lượng dữ liệu (dùng số liệu cho báo cáo Word).

    python -m tools.latency_report --minutes 60
    python -m tools.latency_report --minutes 60 --demo

Kết quả: in bảng ra màn hình, lưu output/latency_summary.csv, output/latency_hist.png
"""
from __future__ import annotations

import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from app.data import get_source  # noqa: E402

PARTS = [("rtt_ms", "RTT ping/pong (gateway ↔ broker ↔ thiết bị)"),
         ("net_ms", "Mạng 1 chiều ≈ RTT/2"),
         ("proc_ms", "Xử lý tại gateway (validate)"),
         ("write_ms", "Ghi InfluxDB"),
         ("e2e_ms", "END-TO-END")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument("--device", default=None)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    src = get_source(args.demo)
    pm = src.pipeline(args.minutes, args.device)
    if pm.empty or "e2e_ms" not in pm:
        print("Chưa có số liệu pipeline_metrics trong khoảng thời gian này.")
        return

    rows = []
    for col, label in PARTS:
        s = pd.to_numeric(pm.get(col), errors="coerce").dropna() if col in pm else pd.Series(dtype=float)
        if s.empty:
            continue
        rows.append({"Thành phần": label, "N": len(s), "Min": s.min(), "Trung bình": s.mean(),
                     "P50": s.quantile(.5), "P95": s.quantile(.95), "P99": s.quantile(.99),
                     "Max": s.max(), "Độ lệch chuẩn": s.std()})
    tab = pd.DataFrame(rows).round(2)
    print(f"\nĐỘ TRỄ (ms) - {args.minutes} phút gần nhất - nguồn {src.name}")
    print(tab.to_string(index=False))
    tab.to_csv(config.OUTPUT_DIR / "latency_summary.csv", index=False, encoding="utf-8-sig")

    # Chất lượng: mất gói / trùng / bị loại
    ev = src.events(args.minutes)
    raw_n = sum(len(src.raw(d, args.minutes)) for d in src.devices(args.minutes)
                if not args.device or d == args.device)
    if not ev.empty:
        lost = pd.to_numeric(ev.get("lost"), errors="coerce").fillna(0).sum() if "lost" in ev else 0
        kinds = ev["kind"].value_counts()
        print("\nCHẤT LƯỢNG DỮ LIỆU")
        print(f"  Bản tin đã lưu      : {raw_n}")
        print(f"  Gói mất (theo seq)  : {int(lost)}  ({100 * lost / max(raw_n + lost, 1):.2f}%)")
        print(kinds.to_string())

    e2e = pd.to_numeric(pm["e2e_ms"], errors="coerce").dropna()
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.6))
    axes[0].hist(e2e.clip(upper=e2e.quantile(.99)), bins=30, color="#2a78d6", edgecolor="white")
    axes[0].axvline(e2e.quantile(.5), color="#52514e", ls="--", lw=1)
    axes[0].axvline(e2e.quantile(.95), color="#e34948", ls="--", lw=1)
    axes[0].text(e2e.quantile(.5), axes[0].get_ylim()[1] * .92, " P50", color="#52514e")
    axes[0].text(e2e.quantile(.95), axes[0].get_ylim()[1] * .80, " P95", color="#e34948")
    axes[0].set(title="Phân bố độ trễ end-to-end", xlabel="ms", ylabel="số bản tin")
    axes[1].plot(e2e.index, e2e.values, color="#2a78d6", lw=1.5)
    axes[1].set(title="Độ trễ end-to-end theo thời gian", ylabel="ms")
    axes[1].tick_params(axis="x", rotation=30)
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=.2)
    fig.tight_layout()
    out = config.OUTPUT_DIR / "latency_hist.png"
    fig.savefig(out, dpi=150)
    print(f"\nĐã lưu {out} và output/latency_summary.csv")


if __name__ == "__main__":
    main()
