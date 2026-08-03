from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass(frozen=True)
class Candidate:
    name: str
    estimator: Pipeline


@dataclass
class ShrunkProbabilityModel:
    estimator: Pipeline
    prior_probability: float
    shrinkage: float

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        raw = self.estimator.predict_proba(X)[:, 1]
        p = self.shrinkage * raw + (1.0 - self.shrinkage) * self.prior_probability
        p = np.clip(p, 1e-6, 1 - 1e-6)
        return np.column_stack([1.0 - p, p])


def _log(message: str) -> None:
    print(f"[validation] {message}", flush=True)


def make_candidates(random_state: int, profile: str = "compact") -> list[Candidate]:
    if profile not in {"compact", "extended"}:
        raise ValueError(f"Unknown candidate profile: {profile}")
    logistic_cs = (0.1, 1.0) if profile == "compact" else (0.03, 0.1, 0.3, 1.0)
    leaves_values = (7, 15) if profile == "compact" else (7, 15, 31)
    candidates: list[Candidate] = []
    for c in logistic_cs:
        estimator = Pipeline([
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("scale", StandardScaler()),
            ("model", LogisticRegression(C=c, max_iter=2500, random_state=random_state)),
        ])
        candidates.append(Candidate(name=f"logistic_C{c}", estimator=estimator))
    for leaves in leaves_values:
        estimator = Pipeline([
            ("imputer", SimpleImputer(strategy="median", add_indicator=True)),
            ("model", HistGradientBoostingClassifier(
                learning_rate=0.05,
                max_iter=100,
                max_leaf_nodes=leaves,
                min_samples_leaf=30,
                l2_regularization=2.0,
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
    shrinkage_grid: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0),
) -> tuple[Candidate, float, dict[str, float]]:
    if len(X) < 120:
        raise ValueError("Not enough observations for inner time-series validation")
    splitter = TimeSeriesSplit(n_splits=inner_splits, gap=gap)
    folds = list(splitter.split(X))
    scores: dict[str, float] = {}
    best_candidate: Candidate | None = None
    best_alpha = 0.0
    best_score = float("inf")

    for candidate in candidates:
        actual_parts: list[np.ndarray] = []
        raw_parts: list[np.ndarray] = []
        prior_parts: list[np.ndarray] = []
        for train_idx, val_idx in folds:
            if len(np.unique(y.iloc[train_idx])) < 2:
                continue
            model = clone(candidate.estimator)
            model.fit(X.iloc[train_idx], y.iloc[train_idx])
            raw = model.predict_proba(X.iloc[val_idx])[:, 1]
            prior = float(y.iloc[train_idx].mean())
            actual_parts.append(y.iloc[val_idx].to_numpy(dtype=int))
            raw_parts.append(raw)
            prior_parts.append(np.full(len(val_idx), prior, dtype=float))
        if not actual_parts:
            scores[candidate.name] = float("inf")
            continue
        actual = np.concatenate(actual_parts)
        raw = np.concatenate(raw_parts)
        prior = np.concatenate(prior_parts)
        candidate_best_score = float("inf")
        candidate_best_alpha = 0.0
        for alpha in shrinkage_grid:
            blended = alpha * raw + (1.0 - alpha) * prior
            score = _safe_brier(actual, blended)
            if score < candidate_best_score:
                candidate_best_score = score
                candidate_best_alpha = float(alpha)
        scores[candidate.name] = candidate_best_score
        if candidate_best_score < best_score:
            best_score = candidate_best_score
            best_candidate = candidate
            best_alpha = candidate_best_alpha

    if best_candidate is None:
        raise RuntimeError("No candidate could be selected")
    return best_candidate, best_alpha, scores


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
    candidate_profile: str = "compact",
) -> tuple[pd.DataFrame, ShrunkProbabilityModel, str]:
    data = frame[["Date", target_col, return_col, *feature_cols]].copy()
    data = data.dropna(subset=[target_col, return_col]).reset_index(drop=True)
    if len(data) < min_train + test_block:
        raise ValueError(f"Need at least {min_train + test_block} usable rows; got {len(data)}")

    X = data[feature_cols]
    y = data[target_col].astype(int)
    candidates = make_candidates(random_state, profile=candidate_profile)
    records: list[dict[str, Any]] = []
    total_blocks = math.ceil((len(data) - min_train) / test_block)
    started = time.perf_counter()

    for block_no, test_start in enumerate(range(min_train, len(data), test_block), start=1):
        test_end = min(test_start + test_block, len(data))
        train_end = max(0, test_start - gap)
        X_train = X.iloc[:train_end]
        y_train = y.iloc[:train_end]
        if len(np.unique(y_train)) < 2:
            continue
        selected, alpha, cv_scores = select_candidate(X_train, y_train, candidates, inner_splits, gap)
        fitted = clone(selected.estimator).fit(X_train, y_train)
        prior = float(y_train.mean())
        model = ShrunkProbabilityModel(fitted, prior_probability=prior, shrinkage=alpha)
        prob = model.predict_proba(X.iloc[test_start:test_end])[:, 1]
        for offset, row_idx in enumerate(range(test_start, test_end)):
            records.append({
                "Date": data.iloc[row_idx]["Date"],
                "actual": int(y.iloc[row_idx]),
                "realized_return": float(data.iloc[row_idx][return_col]),
                "probability": float(prob[offset]),
                "baseline_probability": prior,
                "selected_model": selected.name,
                "probability_shrinkage": alpha,
                "train_end_date": data.iloc[train_end - 1]["Date"],
                "inner_cv_brier": float(cv_scores[selected.name]),
            })
        if block_no == 1 or block_no % 10 == 0 or block_no == total_blocks:
            _log(
                f"walk-forward {block_no}/{total_blocks}; test={test_start}:{test_end}; "
                f"model={selected.name}; alpha={alpha:.2f}; elapsed={time.perf_counter()-started:.1f}s"
            )

    oos = pd.DataFrame(records)
    if oos.empty:
        raise RuntimeError("No OOS predictions were generated")

    selected, alpha, _ = select_candidate(X, y, candidates, inner_splits, gap)
    fitted = clone(selected.estimator).fit(X, y)
    final_model = ShrunkProbabilityModel(
        fitted,
        prior_probability=float(y.mean()),
        shrinkage=alpha,
    )
    _log(f"final model={selected.name}; alpha={alpha:.2f}; total={time.perf_counter()-started:.1f}s")
    return oos, final_model, f"{selected.name}_shrink{alpha:.2f}"


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
            "active_sessions": int(np.count_nonzero(values)),
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
    direction = (p >= 0.5).astype(int)
    return {
        "classification": {
            "n": int(len(oos)),
            "brier": brier,
            "baseline_brier": baseline_brier,
            "brier_improvement": baseline_brier - brier,
            "log_loss": float(log_loss(y, np.clip(p, 1e-6, 1 - 1e-6), labels=[0, 1])),
            "roc_auc": auc,
            "directional_accuracy_at_0_5": float(np.mean(direction == y)),
            "bootstrap_probability_model_beats_baseline": win_prob,
            "bootstrap_brier_difference_ci90": list(diff_ci90),
        },
        "strategy_proxy": strategy,
        "enriched_oos": enriched,
    }
