from __future__ import annotations

from pathlib import Path

import yaml

from kospi_shadow.config import load_settings
from kospi_shadow.pipeline import run_pipeline


def test_end_to_end_restricts_unofficial_source(tmp_path: Path, synthetic_bundle):
    source = Path(__file__).parents[1] / "config" / "default.yml"
    cfg = yaml.safe_load(source.read_text(encoding="utf-8"))
    cfg["model"]["inner_splits"] = 3
    cfg["model"]["outer_test_block_sessions"] = 126
    cfg["promotion"]["bootstrap_iterations"] = 100
    cfg_path = tmp_path / "config.yml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    metrics = run_pipeline(load_settings(cfg_path), tmp_path, bundle=synthetic_bundle)
    assert metrics["promotion"]["signal_enabled"] is False
    assert metrics["promotion"]["checks"]["official_target"] is False
    assert (tmp_path / "outputs" / "metrics.json").exists()


def test_candidate_session_rolls_after_open():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import pandas as pd
    from kospi_shadow.pipeline import candidate_session_date

    now = datetime(2026, 8, 3, 17, 20, tzinfo=ZoneInfo("Asia/Seoul"))
    candidate = candidate_session_date(now, pd.Timestamp("2026-07-31"))
    assert candidate == pd.Timestamp("2026-08-04")
