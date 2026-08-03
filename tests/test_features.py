from __future__ import annotations

import pandas as pd

from kospi_shadow.features import build_feature_table


def test_external_factor_never_uses_same_date():
    dates = pd.bdate_range("2025-01-02", periods=80)
    target = pd.DataFrame({
        "Date": dates,
        "Open": 100.0,
        "High": 102.0,
        "Low": 99.0,
        "Close": 101.0,
        "Volume": 1000,
    })
    factor = pd.DataFrame({
        "Date": dates,
        "Close": range(1, len(dates) + 1),
    })
    table, _ = build_feature_table(target, {"sentinel": factor})
    # For target date at row 10, factor must come from row 9, not row 10.
    assert table.loc[10, "sentinel_level"] == 10
    assert table.loc[10, "sentinel_age_days"] >= 1


def test_kospi_predictors_are_lagged(synthetic_bundle):
    table, _ = build_feature_table(synthetic_bundle.target, synthetic_bundle.factors)
    expected = synthetic_bundle.target["Close"].pct_change().iloc[100]
    assert abs(table.loc[101, "kospi_ret_lag_1"] - expected) < 1e-12
