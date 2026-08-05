from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")

# Keyword groups discover a theme from information that was already available
# at the checkpoint. They do not assert that a company belongs to a theme.
# Membership comes only from configured symbols or point-in-time related-symbol
# links in received news and disclosures.
DEFAULT_THEME_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "semiconductor_ai",
        "label": "반도체·AI",
        "keywords": ["반도체", "엔비디아", "hbm", "d램", "파운드리", "인공지능", "ai"],
        "global_factor_keys": ["nasdaq", "sox"],
    },
    {
        "key": "secondary_battery",
        "label": "2차전지·전기차",
        "keywords": ["2차전지", "이차전지", "배터리", "전기차", "리튬", "양극재", "음극재"],
        "global_factor_keys": ["nasdaq"],
    },
    {
        "key": "cooling_weather",
        "label": "폭염·냉방",
        "keywords": ["폭염", "무더위", "열대야", "냉방", "에어컨", "제습기", "전력수요"],
        "global_factor_keys": [],
        "event_type": "weather",
    },
    {
        "key": "defense",
        "label": "방산",
        "keywords": ["방산", "방위산업", "무기", "미사일", "군수", "국방"],
        "global_factor_keys": [],
    },
    {
        "key": "shipbuilding",
        "label": "조선·해운",
        "keywords": ["조선", "선박", "lng선", "해운", "수주잔고"],
        "global_factor_keys": [],
    },
    {
        "key": "nuclear_power",
        "label": "원전·전력",
        "keywords": ["원전", "원자력", "smr", "전력망", "전력기기", "변압기"],
        "global_factor_keys": [],
    },
    {
        "key": "robotics",
        "label": "로봇·자동화",
        "keywords": ["로봇", "휴머노이드", "자동화", "스마트팩토리"],
        "global_factor_keys": ["nasdaq"],
    },
    {
        "key": "bio_health",
        "label": "바이오·헬스케어",
        "keywords": ["바이오", "신약", "임상", "의약품", "헬스케어", "의료기기"],
        "global_factor_keys": ["nasdaq"],
    },
    {
        "key": "corporate_material",
        "label": "실적·수주·공시",
        "keywords": ["실적", "영업이익", "수주", "공급계약", "자사주", "배당", "인수", "합병"],
        "global_factor_keys": [],
    },
)

CHECKPOINTS: tuple[tuple[str, int, str], ...] = (
    ("07:30", 7 * 60 + 30, "전일 미국장·전일 마감·기사·이벤트로 예상 테마 구성"),
    ("08:00", 8 * 60, "NXT 거래대금과 테마 동반 움직임 확인"),
    ("08:50", 8 * 60 + 50, "동시호가 가격·수량 유지 여부로 허수 후보 제거"),
    ("09:05", 9 * 60 + 5, "첫 5분·근사 VWAP·시장 폭으로 후보 상태 재확인"),
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


def _relative(summary: dict[str, Any], field: str) -> float | None:
    value = summary.get(field)
    if isinstance(value, dict):
        value = value.get("relative_value")
    return _finite(value)


def _unique(values: Iterable[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def _theme_definitions(config: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = {row["key"]: deepcopy(row) for row in DEFAULT_THEME_DEFINITIONS}
    for custom in config.get("themes") or []:
        if not isinstance(custom, dict) or not str(custom.get("key") or "").strip():
            continue
        key = str(custom["key"]).strip()
        definitions[key] = {**definitions.get(key, {}), **deepcopy(custom), "key": key}
    result = []
    for row in definitions.values():
        row["label"] = str(row.get("label") or row["key"])
        row["keywords"] = _unique(str(value).lower() for value in row.get("keywords") or [])
        row["symbols"] = _unique(str(value) for value in row.get("symbols") or [])
        row["global_factor_keys"] = _unique(str(value) for value in row.get("global_factor_keys") or [])
        result.append(row)
    return result


def _news_text(item: dict[str, Any]) -> str:
    values = [
        item.get("title"),
        item.get("material_type"),
        *(item.get("theme_tags") or []),
    ]
    return " ".join(str(value or "").lower() for value in values)


def _matches_theme(item: dict[str, Any], definition: dict[str, Any]) -> bool:
    text = _news_text(item)
    for keyword in definition.get("keywords") or []:
        if not keyword:
            continue
        # Latin abbreviations such as AI must be whole tokens; otherwise words
        # such as "chairman" or "said" become false semiconductor matches.
        if keyword.isascii() and keyword.isalnum():
            if re.search(rf"(?<![0-9a-z]){re.escape(keyword)}(?![0-9a-z])", text):
                return True
        elif keyword in text:
            return True
    return False


def _member_observation(symbol: dict[str, Any]) -> dict[str, Any]:
    pre = symbol.get("premarket_summary") or {}
    auction = symbol.get("opening_auction_summary") or {}
    opening = symbol.get("opening_five_minute_summary") or {}
    pre_return = _finite(pre.get("nxt_return"))
    opening_return = _finite(opening.get("first_5m_return"))
    if opening_return is None:
        current = _finite(opening.get("current_price"))
        actual_open = _finite(opening.get("actual_open"))
        if current is not None and actual_open not in (None, 0):
            opening_return = current / actual_open - 1.0
    active_return = opening_return if opening.get("data_complete") and opening_return is not None else pre_return
    relative_turnover = _relative(pre, "relative_turnover")
    relative_volume = _relative(pre, "relative_volume")
    return {
        "symbol": str(symbol.get("symbol") or ""),
        "name": str(symbol.get("name") or symbol.get("symbol") or ""),
        "role": None,
        "role_is_inferred": True,
        "nxt_return": pre_return,
        "opening_five_minute_return": opening_return,
        "active_return": active_return,
        "positive": active_return > 0 if active_return is not None else None,
        "cumulative_turnover": _finite(pre.get("cumulative_turnover")),
        "relative_turnover": relative_turnover,
        "relative_volume": relative_volume,
        "above_approximate_vwap": (
            (_finite(opening.get("current_vs_approximate_vwap")) or 0) > 0
            if _finite(opening.get("current_vs_approximate_vwap")) is not None
            else None
        ),
        "auction_direction_matches_nxt": auction.get("direction_matches_nxt"),
        "observed_at": opening.get("observed_at") or pre.get("observed_at"),
        "data_quality": opening.get("data_quality") if opening.get("data_complete") else pre.get("data_quality", "unavailable"),
        "supply_available": any(value is not None for value in (relative_turnover, relative_volume, active_return)),
    }


def _factor_alignment(definition: dict[str, Any], market: dict[str, Any]) -> dict[str, Any]:
    requested = set(definition.get("global_factor_keys") or [])
    factors = [
        row for row in market.get("factors") or []
        if str(row.get("key") or "") in requested and _finite(row.get("change_rate")) is not None
    ]
    if not requested:
        return {
            "availability": "not_applicable",
            "state": "NOT_APPLICABLE",
            "label": "직접 연결 지표 미지정",
            "factors": [],
            "trading_signal": False,
        }
    if not factors:
        return {
            "availability": "unavailable",
            "state": "UNAVAILABLE",
            "label": "전일 미국 관련 지표 미수신",
            "factors": [],
            "trading_signal": False,
        }
    moves = [_finite(row.get("change_rate")) for row in factors]
    usable = [value for value in moves if value is not None]
    positive = sum(value > 0 for value in usable)
    negative = sum(value < 0 for value in usable)
    state = "POSITIVE" if positive and not negative else ("NEGATIVE" if negative and not positive else "MIXED")
    return {
        "availability": "available",
        "state": state,
        "label": {"POSITIVE": "전일 미국 관련 지표 우호", "NEGATIVE": "전일 미국 관련 지표 비우호", "MIXED": "전일 미국 관련 지표 혼재"}[state],
        "factors": [
            {"key": row.get("key"), "name": row.get("name"), "change_rate": _finite(row.get("change_rate")), "date": row.get("date")}
            for row in factors
        ],
        "trading_signal": False,
    }


def _weather_evidence(definition: dict[str, Any], matched_news: list[dict[str, Any]], market: dict[str, Any]) -> dict[str, Any]:
    if definition.get("event_type") != "weather":
        return {"availability": "not_applicable", "source": None, "trading_signal": False}
    weather = market.get("weather") or {}
    if weather.get("availability") == "available":
        return {
            "availability": "available",
            "source": weather.get("source"),
            "observed_at": weather.get("observed_at"),
            "temperature_c": _finite(weather.get("temperature_c")),
            "maximum_temperature_c": _finite(weather.get("maximum_temperature_c")),
            "temperature_anomaly_c": _finite(weather.get("temperature_anomaly_c")),
            "alerts": list(weather.get("alerts") or []),
            "trading_signal": False,
            "note": "날씨는 재료 확인용이며 단독 매매 신호가 아닙니다.",
        }
    if matched_news:
        return {
            "availability": "news_proxy",
            "source": "point-in-time news keywords",
            "observed_at": matched_news[0].get("published_at_kst"),
            "temperature_c": None,
            "maximum_temperature_c": None,
            "temperature_anomaly_c": None,
            "alerts": [],
            "trading_signal": False,
            "note": "실측·예보가 아닌 기사 기반 날씨 재료 프록시입니다.",
        }
    return {
        "availability": "unavailable",
        "source": None,
        "trading_signal": False,
        "note": "검증 가능한 날씨 실측·예보 데이터 미연결",
    }


def _checkpoint_rows(now: datetime) -> list[dict[str, Any]]:
    current = _seoul(now)
    minute = current.hour * 60 + current.minute
    rows = []
    for at, checkpoint_minute, purpose in CHECKPOINTS:
        if minute > checkpoint_minute:
            status = "done"
        elif minute == checkpoint_minute:
            status = "current"
        else:
            status = "upcoming"
        rows.append({"at": at, "status": status, "purpose": purpose})
    return rows


def _supply_state(members: list[dict[str, Any]], minimum_members: int, minimum_ratio: float) -> tuple[str, str]:
    directional = [row for row in members if row.get("positive") is not None]
    if not any(row.get("supply_available") for row in members):
        return "UNAVAILABLE", "수급 데이터 미수신"
    positive = sum(row.get("positive") is True for row in directional)
    ratio = positive / len(directional) if directional else None
    if len(directional) < minimum_members:
        return "SINGLE_NAME", "복수 종목 교차 확인 부족"
    if ratio is not None and ratio >= minimum_ratio:
        return "BROAD_POSITIVE", "테마 구성 종목의 상승 확산 관찰"
    if ratio is not None and ratio <= 1.0 - minimum_ratio:
        return "BROAD_NEGATIVE", "테마 구성 종목의 약세 확산 관찰"
    return "MIXED", "테마 구성 종목 방향 혼재"


def build_theme_supply_radar(
    *,
    now: datetime,
    phase: dict[str, Any],
    symbols: Iterable[dict[str, Any]],
    news: Iterable[dict[str, Any]],
    market: dict[str, Any],
    market_gate: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a point-in-time, observation-only theme and supply radar.

    The radar can rank evidence for inspection, but it cannot unlock entries,
    produce a probability, or override the KOSPI Market Gate.
    """
    cfg = config or {}
    current = _seoul(now)
    symbol_rows = [deepcopy(row) for row in symbols]
    symbol_lookup = {str(row.get("symbol") or ""): row for row in symbol_rows if row.get("symbol")}
    news_rows = [deepcopy(row) for row in news]
    definitions = _theme_definitions(cfg)
    minimum_members = max(2, int(cfg.get("minimum_theme_members", 2)))
    minimum_ratio = min(1.0, max(0.5, float(cfg.get("minimum_breadth_ratio", 0.6))))
    maximum_nxt_return = max(0.0, float(cfg.get("maximum_nxt_return_before_chase_review", 0.08)))
    maximum_opening_return = max(0.0, float(cfg.get("maximum_opening_return_before_chase_review", 0.05)))
    maximum_members = max(1, int(cfg.get("maximum_members_per_theme", 3)))
    themes: list[dict[str, Any]] = []

    for definition in definitions:
        matched_news = [item for item in news_rows if _matches_theme(item, definition)]
        member_symbols = set(definition.get("symbols") or [])
        for item in matched_news:
            member_symbols.update(str(value) for value in item.get("related_symbols") or [] if value)
        members = [_member_observation(symbol_lookup[symbol]) for symbol in sorted(member_symbols) if symbol in symbol_lookup]
        if not matched_news and not members:
            continue
        members.sort(
            key=lambda row: (
                row.get("relative_turnover") if row.get("relative_turnover") is not None else -1.0,
                row.get("cumulative_turnover") if row.get("cumulative_turnover") is not None else -1.0,
                row.get("active_return") if row.get("active_return") is not None else -1.0,
                row.get("symbol"),
            ),
            reverse=True,
        )
        for index, member in enumerate(members):
            member["role"] = "LEADER_OBSERVATION" if index == 0 else "FOLLOWER_OBSERVATION"

        supply_state, supply_label = _supply_state(members, minimum_members, minimum_ratio)
        directional = [row for row in members if row.get("positive") is not None]
        positive_count = sum(row.get("positive") is True for row in directional)
        positive_ratio = positive_count / len(directional) if directional else None
        relative_turnovers = [row["relative_turnover"] for row in members if row.get("relative_turnover") is not None]
        relative_volumes = [row["relative_volume"] for row in members if row.get("relative_volume") is not None]
        total_turnover = sum(row["cumulative_turnover"] for row in members if row.get("cumulative_turnover") is not None) or None

        chase_reasons = []
        for member in members:
            if member.get("nxt_return") is not None and member["nxt_return"] >= maximum_nxt_return:
                chase_reasons.append(f"{member['name']} NXT 단기 급등 후 추격 검토 필요")
            if member.get("opening_five_minute_return") is not None and member["opening_five_minute_return"] >= maximum_opening_return:
                chase_reasons.append(f"{member['name']} 첫 5분 급등 후 추격 검토 필요")
        if members and len(directional) < minimum_members:
            chase_reasons.append("한 종목 움직임만으로 테마 확산을 확인할 수 없음")

        new_news_count = sum(bool(item.get("is_new_since_last_checkpoint")) for item in matched_news)
        fresh_news_count = sum(item.get("freshness_label") == "새 기사" for item in matched_news)
        attention_state = "RISING_PROXY" if new_news_count or len(matched_news) >= 2 else ("OBSERVED" if matched_news else "UNAVAILABLE")
        alignment = _factor_alignment(definition, market)
        weather = _weather_evidence(definition, matched_news, market)
        catalysts = [
            {
                "title": item.get("title"),
                "source_name": item.get("source_name"),
                "source_url": item.get("source_url"),
                "published_at_kst": item.get("published_at_kst"),
                "official_disclosure": bool(item.get("official_disclosure")),
            }
            for item in matched_news[:3]
        ]
        blockers = ["테마·수급 단기 모델 미학습·미검증"]
        if not market_gate.get("stock_entries_allowed"):
            blockers.insert(0, f"KOSPI Gate {market_gate.get('status', 'UNAVAILABLE')} · 신규 진입 잠금")
        if supply_state in {"UNAVAILABLE", "SINGLE_NAME", "MIXED", "BROAD_NEGATIVE"}:
            blockers.append(supply_label)
        if chase_reasons:
            blockers.extend(chase_reasons)
        blockers.append("직접 조회수·검색순위 데이터 미연결")

        evidence = []
        if catalysts:
            evidence.append(f"체크포인트 이전 기사·공시 {len(catalysts)}건")
        if supply_state != "UNAVAILABLE":
            evidence.append(supply_label)
        if relative_turnovers:
            evidence.append(f"구성 종목 상대거래대금 중앙값 {median(relative_turnovers):.2f}배")
        if alignment.get("availability") == "available":
            evidence.append(alignment["label"])
        if weather.get("availability") in {"available", "news_proxy"}:
            evidence.append("날씨 재료 확인 · 단독 신호 아님")

        action = "CHASE_REVIEW" if chase_reasons else ("OBSERVE" if members and supply_state != "UNAVAILABLE" else "WAIT")
        themes.append({
            "key": definition["key"],
            "label": definition["label"],
            "rank": None,
            "action": action,
            "action_label": {"CHASE_REVIEW": "추격 검토", "OBSERVE": "관찰", "WAIT": "데이터 대기"}[action],
            "observation_only": True,
            "entry_signal_enabled": False,
            "score": None,
            "score_is_probability": False,
            "catalysts": catalysts,
            "attention": {
                "availability": "proxy" if matched_news else "unavailable",
                "state": attention_state,
                "news_count": len(matched_news),
                "fresh_news_count": fresh_news_count,
                "new_since_checkpoint_count": new_news_count,
                "direct_query_rank_available": False,
                "direct_query_rank": None,
                "note": "기사·공시 빈도 프록시이며 포털 조회수 데이터가 아닙니다.",
            },
            "supply": {
                "availability": "available" if any(row.get("supply_available") for row in members) else "unavailable",
                "state": supply_state,
                "label": supply_label,
                "member_count": len(members),
                "directional_member_count": len(directional),
                "positive_member_count": positive_count,
                "positive_member_ratio": positive_ratio,
                "relative_turnover_median": median(relative_turnovers) if relative_turnovers else None,
                "relative_volume_median": median(relative_volumes) if relative_volumes else None,
                "cumulative_turnover": total_turnover,
                "scope": "configured symbols only; not market-wide theme breadth",
            },
            "global_alignment": alignment,
            "previous_close_context": {
                "availability": "available" if any(
                    _finite(((symbol_lookup.get(row["symbol"]) or {}).get("premarket_summary") or {}).get("previous_close")) is not None
                    for row in members
                ) else "unavailable",
                "kospi": deepcopy(market.get("kospi")),
                "note": "NXT 수익률은 수신된 전일 종가 기준값과 함께 해석합니다.",
            },
            "weather_event": weather,
            "members": members[:maximum_members],
            "chase_risk": {
                "active": bool(chase_reasons),
                "reasons": _unique(chase_reasons),
                "thresholds_are_unvalidated_safety_rules": True,
            },
            "why_watch": _unique(evidence)[:4],
            "blockers": _unique(blockers)[:6],
        })

    themes.sort(
        key=lambda row: (
            row["supply"]["state"] == "BROAD_POSITIVE",
            row["attention"]["new_since_checkpoint_count"],
            row["attention"]["news_count"],
            row["supply"]["member_count"],
            row["supply"]["relative_turnover_median"] or -1.0,
            row["label"],
        ),
        reverse=True,
    )
    themes = themes[: max(1, int(cfg.get("maximum_themes", 3)))]
    annotations: dict[str, dict[str, Any]] = {}
    for rank, theme in enumerate(themes, 1):
        theme["rank"] = rank
        for member in theme["members"]:
            annotation = annotations.setdefault(member["symbol"], {
                "theme_labels": [],
                "primary_theme": None,
                "theme_rank": None,
                "role": None,
                "supply_state": None,
                "attention_state": None,
                "relative_turnover": member.get("relative_turnover"),
                "chase_risk": False,
                "observation_only": True,
                "entry_signal_enabled": False,
            })
            annotation["theme_labels"].append(theme["label"])
            if annotation["primary_theme"] is None:
                annotation.update({
                    "primary_theme": theme["label"],
                    "theme_rank": rank,
                    "role": member.get("role"),
                    "supply_state": theme["supply"]["state"],
                    "attention_state": theme["attention"]["state"],
                    "chase_risk": theme["chase_risk"]["active"],
                })

    core_theme_news = any(theme["catalysts"] for theme in themes)
    core_supply = any(theme["supply"]["availability"] == "available" for theme in themes)
    availability = "available" if core_theme_news and core_supply else ("partial" if themes else "unavailable")
    factors = market.get("factors") or []
    direct_weather = (market.get("weather") or {}).get("availability") == "available"
    checkpoint_rows = _checkpoint_rows(current)
    applicable_checkpoints = [row["at"] for row in checkpoint_rows if row["status"] in {"done", "current"}]
    checkpoint = applicable_checkpoints[-1] if applicable_checkpoints else CHECKPOINTS[0][0]
    summary = (
        f"관찰 테마 {len(themes)}개 · 검증 전 Shadow 모드"
        if themes else "체크포인트 이전 테마·수급 근거를 확인하지 못했습니다."
    )
    gate_status = str(market_gate.get("status") or "UNAVAILABLE")
    return {
        "schema_version": 1,
        "feature_name": "theme_supply_radar_v1",
        "mode": "shadow_only",
        "availability": availability,
        "summary": summary,
        "generated_at": current.isoformat(),
        "checkpoint": checkpoint,
        "next_checkpoint_at": phase.get("next_checkpoint_at"),
        "checkpoints": checkpoint_rows,
        "depends_on_kospi_gate": True,
        "kospi_gate_status": gate_status,
        "observation_only": True,
        "entry_signal_enabled": False,
        "probability_available": False,
        "probability": None,
        "abstention": {
            "active": True,
            "label": "테마 매매 보류",
            "reasons": _unique([
                "테마·수급 단기 모델 미학습·미검증",
                None if market_gate.get("stock_entries_allowed") else f"KOSPI Gate {gate_status}",
                "조회수·검색순위 직접 데이터 미연결",
            ]),
        },
        "universe": {
            "configured_symbol_count": len(symbol_rows),
            "scope": "configured symbols only",
            "market_wide_scanner_available": False,
            "note": "PREMARKET_SYMBOLS 범위 밖 종목은 발견하지 않습니다.",
        },
        "source_availability": {
            "nxt_supply": "available" if any(_member_observation(row)["supply_available"] for row in symbol_rows) else "unavailable",
            "theme_news_and_disclosures": "available" if core_theme_news else "unavailable",
            "previous_us_market": "available" if any(str(row.get("key") or "") in {"nasdaq", "sox"} for row in factors) else "unavailable",
            "direct_query_rank": "unavailable",
            "weather_observation_or_forecast": "available" if direct_weather else "unavailable",
        },
        "themes": themes,
        "candidate_annotations": annotations,
        "validation": {
            "status": "unvalidated",
            "target_horizons_minutes": list(cfg.get("validation_horizons_minutes") or [5, 15, 30]),
            "chronological_walk_forward": False,
            "transaction_costs_included": False,
            "slippage_included": False,
            "future_leakage_check": True,
            "weights_fitted": False,
            "friend_claim_used_as_weight": False,
        },
        "integrity": {
            "theme_membership_invented": False,
            "query_rank_fabricated": False,
            "weather_signal_fabricated": False,
            "can_override_kospi_gate": False,
        },
    }


def update_theme_radar_ledger(
    *,
    project_root: Path,
    radar: dict[str, Any],
    persist: bool,
    maximum_records: int = 5000,
) -> dict[str, Any]:
    path = project_root / "app_state" / "theme_supply_radar.jsonl"
    records: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                records.append(row)
    generated_at = str(radar.get("generated_at") or "")
    key = f"{generated_at[:16]}|{radar.get('kospi_gate_status')}"
    record = {
        "record_id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:20],
        "generated_at": generated_at,
        "checkpoint": radar.get("checkpoint"),
        "kospi_gate_status": radar.get("kospi_gate_status"),
        "mode": "shadow_only",
        "entry_signal_enabled": False,
        "themes": [
            {
                "key": row.get("key"),
                "label": row.get("label"),
                "rank": row.get("rank"),
                "supply_state": (row.get("supply") or {}).get("state"),
                "member_symbols": [member.get("symbol") for member in row.get("members") or []],
                "chase_risk": (row.get("chase_risk") or {}).get("active"),
                "return_5m": None,
                "return_15m": None,
                "return_30m": None,
            }
            for row in radar.get("themes") or []
        ],
    }
    records = [row for row in records if row.get("record_id") != record["record_id"]]
    records.append(record)
    records = sorted(records, key=lambda row: str(row.get("generated_at") or ""))[-max(1, maximum_records):]
    if persist:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    return {
        "availability": "available" if records else "unavailable",
        "record_count": len(records),
        "records": records[-20:],
        "path": str(path.relative_to(project_root)),
        "actual_orders_enabled": False,
        "outcome_labels_complete": any(
            any(theme.get("return_5m") is not None for theme in row.get("themes") or [])
            for row in records
        ),
    }
