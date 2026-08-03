from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from kospi_shadow.config import load_settings
from kospi_shadow.data import DataBundle
from kospi_shadow.pipeline import run_pipeline


def make_bundle() -> DataBundle:
    rng = np.random.default_rng(20260731)
    dates = pd.bdate_range("2020-01-02", periods=900)
    overnight = rng.normal(0, 0.007, len(dates))
    us_ret = rng.normal(0, 0.009, len(dates))
    intraday = 0.15 * np.r_[0.0, us_ret[:-1]] + rng.normal(0, 0.008, len(dates))
    base_close = 2200 * np.cumprod(1 + rng.normal(0.0001, 0.009, len(dates)))
    open_ = np.r_[base_close[0], base_close[:-1] * (1 + overnight[1:])]
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
    return DataBundle(
        target=target,
        factors={"us": factor},
        manifest={
            "target_provider": "synthetic_test_only",
            "target_official": False,
            "target_rows": len(target),
            "target_date_min": dates.min().strftime("%Y-%m-%d"),
            "target_date_max": dates.max().strftime("%Y-%m-%d"),
            "target_sha256": "synthetic-not-a-market-dataset",
            "factor_names": ["us"],
            "collection_warnings": ["Synthetic verification data; not market evidence."],
        },
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    verification_root = root / "verification" / "synthetic_run"
    verification_root.mkdir(parents=True, exist_ok=True)
    raw_cfg = yaml.safe_load((root / "config" / "default.yml").read_text(encoding="utf-8"))
    raw_cfg["model"]["outer_test_block_sessions"] = 84
    raw_cfg["model"]["inner_splits"] = 3
    raw_cfg["promotion"]["bootstrap_iterations"] = 250
    cfg_path = verification_root / "synthetic_config.yml"
    cfg_path.write_text(yaml.safe_dump(raw_cfg, sort_keys=False), encoding="utf-8")
    metrics = run_pipeline(load_settings(cfg_path), verification_root, bundle=make_bundle())
    summary = {
        "verification_type": "synthetic_logic_test",
        "not_live_market_validation": True,
        "status": metrics["promotion"]["status"],
        "signal_enabled": metrics["promotion"]["signal_enabled"],
        "oos_n": metrics["classification"]["n"],
        "brier": metrics["classification"]["brier"],
        "baseline_brier": metrics["classification"]["baseline_brier"],
        "official_source_check": metrics["promotion"]["checks"]["official_target"],
        "latest_prediction_actionable": metrics["latest_prediction"]["actionable"],
    }
    (root / "verification" / "synthetic_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
