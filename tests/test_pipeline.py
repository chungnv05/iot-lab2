import numpy as np
import pandas as pd

from preprocessing.pipeline import detect_outliers, run_pipeline


def make_raw(n=360, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-10-07 05:00", periods=n, freq="5s", tz="UTC")
    df = pd.DataFrame({
        "temperature": 25 + rng.normal(0, 0.2, n),
        "humidity": 55 + rng.normal(0, 0.5, n),
        "distance_cm": 100 + rng.normal(0, 1, n),
        "light_lux": 500 + rng.normal(0, 10, n),
    }, index=idx)
    df.iloc[50, 0] = 40.0          # spike
    df.iloc[120, 0] = 10.0         # spike
    df.iloc[60:63, 1] = np.nan     # thiếu
    df.iloc[200:260] = np.nan      # mất kết nối 5 phút
    df = df.drop(df.index[200:260])
    df.iloc[10, 2] = 999           # ngoài miền đo
    return df


def test_outlier_detects_spikes():
    raw = make_raw()
    for method in ("iqr", "zscore"):
        mask, _ = detect_outliers(raw["temperature"], method=method, window=15, min_spread=0.5)
        assert mask.iloc[50] and mask.iloc[120]
        assert mask.sum() <= 5


def test_step_change_not_outlier():
    idx = pd.date_range("2026-10-07", periods=60, freq="5s", tz="UTC")
    s = pd.Series([25.0] * 30 + [30.0] * 30, index=idx)
    mask, _ = detect_outliers(s, "iqr", window=15, min_spread=0.5)
    assert mask.sum() == 0


def test_pipeline_end_to_end():
    df, rep = run_pipeline(make_raw(), rule="30s")
    assert rep.out_of_range["distance_cm"] == 1
    assert rep.outliers["temperature"] >= 2
    assert df["temperature"].max() < 27
    assert not df[["temperature", "humidity", "distance_cm", "light_lux"]].isna().any().any()
    assert rep.empty_windows >= 8                 # 5 phút mất kết nối
    assert rep.rows_dropped_after_impute > 0      # khoảng trống dài không bị bịa dữ liệu
    for c in ["temperature_rollmean", "temperature_delta", "dew_point", "humidity_minmax", "light_lux_z"]:
        assert c in df
    assert df["humidity_minmax"].between(0, 1).all()
