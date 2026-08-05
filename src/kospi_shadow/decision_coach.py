from __future__ import annotations

import hashlib
import json
import math
import os
import re
from copy import deepcopy
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from .market_gate import build_kospi_market_gate, update_live_prediction_ledger


SEOUL = ZoneInfo("Asia/Seoul")
ACTION_LABELS = {
    "WATCH": "관찰",
    "WAIT": "대기",
    "ENTRY_CANDIDATE": "진입 검토",
    "HOLD": "보유 조건 유지",
    "REDUCE_CANDIDATE": "축소 검토",
    "EXIT_CANDIDATE": "청산 검토",
    "AVOID": "회피",
    "DATA_INSUFFICIENT": "데이터 부족",
}

PHASES = (
    (0, "overnight_brief", "아침 브리핑", "07:30"),
    (8 * 60, "nxt_premarket", "NXT 프리마켓", "08:00"),
    (8 * 60 + 50, "opening_auction", "동시호가 반영", "08:50"),
    (9 * 60, "opening_confirmation", "시초 확인 중", "09:00"),
    (9 * 60 + 5, "entry_decision", "진입 조건 확인", "09:05"),
    (9 * 60 + 30, "intraday_management", "장중 관리", "09:30"),
    (15 * 60 + 30, "closing_review", "정규장 마감 분석", "15:30"),
    (15 * 60 + 40, "nxt_aftermarket", "NXT 애프터마켓", "15:40"),
    (20 * 60 + 5, "next_day_watch", "다음날 관찰", "20:05"),
)


def _seoul(value: datetime) -> datetime:
    return value.replace(tzinfo=SEOUL) if value.tzinfo is None else value.astimezone(SEOUL)


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def resolve_decision_phase(now: datetime) -> dict[str, Any]:
    """Resolve the official v5 phase with exact Asia/Seoul boundaries."""
    current = _seoul(now)
    minute = current.hour * 60 + current.minute
    selected = PHASES[0]
    for candidate in PHASES:
        if minute >= candidate[0]:
            selected = candidate
        else:
            break
    scheduled = datetime.combine(current.date(), dtime.fromisoformat(selected[3]), SEOUL)
    return {
        "phase": selected[1],
        "display": selected[2],
        "timezone": "Asia/Seoul",
        "scheduled_at": scheduled.isoformat(),
        "generated_at": current.isoformat(),
        "schedule_delay_seconds": max(0, int((current - scheduled).total_seconds())),
        "weekend_caveat": current.weekday() >= 5,
    }


def _parse_news_time(value: Any, source_timezone: str | None = None) -> tuple[str | None, str | None, str, datetime | None]:
    """Return original, KST display value, precision and a comparable instant.

    Date-only values intentionally remain dates. No artificial midnight is
    introduced, because doing so would create false freshness and leakage.
    """
    if value is None or str(value).strip() == "":
        return None, None, "unknown", None
    text = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text, text, "date_only", None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text, None, "unknown", None
    if parsed.tzinfo is None:
        if not source_timezone:
            return text, None, "unknown", None
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(source_timezone))
        except Exception:
            return text, None, "unknown", None
    converted = parsed.astimezone(SEOUL)
    precision = "minute" if converted.second == 0 and converted.microsecond == 0 else "exact"
    return text, converted.isoformat(), precision, converted


def _session_bucket(published: datetime | None, precision: str) -> str:
    if published is None or precision in {"date_only", "unknown"}:
        return "unknown"
    minute = published.hour * 60 + published.minute
    if minute < 7 * 60 + 30:
        return "overnight"
    if minute < 8 * 60:
        return "before_premarket"
    if minute < 8 * 60 + 50:
        return "premarket"
    if minute < 9 * 60:
        return "opening_auction"
    if minute < 15 * 60 + 30:
        return "regular_session"
    if minute < 15 * 60 + 40:
        return "after_close"
    if minute < 20 * 60 + 5:
        return "after_market"
    return "unknown"


def _freshness(age_minutes: int | None, fresh_minutes: int, recent_minutes: int) -> str:
    if age_minutes is None:
        return "시간 미제공"
    if age_minutes <= fresh_minutes:
        return "새 기사"
    if age_minutes <= recent_minutes:
        return "최근 기사"
    return "지난 기사"


def _material_type(title: str) -> str:
    groups = {
        "실적": ("실적", "영업이익", "매출", "순이익"),
        "수주": ("수주",),
        "공급계약": ("공급계약", "공급 계약"),
        "정책": ("정책", "정부", "지원책"),
        "규제": ("규제", "제재", "과징금"),
        "자금조달": ("자금조달", "회사채", "전환사채"),
        "유상증자": ("유상증자",),
        "인수합병": ("인수", "합병", "m&a"),
        "주주환원": ("배당", "자사주", "주주환원"),
        "증권사 의견": ("목표가", "투자의견", "리포트"),
        "루머": ("루머", "설", "소문"),
    }
    lowered = title.lower()
    return next((name for name, words in groups.items() if any(word.lower() in lowered for word in words)), "기타")


def _source_priority(source_type: str, official: bool) -> int:
    if official or source_type == "official_disclosure":
        return 60
    return {
        "company_release": 50,
        "news": 35,
        "broker_research": 20,
        "rumor": 5,
    }.get(source_type, 10)


def normalize_news_item(
    item: dict[str, Any],
    *,
    now: datetime,
    fresh_minutes: int = 120,
    recent_minutes: int = 720,
    last_checkpoint_at: datetime | None = None,
) -> dict[str, Any]:
    current = _seoul(now)
    source_timezone = item.get("source_timezone")
    original, published_kst, precision, instant = _parse_news_time(item.get("published_at"), source_timezone)
    age = max(0, int((current - instant).total_seconds() // 60)) if instant and instant <= current else None
    title = str(item.get("title") or "").strip()
    source_type = str(item.get("source_type") or ("official_disclosure" if item.get("official_disclosure") else "news"))
    official = bool(item.get("official_disclosure"))
    direction = item.get("material_direction") or item.get("impact")
    if direction == "neutral":
        direction = "unknown"
    if direction not in {"positive", "negative", "mixed", "unknown"}:
        direction = "unknown"
    received_at = item.get("received_at") or current.isoformat()
    observed_at = item.get("observed_at") or received_at
    try:
        received_instant = _seoul(datetime.fromisoformat(str(received_at)))
    except (TypeError, ValueError):
        received_instant = None
    try:
        observed_instant = _seoul(datetime.fromisoformat(str(observed_at)))
    except (TypeError, ValueError):
        observed_instant = None
    data_delay = max(0, int((received_instant - observed_instant).total_seconds())) if observed_instant and received_instant else None
    stale = data_delay > recent_minutes * 60 if data_delay is not None else None
    is_new = bool(instant and last_checkpoint_at and instant > _seoul(last_checkpoint_at) and instant <= current)
    if last_checkpoint_at is None:
        is_new = False
    date_label = "시간 미제공"
    if precision == "date_only":
        date_label = f"{published_kst} · 정확한 시각 미제공"
    elif instant:
        date_label = instant.strftime("%Y-%m-%d %H:%M KST")
        if age is not None:
            date_label += f" · {age}분 전"
    return {
        "title": title,
        "source_name": str(item.get("source_name") or item.get("source") or "").strip() or None,
        "source_type": source_type,
        "source_url": item.get("source_url") or item.get("link"),
        "published_at": original,
        "published_at_kst": published_kst,
        "source_timezone": source_timezone or ("Asia/Seoul" if instant else None),
        "time_precision": item.get("time_precision") or precision,
        "observed_at": observed_at,
        "received_at": received_at,
        "data_delay_seconds": data_delay,
        "stale": stale,
        "data_quality": "stale" if stale else ("good" if instant else ("date_only" if precision == "date_only" else "unknown_time")),
        "age_minutes": age,
        "date_label": date_label,
        "freshness_label": _freshness(age, fresh_minutes, recent_minutes),
        "material_type": item.get("material_type") or _material_type(title),
        "material_direction": direction,
        "material_confidence": item.get("material_confidence") or ("high" if official else "unverified"),
        "official_disclosure": official,
        "source_count": 1,
        "duplicate_group_id": None,
        "related_symbols": list(item.get("related_symbols") or []),
        "is_new_since_last_checkpoint": is_new,
        "session_bucket": _session_bucket(instant, precision),
        "impact_horizon": item.get("impact_horizon") or "unknown",
        "experimental": not official,
        "_instant": instant,
        "_priority": _source_priority(source_type, official),
    }


def _title_key(title: str) -> str:
    text = re.sub(r"\[[^]]+\]|\([^)]*속보[^)]*\)", " ", title.lower())
    text = re.sub(r"[^0-9a-z가-힣]+", " ", text)
    tokens = [token for token in text.split() if token not in {"속보", "단독", "종합"}]
    return " ".join(tokens[:16])


def normalize_and_deduplicate_news(
    items: Iterable[dict[str, Any]],
    *,
    now: datetime,
    fresh_minutes: int = 120,
    recent_minutes: int = 720,
    duplicate_window_minutes: int = 360,
    last_checkpoint_at: datetime | None = None,
) -> list[dict[str, Any]]:
    normalized = [
        normalize_news_item(
            item,
            now=now,
            fresh_minutes=fresh_minutes,
            recent_minutes=recent_minutes,
            last_checkpoint_at=last_checkpoint_at,
        )
        for item in items
        if str(item.get("title") or "").strip()
    ]
    groups: list[list[dict[str, Any]]] = []
    for item in normalized:
        key = _title_key(item["title"])
        match: list[dict[str, Any]] | None = None
        for group in groups:
            if _title_key(group[0]["title"]) != key:
                continue
            left, right = item.get("_instant"), group[0].get("_instant")
            if left is None or right is None or abs((left - right).total_seconds()) <= duplicate_window_minutes * 60:
                match = group
                break
        if match is not None:
            match.append(item)
        else:
            groups.append([item])
    result: list[dict[str, Any]] = []
    for group in groups:
        representative = sorted(
            group,
            key=lambda row: (row["_priority"], row.get("_instant") or datetime.min.replace(tzinfo=SEOUL)),
            reverse=True,
        )[0]
        group_key = _title_key(representative["title"])
        representative["duplicate_group_id"] = hashlib.sha256(group_key.encode("utf-8")).hexdigest()[:16]
        representative["source_count"] = len({row.get("source_name") or row.get("source_url") for row in group})
        representative["related_articles"] = [
            {"title": row["title"], "source_name": row.get("source_name"), "source_url": row.get("source_url")}
            for row in group
            if row is not representative
        ]
        representative["related_symbols"] = sorted({symbol for row in group for symbol in row.get("related_symbols", [])})
        representative.pop("_instant", None)
        representative.pop("_priority", None)
        result.append(representative)
    result.sort(
        key=lambda row: (
            row.get("published_at_kst") or row.get("published_at") or "",
            bool(row.get("official_disclosure")),
        ),
        reverse=True,
    )
    return result


def news_available_at(items: Iterable[dict[str, Any]], cutoff: datetime) -> list[dict[str, Any]]:
    """Conservative point-in-time filter that excludes unknown same-day times."""
    limit = _seoul(cutoff)
    result: list[dict[str, Any]] = []
    for item in items:
        precision = item.get("time_precision")
        value = item.get("published_at_kst")
        if precision == "date_only":
            try:
                published_date = date.fromisoformat(str(value))
            except ValueError:
                continue
            if published_date < limit.date():
                result.append(deepcopy(item))
            continue
        if not value:
            continue
        try:
            instant = _seoul(datetime.fromisoformat(str(value)))
        except ValueError:
            continue
        if instant <= limit:
            result.append(deepcopy(item))
    return result


def candidate_transition(premarket: dict[str, Any], auction: dict[str, Any]) -> dict[str, str]:
    if auction.get("availability") != "available":
        return {"code": "not_received", "label": "데이터 미수신", "reason": auction.get("unavailable_reason") or "동시호가 데이터 미수신"}
    if auction.get("data_quality") == "stale":
        return {"code": "excluded", "label": "제외", "reason": "동시호가 데이터 지연"}
    agreement = auction.get("direction_matches_nxt")
    quantity_change = _finite(auction.get("expected_volume_change"))
    if agreement is False:
        return {"code": "weakened", "label": "약화", "reason": "NXT 방향과 예상체결가 방향 불일치"}
    if agreement is True and quantity_change is not None and quantity_change > 0:
        return {"code": "strengthened", "label": "강화", "reason": "방향 일치와 예상체결수량 증가"}
    if quantity_change is not None and quantity_change < 0:
        return {"code": "weakened", "label": "약화", "reason": "예상체결수량 감소"}
    return {"code": "maintained", "label": "유지", "reason": "확인된 정보에서 후보 상태 변화 없음"}


def _metric_value(summary: dict[str, Any], name: str) -> float | None:
    value = summary.get(name)
    if isinstance(value, dict):
        value = value.get("relative_value", value.get("value"))
    return _finite(value)


def _condition(label: str, status: str, source: str, observed_at: str | None, note: str | None = None) -> dict[str, Any]:
    return {
        "label": label,
        "status": status,
        "source": source,
        "observed_at": observed_at,
        "note": note,
        "experimental": True,
    }


def _observation_evidence(
    symbol: dict[str, Any], *, completeness_weight: float = 0.7, directional_weight: float = 0.3
) -> tuple[float | None, float, list[str], list[str]]:
    pre = symbol.get("premarket_summary") or {}
    opening = symbol.get("opening_five_minute_summary") or {}
    fields = (
        "nxt_return", "cumulative_turnover", "relative_volume", "relative_turnover",
        "bid_ask_spread", "orderbook_imbalance", "execution_imbalance", "last_5m_return",
    )
    available = sum(_metric_value(pre, field) is not None for field in fields)
    completeness = available / len(fields)
    if pre.get("availability") != "available":
        return None, completeness, [], ["NXT 실데이터 미수신"]
    positive: list[str] = []
    risk: list[str] = []
    for field, label in (("relative_volume", "동일 시간대 상대거래량"), ("relative_turnover", "동일 시간대 상대거래대금")):
        value = _metric_value(pre, field)
        if value is not None:
            (positive if value > 1 else risk).append(f"{label} {value:.2f}배")
    for field, label in (("nxt_return", "NXT 가격 방향"), ("orderbook_imbalance", "호가잔량 불균형"), ("execution_imbalance", "체결 불균형")):
        value = _metric_value(pre, field)
        if value is not None:
            (positive if value > 0 else risk).append(label)
    if pre.get("stale"):
        risk.append("프리마켓 데이터 지연")
    if opening.get("data_complete"):
        if opening.get("open_held") is True:
            positive.append("첫 5분 시가 구조 유지")
        elif opening.get("open_held") is False:
            risk.append("첫 5분 시가 구조 이탈")
        current_vs_vwap = _finite(opening.get("current_vs_approximate_vwap"))
        if current_vs_vwap is not None:
            (positive if current_vs_vwap > 0 else risk).append("근사 VWAP 대비 위치")
    directional_count = len(positive) + len(risk)
    directional_share = len(positive) / directional_count if directional_count else 0.0
    # This configurable coverage score is explicitly not a probability or a
    # validated trading signal. It only makes observed candidates sortable.
    weight_sum = completeness_weight + directional_weight
    if weight_sum <= 0:
        return None, completeness, positive[:3], risk[:3]
    score = 100.0 * (
        completeness_weight / weight_sum * completeness
        + directional_weight / weight_sum * directional_share
    )
    return round(score, 1), completeness, positive[:3], risk[:3]


def _phase_cutoff(now: datetime, phase: str) -> datetime:
    current = _seoul(now)
    if phase in {"overnight_brief", "nxt_premarket", "opening_auction"}:
        return min(current, datetime.combine(current.date(), dtime(9, 0), SEOUL) - timedelta(microseconds=1))
    if phase == "opening_confirmation":
        return current
    return min(current, datetime.combine(current.date(), dtime(9, 5), SEOUL))


def _summary_with_cutoff(
    summary: dict[str, Any], *, cutoff: datetime, unavailable_reason: str
) -> dict[str, Any]:
    result = deepcopy(summary)
    observed_text = result.get("observed_at")
    if observed_text:
        try:
            observed = _seoul(datetime.fromisoformat(str(observed_text)))
        except ValueError:
            result["data_quality"] = "unknown_time"
            return result
        if observed > cutoff:
            return {
                "availability": "unavailable",
                "unavailable_reason": unavailable_reason,
                "observed_at": None,
                "received_at": None,
                "data_quality": "unavailable",
            }
    elif result.get("availability") == "available":
        result["data_quality"] = "unknown_time"
    return result


def sanitize_symbol_for_phase(symbol: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Apply operational cutoffs even when callers provide an invalid payload."""
    current = _seoul(now)
    result = deepcopy(symbol)
    pre_cutoff = datetime.combine(current.date(), dtime(9, 0), SEOUL) - timedelta(microseconds=1)
    auction_cutoff = datetime.combine(current.date(), dtime(9, 0), SEOUL)
    opening_cutoff = datetime.combine(current.date(), dtime(9, 5), SEOUL)
    result["premarket_summary"] = _summary_with_cutoff(
        result.get("premarket_summary") or {},
        cutoff=pre_cutoff,
        unavailable_reason="premarket_feature_after_cutoff",
    )
    if current < datetime.combine(current.date(), dtime(8, 50), SEOUL):
        result["opening_auction_summary"] = {
            "availability": "unavailable",
            "unavailable_reason": "opening_auction_not_started",
            "data_quality": "unavailable",
        }
    else:
        result["opening_auction_summary"] = _summary_with_cutoff(
            result.get("opening_auction_summary") or {},
            cutoff=auction_cutoff,
            unavailable_reason="opening_auction_feature_after_cutoff",
        )
    if current < opening_cutoff:
        result["opening_five_minute_summary"] = {
            "availability": "unavailable",
            "unavailable_reason": "opening_confirmation_in_progress" if current.hour >= 9 else "not_started",
            "data_complete": False,
            "data_quality": "unavailable",
        }
    else:
        result["opening_five_minute_summary"] = _summary_with_cutoff(
            result.get("opening_five_minute_summary") or {},
            cutoff=opening_cutoff,
            unavailable_reason="opening_feature_after_0905_cutoff",
        )
        if result["opening_five_minute_summary"].get("availability") == "unavailable":
            result["opening_five_minute_summary"]["data_complete"] = False
    return result


def build_decision_card(
    symbol: dict[str, Any],
    *,
    rank: int,
    phase: dict[str, Any],
    signal_enabled: bool,
    model_trained: bool,
    news: list[dict[str, Any]],
    completeness_weight: float = 0.7,
    directional_weight: float = 0.3,
) -> dict[str, Any]:
    pre = symbol.get("premarket_summary") or {}
    auction = symbol.get("opening_auction_summary") or {}
    opening = symbol.get("opening_five_minute_summary") or {}
    score, completeness, positive, risks = _observation_evidence(
        symbol,
        completeness_weight=completeness_weight,
        directional_weight=directional_weight,
    )
    has_data = pre.get("availability") == "available" or opening.get("data_complete") is True
    current_phase = phase["phase"]
    if not has_data:
        action = "DATA_INSUFFICIENT"
    elif current_phase in {"overnight_brief", "nxt_premarket", "opening_auction"}:
        action = "WATCH"
    elif not opening.get("data_complete"):
        action = "DATA_INSUFFICIENT"
    else:
        action = "WAIT"
    observed_at = opening.get("observed_at") or pre.get("observed_at")
    related_news = [item for item in news if symbol.get("symbol") in item.get("related_symbols", [])]
    transition = candidate_transition(pre, auction)
    closing = symbol.get("closing_summary") or {}
    aftermarket = symbol.get("aftermarket_summary") or {}
    entry_conditions = [
        _condition("09:05 첫 5분 데이터 완성", "met" if opening.get("data_complete") else "pending", "KIS KRX 분봉", opening.get("observed_at")),
        _condition("시가 구조 유지 또는 회복 확인", "met" if opening.get("open_held") is True or opening.get("open_recovery") is True else ("not_met" if opening.get("data_complete") else "pending"), "KIS KRX 분봉", opening.get("observed_at")),
        _condition("근사 VWAP 대비 위치 확인", "met" if (_finite(opening.get("current_vs_approximate_vwap")) or 0) > 0 else ("not_met" if _finite(opening.get("current_vs_approximate_vwap")) is not None else "unavailable"), "분봉 종가×거래량 근사", opening.get("observed_at"), "체결 VWAP이 아닌 근사값"),
        _condition("동일 시간대 상대거래대금 기준 확보", "met" if (pre.get("relative_turnover") or {}).get("baseline_available") else "unavailable", "premarket-history", pre.get("observed_at")),
    ]
    required_met = all(item["status"] == "met" for item in entry_conditions)
    if signal_enabled and model_trained and required_met:
        action = "ENTRY_CANDIDATE"
    entry_window = "09:05 이후 첫 5분 데이터 완성 후" if current_phase not in {"overnight_brief", "nxt_premarket", "opening_auction"} else "09:05 확인 전 진입 판단 보류"
    return {
        "symbol": symbol.get("symbol"),
        "name": symbol.get("name") or symbol.get("symbol"),
        "market_phase": current_phase,
        "action_state": action,
        "action_label": ACTION_LABELS[action],
        "candidate_rank": rank,
        "observation_score": score,
        "score_label": "관찰 점수",
        "score_is_probability": False,
        "candidate_grade": "A" if rank <= 2 else ("B" if rank <= 5 else "C"),
        "data_completeness": round(completeness, 3),
        "data_quality": opening.get("data_quality") if opening.get("data_complete") else pre.get("data_quality", "unavailable"),
        "model_confidence": "low",
        "signal_enabled": bool(signal_enabled and model_trained),
        "probability": None,
        "probability_available": False,
        "experimental": True,
        "latest_observed_at": observed_at,
        "price_at_decision": opening.get("current_price") or pre.get("nxt_final_price"),
        "latest_news_at": related_news[0].get("published_at_kst") if related_news else None,
        "new_news_count": sum(bool(item.get("is_new_since_last_checkpoint")) for item in related_news),
        "official_disclosure": any(bool(item.get("official_disclosure")) for item in related_news),
        "why_watch": positive or (["실데이터 수신 여부 확인"] if not has_data else ["관찰 데이터 완전성 확인"]),
        "risk_factors": risks or ["검증된 종목 확률 모델 없음"],
        "auction_transition": transition,
        "entry_window": entry_window,
        "entry_trigger_conditions": entry_conditions,
        "confirmation_conditions": [
            _condition("시장·업종 방향 교차 확인", "unavailable" if opening.get("sector_index_direction") is None else "met", "시장·업종 지표", opening.get("observed_at")),
        ],
        "do_not_chase_conditions": [
            _condition("첫 5분 고점 이격과 거래대금 동행 여부 확인", "pending" if opening.get("data_complete") else "unavailable", "KIS KRX 분봉", opening.get("observed_at")),
        ],
        "required_data": ["NXT 프리마켓", "동시호가(제공 시)", "09:00~09:05 완성 분봉", "시장지표"],
        "next_review_at": phase.get("next_checkpoint_at"),
        "invalidation_conditions": [
            _condition("첫 5분 저가 이탈 여부", "pending", "KIS KRX 분봉", opening.get("observed_at")),
            _condition("근사 VWAP 이탈 후 회복 실패", "pending", "분봉 종가×거래량 근사", opening.get("observed_at")),
        ],
        "reduce_conditions": [
            _condition("시장·업종 대비 급격한 약화", "pending", "시장·업종 지표", opening.get("observed_at")),
        ],
        "exit_conditions": [
            _condition("진입 논리 무효화 또는 반대 공식 공시", "pending", "가격·공시", observed_at),
        ],
        "trailing_review_conditions": ["시가·첫 5분 범위·근사 VWAP과 신규 기사 재확인"],
        "close_before_end_of_session": None,
        "overnight_hold_status": "판단 보류",
        "overnight_hold_risks": ["애프터마켓 및 장 마감 후 공시 데이터 확인 필요"],
        "entry_conditions_met": required_met and signal_enabled and model_trained,
        "model_status": "2단계 데이터 수집·피처·검증 프레임워크. 종목 확률 모델은 미학습 상태.",
        "result_labels": deepcopy(symbol.get("labels") or {}),
        "result_prices": {
            "price_at_0930": closing.get("price_0930"),
            "close_price": closing.get("close_price"),
            "aftermarket_final_price": aftermarket.get("current_price"),
            "session_high": closing.get("high"),
            "session_low": closing.get("low"),
            "actual_open": closing.get("actual_open") or opening.get("actual_open"),
        },
        "state_update": {
            "previous_state": None,
            "current_state": action,
            "change_reason": "신규 스냅샷" if has_data else "필수 데이터 미수신",
            "holding_thesis_maintained": None,
            "risk_change": risks,
        },
    }


def _next_checkpoint(phase: str) -> tuple[str, str]:
    return {
        "overnight_brief": ("08:00", "NXT 프리마켓 확인"),
        "nxt_premarket": ("08:50", "동시호가 재평가"),
        "opening_auction": ("09:05", "첫 5분 확인"),
        "opening_confirmation": ("09:05", "첫 5분 완성 대기"),
        "entry_decision": ("09:30", "첫 성과·구조 확인"),
        "intraday_management": ("다음 장중 체크포인트", "보유 논리 재평가"),
        "closing_review": ("15:40", "NXT 애프터마켓 확인"),
        "nxt_aftermarket": ("20:05", "익일 관찰 후보 확정"),
        "next_day_watch": ("다음 영업일 07:30", "아침 브리핑"),
    }[phase]


def _next_intraday_review(now: datetime, configured: Iterable[str]) -> str:
    current = _seoul(now)
    minute = current.hour * 60 + current.minute
    for text in configured:
        try:
            hour, minute_part = (int(value) for value in str(text).split(":", 1))
        except (TypeError, ValueError):
            continue
        if hour * 60 + minute_part > minute:
            return f"{hour:02d}:{minute_part:02d}"
    return "15:20"


def _history_root(settings_raw: dict[str, Any], project_root: Path) -> Path:
    configured = os.getenv("PREMARKET_HISTORY_DIR") or str((settings_raw.get("premarket") or {}).get("history_dir", "data/premarket_history"))
    root = Path(configured)
    return root if root.is_absolute() else project_root / root


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            result.append(item)
    return result


def build_data_lab(settings_raw: dict[str, Any], project_root: Path, symbols: Iterable[dict[str, Any]]) -> dict[str, Any]:
    root = _history_root(settings_raw, project_root)
    minimum = int((settings_raw.get("premarket") or {}).get("minimum_model_samples", 252))
    rows: list[dict[str, Any]] = []
    for symbol_row in symbols:
        symbol = str(symbol_row.get("symbol") or "")
        raw = _jsonl(root / "raw" / f"{symbol}.jsonl")
        training = _jsonl(root / "training" / f"{symbol}.jsonl")
        dates = {str(row.get("collected_at") or "")[:10] for row in raw if row.get("collected_at")}
        pre_count = sum("premarket_summary" in row and row.get("premarket_summary") is not None for row in raw)
        auction_count = sum("auction_snapshot" in row and row.get("auction_snapshot") is not None for row in raw)
        opening_count = sum(
            "opening_five_minute_summary" in row and row.get("opening_five_minute_summary") is not None
            for row in raw
        )
        aftermarket_count = sum(
            "aftermarket_summary" in row and row.get("aftermarket_summary") is not None
            for row in raw
        )
        label_0930 = sum((row.get("labels") or {}).get("open_to_0930_up") is not None for row in raw)
        label_close = sum((row.get("labels") or {}).get("open_to_close_up") is not None for row in raw)
        expected = max(1, len(dates) * 3)
        observed = pre_count + auction_count + opening_count
        completeness = min(1.0, observed / expected) if dates else 0.0
        observed_times = [str(row.get("collected_at")) for row in raw if row.get("collected_at")]
        rows.append({
            "symbol": symbol,
            "name": symbol_row.get("name") or symbol,
            "collected_trading_days": len(dates),
            "premarket_sample_count": pre_count,
            "opening_auction_sample_count": auction_count,
            "opening_five_minute_sample_count": opening_count,
            "label_0930_count": label_0930,
            "close_label_count": label_close,
            "aftermarket_sample_count": aftermarket_count,
            "aftermarket_availability": "available" if aftermarket_count else "unavailable",
            "data_completeness": round(completeness, 3),
            "missing_rate": round(1.0 - completeness, 3),
            "last_successful_collection_at": max(observed_times) if observed_times else None,
            "provider_success_count": None,
            "provider_failure_count": None,
            "stale_count": sum(
                bool((row.get("premarket_summary") or {}).get("stale")) for row in raw
            ),
            "training_record_count": len(training),
            "model_trainable": len(dates) >= minimum,
            "minimum_model_samples": minimum,
            "trading_days_remaining": max(0, minimum - len(dates)),
        })
    return {
        "symbols": rows,
        "models": {
            "premarket_prediction": _unavailable_model_metrics("walk_forward_backtest_not_available"),
            "post_open_0905_prediction": _unavailable_model_metrics("walk_forward_backtest_not_available"),
        },
        "history_storage": str(root.relative_to(project_root)) if root.is_relative_to(project_root) else str(root),
        "durable_storage": "premarket-history branch",
    }


def _unavailable_model_metrics(reason: str) -> dict[str, Any]:
    return {
        "availability": "unavailable",
        "unavailable_reason": reason,
        "brier_score": None,
        "log_loss": None,
        "roc_auc": None,
        "precision": None,
        "recall": None,
        "calibration": None,
        "sample_count": 0,
        "cost_adjusted_return": None,
        "max_drawdown": None,
    }


def build_shadow_snapshots(cards: Iterable[dict[str, Any]], phase: dict[str, Any], settings_raw: dict[str, Any]) -> list[dict[str, Any]]:
    paper = settings_raw.get("decision_coach") or {}
    fee = float(paper.get("paper_fee_bps_per_side", 5.0))
    slippage = float(paper.get("paper_slippage_bps_per_side", 5.0))
    generated = phase["generated_at"]
    result: list[dict[str, Any]] = []
    for card in cards:
        key = f"{card.get('symbol')}|{phase['phase']}|{generated[:16]}"
        trade_created = card.get("action_state") == "ENTRY_CANDIDATE" and card.get("entry_conditions_met") is True
        prices = card.get("result_prices") or {}
        open_price = _finite(prices.get("actual_open"))
        high = _finite(prices.get("session_high"))
        low = _finite(prices.get("session_low"))
        result.append({
            "decision_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:20],
            "symbol": card.get("symbol"),
            "stage": phase["phase"],
            "action_state": card.get("action_state"),
            "generated_at": generated,
            "price_at_decision": card.get("price_at_decision"),
            "entry_conditions": card.get("entry_trigger_conditions", []),
            "invalidation_conditions": card.get("invalidation_conditions", []),
            "exit_conditions": card.get("exit_conditions", []),
            "next_review_at": card.get("next_review_at"),
            "data_quality": card.get("data_quality"),
            "model_version": "untrained-stock-framework-v5",
            "experimental": True,
            "hypothetical_trade_created": trade_created,
            "price_at_0930": prices.get("price_at_0930"),
            "close_price": prices.get("close_price"),
            "aftermarket_final_price": prices.get("aftermarket_final_price"),
            "max_favorable_excursion": high / open_price - 1.0 if high is not None and open_price not in (None, 0) else None,
            "max_adverse_excursion": low / open_price - 1.0 if low is not None and open_price not in (None, 0) else None,
            "hypothetical_entry_price": None,
            "hypothetical_exit_price": None,
            "fee_bps_per_side": fee,
            "slippage_bps_per_side": slippage,
            "net_return": None,
        })
    return result


def persist_shadow_snapshots(settings_raw: dict[str, Any], project_root: Path, snapshots: Iterable[dict[str, Any]]) -> None:
    root = _history_root(settings_raw, project_root) / "decisions"
    maximum = int((settings_raw.get("decision_coach") or {}).get("maximum_decision_records_per_symbol", 5000))
    for snapshot in snapshots:
        symbol = str(snapshot.get("symbol") or "")
        if not symbol:
            continue
        path = root / f"{symbol}.jsonl"
        records = _jsonl(path)
        records = [row for row in records if row.get("decision_id") != snapshot.get("decision_id")]
        records.append(snapshot)
        records = sorted(records, key=lambda row: str(row.get("generated_at") or ""))[-maximum:]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")


def apply_previous_states(
    settings_raw: dict[str, Any], project_root: Path, cards: Iterable[dict[str, Any]]
) -> None:
    root = _history_root(settings_raw, project_root) / "decisions"
    for card in cards:
        records = _jsonl(root / f"{card.get('symbol')}.jsonl")
        previous = records[-1].get("action_state") if records else None
        update = card["state_update"]
        update["previous_state"] = previous
        if previous is None:
            update["change_reason"] = "첫 의사결정 스냅샷"
        elif previous == card["action_state"]:
            update["change_reason"] = "상태 유지; 최신 데이터와 위험 요인 재확인"
        else:
            update["change_reason"] = f"{previous}에서 {card['action_state']}로 변경"


def _market_environment(market: dict[str, Any], news: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    factors = market.get("factors") or []
    by_key = {row.get("key"): row for row in factors}
    available_moves = [
        _finite(row.get("change_rate")) for row in factors
        if _finite(row.get("change_rate")) is not None
    ]
    risk_tone = "판단 보류"
    if available_moves:
        adjusted = sum(
            (_finite(row.get("change_rate")) or 0) * (-1 if row.get("key") in {"vix", "usdk_rw"} else 1)
            for row in factors
        )
        risk_tone = "위험 선호 참고" if adjusted > 0 else ("위험 회피 참고" if adjusted < 0 else "혼재")
    checks = []
    for key, label in (("sox", "미국 반도체"), ("usdk_rw", "원·달러 환율"), ("vix", "VIX")):
        if by_key.get(key):
            checks.append(label)
    if not checks and news:
        checks.append("주요 기사 시각과 방향")
    if events:
        checks.append("주요 글로벌 일정")
    return {
        "summary": risk_tone,
        "risk_appetite": risk_tone,
        "gap_up_environment": "실험적 참고" if available_moves else "데이터 부족",
        "gap_down_environment": "실험적 참고" if available_moves else "데이터 부족",
        "volatility_expansion": "VIX 확인" if by_key.get("vix") else "데이터 부족",
        "top_checks": checks[:3],
        "kospi_previous": market.get("kospi"),
        "kosdaq_previous": None,
        "global_factors": factors,
        "events": events,
        "availability": {
            "kospi": "available" if market.get("kospi") else "unavailable",
            "kosdaq": "unavailable",
            "kospi200_night_futures": "unavailable",
            "foreign_investor_flow": "unavailable",
            "domestic_economic_calendar": "unavailable",
            "earnings_calendar": "unavailable",
            "global_release_calendar": "available" if events else "unavailable",
        },
    }


def build_decision_coach(
    *,
    settings_raw: dict[str, Any],
    project_root: Path,
    now: datetime,
    premarket_experiment: dict[str, Any],
    news: list[dict[str, Any]],
    events: list[dict[str, Any]],
    market: dict[str, Any],
    index_signal_enabled: bool,
    index_prediction: dict[str, Any] | None = None,
    promotion: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
    persist_history: bool = True,
) -> dict[str, Any]:
    phase = resolve_decision_phase(now)
    next_at, next_label = _next_checkpoint(phase["phase"])
    cfg = settings_raw.get("decision_coach") or {}
    if phase["phase"] == "intraday_management":
        next_at = _next_intraday_review(now, cfg.get("intraday_review_times") or [])
    phase["next_checkpoint_at"] = next_at
    phase["next_checkpoint_label"] = next_label
    raw_news = deepcopy(news)
    configured_symbols = [
        {"symbol": str(row.get("symbol") or ""), "name": str(row.get("name") or "")}
        for row in premarket_experiment.get("symbols") or []
    ]
    for item in raw_news:
        title = str(item.get("title") or "")
        inferred = [
            row["symbol"] for row in configured_symbols
            if row["symbol"] and (row["symbol"] in title or (row["name"] and row["name"] in title))
        ]
        item["related_symbols"] = sorted(set(item.get("related_symbols") or []) | set(inferred))
    normalized_news = normalize_and_deduplicate_news(
        raw_news,
        now=_seoul(now),
        fresh_minutes=int(cfg.get("news_fresh_minutes", 120)),
        recent_minutes=int(cfg.get("news_recent_minutes", 720)),
        duplicate_window_minutes=int(cfg.get("news_duplicate_window_minutes", 360)),
        last_checkpoint_at=(
            datetime.fromisoformat(phase["scheduled_at"])
            if datetime.fromisoformat(phase["scheduled_at"]) <= _seoul(now)
            else None
        ),
    )[: int(cfg.get("maximum_news_items", 20))]
    cutoff_news = news_available_at(normalized_news, _phase_cutoff(now, phase["phase"]))
    symbols = [sanitize_symbol_for_phase(item, now) for item in premarket_experiment.get("symbols") or []]
    effective_promotion = deepcopy(promotion or {})
    effective_promotion.setdefault("signal_enabled", bool(index_signal_enabled))
    market_gate = build_kospi_market_gate(
        now=now,
        prediction=deepcopy(index_prediction or {}),
        promotion=effective_promotion,
        validation=deepcopy(validation or {}),
        market=market,
        premarket_experiment=premarket_experiment,
        config=cfg.get("kospi_market_gate") or {},
    )
    production_truth = premarket_experiment.get("production_truth") or {}
    stock_signal_requested = bool(
        production_truth.get("stock_model_trained")
        and production_truth.get("stock_signal_enabled")
    )
    stock_signal_enabled = bool(stock_signal_requested and market_gate["stock_entries_allowed"])
    ranked = []
    completeness_weight = float(cfg.get("ranking_completeness_weight", 0.7))
    directional_weight = float(cfg.get("ranking_directional_weight", 0.3))
    for symbol in symbols:
        score, completeness, positive, risks = _observation_evidence(
            symbol,
            completeness_weight=completeness_weight,
            directional_weight=directional_weight,
        )
        ranked.append((score if score is not None else -1.0, completeness, symbol, positive, risks))
    ranked.sort(key=lambda row: (row[0], row[1], str(row[2].get("symbol"))), reverse=True)
    maximum_watch = int(cfg.get("maximum_watch_candidates", 5))
    cards = [
        build_decision_card(
            row[2], rank=index + 1, phase=phase,
            signal_enabled=stock_signal_enabled,
            model_trained=bool(production_truth.get("stock_model_trained")),
            news=cutoff_news,
            completeness_weight=completeness_weight,
            directional_weight=directional_weight,
        )
        for index, row in enumerate(ranked[:maximum_watch])
    ]
    for card in cards:
        card["kospi_gate_status"] = market_gate["status"]
        card["kospi_gate_label"] = market_gate["status_label"]
        card["blocked_by_kospi_gate"] = not market_gate["stock_entries_allowed"]
        card["stock_signal_requested"] = stock_signal_requested
        if card["blocked_by_kospi_gate"]:
            card["risk_factors"] = [
                f"KOSPI Market Gate: {market_gate['status_label']}",
                *card["risk_factors"],
            ]
    entry_max = int(cfg.get("maximum_entry_candidates", 3))
    entry_candidates = [card for card in cards if card["action_state"] == "ENTRY_CANDIDATE"][:entry_max]
    apply_previous_states(settings_raw, project_root, cards)
    shadow = build_shadow_snapshots(cards, phase, settings_raw)
    if persist_history:
        persist_shadow_snapshots(settings_raw, project_root, shadow)
    live_ledger = update_live_prediction_ledger(
        settings_raw=settings_raw,
        project_root=project_root,
        gate=market_gate,
        persist=persist_history,
        maximum_records=int((cfg.get("kospi_market_gate") or {}).get("maximum_ledger_records", 5000)),
    )
    data_lab = build_data_lab(settings_raw, project_root, symbols)
    after_close_news = [item for item in normalized_news if item.get("session_bucket") in {"after_close", "after_market"}]
    symbol_lookup = {str(item.get("symbol")): item for item in symbols}
    closing_rows: list[dict[str, Any]] = []
    aftermarket_rows: list[dict[str, Any]] = []
    for card in cards:
        source = symbol_lookup.get(str(card.get("symbol"))) or {}
        closing = source.get("closing_summary") or {}
        labels = source.get("labels") or {}
        open_price = _finite(closing.get("actual_open"))
        high = _finite(closing.get("high"))
        low = _finite(closing.get("low"))
        closing_rows.append({
            "symbol": card["symbol"],
            "name": card["name"],
            "premarket_rank": card["candidate_rank"],
            "state_0905": card["action_state"],
            "actual_open": open_price,
            "price_0930": closing.get("price_0930"),
            "close_price": closing.get("close_price"),
            "high": high,
            "low": low,
            "premarket_label": labels.get("open_to_0930_up"),
            "post_open_label": labels.get("open_to_close_up"),
            "max_favorable_excursion": high / open_price - 1.0 if high is not None and open_price not in (None, 0) else None,
            "max_adverse_excursion": low / open_price - 1.0 if low is not None and open_price not in (None, 0) else None,
            "cost_adjusted_result": None,
            "availability": closing.get("availability", "unavailable"),
            "unavailable_reason": closing.get("unavailable_reason"),
            "data_quality": closing.get("data_quality", "unavailable"),
        })
        after = source.get("aftermarket_summary") or {}
        if after.get("availability") == "available":
            aftermarket_rows.append({"symbol": card["symbol"], "name": card["name"], **after})
    next_day = []
    for card in cards:
        related = [item for item in after_close_news if card["symbol"] in item.get("related_symbols", [])]
        after = (symbol_lookup.get(str(card["symbol"])) or {}).get("aftermarket_summary") or {}
        if not related and after.get("availability") != "available":
            continue
        reasons = []
        if after.get("availability") == "available":
            reasons.append("NXT 애프터마켓 실데이터 수신")
        if related:
            reasons.append("장 마감 후 실제 기사·공시 수신")
        next_day.append({
            "rank": len(next_day) + 1,
            "symbol": card["symbol"],
            "name": card["name"],
            "status": "프리마켓 재확인 필요",
            "reasons": reasons,
            "krx_close": after.get("krx_close"),
            "nxt_aftermarket_final": after.get("current_price"),
            "close_gap": after.get("krx_close_return"),
            "aftermarket_turnover": after.get("cumulative_turnover"),
            "latest_news": related[0] if related else None,
            "first_review_at": "다음 영업일 07:30",
            "required_conditions": ["NXT 거래 가능 여부", "08:00 실데이터", "기사 정정 여부"],
            "risks": ["애프터마켓 방향은 익일 방향을 보장하지 않음"],
            "data_quality": after.get("data_quality", "partial"),
        })
    return {
        "schema_version": 1,
        "feature_name": "time_based_decision_coach_v5",
        "phase": phase,
        "kospi_market_gate": market_gate,
        "kospi_model_lab": market_gate["model_lab"],
        "live_prediction_ledger": live_ledger,
        "market_environment": _market_environment(market, normalized_news, events),
        "official_disclosure": {
            "availability": "available" if any(item.get("official_disclosure") for item in normalized_news) else "unavailable",
            "unavailable_reason": None if any(item.get("official_disclosure") for item in normalized_news) else "DART_API_KEY_NOT_CONFIGURED" if not os.getenv("DART_API_KEY") else "NO_DISCLOSURES_RECEIVED",
        },
        "news": normalized_news,
        "decision_cards": cards,
        "watch_ranking": cards,
        "entry_candidates": entry_candidates,
        "waiting_candidates": [card for card in cards if card["action_state"] in {"WATCH", "WAIT"}],
        "excluded_candidates": [card for card in cards if card["action_state"] in {"AVOID", "DATA_INSUFFICIENT"}],
        "closing_review": {
            "availability": "available" if any(row["availability"] == "available" for row in closing_rows) else "unavailable",
            "unavailable_reason": None if any(row["availability"] == "available" for row in closing_rows) else "closing_labels_not_complete",
            "premarket_and_0905_evaluated_separately": True,
            "symbols": closing_rows,
        },
        "nxt_aftermarket": {
            "availability": "available" if aftermarket_rows else "unavailable",
            "unavailable_reason": None if aftermarket_rows else "NXT_AFTERMARKET_DATA_NOT_RECEIVED",
            "symbols": aftermarket_rows,
            "warning": "애프터마켓 상승은 다음날 방향을 보장하지 않습니다.",
        },
        "next_day_watchlist": next_day[: int(cfg.get("maximum_next_day_candidates", 5))],
        "data_lab": data_lab,
        "shadow_trading": {
            "actual_orders_enabled": False,
            "snapshots": shadow,
            "trade_creation_rule": "entry conditions must be met; untrained gate creates no hypothetical trade",
        },
        "operations": {
            "app_version": "5.2.0",
            "build_sha": os.getenv("GITHUB_SHA") or None,
            "last_netlify_deploy": None,
            "last_data_collection": premarket_experiment.get("generated_at"),
            "last_successful_workflow": None,
            "next_scheduled_checkpoint": {"at": next_at, "label": next_label},
            "coach_deploy_times": list(cfg.get("coach_deploy_times") or []),
            "collector_times": list(cfg.get("collector_times") or []),
            "refresh_behavior": "static_deployment_check_only",
        },
        "signal_gate": {
            "stock_signal_enabled": stock_signal_enabled,
            "stock_signal_requested": stock_signal_requested,
            "stock_signal_depends_on_kospi_gate": True,
            "kospi_market_gate_status": market_gate["status"],
            "kospi_stock_entries_allowed": market_gate["stock_entries_allowed"],
            "index_signal_enabled": bool(effective_promotion.get("signal_enabled")),
            "stock_model_trained": bool(production_truth.get("stock_model_trained")),
            "probability_available": False,
            "probability": None,
            "experimental": True,
            "reason": "stock_level_training_and_walk_forward_validation_unavailable",
            "criteria": {
                "minimum_sample_count": int((settings_raw.get("premarket") or {}).get("minimum_model_samples", 252)),
                "chronological_validation": False,
                "walk_forward_validation": False,
                "future_leakage_check": True,
                "brier_improvement_over_baseline": None,
                "calibration_status": "unavailable",
                "cost_adjusted_expected_return": None,
                "max_drawdown": None,
                "minimum_data_completeness": float(cfg.get("minimum_data_completeness", 0.75)),
            },
        },
        "integrity": {
            "fabricated_market_data": False,
            "fabricated_probability": False,
            "mock_production_response": False,
            "future_news_cutoff_applied": True,
            "post_open_data_used_before_0905": False,
        },
    }
