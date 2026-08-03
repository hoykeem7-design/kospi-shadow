from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kospi_shadow.data import DataBundle


@pytest.fixture
def synthetic_bundle() -> DataBundle:
    rng = np.random.default_rng(20260731)
    dates = pd.bdate_range("2020-01-02", periods=900)
    overnight = rng.normal(0, 0.007, len(dates))
    # A modest predictable component from previous US factor return.
    us_ret = rng.normal(0, 0.009, len(dates))
    intraday = 0.15 * np.r_[0.0, us_ret[:-1]] + rng.normal(0, 0.008, len(dates))
    close = 2200 * np.cumprod(1 + rng.normal(0.0001, 0.009, len(dates)))
    open_ = np.r_[close[0], close[:-1] * (1 + overnight[1:])]
    close = open_ * (1 + intraday)
    high = np.maximum(open_, close) * (1 + rng.uniform(0.0001, 0.006, len(dates)))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.0001, 0.006, len(dates)))
    target = pd.DataFrame({
        "Date": dates,
        "Open": open_,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": rng.integers(1_000_000, 9_000_000, len(dates)),
    })
    us_level = 100 * np.cumprod(1 + us_ret)
    factor = pd.DataFrame({
        "Date": dates,
        "Open": us_level,
        "High": us_level * 1.002,
        "Low": us_level * 0.998,
        "Close": us_level,
        "Volume": 1_000_000,
    })
    manifest = {
        "target_provider": "synthetic_test",
        "target_official": False,
        "target_rows": len(target),
        "target_date_min": dates.min().strftime("%Y-%m-%d"),
        "target_date_max": dates.max().strftime("%Y-%m-%d"),
        "target_sha256": "synthetic",
        "factor_names": ["us"],
        "collection_warnings": [],
    }
    return DataBundle(target=target, factors={"us": factor}, manifest=manifest)
