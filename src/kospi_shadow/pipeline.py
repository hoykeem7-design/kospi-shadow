from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import joblib
import pandas as pd

from .config import Settings
from .data import DataBundle, collect_data
from .features import build_feature_table
from .validation import evaluate_oos, expanding_walk_forward


def _log(message: str) -> None:
    print(f"[pipeline] {message}", flush=True)


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
        "bootstrap_support": cls["bootstrap_probability_model_beats_baseline"] >= float(
            promotion["min_bootstrap_win_probability"]
        ),
        "official_target": (not bool(promotion["require_official_target"])) or bool(manifest["target_official"]),
        "data_fresh": age <= int(promotion["max_data_age_calendar_days"]),
        "positive_net_return": (not bool(promotion["require_cost_adjusted_positive_return"]))
        or strat["model"]["cumulative_return"] > 0,
        "sharpe_above_long": (not bool(promotion["require_sharpe_above_long_baseline"]))
        or (strat["model"]["annualized_sharpe"] > strat["long_baseline"]["annualized_sharpe"]),
    }
    enabled = all(checks.values())
    return {
        "signal_enabled": enabled,
        "status": "VALIDATED_SHADOW" if enabled else "RESTRICTED_SHADOW",
        "checks": checks,
        "target_data_age_calendar_days": age,
    }


def candidate_session_date(now_seoul: datetime, latest_target_date: pd.Timestamp) -> pd.Timestamp:
    """Choose the session that the dashboard should discuss.

    Before the 15:30 regular-market close, repeated checkpoint runs retain the
    current session target. After the close they roll to the next business day.
    This prevents a 09:10 dashboard refresh from unexpectedly switching from
    today's forecast to tomorrow's forecast.
    """
    today = pd.Timestamp(now_seoul.date())
    weekday = now_seoul.weekday() < 5
    before_regular_close = (now_seoul.hour, now_seoul.minute) < (15, 30)
    decision_floor = today if weekday and before_regular_close else today + pd.offsets.BDay(1)
    next_after_target = latest_target_date.normalize() + pd.offsets.BDay(1)
    return max(pd.Timestamp(decision_floor), pd.Timestamp(next_after_target)).normalize()


_FACTOR_LABELS = {
    "sp500": "S&P500",
    "nasdaq": "NASDAQ",
    "sox": "반도체지수",
    "vix": "VIX",
    "usdk_rw": "원·달러 환율",
    "us10y": "미국 10년물 금리",
    "us2y": "미국 2년물 금리",
}


def _feature_label(name: str) -> str:
    if name.startswith("kospi_ret_lag_"):
        return f"KOSPI {name.rsplit('_', 1)[-1]}거래일 전 수익률"
    if name.startswith("kospi_mom_"):
        return f"KOSPI {name.rsplit('_', 1)[-1]}일 모멘텀"
    if name.startswith("kospi_vol_"):
        return f"KOSPI {name.rsplit('_', 1)[-1]}일 변동성"
    if name.startswith("kospi_ma_dist_"):
        return f"KOSPI {name.rsplit('_', 1)[-1]}일 이동평균 괴리"
    kospi_names = {
        "kospi_prev_gap": "전일 시가 갭",
        "kospi_prev_intraday": "전일 장중 수익률",
        "kospi_prev_range": "전일 장중 변동폭",
    }
    if name in kospi_names:
        return kospi_names[name]
    for suffix, label_suffix in (
        ("_level", "수준"),
        ("_ret1", "1일 변화율"),
        ("_ret5", "5일 변화율"),
        ("_vol20", "20일 변동성"),
    ):
        if name.endswith(suffix):
            base = name[: -len(suffix)]
            return f"{_FACTOR_LABELS.get(base, base.upper())} {label_suffix}"
    return name.replace("_", " ")


def _format_feature_value(name: str, value: float) -> str:
    percentage_tokens = ("ret", "mom", "vol", "ma_dist", "gap", "intraday", "range")
    if any(token in name for token in percentage_tokens):
        return f"{value:+.2%}"
    return f"{value:,.2f}"


def _build_prediction_explanation(
    final_model: Any,
    row: pd.DataFrame,
    feature_cols: list[str],
    final_probability: float,
    *,
    limit: int = 3,
) -> dict[str, Any]:
    """Explain the final probability without pretending to provide causality.

    The deployed model blends a raw estimator probability with the historical
    training prior.  Each local factor effect below is a one-feature
    counterfactual: the current feature is replaced with the fitted imputer's
    training median (by passing NaN), while all other features stay fixed.
    This works for both the logistic and histogram-gradient candidates.
    """
    prior = float(getattr(final_model, "prior_probability", final_probability))
    model_weight = float(getattr(final_model, "shrinkage", 1.0))
    model_weight = max(0.0, min(1.0, model_weight))
    estimator = getattr(final_model, "estimator", None)
    try:
        raw_probability = float(estimator.predict_proba(row)[:, 1][0]) if estimator is not None else final_probability
    except Exception:
        raw_probability = final_probability

    records: list[dict[str, Any]] = []
    excluded = {"day_of_week", "month"}
    for feature in feature_cols:
        if feature in excluded or feature.endswith("_age_days"):
            continue
        value = row.iloc[0][feature]
        if pd.isna(value):
            continue
        counterfactual = row.copy()
        counterfactual.loc[counterfactual.index[0], feature] = float("nan")
        try:
            neutral_probability = float(final_model.predict_proba(counterfactual)[:, 1][0])
        except Exception:
            continue
        effect = float(final_probability - neutral_probability)
        if abs(effect) < 0.00005:
            continue
        numeric_value = float(value)
        records.append({
            "feature": feature,
            "label": _feature_label(feature),
            "value": numeric_value,
            "value_text": _format_feature_value(feature, numeric_value),
            "effect_probability_points": effect,
            "neutral_probability": neutral_probability,
        })

    positive = sorted((item for item in records if item["effect_probability_points"] > 0),
                      key=lambda item: item["effect_probability_points"], reverse=True)[:limit]
    negative = sorted((item for item in records if item["effect_probability_points"] < 0),
                      key=lambda item: item["effect_probability_points"])[:limit]

    if model_weight <= 0.05:
        summary = (
            f"최종 {final_probability:.1%}는 거의 전부 학습 기준확률 {prior:.1%}에서 왔습니다. "
            f"검증 과정에서 원모델 반영비중이 {model_weight:.0%}로 축소되어, "
            "오늘의 개별 변수보다 과거 학습구간의 장중 상승 빈도가 주된 이유입니다."
        )
    elif model_weight < 0.5:
        summary = (
            f"최종 {final_probability:.1%}는 학습 기준확률 {prior:.1%}를 {1.0-model_weight:.0%}, "
            f"원모델 확률 {raw_probability:.1%}를 {model_weight:.0%} 반영한 값입니다. "
            "따라서 당일 요인보다 장기 기준확률의 영향이 더 큽니다."
        )
    else:
        summary = (
            f"최종 {final_probability:.1%}는 학습 기준확률 {prior:.1%}와 "
            f"원모델 확률 {raw_probability:.1%}를 결합한 값이며, 원모델 반영비중은 {model_weight:.0%}입니다."
        )

    return {
        "method": "one_feature_to_training_median",
        "final_probability": final_probability,
        "training_prior_probability": prior,
        "raw_model_probability": raw_probability,
        "model_weight": model_weight,
        "prior_weight": 1.0 - model_weight,
        "summary": summary,
        "positive_factors": positive,
        "negative_factors": negative,
        "feature_count_evaluated": len(records),
        "note": (
            "기여도는 해당 변수 하나만 학습 중간값으로 바꿔 본 국소 민감도입니다. "
            "인과관계나 수익 보장을 뜻하지 않으며, 변수 간 상관 때문에 기여도의 합은 최종 확률과 정확히 일치하지 않습니다."
        ),
    }


def _make_latest_prediction(
    final_model: Any,
    target: pd.DataFrame,
    factors: dict[str, pd.DataFrame],
    feature_cols: list[str],
    model_cfg: dict[str, Any],
    gate: dict[str, Any],
    *,
    trained_at_utc: str | None = None,
) -> dict[str, Any]:
    now_seoul = datetime.now(ZoneInfo("Asia/Seoul"))
    latest_target_date = pd.to_datetime(target["Date"]).max().normalize()
    candidate_date = candidate_session_date(now_seoul, latest_target_date)

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
        extended,
        factors,
        max_feature_staleness_days=int(model_cfg["max_feature_staleness_days"]),
    )
    if live_cols != feature_cols:
        raise RuntimeError("Live feature schema differs from training schema")
    row = live_table.loc[live_table["Date"] == candidate_date, feature_cols]
    if len(row) != 1:
        raise RuntimeError("Could not construct exactly one live feature row")
    probability = float(final_model.predict_proba(row)[:, 1][0])
    explanation = _build_prediction_explanation(final_model, row, feature_cols, probability)
    threshold = float(model_cfg["probability_trade_threshold"])
    direction = "LONG" if probability >= threshold else ("SHORT" if probability <= 1.0 - threshold else "FLAT")
    before_open_cutoff = now_seoul.weekday() < 5 and now_seoul.hour < 9
    timing_valid = before_open_cutoff and candidate_date.date() == now_seoul.date()
    if candidate_date.date() > now_seoul.date():
        prediction_scope = "next_session_plan"
    elif before_open_cutoff:
        prediction_scope = "preopen_full_session"
    else:
        prediction_scope = "current_session_reference_not_remaining_session_probability"
    actionable = bool(gate["signal_enabled"] and timing_valid)
    if not gate["signal_enabled"]:
        reason = "Model promotion gate is closed; research output only."
    elif not timing_valid:
        reason = "Prediction was not generated before 09:00 for the target session."
    else:
        reason = "Gate and timing passed, but execution-instrument fills are still not modeled."
        actionable = False
    return {
        "generated_at_seoul": now_seoul.isoformat(),
        "candidate_target_date": candidate_date.strftime("%Y-%m-%d"),
        "latest_target_observation": latest_target_date.strftime("%Y-%m-%d"),
        "probability_intraday_up": probability,
        "research_direction": direction,
        "probability_threshold": threshold,
        "model_gate_signal_enabled": bool(gate["signal_enabled"]),
        "timing_before_09_00": before_open_cutoff,
        "timing_valid_for_target": timing_valid,
        "prediction_scope": prediction_scope,
        "trained_at_utc": trained_at_utc,
        "actionable": actionable,
        "actionable_reason": reason,
        "explanation": explanation,
    }


def _write_daily_brief(output_dir: Path, metrics: dict[str, Any]) -> None:
    pred = metrics["latest_prediction"]
    gate = metrics["promotion"]
    cls = metrics["classification"]
    strategy = metrics["strategy_proxy"]["model"]
    failed = [name for name, passed in gate["checks"].items() if not passed]
    brief = f"""# KOSPI SHADOW Daily Brief

- Target session: **{pred['candidate_target_date']}**
- Intraday-up probability: **{pred['probability_intraday_up']:.1%}**
- Research direction: **{pred['research_direction']}**
- Status: **{gate['status']}** (`signal_enabled={str(gate['signal_enabled']).lower()}`)
- Timing valid: **{str(pred['timing_valid_for_target']).lower()}**
- Actionable: **false**

## Validation snapshot

- OOS observations: {cls['n']}
- Brier improvement vs expanding prior: {cls['brier_improvement']:.6f}
- ROC-AUC: {cls['roc_auc']:.4f}
- Cost-adjusted cumulative proxy return: {strategy['cumulative_return']:.2%}
- Failed promotion checks: {', '.join(failed) if failed else 'none'}

## Interpretation

{pred['actionable_reason']} Do not place an order solely from this artifact.
"""
    (output_dir / "daily_brief.md").write_text(brief, encoding="utf-8")


def _state_paths(project_root: Path) -> dict[str, Path]:
    state_dir = project_root / "state"
    return {
        "dir": state_dir,
        "model": state_dir / "challenger_model.joblib",
        "features": state_dir / "feature_columns.json",
        "metrics": state_dir / "training_metrics.json",
        "meta": state_dir / "training_state.json",
    }


def _save_state(
    project_root: Path,
    model: Any,
    feature_cols: list[str],
    metrics: dict[str, Any],
) -> None:
    paths = _state_paths(project_root)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    joblib.dump(model, paths["model"])
    paths["features"].write_text(json.dumps(feature_cols, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["metrics"].write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["meta"].write_text(
        json.dumps({
            "trained_at_utc": metrics["created_at_utc"],
            "final_selected_model": metrics["final_selected_model"],
            "target_date_max": metrics["data_manifest"]["target_date_max"],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _state_is_usable(project_root: Path, max_age_days: int) -> bool:
    paths = _state_paths(project_root)
    if not all(paths[k].exists() for k in ("model", "features", "metrics", "meta")):
        return False
    try:
        meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
        trained = pd.Timestamp(meta["trained_at_utc"])
        now = pd.Timestamp.now(tz="UTC")
        return (now - trained).total_seconds() <= max_age_days * 86400
    except Exception:
        return False


def _run_full(settings: Settings, project_root: Path, bundle: DataBundle | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    project = settings.section("project")
    data_cfg = settings.section("data")
    model_cfg = settings.section("model")
    promotion_cfg = settings.section("promotion")
    output_dir = project_root / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    _log("mode=full: collect data and perform walk-forward validation")
    if bundle is None:
        bundle = collect_data(data_cfg, project_root, allow_provisional=False)
    feature_started = time.perf_counter()
    feature_table, feature_cols = build_feature_table(
        bundle.target,
        bundle.factors,
        max_feature_staleness_days=int(model_cfg["max_feature_staleness_days"]),
    )
    feature_seconds = time.perf_counter() - feature_started
    _log(f"feature table rows={len(feature_table)} cols={len(feature_cols)} in {feature_seconds:.1f}s")

    validation_started = time.perf_counter()
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
        candidate_profile=str(model_cfg.get("candidate_profile", "compact")),
    )
    validation_seconds = time.perf_counter() - validation_started
    evaluated = evaluate_oos(
        oos,
        threshold=float(model_cfg["probability_trade_threshold"]),
        cost_bps=float(model_cfg["transaction_cost_bps_per_side"]),
        bootstrap_iterations=int(promotion_cfg["bootstrap_iterations"]),
        random_state=int(project["random_state"]),
    )
    enriched = evaluated.pop("enriched_oos")
    gate = promotion_gate(evaluated, bundle.manifest, promotion_cfg)
    created_at = datetime.now(timezone.utc).isoformat()
    latest_prediction = _make_latest_prediction(
        final_model,
        bundle.target,
        bundle.factors,
        feature_cols,
        model_cfg,
        gate,
        trained_at_utc=created_at,
    )

    metrics = {
        "created_at_utc": created_at,
        "run_mode": "full",
        "project": project["name"],
        "model_target": model_cfg["target"],
        "return_column": model_cfg["return_column"],
        "final_selected_model": final_model_name,
        "data_manifest": bundle.manifest,
        "runtime_seconds": {
            "features": round(feature_seconds, 3),
            "validation_and_fit": round(validation_seconds, 3),
            "total": round(time.perf_counter() - started, 3),
        },
        "validation_design": {
            "outer": "expanding walk-forward blocks",
            "inner": f"TimeSeriesSplit(n_splits={model_cfg['inner_splits']}, gap={model_cfg['purge_gap_sessions']})",
            "test_block_sessions": int(model_cfg["outer_test_block_sessions"]),
            "minimum_initial_train_sessions": int(model_cfg["min_train_sessions"]),
            "candidate_profile": str(model_cfg.get("candidate_profile", "compact")),
            "probability_regularization": "inner-CV shrinkage toward training prior",
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
            "A passing backtest does not establish future profitability.",
        ],
    }

    joblib.dump(final_model, output_dir / "challenger_model.joblib")
    enriched.to_csv(output_dir / "oos_predictions.csv", index=False)
    feature_table.tail(400).to_csv(output_dir / "feature_tail.csv", index=False)
    (output_dir / "feature_columns.json").write_text(json.dumps(feature_cols, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "data_manifest.json").write_text(json.dumps(bundle.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "latest_prediction.json").write_text(json.dumps(latest_prediction, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_daily_brief(output_dir, metrics)
    _save_state(project_root, final_model, feature_cols, metrics)
    _write_model_card(output_dir, metrics)
    _log(f"full run complete in {metrics['runtime_seconds']['total']}s; status={gate['status']}")
    return metrics


def _run_predict(settings: Settings, project_root: Path, bundle: DataBundle | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    data_cfg = settings.section("data")
    model_cfg = settings.section("model")
    promotion_cfg = settings.section("promotion")
    output_dir = project_root / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = _state_paths(project_root)
    if not _state_is_usable(project_root, int(model_cfg.get("max_model_age_days", 8))):
        raise RuntimeError("No usable trained state; run with --mode full first")

    _log("mode=predict: restore trained state and refresh only data/features")
    training_metrics = json.loads(paths["metrics"].read_text(encoding="utf-8"))
    meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
    feature_cols = json.loads(paths["features"].read_text(encoding="utf-8"))
    model = joblib.load(paths["model"])
    if bundle is None:
        bundle = collect_data(data_cfg, project_root, allow_provisional=True)
    gate = promotion_gate(training_metrics, bundle.manifest, promotion_cfg)
    latest_prediction = _make_latest_prediction(
        model,
        bundle.target,
        bundle.factors,
        feature_cols,
        model_cfg,
        gate,
        trained_at_utc=meta.get("trained_at_utc"),
    )
    metrics = {
        **training_metrics,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_mode": "predict",
        "data_manifest": bundle.manifest,
        "promotion": gate,
        "latest_prediction": latest_prediction,
        "runtime_seconds": {"total": round(time.perf_counter() - started, 3)},
        "training_created_at_utc": meta.get("trained_at_utc"),
    }
    (output_dir / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "data_manifest.json").write_text(json.dumps(bundle.manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "latest_prediction.json").write_text(json.dumps(latest_prediction, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_daily_brief(output_dir, metrics)
    _write_model_card(output_dir, metrics)
    _log(f"prediction complete in {metrics['runtime_seconds']['total']}s; target={latest_prediction['candidate_target_date']}")
    return metrics


def _write_model_card(output_dir: Path, metrics: dict[str, Any]) -> None:
    gate = metrics["promotion"]
    pred = metrics["latest_prediction"]
    manifest = metrics["data_manifest"]
    cls = metrics["classification"]
    card = f"""# KOSPI SHADOW COACH v4.0 — Model Card

## Status

**{gate['status']}**  
`signal_enabled={str(gate['signal_enabled']).lower()}`

## Current research prediction

- Candidate target date: {pred['candidate_target_date']}
- Intraday-up probability: {pred['probability_intraday_up']:.4f}
- Research direction: {pred['research_direction']}
- Timing valid: {pred['timing_valid_for_target']}
- Actionable: **false** — {pred['actionable_reason']}

## Validation

- OOS observations: {cls['n']}
- Model Brier: {cls['brier']:.6f}
- Expanding-prior baseline Brier: {cls['baseline_brier']:.6f}
- Brier improvement: {cls['brier_improvement']:.6f}
- Bootstrap probability of beating baseline: {cls['bootstrap_probability_model_beats_baseline']:.3f}
- Probability predictions are shrunk toward the training prior using inner time-series CV.

## Data

- Target provider: `{manifest['target_provider']}`
- Official target: `{manifest['target_official']}`
- Target range: {manifest['target_date_min']} to {manifest['target_date_max']}
- Factors: {', '.join(manifest['factor_names'])}
- Collection warnings: {len(manifest['collection_warnings'])}

## Operating design

- Weekly/full mode performs leakage-controlled validation and refits the model.
- Daily/predict mode reuses the validated state and only refreshes data and the next-session probability.
- This remains a research system and does not execute trades.
"""
    (output_dir / "model_card.md").write_text(card, encoding="utf-8")


def run_pipeline(
    settings: Settings,
    project_root: Path,
    bundle: DataBundle | None = None,
    *,
    mode: str = "auto",
) -> dict[str, Any]:
    if mode not in {"auto", "full", "predict"}:
        raise ValueError("mode must be auto, full, or predict")
    model_cfg = settings.section("model")
    resolved = mode
    if mode == "auto":
        resolved = "predict" if _state_is_usable(project_root, int(model_cfg.get("max_model_age_days", 8))) else "full"
    return _run_full(settings, project_root, bundle=bundle) if resolved == "full" else _run_predict(
        settings, project_root, bundle=bundle
    )
