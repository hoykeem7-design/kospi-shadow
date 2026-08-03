from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import joblib
import pandas as pd

from .config import Settings
from .data import DataBundle, collect_data
from .features import build_feature_table
from .validation import evaluate_oos, expanding_walk_forward


def promotion_gate(
    metrics: dict[str, Any],
    manifest: dict[str, Any],
    promotion: dict[str, Any],
) -> dict[str, Any]:
    cls = metrics["classification"]
    strat = metrics["strategy_proxy"]
    latest = pd.Timestamp(manifest["target_date_max"]).date()
    seoul_today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    age = (seoul_today - latest).days
    checks = {
        "minimum_oos": cls["n"] >= int(promotion["min_oos_sessions"]),
        "brier_improvement": cls["brier_improvement"] >= float(promotion["min_brier_improvement"]),
        "bootstrap_support": cls["bootstrap_probability_model_beats_baseline"] >= float(promotion["min_bootstrap_win_probability"]),
        "official_target": (not bool(promotion["require_official_target"])) or bool(manifest["target_official"]),
        "data_fresh": age <= int(promotion["max_data_age_calendar_days"]),
        "positive_net_return": (not bool(promotion["require_cost_adjusted_positive_return"])) or strat["model"]["cumulative_return"] > 0,
        "sharpe_above_long": (not bool(promotion["require_sharpe_above_long_baseline"])) or (
            strat["model"]["annualized_sharpe"] > strat["long_baseline"]["annualized_sharpe"]
        ),
    }
    enabled = all(checks.values())
    return {
        "signal_enabled": enabled,
        "status": "VALIDATED_SHADOW" if enabled else "RESTRICTED_SHADOW",
        "checks": checks,
        "target_data_age_calendar_days": age,
    }


def _make_latest_prediction(
    final_model: Any,
    target: pd.DataFrame,
    factors: dict[str, pd.DataFrame],
    feature_cols: list[str],
    model_cfg: dict[str, Any],
    gate: dict[str, Any],
) -> dict[str, Any]:
    now_seoul = datetime.now(ZoneInfo("Asia/Seoul"))
    candidate_date = pd.Timestamp(now_seoul.date())
    latest_target_date = pd.to_datetime(target["Date"]).max().normalize()
    if candidate_date <= latest_target_date:
        candidate_date = latest_target_date + pd.offsets.BDay(1)

    placeholder = pd.DataFrame([{
        "Date": candidate_date,
        "Open": float("nan"),
        "High": float("nan"),
        "Low": float("nan"),
        "Close": float("nan"),
        "Volume": float("nan"),
    }])
    extended = pd.concat([target, placeholder], ignore_index=True)
    live_table, live_cols = build_feature_table(
        extended, factors,
        max_feature_staleness_days=int(model_cfg["max_feature_staleness_days"]),
    )
    if live_cols != feature_cols:
        raise RuntimeError("Live feature schema differs from training schema")
    row = live_table.loc[live_table["Date"] == candidate_date, feature_cols]
    if len(row) != 1:
        raise RuntimeError("Could not construct exactly one live feature row")
    probability = float(final_model.predict_proba(row)[:, 1][0])
    threshold = float(model_cfg["probability_trade_threshold"])
    direction = "LONG" if probability >= threshold else ("SHORT" if probability <= 1.0 - threshold else "FLAT")
    before_open_cutoff = now_seoul.hour < 9
    weekday = now_seoul.weekday() < 5
    return {
        "generated_at_seoul": now_seoul.isoformat(),
        "candidate_target_date": candidate_date.strftime("%Y-%m-%d"),
        "probability_intraday_up": probability,
        "research_direction": direction,
        "model_gate_signal_enabled": bool(gate["signal_enabled"]),
        "timing_before_09_00": before_open_cutoff,
        "weekday": weekday,
        "actionable": False,
        "actionable_reason": "Research-only index proxy; Korean exchange holiday and tradable-instrument execution are not verified.",
    }


def run_pipeline(settings: Settings, project_root: Path, bundle: DataBundle | None = None) -> dict[str, Any]:
    project = settings.section("project")
    data_cfg = settings.section("data")
    model_cfg = settings.section("model")
    promotion_cfg = settings.section("promotion")
    output_dir = project_root / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    if bundle is None:
        bundle = collect_data(data_cfg, project_root)

    feature_table, feature_cols = build_feature_table(
        bundle.target,
        bundle.factors,
        max_feature_staleness_days=int(model_cfg["max_feature_staleness_days"]),
    )
    oos, final_model, final_model_name = expanding_walk_forward(
        feature_table,
        feature_cols,
        str(model_cfg["target"]),
        str(model_cfg["return_column"]),
        min_train=int(model_cfg["min_train_sessions"]),
        test_block=int(model_cfg["outer_test_block_sessions"]),
        inner_splits=int(model_cfg["inner_splits"]),
        gap=int(model_cfg["purge_gap_sessions"]),
        random_state=int(project["random_state"]),
    )
    evaluated = evaluate_oos(
        oos,
        threshold=float(model_cfg["probability_trade_threshold"]),
        cost_bps=float(model_cfg["transaction_cost_bps_per_side"]),
        bootstrap_iterations=int(promotion_cfg["bootstrap_iterations"]),
        random_state=int(project["random_state"]),
    )
    enriched = evaluated.pop("enriched_oos")
    gate = promotion_gate(evaluated, bundle.manifest, promotion_cfg)
    latest_prediction = _make_latest_prediction(
        final_model, bundle.target, bundle.factors, feature_cols, model_cfg, gate
    )

    metrics = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "project": project["name"],
        "model_target": model_cfg["target"],
        "return_column": model_cfg["return_column"],
        "final_selected_model": final_model_name,
        "data_manifest": bundle.manifest,
        "validation_design": {
            "outer": "expanding walk-forward blocks",
            "inner": f"TimeSeriesSplit(n_splits={model_cfg['inner_splits']}, gap={model_cfg['purge_gap_sessions']})",
            "test_block_sessions": int(model_cfg["outer_test_block_sessions"]),
            "minimum_initial_train_sessions": int(model_cfg["min_train_sessions"]),
            "external_factor_alignment": "strictly prior calendar date; exact date matches prohibited",
            "strategy_note": "Research proxy on KOSPI index returns; not an executable ETF/futures fill model.",
        },
        **evaluated,
        "promotion": gate,
        "latest_prediction": latest_prediction,
        "limitations": [
            "Yahoo factors are unofficial and intended for research/personal use; provider availability is not guaranteed.",
            "FRED final historical observations are not a substitute for ALFRED point-in-time vintages for revised macro series.",
            "The strategy calculation uses index returns as a proxy and does not model an actual ETF/futures order book, taxes, spread, or market impact.",
            "Scheduled GitHub workflows can be delayed or occasionally dropped during high load.",
            "A passing backtest does not establish future profitability.",
        ],
    }

    joblib.dump(final_model, output_dir / "challenger_model.joblib")
    enriched.to_csv(output_dir / "oos_predictions.csv", index=False)
    feature_table.tail(400).to_csv(output_dir / "feature_tail.csv", index=False)
    (output_dir / "feature_columns.json").write_text(
        json.dumps(feature_cols, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "data_manifest.json").write_text(
        json.dumps(bundle.manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "latest_prediction.json").write_text(
        json.dumps(latest_prediction, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    card = f"""# KOSPI SHADOW AUTO v2 — Model Card

## Status

**{gate['status']}**  
`signal_enabled={str(gate['signal_enabled']).lower()}`

## Target and timing

- Target: `{model_cfg['target']}`
- Return used by strategy proxy: `{model_cfg['return_column']}`
- Intended decision time: before the KOSPI session opens.
- External daily factors are matched only when their observation date is **strictly earlier** than the KOSPI target date.

## Validation

- Expanding walk-forward outer evaluation.
- Model selection occurs inside each training window only.
- Inner split uses an explicit {model_cfg['purge_gap_sessions']}-session gap.
- OOS observations: {metrics['classification']['n']}
- Model Brier: {metrics['classification']['brier']:.6f}
- Expanding-rate baseline Brier: {metrics['classification']['baseline_brier']:.6f}
- Brier improvement: {metrics['classification']['brier_improvement']:.6f}
- Bootstrap probability of beating baseline: {metrics['classification']['bootstrap_probability_model_beats_baseline']:.3f}

## Promotion checks

```json
{json.dumps(gate['checks'], ensure_ascii=False, indent=2)}
```

## Data

- Target provider: `{bundle.manifest['target_provider']}`
- Official target: `{bundle.manifest['target_official']}`
- Target range: {bundle.manifest['target_date_min']} to {bundle.manifest['target_date_max']}
- Collection warnings: {len(bundle.manifest['collection_warnings'])}

## Latest research prediction

- Candidate target date: {latest_prediction['candidate_target_date']}
- Intraday-up probability: {latest_prediction['probability_intraday_up']:.4f}
- Research direction: {latest_prediction['research_direction']}
- Actionable: **false** — {latest_prediction['actionable_reason']}

## Interpretation

This is a shadow-research system. It is allowed to produce a probability artifact even when restricted, but live signals remain disabled unless every promotion check passes.
"""
    (output_dir / "model_card.md").write_text(card, encoding="utf-8")
    return metrics
