from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
from sklearn.metrics import (
    brier_score_loss,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)

from .premarket import validate_stage_feature_bundle


@dataclass(frozen=True)
class BacktestRecord:
    symbol: str
    trading_date: str
    stage: str
    probability: float | None
    actual_label: bool | None
    realized_return: float | None = None
    feature_cutoff: str | None = None
    feature_bundle: dict[str, Any] | None = None


def build_backtest_dataset(
    records: Iterable[BacktestRecord], *, stage: str
) -> tuple[list[BacktestRecord], dict[str, int]]:
    """Select a stage and enforce its point-in-time cutoff before scoring."""
    selected: list[BacktestRecord] = []
    verified = 0
    unknown_time = 0
    for record in records:
        if record.stage != stage:
            continue
        if record.feature_bundle is None:
            unknown_time += 1
        else:
            checked = validate_stage_feature_bundle(
                record.feature_bundle,
                trading_date=record.trading_date,
                stage=stage,
            )
            if any(item.get("cutoff_validation") == "timestamp_unavailable" for item in checked):
                unknown_time += 1
            else:
                verified += 1
        selected.append(record)
    return selected, {
        "cutoff_verified_record_count": verified,
        "unknown_time_record_count": unknown_time,
    }


def _calibration(actual: np.ndarray, probability: np.ndarray, bins: int = 10) -> dict[str, Any]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    rows: list[dict[str, Any]] = []
    weighted_error = 0.0
    for index in range(bins):
        lower = edges[index]
        upper = edges[index + 1]
        mask = (probability >= lower) & (probability < upper if index < bins - 1 else probability <= upper)
        count = int(mask.sum())
        if count == 0:
            continue
        predicted = float(probability[mask].mean())
        observed = float(actual[mask].mean())
        weighted_error += count * abs(predicted - observed)
        rows.append({
            "lower": float(lower),
            "upper": float(upper),
            "sample_count": count,
            "mean_probability": predicted,
            "observed_rate": observed,
        })
    return {"bins": rows, "expected_calibration_error": weighted_error / len(actual)}


def evaluate_stage_backtest(
    records: Iterable[BacktestRecord],
    *,
    stage: str,
    minimum_sample_count: int,
    transaction_cost_bps_per_side: float,
    slippage_bps_per_side: float,
) -> dict[str, Any]:
    if stage not in {"premarket_prediction", "post_open_0905_prediction"}:
        raise ValueError("unknown two-stage backtest stage")
    stage_records, cutoff_quality = build_backtest_dataset(records, stage=stage)
    selected = [
        record for record in stage_records
        if record.probability is not None
        and record.actual_label is not None
    ]
    if len(selected) < int(minimum_sample_count):
        return {
            "status": "unavailable",
            "reason": "insufficient_labeled_probabilities",
            "stage": stage,
            "sample_count": len(selected),
            "minimum_required_sample_count": int(minimum_sample_count),
            "brier_score": None,
            "log_loss": None,
            "roc_auc": None,
            "precision": None,
            "recall": None,
            "f1": None,
            "calibration": None,
            "cost_adjusted_expected_return": None,
            "max_drawdown": None,
            "feature_time_quality": cutoff_quality,
        }
    actual = np.asarray([int(record.actual_label) for record in selected], dtype=int)
    probability = np.clip(np.asarray([float(record.probability) for record in selected]), 1e-6, 1 - 1e-6)
    predicted = (probability >= 0.5).astype(int)
    returns = np.asarray([
        float(record.realized_return) if record.realized_return is not None else np.nan
        for record in selected
    ])
    cost = 2.0 * (float(transaction_cost_bps_per_side) + float(slippage_bps_per_side)) / 10000.0
    valid_returns = np.isfinite(returns)
    cost_adjusted = np.where(predicted == 1, returns, -returns) - cost
    usable_returns = cost_adjusted[valid_returns]
    if len(usable_returns):
        curve = np.cumprod(1.0 + usable_returns)
        peaks = np.maximum.accumulate(curve)
        max_drawdown = float(np.min(curve / peaks - 1.0))
        expected_return = float(np.mean(usable_returns))
    else:
        max_drawdown = None
        expected_return = None
    return {
        "status": "available",
        "reason": None,
        "stage": stage,
        "sample_count": len(selected),
        "symbol_count": len({record.symbol for record in selected}),
        "trading_day_count": len({record.trading_date for record in selected}),
        "brier_score": float(brier_score_loss(actual, probability)),
        "log_loss": float(log_loss(actual, probability, labels=[0, 1])),
        "roc_auc": float(roc_auc_score(actual, probability)) if len(np.unique(actual)) == 2 else None,
        "precision": float(precision_score(actual, predicted, zero_division=0)),
        "recall": float(recall_score(actual, predicted, zero_division=0)),
        "f1": float(f1_score(actual, predicted, zero_division=0)),
        "calibration": _calibration(actual, probability),
        "cost_adjusted_expected_return": expected_return,
        "max_drawdown": max_drawdown,
        "transaction_cost_bps_per_side": float(transaction_cost_bps_per_side),
        "slippage_bps_per_side": float(slippage_bps_per_side),
        "feature_time_quality": cutoff_quality,
    }
