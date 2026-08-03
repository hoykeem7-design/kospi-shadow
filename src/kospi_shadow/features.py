from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _prepare_factor(name: str, frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy().sort_values("Date").drop_duplicates("Date", keep="last")
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None).dt.normalize()
    if "Close" in df.columns:
        value = pd.to_numeric(df["Close"], errors="coerce")
    elif "value" in df.columns:
        value = pd.to_numeric(df["value"], errors="coerce")
    else:
        raise ValueError(f"Factor {name} needs Close or value")
    out = pd.DataFrame({
        "factor_date": df["Date"],
        f"{name}_level": value,
        f"{name}_ret1": value.pct_change(fill_method=None),
        f"{name}_ret5": value.pct_change(5, fill_method=None),
        f"{name}_vol20": value.pct_change(fill_method=None).rolling(20).std(),
    }).dropna(subset=[f"{name}_level"])
    return out


def build_feature_table(
    target: pd.DataFrame,
    factors: dict[str, pd.DataFrame],
    *,
    max_feature_staleness_days: int = 7,
) -> tuple[pd.DataFrame, list[str]]:
    """Build targets and predictors known before each KOSPI session opens.

    Critical alignment rule: every external factor is joined with allow_exact_matches=False,
    so a KOSPI row dated t can only use a factor observation dated strictly before t.
    KOSPI-derived predictors are shifted by one session.
    """
    df = target.copy().sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)
    df["Date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None).dt.normalize()
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    close_ret = df["Close"].pct_change(fill_method=None)
    gap_ret = df["Open"] / df["Close"].shift(1) - 1.0
    intraday_ret = df["Close"] / df["Open"] - 1.0
    range_ret = (df["High"] - df["Low"]) / df["Close"].shift(1)

    df["gap_return"] = gap_ret
    df["gap_up"] = (gap_ret > 0).astype(float).where(gap_ret.notna())
    df["intraday_return"] = intraday_ret
    df["intraday_up"] = (intraday_ret > 0).astype(float).where(intraday_ret.notna())

    for lag in (1, 2, 3, 5, 10):
        df[f"kospi_ret_lag_{lag}"] = close_ret.shift(lag)
    for window in (5, 10, 20, 60):
        df[f"kospi_mom_{window}"] = df["Close"].pct_change(window, fill_method=None).shift(1)
        df[f"kospi_vol_{window}"] = close_ret.rolling(window).std().shift(1)
        df[f"kospi_ma_dist_{window}"] = (df["Close"] / df["Close"].rolling(window).mean() - 1.0).shift(1)
    df["kospi_prev_gap"] = gap_ret.shift(1)
    df["kospi_prev_intraday"] = intraday_ret.shift(1)
    df["kospi_prev_range"] = range_ret.shift(1)
    df["day_of_week"] = df["Date"].dt.dayofweek.astype(float)
    df["month"] = df["Date"].dt.month.astype(float)

    merged = df.sort_values("Date")
    for name, frame in sorted(factors.items()):
        factor = _prepare_factor(name, frame)
        merged = pd.merge_asof(
            merged.sort_values("Date"),
            factor.sort_values("factor_date"),
            left_on="Date",
            right_on="factor_date",
            direction="backward",
            allow_exact_matches=False,
        )
        age = (merged["Date"] - merged["factor_date"]).dt.days
        level_col = f"{name}_level"
        factor_cols = [c for c in factor.columns if c != "factor_date"]
        stale = age > int(max_feature_staleness_days)
        merged.loc[stale, factor_cols] = np.nan
        merged[f"{name}_age_days"] = age.astype(float)
        merged = merged.drop(columns=["factor_date"])

    excluded = {
        "Date", "Open", "High", "Low", "Close", "Volume",
        "gap_return", "gap_up", "intraday_return", "intraday_up",
    }
    feature_cols = [c for c in merged.columns if c not in excluded]
    merged = merged.replace([np.inf, -np.inf], np.nan)
    return merged, feature_cols
