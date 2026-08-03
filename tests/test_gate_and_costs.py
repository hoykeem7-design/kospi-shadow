from __future__ import annotations

from datetime import date

import numpy as np

from kospi_shadow.pipeline import promotion_gate
from kospi_shadow.validation import strategy_metrics


def test_intraday_cost_charges_two_sides():
    metrics, position, net = strategy_metrics(
        realized_return=np.array([0.01]),
        probability=np.array([0.90]),
        threshold=0.55,
        cost_bps=5.0,
    )
    assert position.tolist() == [1.0]
    assert abs(net[0] - 0.009) < 1e-12  # 10bp round-trip cost
    assert abs(metrics["long_baseline"]["cumulative_return"] - 0.009) < 1e-12


def test_promotion_gate_can_pass_only_when_all_checks_pass():
    metrics = {
        "classification": {
            "n": 400,
            "brier_improvement": 0.01,
            "bootstrap_probability_model_beats_baseline": 0.95,
        },
        "strategy_proxy": {
            "model": {"cumulative_return": 0.10, "annualized_sharpe": 1.1},
            "long_baseline": {"annualized_sharpe": 0.4},
        },
    }
    manifest = {
        "target_date_max": date.today().isoformat(),
        "target_official": True,
    }
    promotion = {
        "min_oos_sessions": 252,
        "min_brier_improvement": 0.0025,
        "min_bootstrap_win_probability": 0.80,
        "require_official_target": True,
        "require_cost_adjusted_positive_return": True,
        "require_sharpe_above_long_baseline": True,
        "max_data_age_calendar_days": 7,
    }
    result = promotion_gate(metrics, manifest, promotion)
    assert result["signal_enabled"] is True
    manifest["target_official"] = False
    result = promotion_gate(metrics, manifest, promotion)
    assert result["signal_enabled"] is False
