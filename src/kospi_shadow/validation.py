from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class Candidate:
    name: str
    estimator: Pipeline


def make_candidates(random_state: int) -> list[Candidate]:
    candidates: list[Candidate] = []
    for c in (0.03, 0.1, 0.3, 1.0):
        estimator = Pipeline([
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=c, max_iter=4000, random_state=random_state)),
        ])
        candidates.append(Candidate(name=f"logistic_C{c}", estimator=estimator))
    for leaves in (7, 15, 31):
        estimator = Pipeline([
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("model", HistGradientBoostingClassifier(
                learning_rate=0.05,
                max_iter=150,
                max_leaf_nodes=leaves,
                min_samples_leaf=20,
                l2_regularization=1.0,
                random_state=random_state,
            )),
        ])
        candidates.append(Candidate(name=f"histgb_leaves{leaves}", estimator=estimator))
    return candidates


def _safe_brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(brier_score_loss(y, np.clip(p, 1e-6, 1 - 1e-6)))


def select_candidate(
    X: pd.DataFrame,
    y: pd.Series,
    candidates: list[Candidate],
    inner_splits: int,
    gap: int,
) -> tuple[Candidate, dict[str, float]]:
    if len(X) < 120:
        raise ValueError("Not enough observations for inner time-series validation")
    splitter = TimeSeriesSplit(n_splits=inner_splits, gap=gap)
    scores: dict[str, float] = {}
    for candidate in candidates:
        fold_scores: list[float] = []
        for train_idx, val_idx in splitter.split(X):
            if len(np.unique(y.iloc[train_idx])) < 2:
                continue
            model = clone(candidate.estimator)
            model.fit(X.iloc[train_idx], y.iloc[train_idx])
            prob = model.predict_proba(X.iloc[val_idx])[:, 1]
            fold_scores.append(_safe_brier(y.iloc[val_idx].to_numpy(), prob))
        scores[candidate.name] = float(np.mean(fold_scores)) if fold_scores else float("inf")
    best = min(candidates, key=lambda c: scores[c.name])
    return best, scores


def expanding_walk_forward(
    frame: pd.DataFrame,
    feature_cols: list[str],
    target_col: str,
    return_col: str,
    *,
    min_train: int,
    test_block: int,
    inner_splits: int,
    gap: int,
    random_state: int,
) -> tuple[pd.DataFrame, Pipeline, str]:
    data = frame[["Date", target_col, return_col, *feature_cols]].copy()
    data = data.dropna(subset=[target_col, return_col]).reset_index(drop=True)
    if len(data) < min_train + test_block:
        raise ValueError(f"Need at least {min_train + test_block} usable rows; got {len(data)}")

    X = data[feature_cols]
    y = data[target_col].astype(int)
    candidates = make_candidates(random_state)
    records: list[dict[str, Any]] = []
    last_selected = ""
    final_model: Pipeline | None = None

    for test_start in range(min_train, len(data), test_block):
        test_end = min(test_start + test_block, len(data))
        train_end = max(0, test_start - gap)
        X_train = X.iloc[:train_end]
        y_train = y.iloc[:train_end]
        if len(np.unique(y_train)) < 2:
            continue
        selected, cv_scores = select_candidate(X_train, y_train, candidates, inner_splits, gap)
        model = clone(selected.estimator).fit(X_train, y_train)
        prob = model.predict_proba(X.iloc[test_start:test_end])[:, 1]
        baseline_p = float(y_train.mean())
        for offset, row_idx in enumerate(range(test_start, test_end)):
            records.append({
                "Date": data.iloc[row_idx]["Date"],
                "actual": int(y.iloc[row_idx]),
                "realized_return": float(data.iloc[row_idx][return_col]),
                "probability": float(prob[offset]),
                "baseline_probability": baseline_p,
                "selected_model": selected.name,
                "train_end_date": data.iloc[train_end - 1]["Date"],
                "inner_cv_brier": float(cv_scores[selected.name]),
            })
        last_selected = selected.name
        final_model = model

    oos = pd.DataFrame(records)
    if oos.empty or final_model is None:
        raise RuntimeError("No OOS predictions were generated")

    # Final deployable shadow artifact is refit on all labeled rows using only inner CV on all history.
    selected, _ = select_candidate(X, y, candidates, inner_splits, gap)
    final_model = clone(selected.estimator).fit(X, y)
    return oos, final_model, selected.name


def block_bootstrap_win_probability(
    actual: np.ndarray,
    model_p: np.ndarray,
    baseline_p: np.ndarray,
    *,
    iterations: int,
    random_state: int,
    block_size: int = 10,
) -> tuple[float, tuple[float, float]]:
    rng = np.random.default_rng(random_state)
    n = len(actual)
    if n == 0:
        return 0.0, (float("nan"), float("nan"))
    diffs: list[float] = []
    starts = np.arange(max(1, n - block_size + 1))
    for _ in range(iterations):
        idx: list[int] = []
        while len(idx) < n:
            start = int(rng.choice(starts))
            idx.extend(range(start, min(start + block_size, n)))
        sample = np.asarray(idx[:n], dtype=int)
        diff = _safe_brier(actual[sample], model_p[sample]) - _safe_brier(actual[sample], baseline_p[sample])
        diffs.append(diff)
    arr = np.asarray(diffs)
    return float(np.mean(arr < 0)), (float(np.quantile(arr, 0.05)), float(np.quantile(arr, 0.95)))


def strategy_metrics(
    realized_return: np.ndarray,
    probability: np.ndarray,
    threshold: float,
    cost_bps: float,
) -> tuple[dict[str, float], np.ndarray, np.ndarray]:
    p = np.asarray(probability, dtype=float)
    r = np.asarray(realized_return, dtype=float)
    position = np.where(p >= threshold, 1.0, np.where(p <= 1.0 - threshold, -1.0, 0.0))
    # Target is an intraday open-to-close return. Every non-zero position is opened
    # at the session open and closed at the session close, so charge two sides daily.
    round_trip_cost = 2.0 * np.abs(position) * float(cost_bps) / 10000.0
    net = position * r - round_trip_cost
    long_net = r - 2.0 * float(cost_bps) / 10000.0

    def summarise(values: np.ndarray) -> dict[str, float]:
        mean = float(np.mean(values))
        std = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0
        sharpe = mean / std * math.sqrt(252) if std > 0 else 0.0
        equity = np.cumprod(1.0 + values)
        peak = np.maximum.accumulate(equity)
        drawdown = equity / peak - 1.0
        return {
            "cumulative_return": float(equity[-1] - 1.0),
            "annualized_return": float((equity[-1] ** (252 / len(values))) - 1.0) if equity[-1] > 0 else -1.0,
            "annualized_sharpe": float(sharpe),
            "max_drawdown": float(drawdown.min()),
        }

    return {"model": summarise(net), "long_baseline": summarise(long_net)}, position, net


def evaluate_oos(
    oos: pd.DataFrame,
    *,
    threshold: float,
    cost_bps: float,
    bootstrap_iterations: int,
    random_state: int,
) -> dict[str, Any]:
    y = oos["actual"].to_numpy(dtype=int)
    p = oos["probability"].to_numpy(dtype=float)
    base = oos["baseline_probability"].to_numpy(dtype=float)
    brier = _safe_brier(y, p)
    baseline_brier = _safe_brier(y, base)
    auc = float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else float("nan")
    win_prob, diff_ci90 = block_bootstrap_win_probability(
        y, p, base, iterations=bootstrap_iterations, random_state=random_state
    )
    strategy, position, net = strategy_metrics(
        oos["realized_return"].to_numpy(), p, threshold, cost_bps
    )
    enriched = oos.copy()
    enriched["position"] = position
    enriched["net_strategy_return"] = net
    return {
        "classification": {
            "n": int(len(oos)),
            "brier": brier,
            "baseline_brier": baseline_brier,
            "brier_improvement": baseline_brier - brier,
            "log_loss": float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1])),
            "roc_auc": auc,
            "bootstrap_probability_model_beats_baseline": win_prob,
            "bootstrap_brier_difference_ci90": list(diff_ci90),
        },
        "strategy_proxy": strategy,
        "enriched_oos": enriched,
    }
