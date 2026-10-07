"""Các bước tiền xử lý dữ liệu cảm biến (thuần pandas, không phụ thuộc DB -> dễ kiểm thử).

Thứ tự:  clean -> outlier -> resample -> missing values -> features -> normalize
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler, StandardScaler

from config import SENSOR_FIELDS, VALID_RANGES

# Độ phân tán tối thiểu dùng khi IQR/MAD ~ 0 (dữ liệu Wokwi gần như hằng số) để tránh
# coi một thay đổi rất nhỏ là outlier. Đơn vị theo từng đại lượng.
MIN_SPREAD = {"temperature": 0.5, "humidity": 1.0, "distance_cm": 2.0, "light_lux": 20.0}


@dataclass
class Report:
    """Thống kê từng bước - dùng cho báo cáo."""
    rows_in: int = 0
    duplicates_removed: int = 0
    out_of_range: dict = field(default_factory=dict)
    missing_before: dict = field(default_factory=dict)
    outliers: dict = field(default_factory=dict)
    outlier_bounds: dict = field(default_factory=dict)
    windows: int = 0
    empty_windows: int = 0
    imputed: dict = field(default_factory=dict)
    rows_dropped_after_impute: int = 0
    rows_out: int = 0
    scaler: dict = field(default_factory=dict)

    def to_dict(self):
        return self.__dict__


# ---------------------------------------------------------------- 1. làm sạch
def clean(df: pd.DataFrame, rep: Report) -> pd.DataFrame:
    rep.rows_in = len(df)
    df = df.sort_index()
    dup = df.index.duplicated(keep="first")
    rep.duplicates_removed = int(dup.sum())
    df = df[~dup].copy()
    for c in SENSOR_FIELDS:
        if c not in df:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
        lo, hi = VALID_RANGES[c]
        bad = (df[c] < lo) | (df[c] > hi)
        rep.out_of_range[c] = int(bad.sum())
        df.loc[bad, c] = np.nan
        rep.missing_before[c] = int(df[c].isna().sum())
    return df


# ---------------------------------------------------------------- 2. outlier
def detect_outliers(s: pd.Series, method: str = "iqr", k: float = 1.5, z: float = 3.0,
                    window: int = 0, min_spread: float = 0.0) -> tuple[pd.Series, tuple[float, float]]:
    """Trả về (mask outlier, (cận dưới, cận trên)).

    method="iqr"   : ngoài [Q1 - k*IQR, Q3 + k*IQR]
    method="zscore": |x - mean| / std > z
    window > 0     : tính thống kê trên cửa sổ trượt (theo số mẫu) thay vì toàn chuỗi,
                     phù hợp khi giá trị thay đổi theo bậc (kéo thanh trượt trên Wokwi).
    """
    x = s.astype(float)
    if x.notna().sum() < 4:
        return pd.Series(False, index=s.index), (np.nan, np.nan)
    if window and window > 2:
        roll = x.rolling(window, center=True, min_periods=3)
        if method == "iqr":
            q1, q3 = roll.quantile(0.25), roll.quantile(0.75)
            iqr = (q3 - q1).clip(lower=max(min_spread, 1e-6))
            lo, hi = q1 - k * iqr, q3 + k * iqr
        else:
            med = roll.median()
            mad = (x - med).abs().rolling(window, center=True, min_periods=3).median().clip(lower=max(min_spread, 1e-6) / 1.4826)
            lo, hi = med - z * 1.4826 * mad, med + z * 1.4826 * mad
        mask = (x < lo) | (x > hi)
        return mask.fillna(False), (float(np.nanmin(lo)), float(np.nanmax(hi)))
    if method == "iqr":
        q1, q3 = x.quantile(0.25), x.quantile(0.75)
        iqr = max(q3 - q1, min_spread)
        lo, hi = q1 - k * iqr, q3 + k * iqr
    elif method == "zscore":
        mu, sd = x.mean(), x.std(ddof=0)
        if np.isnan(sd):
            return pd.Series(False, index=s.index), (mu, mu)
        sd = max(sd, min_spread)
        lo, hi = mu - z * sd, mu + z * sd
    else:
        raise ValueError(method)
    return ((x < lo) | (x > hi)).fillna(False), (float(lo), float(hi))


def remove_outliers(df: pd.DataFrame, rep: Report, method: str = "iqr", window: int = 0,
                    fields: list[str] | None = None) -> pd.DataFrame:
    df = df.copy()
    for c in fields or SENSOR_FIELDS:
        mask, bounds = detect_outliers(df[c], method=method, window=window,
                                       min_spread=MIN_SPREAD.get(c, 0.0))
        rep.outliers[c] = int(mask.sum())
        rep.outlier_bounds[c] = [round(b, 3) if b == b else None for b in bounds]
        df[f"{c}_is_outlier"] = mask
        df.loc[mask, c] = np.nan     # coi outlier như giá trị thiếu, sẽ nội suy ở bước sau
    return df


# ---------------------------------------------------------------- 3. resample
def resample(df: pd.DataFrame, rep: Report, rule: str = "30s") -> pd.DataFrame:
    agg = df[SENSOR_FIELDS].resample(rule).mean()
    agg["n_samples"] = df[SENSOR_FIELDS[0]].resample(rule).size()
    agg["n_outliers"] = (df[[f"{c}_is_outlier" for c in SENSOR_FIELDS if f"{c}_is_outlier" in df]]
                         .sum(axis=1).resample(rule).sum() if any(
                             f"{c}_is_outlier" in df for c in SENSOR_FIELDS) else 0)
    rep.windows = len(agg)
    rep.empty_windows = int((agg["n_samples"] == 0).sum())
    return agg


# ---------------------------------------------------------------- 4. missing values
def fill_missing(df: pd.DataFrame, rep: Report, limit: int = 3) -> pd.DataFrame:
    """Nội suy tuyến tính theo thời gian, tối đa `limit` cửa sổ liên tiếp; khoảng trống dài hơn
    (thiết bị offline) giữ NaN rồi bỏ - không 'bịa' dữ liệu cho thời gian dài."""
    df = df.copy()
    for c in SENSOR_FIELDS:
        before = df[c].isna()
        df[c] = df[c].interpolate(method="time", limit=limit, limit_area="inside")
        df[c] = df[c].ffill(limit=1).bfill(limit=1)
        df[f"{c}_imputed"] = before & df[c].notna()
        rep.imputed[c] = int(df[f"{c}_imputed"].sum())
    n = len(df)
    df = df.dropna(subset=SENSOR_FIELDS, how="all")
    # cột nào không có cảm biến (vd LDR không nối) thì bỏ cột thay vì bỏ hết hàng
    keep = [c for c in SENSOR_FIELDS if df[c].notna().any()]
    df = df.dropna(subset=keep)
    rep.rows_dropped_after_impute = n - len(df)
    return df


# ---------------------------------------------------------------- 5. đặc trưng
def add_features(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    df = df.copy()
    for c in SENSOR_FIELDS:
        if df[c].notna().any():
            df[f"{c}_rollmean"] = df[c].rolling(window, min_periods=1).mean()
            df[f"{c}_rollstd"] = df[c].rolling(window, min_periods=2).std().fillna(0)
            df[f"{c}_delta"] = df[c].diff().fillna(0)
    # Điểm sương (công thức Magnus) - đặc trưng kết hợp nhiệt độ & độ ẩm
    if df["temperature"].notna().any() and df["humidity"].notna().any():
        a, b = 17.62, 243.12
        rh = df["humidity"].clip(lower=1)
        gamma = np.log(rh / 100) + a * df["temperature"] / (b + df["temperature"])
        df["dew_point"] = b * gamma / (a - gamma)
    return df


# ---------------------------------------------------------------- 6. chuẩn hóa
def normalize(df: pd.DataFrame, rep: Report) -> pd.DataFrame:
    df = df.copy()
    cols = [c for c in SENSOR_FIELDS if df[c].notna().any()]
    if not cols or len(df) == 0:
        return df
    mm, ss = MinMaxScaler(), StandardScaler()
    df[[f"{c}_minmax" for c in cols]] = mm.fit_transform(df[cols])
    df[[f"{c}_z" for c in cols]] = ss.fit_transform(df[cols])
    rep.scaler = {c: {"min": float(mm.data_min_[i]), "max": float(mm.data_max_[i]),
                      "mean": float(ss.mean_[i]), "std": float(np.sqrt(ss.var_[i]))}
                  for i, c in enumerate(cols)}
    return df


# ---------------------------------------------------------------- toàn bộ
def run_pipeline(raw: pd.DataFrame, rule: str = "30s", outlier: str = "iqr",
                 outlier_window: int = 0, fill_limit: int = 3, feat_window: int = 5):
    rep = Report()
    df = clean(raw, rep)
    df = remove_outliers(df, rep, method=outlier, window=outlier_window)
    df = resample(df, rep, rule)
    df = fill_missing(df, rep, limit=fill_limit)
    df = add_features(df, feat_window)
    df = normalize(df, rep)
    rep.rows_out = len(df)
    return df, rep
