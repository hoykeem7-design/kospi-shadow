from __future__ import annotations

import json
import math
import os
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import pandas as pd

from .config import Settings
from .data import (
    KIS_BASE_URL,
    _kis_credentials,
    _retry_get,
    fetch_kis_access_token,
)

SEOUL = ZoneInfo("Asia/Seoul")
KIS_INDEX_PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-index-price"
KIS_INDEX_PRICE_TR_ID = "FHPUP02100000"
KIS_FUTURES_BOARD_ENDPOINT = "/uapi/domestic-futureoption/v1/quotations/display-board-futures"
KIS_FUTURES_BOARD_TR_ID = "FHPIF05030200"
FRED_RELEASE_DATES_ENDPOINT = "https://api.stlouisfed.org/fred/releases/dates"


@dataclass(frozen=True)
class SessionContext:
    code: str
    label: str
    description: str
    target_mode: str
    next_checkpoint_at: str
    next_checkpoint_label: str


def _between(value: dtime, start: dtime, end: dtime) -> bool:
    return start <= value < end


def resolve_session_context(now_seoul: datetime) -> SessionContext:
    """Resolve the user-facing market phase from Korea time.

    This is deliberately a clock/session resolver, not an exchange-holiday
    calendar. The dashboard displays a holiday caveat when it is a weekend.
    """
    if now_seoul.tzinfo is None:
        now_seoul = now_seoul.replace(tzinfo=SEOUL)
    else:
        now_seoul = now_seoul.astimezone(SEOUL)
    t = now_seoul.timetz().replace(tzinfo=None)
    weekday = now_seoul.weekday() < 5
    if not weekday:
        return SessionContext(
            "WEEKEND_CLOSED", "주말·휴장 구간", "다음 영업일 장전 계획을 준비합니다.",
            "next_session", "다음 평일 07:45", "장전 브리핑",
        )
    if _between(t, dtime(0, 0), dtime(6, 0)):
        return SessionContext(
            "NIGHT_FUTURES", "야간 선물", "KOSPI200 야간선물과 미국장을 반영하는 구간입니다.",
            "next_session", "06:05", "야간장 마감 확인",
        )
    if _between(t, dtime(6, 0), dtime(8, 0)):
        return SessionContext(
            "PREOPEN_BRIEF", "장전 준비", "전일 국장·미국장·야간선물·뉴스를 합쳐 오늘 계획을 만듭니다.",
            "same_session", "08:00", "NXT 프리마켓 시작",
        )
    if _between(t, dtime(8, 0), dtime(8, 45)):
        return SessionContext(
            "NXT_PRE", "NXT 프리마켓", "08:00 초기 반응을 보되 08:10 확인 전 추격을 피합니다.",
            "same_session", "08:10", "프리마켓 10분 확인",
        )
    if _between(t, dtime(8, 45), dtime(9, 0)):
        return SessionContext(
            "FUTURES_PREOPEN", "선물 개장·본장 직전", "KOSPI200 선물 방향과 09:00 현물 개장을 교차 확인합니다.",
            "same_session", "09:10", "본장 10분 확인",
        )
    if _between(t, dtime(9, 0), dtime(9, 10)):
        return SessionContext(
            "KRX_OPEN_DISCOVERY", "본장 가격발견", "개장 직후 변동성이 커서 첫 10분 확인을 우선합니다.",
            "same_session", "09:10", "개장 10분 확인",
        )
    if _between(t, dtime(9, 10), dtime(12, 0)):
        return SessionContext(
            "MORNING_SESSION", "오전 본장", "프리마켓·선물·현물 방향의 일치 여부를 감시합니다.",
            "same_session", "12:00", "정오 재평가",
        )
    if _between(t, dtime(12, 0), dtime(15, 20)):
        return SessionContext(
            "AFTERNOON_SESSION", "오후 본장", "오전 추세 지속과 외국인·선물 변화를 재평가합니다.",
            "same_session", "15:20", "마감 구간 점검",
        )
    if _between(t, dtime(15, 20), dtime(15, 30)):
        return SessionContext(
            "CLOSE_WINDOW", "마감 직전", "신규 추격보다 종가 위험과 익일 보유 여부를 점검합니다.",
            "same_session", "15:30", "정규장 종료",
        )
    if _between(t, dtime(15, 30), dtime(18, 0)):
        return SessionContext(
            "NXT_AFTER", "NXT 애프터마켓", "정규장 결과를 확인하고 다음 영업일 시나리오를 준비합니다.",
            "next_session", "18:00", "야간선물 시작",
        )
    if _between(t, dtime(18, 0), dtime(20, 0)):
        return SessionContext(
            "NXT_AFTER_NIGHT_FUTURES", "애프터마켓·야간선물", "NXT 애프터와 야간선물의 방향 일치를 확인합니다.",
            "next_session", "20:05", "애프터 종료 브리핑",
        )
    return SessionContext(
        "POST_MARKET", "장 종료 후", "오늘 국장과 애프터·야간선물 초기 흐름으로 다음 장을 예측합니다.",
        "next_session", "다음 영업일 07:45", "장전 브리핑",
    )


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in {"", "-", "None", "nan"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _pct(value: Any) -> float | None:
    number = _safe_float(value)
    return number / 100.0 if number is not None else None


def _common_kis_headers(token: str, tr_id: str) -> dict[str, str]:
    app_key, app_secret = _kis_credentials()
    if not app_key or not app_secret:
        raise RuntimeError("KIS_APP_KEY and KIS_APP_SECRET must both be set")
    return {
        "Content-Type": "application/json",
        "Accept": "text/plain",
        "charset": "UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": tr_id,
        "custtype": "P",
    }


def fetch_kis_index_snapshot(*, timeout: int, retries: int, token: str | None = None) -> dict[str, Any]:
    token = token or fetch_kis_access_token(timeout=timeout, retries=retries)
    response = _retry_get(
        f"{KIS_BASE_URL}{KIS_INDEX_PRICE_ENDPOINT}",
        params={"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"},
        headers=_common_kis_headers(token, KIS_INDEX_PRICE_TR_ID),
        timeout=timeout,
        retries=retries,
    )
    payload = response.json()
    if str(payload.get("rt_cd", "")) != "0":
        raise RuntimeError(f"KIS index snapshot failed [{payload.get('msg_cd', '')}]: {payload.get('msg1', '')}")
    row = payload.get("output") or {}
    return {
        "name": "KOSPI",
        "price": _safe_float(row.get("bstp_nmix_prpr")),
        "change_rate": _pct(row.get("bstp_nmix_prdy_ctrt")),
        "change": _safe_float(row.get("bstp_nmix_prdy_vrss")),
        "open": _safe_float(row.get("bstp_nmix_oprc")),
        "high": _safe_float(row.get("bstp_nmix_hgpr")),
        "low": _safe_float(row.get("bstp_nmix_lwpr")),
        "volume": _safe_float(row.get("acml_vol")),
        "advancers": _safe_float(row.get("ascn_issu_cnt")),
        "decliners": _safe_float(row.get("down_issu_cnt")),
        "source": "KIS",
    }


def fetch_kis_futures_snapshot(*, timeout: int, retries: int, token: str | None = None) -> dict[str, Any]:
    token = token or fetch_kis_access_token(timeout=timeout, retries=retries)
    response = _retry_get(
        f"{KIS_BASE_URL}{KIS_FUTURES_BOARD_ENDPOINT}",
        params={
            "FID_COND_MRKT_DIV_CODE": "F",
            "FID_COND_SCR_DIV_CODE": "20503",
            "FID_COND_MRKT_CLS_CODE": "",
        },
        headers=_common_kis_headers(token, KIS_FUTURES_BOARD_TR_ID),
        timeout=timeout,
        retries=retries,
    )
    payload = response.json()
    if str(payload.get("rt_cd", "")) != "0":
        raise RuntimeError(f"KIS futures board failed [{payload.get('msg_cd', '')}]: {payload.get('msg1', '')}")
    rows = payload.get("output1") or payload.get("output") or []
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("KIS futures board returned no contracts")
    row = rows[0]
    return {
        "name": str(row.get("hts_kor_isnm") or "KOSPI200 선물").strip(),
        "symbol": str(row.get("futs_shrn_iscd") or "").strip(),
        "price": _safe_float(row.get("futs_prpr")),
        "change_rate": _pct(row.get("futs_prdy_ctrt")),
        "change": _safe_float(row.get("futs_prdy_vrss")),
        "basis": _safe_float(row.get("basis")),
        "ask": _safe_float(row.get("futs_askp")),
        "bid": _safe_float(row.get("futs_bidp")),
        "volume": _safe_float(row.get("acml_vol")),
        "source": "KIS",
    }


def _latest_cache_snapshot(cache_dir: Path, key: str) -> dict[str, Any] | None:
    candidates = [cache_dir / f"yahoo_{key}.csv", cache_dir / f"fred_{key}.csv"]
    path = next((p for p in candidates if p.exists()), None)
    if path is None:
        return None
    frame = pd.read_csv(path)
    if frame.empty or "Date" not in frame.columns:
        return None
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    value_col = "Close" if "Close" in frame.columns else ("value" if "value" in frame.columns else None)
    if value_col is None:
        return None
    frame[value_col] = pd.to_numeric(frame[value_col], errors="coerce")
    frame = frame.dropna(subset=["Date", value_col]).sort_values("Date")
    if frame.empty:
        return None
    latest = frame.iloc[-1]
    previous = frame.iloc[-2] if len(frame) > 1 else None
    value = float(latest[value_col])
    change_rate = None
    change = None
    if previous is not None and float(previous[value_col]) != 0:
        prev = float(previous[value_col])
        change = value - prev
        change_rate = value / prev - 1.0
    return {
        "name": key,
        "price": value,
        "change": change,
        "change_rate": change_rate,
        "date": pd.Timestamp(latest["Date"]).strftime("%Y-%m-%d"),
        "source": "cache",
    }


def collect_factor_snapshots(cache_dir: Path) -> list[dict[str, Any]]:
    labels = {
        "sp500": "S&P500",
        "nasdaq": "NASDAQ",
        "sox": "반도체지수",
        "vix": "VIX",
        "usdk_rw": "USD/KRW",
        "us10y": "미국 10년물",
        "us2y": "미국 2년물",
    }
    result: list[dict[str, Any]] = []
    for key, label in labels.items():
        item = _latest_cache_snapshot(cache_dir, key)
        if item:
            item["key"] = key
            item["name"] = label
            result.append(item)
    return result


def _parse_rss_date(text: str | None) -> str | None:
    if not text:
        return None
    try:
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(text)
        return parsed.astimezone(SEOUL).isoformat()
    except Exception:
        return None


def _news_impact(title: str) -> tuple[str, list[str]]:
    positive = ("급등", "상승", "호조", "완화", "인하", "수출 증가", "실적 개선", "협상 타결")
    negative = ("급락", "하락", "충돌", "관세", "긴축", "인상", "침체", "제재", "실적 악화", "파업")
    tags: list[str] = []
    lowered = title.lower()
    for tag, words in {
        "미국": ("미국", "연준", "fomc", "나스닥", "다우"),
        "반도체": ("반도체", "엔비디아", "삼성전자", "sk하이닉스"),
        "환율": ("환율", "원달러", "달러"),
        "금리": ("금리", "채권", "국채"),
        "정책": ("정부", "세제", "관세", "규제"),
    }.items():
        if any(word.lower() in lowered for word in words):
            tags.append(tag)
    score = sum(word in title for word in positive) - sum(word in title for word in negative)
    impact = "positive" if score > 0 else ("negative" if score < 0 else "neutral")
    return impact, tags[:3]


def fetch_market_news(*, query: str, limit: int, timeout: int, retries: int) -> list[dict[str, Any]]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"
    response = _retry_get(
        url,
        headers={"User-Agent": "Mozilla/5.0 KOSPI-Shadow-Coach/4.0"},
        timeout=timeout,
        retries=retries,
    )
    root = ET.fromstring(response.content)
    items: list[dict[str, Any]] = []
    for node in root.findall("./channel/item")[:limit]:
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        source_node = node.find("source")
        source = (source_node.text or "").strip() if source_node is not None else ""
        impact, tags = _news_impact(title)
        items.append({
            "title": title,
            "link": link,
            "source": source,
            "published_at": _parse_rss_date(node.findtext("pubDate")),
            "impact": impact,
            "tags": tags,
        })
    return items


def fetch_fred_release_calendar(*, start: str, end: str, timeout: int, retries: int) -> list[dict[str, Any]]:
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        return []
    response = _retry_get(
        FRED_RELEASE_DATES_ENDPOINT,
        params={
            "api_key": api_key,
            "file_type": "json",
            "realtime_start": start,
            "realtime_end": end,
            "include_release_dates_with_no_data": "true",
            "limit": 100,
            "order_by": "release_date",
            "sort_order": "asc",
        },
        timeout=timeout,
        retries=retries,
    )
    payload = response.json()
    rows = payload.get("release_dates") or []
    return [
        {
            "date": str(row.get("date") or ""),
            "name": str(row.get("release_name") or "").strip(),
            "release_id": row.get("release_id"),
            "source": "FRED",
        }
        for row in rows
        if row.get("date") and row.get("release_name")
    ]


def build_briefing(
    *,
    prediction: dict[str, Any],
    index: dict[str, Any] | None,
    futures: dict[str, Any] | None,
    factors: list[dict[str, Any]],
    news: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    probability = float(prediction.get("probability_intraday_up", 0.5))
    direction = str(prediction.get("research_direction", "FLAT"))
    items.append({
        "tone": "positive" if probability > 0.55 else ("negative" if probability < 0.45 else "neutral"),
        "title": f"모델 {direction}",
        "text": f"대상일 상승확률은 {probability:.1%}입니다. 승격 게이트와 실시간 확인을 함께 봐야 합니다.",
    })

    live_parts: list[str] = []
    if index and index.get("change_rate") is not None:
        live_parts.append(f"KOSPI {float(index['change_rate']):+.2%}")
    if futures and futures.get("change_rate") is not None:
        live_parts.append(f"KOSPI200 선물 {float(futures['change_rate']):+.2%}")
    if live_parts:
        items.append({
            "tone": "positive" if sum((index or {}).get("change_rate") or 0 for _ in [0]) + ((futures or {}).get("change_rate") or 0) > 0 else "negative",
            "title": "국내 실시간",
            "text": " · ".join(live_parts),
        })

    factor_moves = [item for item in factors if item.get("change_rate") is not None]
    factor_moves.sort(key=lambda item: abs(float(item["change_rate"])), reverse=True)
    if factor_moves:
        top = factor_moves[:3]
        text = " · ".join(f"{item['name']} {float(item['change_rate']):+.2%}" for item in top)
        signed = sum(float(item["change_rate"]) * (-1 if item.get("key") in {"vix", "usdk_rw"} else 1) for item in top)
        items.append({"tone": "positive" if signed > 0 else ("negative" if signed < 0 else "neutral"), "title": "해외·매크로", "text": text})

    counts = {"positive": 0, "negative": 0, "neutral": 0}
    for item in news:
        counts[item.get("impact", "neutral")] = counts.get(item.get("impact", "neutral"), 0) + 1
    if news:
        tone = "positive" if counts["positive"] > counts["negative"] else ("negative" if counts["negative"] > counts["positive"] else "neutral")
        items.append({
            "tone": tone,
            "title": "뉴스 흐름",
            "text": f"긍정 {counts['positive']} · 부정 {counts['negative']} · 중립 {counts['neutral']}건으로 분류됐습니다.",
        })
    if events:
        nearest = events[0]
        items.append({"tone": "neutral", "title": "가까운 발표", "text": f"{nearest['date']} · {nearest['name']}"})
    return items[:5]


def _market_alignment(prediction: dict[str, Any], index: dict[str, Any] | None, futures: dict[str, Any] | None) -> dict[str, Any]:
    probability = float(prediction.get("probability_intraday_up", 0.5))
    base_direction = "up" if probability >= 0.5 else "down"
    signals: list[tuple[str, float]] = []
    for label, item, weight in (("KOSPI", index, 0.45), ("KOSPI200 선물", futures, 0.55)):
        if item and item.get("change_rate") is not None:
            rate = float(item["change_rate"])
            signed = max(-1.0, min(1.0, rate / 0.01))
            signals.append((label, signed * weight))
    live_score = sum(value for _, value in signals)
    live_direction = "up" if live_score > 0.08 else ("down" if live_score < -0.08 else "neutral")
    aligned = live_direction == base_direction or live_direction == "neutral"
    return {
        "base_direction": base_direction,
        "live_direction": live_direction,
        "live_score": round(live_score, 4),
        "aligned": aligned,
        "components": [{"name": name, "score": round(score, 4)} for name, score in signals],
    }


def build_coaching(
    *,
    prediction: dict[str, Any],
    promotion: dict[str, Any],
    session: SessionContext,
    index: dict[str, Any] | None,
    futures: dict[str, Any] | None,
) -> dict[str, Any]:
    p = float(prediction.get("probability_intraday_up", 0.5))
    direction = str(prediction.get("research_direction", "FLAT"))
    alignment = _market_alignment(prediction, index, futures)
    gate_open = bool(promotion.get("signal_enabled"))
    confidence = min(1.0, abs(p - 0.5) / 0.15)
    timing_score = round(100 * (0.35 * confidence + 0.65 * min(1.0, abs(alignment["live_score"]))), 0)

    if not gate_open:
        action = "WAIT"
        headline = "관망 우선"
        rationale = "모델 승격 기준이 닫혀 있어 실제 매수 신호로 사용하지 않습니다."
    elif session.code == "NXT_PRE":
        action = "WAIT_CONFIRMATION"
        headline = "08:47 선물 확인 대기"
        rationale = "현재 정적 앱은 NXT 실시간 체결을 직접 구독하지 않으므로 08:45 선물 개장 전 진입을 권하지 않습니다."
    elif direction == "FLAT":
        action = "WAIT"
        headline = "방향 우위 없음"
        rationale = "상승·하락 확률 차이가 작아 신규 진입보다 대기가 낫습니다."
    elif session.code in {"NXT_PRE", "KRX_OPEN_DISCOVERY"} and not alignment["aligned"]:
        action = "WAIT_CONFIRMATION"
        headline = "10분 확인 후 판단"
        rationale = "초기 시장 반응이 모델 방향과 맞지 않아 추격하지 않습니다."
    elif session.code == "CLOSE_WINDOW":
        action = "AVOID_CHASE"
        headline = "마감 추격 금지"
        rationale = "마감 직전에는 신규 진입보다 익일 갭 위험을 우선 관리합니다."
    elif alignment["aligned"] and confidence >= 0.45:
        action = "RESEARCH_SCALE_IN"
        headline = "분할 접근 검토"
        rationale = "모델 방향과 현물·선물 확인 신호가 일치하지만 실전 승격 전에는 연구용입니다."
    else:
        action = "WAIT"
        headline = "다음 체크포인트 대기"
        rationale = "모델 우위 또는 실시간 확인 신호가 충분하지 않습니다."

    return {
        "action": action,
        "headline": headline,
        "rationale": rationale,
        "timing_score": timing_score,
        "confidence_label": "높음" if confidence >= 0.7 else ("보통" if confidence >= 0.35 else "낮음"),
        "alignment": alignment,
        "next_checkpoint_at": session.next_checkpoint_at,
        "next_checkpoint_label": session.next_checkpoint_label,
        "disclaimer": "자동주문이 아닌 연구용 코칭입니다. 주문 전 가격·호가·손실한도를 별도로 확인하세요.",
    }


def _checkpoint_timeline(now_seoul: datetime) -> list[dict[str, Any]]:
    checkpoints = [
        ("07:45", "장전 브리핑", "전일 국장·미국장·야간선물·주요 일정"),
        ("08:00", "NXT 개장", "초기 갭과 거래 방향 관찰"),
        ("08:10", "프리마켓 확인", "첫 10분 방향·급등 추격 여부 판단"),
        ("08:47", "선물 확인", "KOSPI200 선물과 현물 예상 방향 비교"),
        ("09:10", "본장 확인", "개장 10분 가격발견 후 재평가"),
        ("12:00", "정오 재평가", "오전 추세 지속성 확인"),
        ("15:20", "마감 점검", "종가·오버나이트 위험 점검"),
        ("20:05", "익일 브리핑", "애프터마켓·야간선물 초기 흐름 반영"),
    ]
    current_minutes = now_seoul.hour * 60 + now_seoul.minute
    result = []
    for at, label, note in checkpoints:
        hour, minute = map(int, at.split(":"))
        point = hour * 60 + minute
        status = "done" if current_minutes > point else ("current" if abs(current_minutes - point) <= 5 else "upcoming")
        result.append({"at": at, "label": label, "note": note, "status": status})
    return result


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def _history_append(path: Path, snapshot: dict[str, Any], limit: int = 120) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = [item for item in loaded if isinstance(item, dict)]
        except Exception:
            history = []
    key = snapshot.get("generated_at_seoul")
    history = [item for item in history if item.get("generated_at_seoul") != key]
    history.append(snapshot)
    history = history[-limit:]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    return history


def generate_coach_app(settings: Settings, project_root: Path, *, now_seoul: datetime | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    now_seoul = (now_seoul or datetime.now(SEOUL)).astimezone(SEOUL)
    output_dir = project_root / "outputs"
    metrics = _load_json(output_dir / "metrics.json")
    prediction = metrics["latest_prediction"]
    promotion = metrics["promotion"]
    manifest = metrics["data_manifest"]
    data_cfg = settings.section("data")
    coach_cfg = settings.raw.get("coach") or {}
    timeout = int(data_cfg.get("request_timeout_seconds", 30))
    retries = int(data_cfg.get("request_retries", 4))
    warnings: list[str] = []
    index: dict[str, Any] | None = None
    futures: dict[str, Any] | None = None

    try:
        token = fetch_kis_access_token(timeout=timeout, retries=retries)
        try:
            index = fetch_kis_index_snapshot(timeout=timeout, retries=retries, token=token)
        except Exception as exc:
            warnings.append(f"KIS index snapshot: {exc}")
        try:
            futures = fetch_kis_futures_snapshot(timeout=timeout, retries=retries, token=token)
        except Exception as exc:
            warnings.append(f"KIS futures snapshot: {exc}")
    except Exception as exc:
        warnings.append(f"KIS token: {exc}")

    cache_dir = project_root / str(data_cfg.get("cache_dir", "data/cache"))
    factors = collect_factor_snapshots(cache_dir)
    try:
        news = fetch_market_news(
            query=str(coach_cfg.get("news_query", "코스피 OR 증시 OR 환율 OR 금리 OR 반도체")),
            limit=int(coach_cfg.get("news_limit", 12)),
            timeout=timeout,
            retries=max(1, min(retries, 2)),
        )
    except Exception as exc:
        news = []
        warnings.append(f"News feed: {exc}")
    try:
        event_start = now_seoul.date().isoformat()
        event_end = (now_seoul.date() + timedelta(days=int(coach_cfg.get("event_horizon_days", 7)))).isoformat()
        events = fetch_fred_release_calendar(start=event_start, end=event_end, timeout=timeout, retries=max(1, min(retries, 2)))
    except Exception as exc:
        events = []
        warnings.append(f"FRED release calendar: {exc}")

    session = resolve_session_context(now_seoul)
    coaching = build_coaching(
        prediction=prediction,
        promotion=promotion,
        session=session,
        index=index,
        futures=futures,
    )
    dashboard = {
        "schema_version": 1,
        "app_version": "4.0.0",
        "generated_at_seoul": now_seoul.isoformat(),
        "session": {
            "code": session.code,
            "label": session.label,
            "description": session.description,
            "target_mode": session.target_mode,
            "weekend_or_holiday_caveat": now_seoul.weekday() >= 5,
        },
        "prediction": prediction,
        "promotion": promotion,
        "validation": {
            "roc_auc": metrics.get("classification", {}).get("roc_auc"),
            "brier": metrics.get("classification", {}).get("brier"),
            "baseline_brier": metrics.get("classification", {}).get("baseline_brier"),
            "brier_improvement": metrics.get("classification", {}).get("brier_improvement"),
            "oos_n": metrics.get("classification", {}).get("n"),
            "strategy_sharpe": metrics.get("strategy_proxy", {}).get("model", {}).get("annualized_sharpe"),
            "max_drawdown": metrics.get("strategy_proxy", {}).get("model", {}).get("max_drawdown"),
        },
        "data_quality": {
            "target_provider": manifest.get("target_provider"),
            "target_official": manifest.get("target_official"),
            "latest_source": manifest.get("target_latest_source"),
            "target_date_max": manifest.get("target_date_max"),
            "warnings": [*manifest.get("collection_warnings", []), *warnings],
        },
        "market": {
            "kospi": index,
            "kospi200_futures": futures,
            "factors": factors,
        },
        "coaching": coaching,
        "briefing": build_briefing(
            prediction=prediction, index=index, futures=futures, factors=factors, news=news, events=events
        ),
        "timeline": _checkpoint_timeline(now_seoul),
        "news": news,
        "events": events,
        "runtime_seconds": round(time.perf_counter() - started, 3),
    }

    site_source = project_root / "app"
    site_output = project_root / "site"
    if site_output.exists():
        shutil.rmtree(site_output)
    shutil.copytree(site_source, site_output)
    data_dir = site_output / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "dashboard.json").write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    escaped = json.dumps(dashboard, ensure_ascii=False).replace("</", "<\\/")
    (data_dir / "initial-data.js").write_text(f"window.__INITIAL_DASHBOARD__ = {escaped};\n", encoding="utf-8")

    history_path = project_root / "app_state" / "history.json"
    history = _history_append(history_path, {
        "generated_at_seoul": dashboard["generated_at_seoul"],
        "target_date": prediction.get("candidate_target_date"),
        "probability_intraday_up": prediction.get("probability_intraday_up"),
        "research_direction": prediction.get("research_direction"),
        "session_code": session.code,
        "coach_action": coaching["action"],
        "coach_headline": coaching["headline"],
        "target_official": manifest.get("target_official"),
    })
    (data_dir / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "coach_dashboard.json").write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    return dashboard
