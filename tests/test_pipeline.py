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


def test_candidate_session_stays_on_current_day_during_market():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import pandas as pd
    from kospi_shadow.pipeline import candidate_session_date

    now = datetime(2026, 8, 3, 9, 10, tzinfo=ZoneInfo("Asia/Seoul"))
    candidate = candidate_session_date(now, pd.Timestamp("2026-07-31"))
    assert candidate == pd.Timestamp("2026-08-03")



def test_prediction_explanation_shows_prior_blend_and_local_drivers():
    import numpy as np
    import pandas as pd
    from kospi_shadow.pipeline import _build_prediction_explanation

    class FakeEstimator:
        def predict_proba(self, frame):
            momentum = frame["kospi_mom_5"].fillna(0.0).to_numpy(dtype=float)
            fx = frame["usdk_rw_ret1"].fillna(0.0).to_numpy(dtype=float)
            raw = np.clip(0.55 + 2.0 * momentum - 2.0 * fx, 0.01, 0.99)
            return np.column_stack([1.0 - raw, raw])

    class FakeModel:
        prior_probability = 0.40
        shrinkage = 0.50
        estimator = FakeEstimator()

        def predict_proba(self, frame):
            raw = self.estimator.predict_proba(frame)[:, 1]
            blended = self.shrinkage * raw + (1.0 - self.shrinkage) * self.prior_probability
            return np.column_stack([1.0 - blended, blended])

    row = pd.DataFrame([{"kospi_mom_5": 0.03, "usdk_rw_ret1": 0.02, "day_of_week": 1.0}])
    model = FakeModel()
    probability = float(model.predict_proba(row)[:, 1][0])
    result = _build_prediction_explanation(model, row, list(row.columns), probability)

    assert result["training_prior_probability"] == 0.40
    assert result["model_weight"] == 0.50
    assert result["positive_factors"][0]["feature"] == "kospi_mom_5"
    assert result["negative_factors"][0]["feature"] == "usdk_rw_ret1"
    assert "원모델 확률" in result["summary"]


def test_prediction_explanation_calls_out_prior_only_result():
    import numpy as np
    import pandas as pd
    from kospi_shadow.pipeline import _build_prediction_explanation

    class PriorOnlyModel:
        prior_probability = 0.406
        shrinkage = 0.0
        estimator = None

        def predict_proba(self, frame):
            p = np.full(len(frame), self.prior_probability)
            return np.column_stack([1.0 - p, p])

    row = pd.DataFrame([{"kospi_mom_5": 0.03}])
    result = _build_prediction_explanation(PriorOnlyModel(), row, list(row.columns), 0.406)
    assert result["positive_factors"] == []
    assert result["negative_factors"] == []
    assert "거의 전부 학습 기준확률" in result["summary"]
