from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")

GATE_LABELS = {
    "TRADE_OK": "매매 가능 구간",
    "SELECTIVE": "선별 대응",
    "WAIT": "매매 보류",
    "RISK_OFF": "위험 회피",
    "UNAVAILABLE": "판단 불가",
}

CHECKPOINTS = (
    (7 * 60 + 30, "07:30", "overnight", "야간·미국장과 당일 기본 확률"),
    (8 * 60, "08:00", "nxt_premarket", "NXT 프리마켓 실제 반응"),
    (8 * 60 + 50, "08:50", "opening_auction", "KOSPI200 선물·동시호가 확인"),
    (9 * 60 + 5, "09:05", "open_confirmation", "현물 첫 5분·시장 폭 확인"),
)


def _seoul(value: datetime) -> datetime:
    return value.replace(tzinfo=SEOUL) if value.tzinfo is None else value.astimezone(SEOUL)


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _probability(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and 0.0 <= number <= 1.0 else None


def _checkpoint_state(now: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    current = _seoul(now)
    minute = current.hour * 60 + current.minute
    selected_index = 0
    for index, row in enumerate(CHECKPOINTS):
        if minute >= row[0]:
            selected_index = index
        else:
            break
    selected = CHECKPOINTS[selected_index]
    rows = []
    for index, (_, at, code, purpose) in enumerate(CHECKPOINTS):
        status = "done" if index < selected_index else ("current" if index == selected_index else "upcoming")
        if minute < CHECKPOINTS[0][0]:
            status = "upcoming"
        rows.append({"at": at, "code": code, "purpose": purpose, "status": status})
    return {
        "at": selected[1],
        "code": selected[2],
        "purpose": selected[3],
        "before_first_checkpoint": minute < CHECKPOINTS[0][0],
    }, rows


def build_market_breadth(index: dict[str, Any] | None) -> dict[str, Any]:
    index = index or {}
    advancers = _finite(index.get("advancers"))
    decliners = _finite(index.get("decliners"))
    change_rate = _finite(index.get("change_rate"))
    total = None if advancers is None or decliners is None else advancers + decliners
    ratio = advancers / total if total and total > 0 else None
    if ratio is None:
        breadth_state = "UNAVAILABLE"
        breadth_label = "시장 폭 데이터 미수신"
    elif ratio >= 0.55:
        breadth_state = "BROAD_UP"
        breadth_label = "상승 종목 우세"
    elif ratio <= 0.45:
        breadth_state = "BROAD_DOWN"
        breadth_label = "하락 종목 우세"
    else:
        breadth_state = "MIXED"
        breadth_label = "상승·하락 종목 혼재"

    # This is deliberately identified as a proxy. KIS index breadth does not
    # expose constituent weights, so exact large-cap contribution is unknown.
    concentration_risk = None
    concentration_label = "직접 측정 불가"
    if ratio is not None and change_rate is not None:
        if change_rate > 0 and ratio < 0.50:
            concentration_risk = True
            concentration_label = "지수 상승 대비 시장 폭 약세 · 대형주 편중 추정"
        elif change_rate > 0 and ratio >= 0.55:
            concentration_risk = False
            concentration_label = "상승 종목 확산 확인"
        elif change_rate <= 0:
            concentration_label = "지수 비상승 구간 · 편중 판단 보류"
    return {
        "availability": "available" if ratio is not None else "unavailable",
        "advancers": advancers,
        "decliners": decliners,
        "advancer_ratio": ratio,
        "state": breadth_state,
        "label": breadth_label,
        "large_cap_concentration": {
            "availability": "inferred" if concentration_risk is not None else "unavailable",
            "risk": concentration_risk,
            "label": concentration_label,
            "method": "KOSPI change and advancer-ratio divergence proxy",
            "direct_constituent_weight_data": False,
        },
    }


def _premarket_direction(experiment: dict[str, Any]) -> dict[str, Any]:
    values = []
    for symbol in experiment.get("symbols") or []:
        summary = symbol.get("premarket_summary") or {}
        value = _finite(summary.get("nxt_return"))
        if summary.get("availability") == "available" and value is not None:
            values.append(value)
    positive = sum(value > 0 for value in values)
    negative = sum(value < 0 for value in values)
    return {
        "availability": "available" if values else "unavailable",
        "sample_count": len(values),
        "positive_count": positive,
        "negative_count": negative,
        "positive_ratio": positive / len(values) if values else None,
        "scope": "configured symbols only; not KOSPI-wide breadth",
    }


def _model_lab(
    *, prediction: dict[str, Any], promotion: dict[str, Any], validation: dict[str, Any]
) -> dict[str, Any]:
    checks = promotion.get("checks") or {}
    return {
        "model_name": "KOSPI intraday open-to-close classifier",
        "target_definition": "KOSPI 종가가 당일 시가보다 높은지",
        "probability_scope": "당일 시가→종가; 현재 시점→종가 확률이 아님",
        "candidate_target_date": prediction.get("candidate_target_date"),
        "trained_at_utc": prediction.get("trained_at_utc"),
        "promotion_status": promotion.get("status"),
        "signal_enabled": bool(promotion.get("signal_enabled")),
        "promotion_checks": checks,
        "failed_checks": sorted(name for name, passed in checks.items() if passed is not True),
        "validation": {
            "roc_auc": validation.get("roc_auc"),
            "brier": validation.get("brier"),
            "baseline_brier": validation.get("baseline_brier"),
            "brier_improvement": validation.get("brier_improvement"),
            "oos_n": validation.get("oos_n"),
            "strategy_sharpe": validation.get("strategy_sharpe"),
            "max_drawdown": validation.get("max_drawdown"),
        },
        "remaining_session_model": {
            "availability": "unavailable",
            "reason": "remaining_session_model_not_trained_or_validated",
            "probability": None,
        },
    }


def build_kospi_market_gate(
    *,
    now: datetime,
    prediction: dict[str, Any],
    promotion: dict[str, Any],
    validation: dict[str, Any],
    market: dict[str, Any],
    premarket_experiment: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = _seoul(now)
    cfg = config or {}
    checkpoint, checkpoint_rows = _checkpoint_state(current)
    probability = _probability(prediction.get("probability_intraday_up"))
    target_date = str(prediction.get("candidate_target_date") or "")
    target_valid = target_date == current.date().isoformat()
    signal_enabled = bool(promotion.get("signal_enabled"))
    trade_threshold = float(cfg.get("trade_ok_probability", prediction.get("probability_threshold") or 0.57))
    selective_threshold = float(cfg.get("selective_probability", max(0.52, trade_threshold - 0.03)))
    risk_off_threshold = float(cfg.get("risk_off_probability", min(0.48, 1.0 - trade_threshold)))
    index = market.get("kospi") or {}
    futures = market.get("kospi200_futures") or {}
    index_rate = _finite(index.get("change_rate"))
    futures_rate = _finite(futures.get("change_rate"))
    breadth = build_market_breadth(index)
    advancer_ratio = _finite(breadth.get("advancer_ratio"))
    concentration_risk = (breadth.get("large_cap_concentration") or {}).get("risk")
    premarket = _premarket_direction(premarket_experiment)
    premarket_ratio = _finite(premarket.get("positive_ratio"))

    confirmations = {
        "nxt_configured_symbols_positive": premarket_ratio is not None and premarket_ratio >= 0.55,
        "kospi200_futures_nonnegative": futures_rate is not None and futures_rate >= 0,
        "kospi_spot_positive": index_rate is not None and index_rate > 0,
        "breadth_supportive": advancer_ratio is not None and advancer_ratio >= 0.52,
        "large_cap_concentration_not_detected": concentration_risk is False,
    }
    positive_confirmation_count = sum(value is True for value in confirmations.values())
    negative_confirmation_count = sum(
        value is not None and value < 0
        for value in (index_rate, futures_rate)
    ) + int(advancer_ratio is not None and advancer_ratio <= 0.45)

    status = "WAIT"
    reasons: list[str] = []
    if probability is None or not target_valid:
        status = "UNAVAILABLE"
        reasons.append("오늘을 대상으로 한 유효한 시가→종가 확률이 없습니다.")
    elif probability <= risk_off_threshold and negative_confirmation_count >= 1:
        status = "RISK_OFF"
        reasons.append("하락 쪽 모델 확률과 실시간 약세 확인이 겹쳤습니다.")
    elif not signal_enabled:
        status = "WAIT"
        reasons.append("모델 승격 게이트가 닫혀 있어 매매 허가를 내리지 않습니다.")
    elif checkpoint["before_first_checkpoint"]:
        status = "WAIT"
        reasons.append("07:30 첫 체크포인트 전입니다.")
    elif checkpoint["code"] in {"overnight", "nxt_premarket", "opening_auction"}:
        if probability >= selective_threshold and positive_confirmation_count >= 1:
            status = "SELECTIVE"
            reasons.append("모델 우위는 있으나 09:05 현물·시장 폭 확인 전입니다.")
        else:
            status = "WAIT"
            reasons.append("09:05 현물 첫 5분 확인 전이거나 확인 신호가 부족합니다.")
    elif (
        probability >= trade_threshold
        and index_rate is not None and index_rate > 0
        and futures_rate is not None and futures_rate >= 0
        and advancer_ratio is not None and advancer_ratio >= 0.52
        and concentration_risk is not True
    ):
        status = "TRADE_OK"
        reasons.append("승격된 모델과 09:05 현물·선물·시장 폭 확인이 모두 통과했습니다.")
    elif probability >= selective_threshold and positive_confirmation_count >= 2:
        status = "SELECTIVE"
        reasons.append("상승 우위는 있으나 일부 확인값이 부족하거나 혼재합니다.")
    else:
        status = "WAIT"
        reasons.append("확률 우위와 실시간 확인 신호가 충분히 일치하지 않습니다.")

    # Hard fail-closed invariant. A disabled model can never emit TRADE_OK.
    if not signal_enabled and status == "TRADE_OK":
        status = "WAIT"
        reasons = ["signal_enabled=false이므로 TRADE_OK를 금지합니다."]

    abstain = status in {"WAIT", "UNAVAILABLE", "RISK_OFF"}
    if status == "RISK_OFF":
        action = "신규 롱 진입을 피하고 기존 위험 노출을 축소 검토"
    elif status == "SELECTIVE":
        action = "시장 폭이 확인된 종목만 소수 선별; 추격 금지"
    elif status == "TRADE_OK":
        action = "손실 한도 내에서 검증된 종목 신호만 검토"
    elif status == "UNAVAILABLE":
        action = "확률 또는 필수 데이터 복구 전 매매 보류"
    else:
        action = "매매 보류; 다음 체크포인트 확인"

    result = {
        "schema_version": 1,
        "feature_name": "kospi_market_gate",
        "display_name": "오늘 KOSPI 매매 판단",
        "status": status,
        "status_label": GATE_LABELS[status],
        "generated_at": current.isoformat(),
        "trading_date": current.date().isoformat(),
        "checkpoint": checkpoint,
        "checkpoints": checkpoint_rows,
        "session_close_up_probability": {
            "availability": "available" if probability is not None and target_valid else "unavailable",
            "probability": probability if target_valid else None,
            "target_definition": "KOSPI 종가 > 당일 시가",
            "target_date": target_date or None,
            "scope_warning": "당일 시가→종가 확률이며 현재 시점→종가 확률로 재해석하지 않음",
        },
        "current_to_close_up_probability": {
            "availability": "unavailable",
            "probability": None,
            "reason": "remaining_session_model_not_trained_or_validated",
        },
        "market_breadth": breadth,
        "configured_stock_premarket": premarket,
        "confirmations": confirmations,
        "positive_confirmation_count": positive_confirmation_count,
        "negative_confirmation_count": negative_confirmation_count,
        "signal_enabled": signal_enabled,
        "stock_entries_allowed": status in {"TRADE_OK", "SELECTIVE"} and signal_enabled,
        "abstention": {
            "active": abstain,
            "label": "매매 보류" if abstain else "조건부 검토",
            "reasons": reasons,
        },
        "action": action,
        "risk_note": "게이트는 시장 환경 필터이며 수익이나 개별 종목 방향을 보장하지 않습니다.",
        "model_lab": _model_lab(prediction=prediction, promotion=promotion, validation=validation),
        "integrity": {
            "trade_ok_requires_signal_enabled": True,
            "trade_ok_when_signal_disabled": status == "TRADE_OK" and not signal_enabled,
            "remaining_session_probability_fabricated": False,
            "large_cap_concentration_is_direct_measure": False,
        },
    }
    return result


def _history_root(settings_raw: dict[str, Any], project_root: Path) -> Path:
    import os

    configured = os.getenv("PREMARKET_HISTORY_DIR") or str(
        (settings_raw.get("premarket") or {}).get("history_dir", "data/premarket_history")
    )
    root = Path(configured)
    return root if root.is_absolute() else project_root / root


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def update_live_prediction_ledger(
    *,
    settings_raw: dict[str, Any],
    project_root: Path,
    gate: dict[str, Any],
    persist: bool,
    maximum_records: int = 5000,
) -> dict[str, Any]:
    path = _history_root(settings_raw, project_root) / "kospi" / "live_prediction_ledger.jsonl"
    rows = _read_jsonl(path)
    generated = str(gate.get("generated_at") or "")
    checkpoint = gate.get("checkpoint") or {}
    key = f"{gate.get('trading_date')}|{checkpoint.get('at')}|{generated[:16]}"
    record = {
        "record_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:20],
        "trading_date": gate.get("trading_date"),
        "checkpoint": checkpoint.get("at"),
        "checkpoint_code": checkpoint.get("code"),
        "generated_at": generated,
        "gate_status": gate.get("status"),
        "signal_enabled": gate.get("signal_enabled"),
        "session_close_up_probability": (gate.get("session_close_up_probability") or {}).get("probability"),
        "current_to_close_up_probability": (gate.get("current_to_close_up_probability") or {}).get("probability"),
        "advancer_ratio": (gate.get("market_breadth") or {}).get("advancer_ratio"),
        "large_cap_concentration_risk": ((gate.get("market_breadth") or {}).get("large_cap_concentration") or {}).get("risk"),
        "abstained": (gate.get("abstention") or {}).get("active"),
        "actual_close_up": None,
        "outcome_available": False,
    }
    rows = [row for row in rows if row.get("record_id") != record["record_id"]]
    rows.append(record)
    rows = sorted(rows, key=lambda row: str(row.get("generated_at") or ""))[-maximum_records:]
    if persist:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    return {
        "availability": "available",
        "storage": str(path.relative_to(project_root)) if path.is_relative_to(project_root) else str(path),
        "record_count": len(rows),
        "records": rows[-40:][::-1],
        "outcome_scoring_status": "pending_until_official_close_label",
    }


def assert_market_gate_invariants(gates: Iterable[dict[str, Any]]) -> None:
    for gate in gates:
        if gate.get("status") == "TRADE_OK" and not gate.get("signal_enabled"):
            raise AssertionError("TRADE_OK is forbidden when signal_enabled=false")
