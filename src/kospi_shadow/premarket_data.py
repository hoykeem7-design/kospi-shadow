from __future__ import annotations

import json
import os
import re
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

from .config import Settings
from .data import KIS_BASE_URL, _kis_credentials, _retry_get, fetch_kis_access_token
from .premarket import (
    PREDICTION_LABELS,
    SEOUL,
    UnavailablePredictor,
    build_stage_feature_bundle,
    build_auction_summary,
    build_opening_five_minute_summary,
    compute_labels,
    data_timing,
    explanation_factors,
    low_liquidity_status,
    order_book_features,
    relative_metric,
    resolve_market_phase,
    safe_ratio,
)


KIS_PRICE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-price"
KIS_PRICE_TR_ID = "FHKST01010100"
KIS_BOOK_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
KIS_BOOK_TR_ID = "FHKST01010200"
KIS_MINUTE_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
KIS_MINUTE_TR_ID = "FHKST03010200"
_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{6,7}$")


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in {"", "-", "None", "nan"}:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _first(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if value not in (None, "", "-"):
            return value
    return None


def _combine_provider_time(received_at: datetime, raw_time: Any, raw_date: Any = None) -> datetime | None:
    text = str(raw_time or "").replace(":", "").strip()
    if len(text) < 6 or not text[:6].isdigit():
        return None
    date_text = str(raw_date or received_at.strftime("%Y%m%d")).replace("-", "")
    if len(date_text) != 8 or not date_text.isdigit():
        return None
    try:
        return datetime.strptime(date_text + text[:6], "%Y%m%d%H%M%S").replace(tzinfo=SEOUL)
    except ValueError:
        return None


def configured_symbols(settings: Settings) -> list[dict[str, str]]:
    section = settings.raw.get("premarket") or {}
    raw = section.get("symbols") or []
    env_text = os.getenv("PREMARKET_SYMBOLS", "").strip()
    if env_text:
        raw = [part.strip() for part in env_text.split(",") if part.strip()]
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            symbol = item.strip().upper()
            name = symbol
        elif isinstance(item, dict):
            symbol = str(item.get("symbol") or "").strip().upper()
            name = str(item.get("name") or symbol).strip()
        else:
            continue
        if not _SYMBOL_PATTERN.fullmatch(symbol) or symbol in seen:
            continue
        seen.add(symbol)
        result.append({"symbol": symbol, "name": name})
    return result


class KisStockProvider:
    """Point-in-time KIS REST adapter; it never manufactures unavailable fields."""

    def __init__(self, *, timeout: int, retries: int) -> None:
        self.timeout = timeout
        self.retries = retries
        self.token = fetch_kis_access_token(timeout=timeout, retries=retries)

    def _headers(self, tr_id: str) -> dict[str, str]:
        app_key, app_secret = _kis_credentials()
        if not app_key or not app_secret:
            raise RuntimeError("KIS_APP_KEY and KIS_APP_SECRET must both be set")
        return {
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "charset": "UTF-8",
            "authorization": f"Bearer {self.token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _get(self, endpoint: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        response = _retry_get(
            f"{KIS_BASE_URL}{endpoint}",
            params=params,
            headers=self._headers(tr_id),
            timeout=self.timeout,
            retries=self.retries,
        )
        payload = response.json()
        if str(payload.get("rt_cd", "")) != "0":
            raise RuntimeError(f"KIS request failed [{payload.get('msg_cd', '')}]: {payload.get('msg1', '')}")
        return payload

    def current_price(self, symbol: str, market: str) -> dict[str, Any]:
        return self._get(
            KIS_PRICE_ENDPOINT,
            KIS_PRICE_TR_ID,
            {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": symbol},
        ).get("output") or {}

    def orderbook(self, symbol: str, market: str) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = self._get(
            KIS_BOOK_ENDPOINT,
            KIS_BOOK_TR_ID,
            {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": symbol},
        )
        output1 = payload.get("output1") or {}
        output2 = payload.get("output2") or {}
        if isinstance(output2, list):
            output2 = output2[0] if output2 else {}
        return output1, output2 if isinstance(output2, dict) else {}

    def minute_bars(self, symbol: str, market: str, input_hour: str) -> list[dict[str, Any]]:
        payload = self._get(
            KIS_MINUTE_ENDPOINT,
            KIS_MINUTE_TR_ID,
            {
                "FID_COND_MRKT_DIV_CODE": market,
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_HOUR_1": input_hour,
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_ETC_CLS_CODE": "",
            },
        )
        rows = payload.get("output2") or []
        return rows if isinstance(rows, list) else []


def normalize_snapshot(
    *,
    symbol: str,
    market: str,
    price_row: dict[str, Any],
    book_row: dict[str, Any],
    expected_row: dict[str, Any],
    received_at: datetime,
    stale_after_seconds: int,
) -> dict[str, Any]:
    observed = _combine_provider_time(
        received_at,
        _first(book_row, "aspr_acpt_hour", "stck_cntg_hour", "bsop_hour"),
        _first(price_row, "stck_bsop_date", "bsop_date"),
    )
    timing = data_timing(
        observed_at=observed,
        received_at=received_at,
        stale_after_seconds=stale_after_seconds,
        source=f"KIS {market} REST",
        available=bool(price_row or book_row or expected_row),
        unavailable_reason="provider_returned_no_rows",
    )
    market_cap_raw = _number(_first(price_row, "hts_avls", "stck_avls"))
    market_cap = market_cap_raw * 100_000_000 if market_cap_raw is not None else None
    book = order_book_features(
        ask_price=_first(book_row, "askp1"),
        bid_price=_first(book_row, "bidp1"),
        ask_quantity=_first(book_row, "askp_rsqn1", "total_askp_rsqn"),
        bid_quantity=_first(book_row, "bidp_rsqn1", "total_bidp_rsqn"),
    )
    previous_close = _number(_first(price_row, "stck_prdy_clpr", "stck_sdpr"))
    current_price = _number(_first(price_row, "stck_prpr"))
    return {
        "symbol": symbol,
        "market": market,
        "current_price": current_price,
        "previous_close": previous_close,
        "return": (current_price / previous_close - 1.0) if current_price is not None and previous_close not in (None, 0) else None,
        "open": _number(_first(price_row, "stck_oprc")),
        "high": _number(_first(price_row, "stck_hgpr")),
        "low": _number(_first(price_row, "stck_lwpr")),
        "cumulative_volume": _number(_first(price_row, "acml_vol")),
        "cumulative_turnover": _number(_first(price_row, "acml_tr_pbmn")),
        "market_cap": market_cap,
        "trade_strength": _number(_first(price_row, "tday_rltv", "cttr")),
        "buy_execution_count": _number(_first(price_row, "shnu_cntg_csnu")),
        "sell_execution_count": _number(_first(price_row, "seln_cntg_csnu")),
        **book,
        "expected_price": _number(_first(expected_row, "antc_cnpr")),
        "expected_volume": _number(_first(expected_row, "antc_vol")),
        **timing,
    }


def normalize_bars(rows: list[dict[str, Any]], *, received_at: datetime, source: str) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for row in rows:
        raw_time = _first(row, "stck_cntg_hour")
        observed = _combine_provider_time(received_at, raw_time, _first(row, "stck_bsop_date"))
        if observed is None:
            continue
        minute = observed.strftime("%H:%M")
        bars.append({
            "minute": minute,
            "price": _number(_first(row, "stck_prpr")),
            "open": _number(_first(row, "stck_oprc")),
            "high": _number(_first(row, "stck_hgpr")),
            "low": _number(_first(row, "stck_lwpr")),
            "volume": _number(_first(row, "cntg_vol")),
            # KIS exposes cumulative turnover here, not per-minute turnover. It is
            # intentionally not summed; the feature function derives price*volume.
            "turnover": None,
            "observed_at": observed.isoformat(),
            "received_at": received_at.isoformat(),
            "source": source,
        })
    return sorted(bars, key=lambda item: item["minute"])


def _history_path(project_root: Path, history_dir: str, symbol: str) -> Path:
    """Raw point-in-time history, separated from the general market cache."""
    root = Path(history_dir)
    if not root.is_absolute():
        root = project_root / root
    return root / "raw" / f"{symbol}.jsonl"


def _training_history_path(project_root: Path, history_dir: str, symbol: str) -> Path:
    """One normalized learning record per symbol/date/stage."""
    root = Path(history_dir)
    if not root.is_absolute():
        root = project_root / root
    return root / "training" / f"{symbol}.jsonl"


def load_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return records


def append_history(path: Path, record: dict[str, Any], *, maximum_records: int) -> None:
    records = load_history(path)
    key = record.get("collected_at")
    records = [item for item in records if item.get("collected_at") != key]
    records.append(record)
    records = sorted(records, key=lambda item: str(item.get("collected_at") or ""))[-int(maximum_records) :]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records), encoding="utf-8")


def upsert_training_history(path: Path, record: dict[str, Any], *, maximum_records: int) -> None:
    records = load_history(path)
    key = record.get("record_key")
    records = [item for item in records if item.get("record_key") != key]
    records.append(record)
    records = sorted(records, key=lambda item: str(item.get("record_key") or ""))[-int(maximum_records) :]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records), encoding="utf-8")


def _minute_of_day(iso_text: str) -> int | None:
    try:
        parsed = datetime.fromisoformat(iso_text).astimezone(SEOUL)
    except (TypeError, ValueError):
        return None
    return parsed.hour * 60 + parsed.minute


def _same_time_baseline(
    history: list[dict[str, Any]], *, now: datetime, field: str, tolerance_minutes: int
) -> list[float]:
    current_minute = now.hour * 60 + now.minute
    current_date = now.date()
    # Select at most one record per prior trading date. A record is eligible
    # only when its minute is not later than the current minute; tolerance is
    # backward-looking, never symmetric.
    candidates: dict[str, tuple[int, datetime, float]] = {}
    for item in history:
        if item.get("phase") not in {"premarket", "opening_auction"}:
            continue
        text = item.get("collected_at")
        if not text:
            continue
        try:
            observed = datetime.fromisoformat(str(text)).astimezone(SEOUL)
        except (TypeError, ValueError):
            continue
        minute = observed.hour * 60 + observed.minute
        if minute > current_minute or current_minute - minute > int(tolerance_minutes):
            continue
        date_text = observed.date().isoformat()
        if observed.date() == current_date:
            continue
        value = _number((item.get("premarket_summary") or {}).get(field))
        if value is None:
            continue
        previous = candidates.get(date_text)
        if previous is None or (minute, observed) > (previous[0], previous[1]):
            candidates[date_text] = (minute, observed, value)
    return [candidates[date][2] for date in sorted(candidates, reverse=True)]


def _opening_baseline(history: list[dict[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    seen_dates: set[str] = set()
    for item in reversed(history):
        summary = item.get("opening_five_minute_summary") or {}
        date_text = str(item.get("collected_at") or "")[:10]
        if not summary.get("data_complete") or not date_text or date_text in seen_dates:
            continue
        value = _number(summary.get(field))
        if value is not None:
            values.append(value)
            seen_dates.add(date_text)
    return values


def _same_time_aftermarket_baseline(
    history: list[dict[str, Any]], *, now: datetime, field: str, tolerance_minutes: int
) -> list[float]:
    current_minute = now.hour * 60 + now.minute
    candidates: dict[str, tuple[int, float]] = {}
    for item in history:
        summary = item.get("aftermarket_summary") or {}
        text = item.get("collected_at")
        if not text or summary.get("availability") != "available":
            continue
        try:
            observed = datetime.fromisoformat(str(text)).astimezone(SEOUL)
        except (TypeError, ValueError):
            continue
        minute = observed.hour * 60 + observed.minute
        if observed.date() == now.date() or minute > current_minute or current_minute - minute > tolerance_minutes:
            continue
        value = _number(summary.get(field))
        date_text = observed.date().isoformat()
        if value is not None and (date_text not in candidates or minute > candidates[date_text][0]):
            candidates[date_text] = (minute, value)
    return [candidates[key][1] for key in sorted(candidates, reverse=True)]


def _build_aftermarket_summary(
    snapshot: dict[str, Any] | None,
    history: list[dict[str, Any]],
    *,
    now: datetime,
    krx_close: Any,
    baseline_period: int,
    minimum_baseline_samples: int,
    same_time_tolerance_minutes: int,
) -> dict[str, Any]:
    if not snapshot or snapshot.get("availability") != "available":
        return {
            "availability": "unavailable",
            "unavailable_reason": "nxt_aftermarket_snapshot_not_received",
            "observed_at": None,
            "received_at": None,
            "data_delay_seconds": None,
            "stale": None,
            "data_quality": "unavailable",
            "source": "KIS NXT REST",
        }
    current = _number(snapshot.get("current_price"))
    close = _number(krx_close)
    volume_baseline = _same_time_aftermarket_baseline(
        history, now=now, field="cumulative_volume", tolerance_minutes=same_time_tolerance_minutes
    )[:baseline_period]
    turnover_baseline = _same_time_aftermarket_baseline(
        history, now=now, field="cumulative_turnover", tolerance_minutes=same_time_tolerance_minutes
    )[:baseline_period]
    observed = datetime.fromisoformat(snapshot["observed_at"]) if snapshot.get("observed_at") else None
    return {
        "availability": "available",
        "unavailable_reason": None,
        "krx_close": close,
        "current_price": current,
        "krx_close_return": current / close - 1.0 if current is not None and close not in (None, 0) else None,
        "high": snapshot.get("high"),
        "low": snapshot.get("low"),
        "cumulative_volume": snapshot.get("cumulative_volume"),
        "cumulative_turnover": snapshot.get("cumulative_turnover"),
        "relative_volume": relative_metric(
            snapshot.get("cumulative_volume"), volume_baseline,
            minimum_sample_count=minimum_baseline_samples, observed_at=observed,
        ),
        "relative_turnover": relative_metric(
            snapshot.get("cumulative_turnover"), turnover_baseline,
            minimum_sample_count=minimum_baseline_samples, observed_at=observed,
        ),
        "bid_ask_spread": snapshot.get("bid_ask_spread"),
        "liquidity_status": "observed_without_validated_threshold",
        "observed_at": snapshot.get("observed_at"),
        "received_at": snapshot.get("received_at"),
        "data_delay_seconds": snapshot.get("data_delay_seconds"),
        "stale": snapshot.get("stale"),
        "data_quality": snapshot.get("data_quality"),
        "source": snapshot.get("source"),
    }


def _last_premarket_summary(
    history: list[dict[str, Any]], *, trading_date: str
) -> dict[str, Any] | None:
    for item in reversed(history):
        if (
            str(item.get("collected_at") or "")[:10] == trading_date
            and item.get("phase") in {"premarket", "opening_auction"}
            and item.get("premarket_summary")
        ):
            return item["premarket_summary"]
    return None


def _build_premarket_summary(
    snapshot: dict[str, Any] | None,
    bars: list[dict[str, Any]],
    history: list[dict[str, Any]],
    *,
    now: datetime,
    baseline_period: int,
    minimum_baseline_samples: int,
    same_time_tolerance_minutes: int,
    minimum_volume: float,
    minimum_turnover: float,
) -> dict[str, Any]:
    if not snapshot or snapshot.get("availability") != "available":
        return {
            "availability": "unavailable",
            "unavailable_reason": "nxt_snapshot_not_received",
            "observed_at": None,
            "received_at": None,
            "data_delay_seconds": None,
            "stale": None,
            "data_quality": "unavailable",
            "source": "KIS NXT REST",
        }
    volume_baseline = _same_time_baseline(
        history, now=now, field="cumulative_volume", tolerance_minutes=same_time_tolerance_minutes
    )[:baseline_period]
    turnover_baseline = _same_time_baseline(
        history, now=now, field="cumulative_turnover", tolerance_minutes=same_time_tolerance_minutes
    )[:baseline_period]
    relative_volume = relative_metric(
        snapshot.get("cumulative_volume"), volume_baseline,
        minimum_sample_count=minimum_baseline_samples,
        observed_at=datetime.fromisoformat(snapshot["observed_at"]) if snapshot.get("observed_at") else None,
    )
    relative_turnover = relative_metric(
        snapshot.get("cumulative_turnover"), turnover_baseline,
        minimum_sample_count=minimum_baseline_samples,
        observed_at=datetime.fromisoformat(snapshot["observed_at"]) if snapshot.get("observed_at") else None,
    )
    recent = [bar for bar in bars if bar.get("minute") and str(bar["minute"]) >= (now.replace(minute=max(0, now.minute - 5))).strftime("%H:%M")]
    recent_prices = [_number(item.get("price")) for item in recent]
    recent_volumes = [_number(item.get("volume")) for item in recent]
    five_minute_return = (
        recent_prices[-1] / recent_prices[0] - 1.0
        if len(recent_prices) >= 2 and recent_prices[0] not in (None, 0) and recent_prices[-1] is not None
        else None
    )
    five_minute_volume_change = (
        recent_volumes[-1] / recent_volumes[0] - 1.0
        if len(recent_volumes) >= 2 and recent_volumes[0] not in (None, 0) and recent_volumes[-1] is not None
        else None
    )
    buy_count = _number(snapshot.get("buy_execution_count"))
    sell_count = _number(snapshot.get("sell_execution_count"))
    execution_imbalance = safe_ratio(
        buy_count - sell_count if buy_count is not None and sell_count is not None else None,
        buy_count + sell_count if buy_count is not None and sell_count is not None else None,
    )
    high = _number(snapshot.get("high"))
    low = _number(snapshot.get("low"))
    return {
        "availability": "available",
        "unavailable_reason": None,
        "nxt_return": snapshot.get("return"),
        "nxt_high": high,
        "nxt_low": low,
        "nxt_range": high - low if high is not None and low is not None else None,
        "nxt_final_price": snapshot.get("current_price"),
        "previous_close": snapshot.get("previous_close"),
        "cumulative_volume": snapshot.get("cumulative_volume"),
        "cumulative_turnover": snapshot.get("cumulative_turnover"),
        "relative_volume": relative_volume,
        "relative_turnover": relative_turnover,
        "turnover_to_market_cap": safe_ratio(snapshot.get("cumulative_turnover"), snapshot.get("market_cap")),
        "bid_ask_spread": snapshot.get("bid_ask_spread"),
        "orderbook_imbalance": snapshot.get("orderbook_imbalance"),
        "trade_strength": snapshot.get("trade_strength"),
        "execution_imbalance": execution_imbalance,
        "last_5m_return": five_minute_return,
        "last_5m_volume_change": five_minute_volume_change,
        "last_5m_turnover_change": None,
        "single_trade_turnover_concentration": None,
        "liquidity": low_liquidity_status(
            volume=snapshot.get("cumulative_volume"),
            turnover=snapshot.get("cumulative_turnover"),
            minimum_volume=minimum_volume,
            minimum_turnover=minimum_turnover,
        ),
        "material": {
            "availability": "unavailable",
            "unavailable_reason": "symbol_news_and_official_disclosure_provider_not_connected",
            "material_type": None,
            "material_direction": None,
            "material_confidence": None,
            "material_freshness": None,
            "source_count": 0,
            "official_disclosure": None,
            "source_type": None,
            "source_name": None,
            "published_at": None,
            "observed_at": None,
            "received_at": None,
            "data_delay_seconds": None,
            "stale": None,
            "data_quality": "unavailable",
            "source": None,
        },
        "observed_at": snapshot.get("observed_at"),
        "received_at": snapshot.get("received_at"),
        "data_delay_seconds": snapshot.get("data_delay_seconds"),
        "stale": snapshot.get("stale"),
        "data_quality": snapshot.get("data_quality"),
        "source": snapshot.get("source"),
    }


def _unavailable_symbol_payload(
    symbol: dict[str, str], phase: dict[str, str], reason: str, *, trading_date: str
) -> dict[str, Any]:
    predictor = UnavailablePredictor(reason)
    pre = {"availability": "unavailable", "unavailable_reason": reason, "data_quality": "unavailable", "observed_at": None}
    auction = build_auction_summary([], previous_close=None, nxt_final_price=None)
    bundle = build_stage_feature_bundle(
        trading_date=trading_date,
        stage="premarket_prediction",
        premarket_summary=pre,
        opening_auction_summary=auction,
    )
    return {
        **symbol,
        "market_phase": phase["phase"],
        "phase_display": phase["display"],
        "data_availability": {"availability": "unavailable", "unavailable_reason": reason},
        "premarket_summary": pre,
        "opening_auction_summary": auction,
        "opening_five_minute_summary": {"availability": "unavailable", "unavailable_reason": reason},
        "premarket_prediction": predictor.predict(bundle, stage="premarket_prediction"),
        "post_open_0905_prediction": None,
        "positive_factors": [],
        "negative_factors": [],
        "model_metadata": {"model_type": "unavailable", "actual_trained_model": False, "experimental": True},
        "labels": None,
    }


def build_premarket_experiment(
    settings: Settings,
    project_root: Path,
    *,
    now_seoul: datetime,
    market_snapshot: dict[str, Any] | None,
    persist_history: bool = True,
) -> dict[str, Any]:
    now = now_seoul.astimezone(SEOUL) if now_seoul.tzinfo else now_seoul.replace(tzinfo=SEOUL)
    phase = resolve_market_phase(now)
    cfg = settings.raw.get("premarket") or {}
    symbols = configured_symbols(settings)
    base = {
        "schema_version": 1,
        "feature_name": "two_stage_nxt_premarket_framework",
        "display_name": "2단계 데이터 수집·피처·검증 프레임워크. 종목 확률 모델은 미학습 상태.",
        "market_phase": phase["phase"],
        "phase_display": phase["display"],
        "timezone": "Asia/Seoul",
        "generated_at": now.isoformat(),
        "configured": bool(symbols),
        "configured_symbol_count": len(symbols),
        "symbols": [],
        "prediction_labels": PREDICTION_LABELS,
        "warning": "프리장 가격과 거래량은 후보 선정 신호이며, 본장 방향을 보장하지 않습니다. 확률은 시초가 이후 데이터에 따라 변경될 수 있습니다.",
        "experimental": True,
        "production_truth": {
            "stock_model_trained": False,
            "backtest_completed": False,
            "prediction_improvement_verified": False,
        },
    }
    if not symbols:
        base["data_availability"] = {
            "availability": "unavailable",
            "unavailable_reason": "no_symbols_configured",
            "configuration": "premarket.symbols or PREMARKET_SYMBOLS repository variable",
        }
        return base

    data_cfg = settings.section("data")
    timeout = int(data_cfg.get("request_timeout_seconds", 30))
    retries = int(data_cfg.get("request_retries", 4))
    history_dir = os.getenv(
        "PREMARKET_HISTORY_DIR",
        str(cfg.get("history_dir", "data/premarket_history")),
    )
    baseline_period = int(cfg.get("baseline_sessions", 20))
    minimum_baseline_samples = int(cfg.get("minimum_baseline_samples", 20))
    tolerance = int(cfg.get("same_time_tolerance_minutes", 5))
    stale_after = int(cfg.get("stale_after_seconds", 180))
    maximum_raw_records = int(cfg.get("maximum_raw_history_records_per_symbol", 25000))
    maximum_training_records = int(cfg.get("maximum_training_records_per_symbol", 5000))
    minimum_model_samples = int(cfg.get("minimum_model_samples", 252))
    try:
        provider = KisStockProvider(timeout=timeout, retries=retries)
    except Exception:
        base["symbols"] = [
            _unavailable_symbol_payload(
                item, phase, "kis_provider_unavailable", trading_date=now.date().isoformat()
            )
            for item in symbols
        ]
        base["data_availability"] = {"availability": "unavailable", "unavailable_reason": "kis_provider_unavailable"}
        return base

    for item in symbols:
        symbol = item["symbol"]
        path = _history_path(project_root, history_dir, symbol)
        training_path = _training_history_path(project_root, history_dir, symbol)
        history = load_history(path)
        warnings: list[str] = []
        nxt_snapshot: dict[str, Any] | None = None
        krx_snapshot: dict[str, Any] | None = None
        bars: list[dict[str, Any]] = []
        label_bars: list[dict[str, Any]] = []
        auction_snapshot: dict[str, Any] | None = None
        aftermarket_snapshot: dict[str, Any] | None = None
        t = now.timetz().replace(tzinfo=None)
        try:
            if t < dtime(9, 0):
                received = datetime.now(SEOUL)
                price = provider.current_price(symbol, "NX")
                book, expected = provider.orderbook(symbol, "NX")
                nxt_snapshot = normalize_snapshot(
                    symbol=symbol, market="NX", price_row=price, book_row=book,
                    expected_row=expected, received_at=received, stale_after_seconds=stale_after,
                )
                try:
                    raw_bars = provider.minute_bars(symbol, "NX", now.strftime("%H%M%S"))
                    bars = normalize_bars(raw_bars, received_at=received, source="KIS NXT one-minute bars")
                except Exception:
                    warnings.append("nxt_minute_bars_not_received")
            else:
                received = datetime.now(SEOUL)
                price = provider.current_price(symbol, "J")
                book, expected = provider.orderbook(symbol, "J")
                krx_snapshot = normalize_snapshot(
                    symbol=symbol, market="J", price_row=price, book_row=book,
                    expected_row=expected, received_at=received, stale_after_seconds=stale_after,
                )
                raw_bars = provider.minute_bars(symbol, "J", "090500")
                bars = normalize_bars(raw_bars, received_at=received, source="KIS KRX one-minute bars")
                if t >= dtime(9, 30):
                    raw_label_bars = provider.minute_bars(symbol, "J", "093000")
                    label_bars = normalize_bars(raw_label_bars, received_at=received, source="KIS KRX one-minute bars")
        except Exception:
            warnings.append("kis_stock_snapshot_not_received")

        if phase["phase"] == "opening_auction":
            try:
                received = datetime.now(SEOUL)
                price = provider.current_price(symbol, "J")
                book, expected = provider.orderbook(symbol, "J")
                auction_snapshot = normalize_snapshot(
                    symbol=symbol, market="J", price_row=price, book_row=book,
                    expected_row=expected, received_at=received, stale_after_seconds=stale_after,
                )
            except Exception:
                warnings.append("krx_opening_auction_not_received")

        if dtime(15, 40) <= t:
            try:
                received = datetime.now(SEOUL)
                price = provider.current_price(symbol, "NX")
                book, expected = provider.orderbook(symbol, "NX")
                aftermarket_snapshot = normalize_snapshot(
                    symbol=symbol, market="NX", price_row=price, book_row=book,
                    expected_row=expected, received_at=received, stale_after_seconds=stale_after,
                )
            except Exception:
                warnings.append("nxt_aftermarket_snapshot_not_received")

        premarket_summary = (
            _build_premarket_summary(
                nxt_snapshot, bars, history, now=now, baseline_period=baseline_period,
                minimum_baseline_samples=minimum_baseline_samples,
                same_time_tolerance_minutes=tolerance,
                minimum_volume=float(cfg.get("minimum_volume", 0)),
                minimum_turnover=float(cfg.get("minimum_turnover", 0)),
            )
            if t < dtime(9, 0)
            else (_last_premarket_summary(history, trading_date=now.date().isoformat()) or {
                "availability": "unavailable",
                "unavailable_reason": "premarket_snapshot_not_collected",
                "data_quality": "unavailable",
            })
        )
        auction_items = [
            item["auction_snapshot"] for item in history
            if str(item.get("collected_at") or "")[:10] == now.date().isoformat() and item.get("auction_snapshot")
        ]
        if auction_snapshot:
            auction_items.append(auction_snapshot)
        auction_summary = build_auction_summary(
            auction_items,
            previous_close=premarket_summary.get("previous_close"),
            nxt_final_price=premarket_summary.get("nxt_final_price"),
        )
        opening_summary = build_opening_five_minute_summary(
            bars,
            previous_close=(krx_snapshot or {}).get("previous_close") or premarket_summary.get("previous_close"),
            baseline_volumes=_opening_baseline(history, "volume")[:baseline_period],
            baseline_turnovers=_opening_baseline(history, "turnover")[:baseline_period],
            minimum_baseline_samples=minimum_baseline_samples,
            stale_after_seconds=stale_after,
        ) if t >= dtime(9, 5) else {
            "availability": "unavailable",
            "unavailable_reason": "opening_confirmation_in_progress" if t >= dtime(9, 0) else "not_started",
            "data_complete": False,
            "data_quality": "unavailable",
        }
        if market_snapshot:
            advancers = _number(market_snapshot.get("advancers"))
            decliners = _number(market_snapshot.get("decliners"))
            opening_summary["market_breadth"] = safe_ratio(
                advancers, advancers + decliners if advancers is not None and decliners is not None else None
            )
            opening_summary["market_advancers"] = advancers
            opening_summary["market_decliners"] = decliners
            opening_summary["market_advance_decline_ratio"] = safe_ratio(advancers, decliners)
            opening_summary["market_index_direction"] = _number(market_snapshot.get("change_rate"))
        market_indicators = {
            "availability": "available" if market_snapshot else "unavailable",
            "unavailable_reason": None if market_snapshot else "market_indicators_not_received",
            "market_index_direction": opening_summary.get("market_index_direction"),
            "market_breadth": opening_summary.get("market_breadth"),
            "market_advancers": opening_summary.get("market_advancers"),
            "market_decliners": opening_summary.get("market_decliners"),
            "market_advance_decline_ratio": opening_summary.get("market_advance_decline_ratio"),
            "sector_index_direction": opening_summary.get("sector_index_direction"),
            "observed_at": (market_snapshot or {}).get("observed_at"),
            "received_at": (market_snapshot or {}).get("received_at"),
            "data_quality": (market_snapshot or {}).get("data_quality", "unknown_time" if market_snapshot else "unavailable"),
            "source": (market_snapshot or {}).get("source", "KIS KRX market snapshot" if market_snapshot else None),
        }
        opening_summary["actual_open_vs_nxt_final"] = safe_ratio(
            opening_summary.get("actual_open"), premarket_summary.get("nxt_final_price")
        )
        if opening_summary["actual_open_vs_nxt_final"] is not None:
            opening_summary["actual_open_vs_nxt_final"] -= 1.0
        price_0930 = next((_number(bar.get("price")) for bar in label_bars if bar.get("minute") == "09:30"), None)
        close_price = (
            (krx_snapshot or {}).get("current_price")
            if t >= dtime(15, 30)
            else None
        )
        closing_summary = {
            "availability": "available" if close_price is not None else "unavailable",
            "unavailable_reason": None if close_price is not None else "official_close_not_received",
            "actual_open": (krx_snapshot or {}).get("open") or opening_summary.get("actual_open"),
            "price_0930": price_0930,
            "close_price": close_price,
            "high": (krx_snapshot or {}).get("high"),
            "low": (krx_snapshot or {}).get("low"),
            "volume": (krx_snapshot or {}).get("cumulative_volume"),
            "turnover": (krx_snapshot or {}).get("cumulative_turnover"),
            "observed_at": (krx_snapshot or {}).get("observed_at"),
            "received_at": (krx_snapshot or {}).get("received_at"),
            "data_quality": (krx_snapshot or {}).get("data_quality", "unavailable"),
            "source": (krx_snapshot or {}).get("source", "KIS KRX REST"),
        }
        aftermarket_summary = _build_aftermarket_summary(
            aftermarket_snapshot,
            history,
            now=now,
            krx_close=close_price,
            baseline_period=baseline_period,
            minimum_baseline_samples=minimum_baseline_samples,
            same_time_tolerance_minutes=tolerance,
        ) if t >= dtime(15, 40) else {
            "availability": "unavailable",
            "unavailable_reason": "nxt_aftermarket_not_started",
            "data_quality": "unavailable",
        }
        labels = compute_labels(
            previous_close=(krx_snapshot or {}).get("previous_close") or premarket_summary.get("previous_close"),
            open_price=(krx_snapshot or {}).get("open") or opening_summary.get("actual_open"),
            price_0930=price_0930,
            close_price=close_price,
            minimum_gap_price_unit=float(cfg.get("minimum_gap_price_unit", 0)),
        )
        predictor = UnavailablePredictor(
            "stock_level_training_and_calibration_unavailable",
            minimum_required_sample_count=minimum_model_samples,
        )
        pre_bundle = build_stage_feature_bundle(
            trading_date=now.date().isoformat(),
            stage="premarket_prediction",
            premarket_summary=premarket_summary,
            opening_auction_summary=auction_summary,
        )
        pre_prediction = predictor.predict(pre_bundle, stage="premarket_prediction")
        post_bundle = (
            build_stage_feature_bundle(
                trading_date=now.date().isoformat(),
                stage="post_open_0905_prediction",
                premarket_summary=premarket_summary,
                opening_auction_summary=auction_summary,
                opening_five_minute_summary=opening_summary,
                market_indicators=market_indicators,
            )
            if t >= dtime(9, 5) and opening_summary.get("data_complete")
            else None
        )
        post_prediction = (
            predictor.predict(post_bundle, stage="post_open_0905_prediction")
            if post_bundle is not None
            else None
        )
        positive, negative = explanation_factors(
            premarket_summary=premarket_summary,
            opening_summary=opening_summary if opening_summary.get("data_complete") else None,
        )
        record = {
            "symbol": symbol,
            "collected_at": now.isoformat(),
            "phase": phase["phase"],
            "premarket_summary": premarket_summary if t < dtime(9, 0) else None,
            "auction_snapshot": auction_snapshot,
            "opening_five_minute_summary": opening_summary if opening_summary.get("data_complete") else None,
            "closing_summary": closing_summary if closing_summary.get("availability") == "available" else None,
            "aftermarket_summary": aftermarket_summary if aftermarket_summary.get("availability") == "available" else None,
            "labels": labels,
        }
        if persist_history:
            append_history(path, record, maximum_records=maximum_raw_records)
            training_bundle = post_bundle or pre_bundle
            training_stage = (
                "post_open_0905_prediction" if post_bundle is not None else "premarket_prediction"
            )
            upsert_training_history(
                training_path,
                {
                    "record_key": f"{now.date().isoformat()}:{training_stage}",
                    "symbol": symbol,
                    "trading_date": now.date().isoformat(),
                    "stage": training_stage,
                    "feature_bundle": training_bundle,
                    "labels": labels,
                    "updated_at": now.isoformat(),
                },
                maximum_records=maximum_training_records,
            )
        base["symbols"].append({
            **item,
            "market_phase": phase["phase"],
            "phase_display": phase["display"],
            "data_availability": {
                "availability": "available" if premarket_summary.get("availability") == "available" or opening_summary.get("data_complete") else "unavailable",
                "unavailable_reason": None if premarket_summary.get("availability") == "available" or opening_summary.get("data_complete") else "required_market_data_not_received",
                "warnings": warnings,
            },
            "premarket_summary": premarket_summary,
            "opening_auction_summary": auction_summary,
            "opening_five_minute_summary": opening_summary,
            "closing_summary": closing_summary,
            "aftermarket_summary": aftermarket_summary,
            "market_indicators": market_indicators,
            "premarket_prediction": pre_prediction,
            "post_open_0905_prediction": post_prediction,
            "positive_factors": positive,
            "negative_factors": negative,
            "model_metadata": {
                "model_type": "TwoStagePredictor interface with UnavailablePredictor production default",
                "actual_trained_model": False,
                "probability_method": None,
                "calibration_method": None,
                "calibration_status": "unavailable",
                "training_sample_count": 0,
                "experimental": True,
            },
            "labels": labels,
            "timestamps": {
                "generated_at": now.isoformat(),
                "premarket_observed_at": premarket_summary.get("observed_at"),
                "opening_observed_at": opening_summary.get("observed_at"),
            },
        })
    base["data_availability"] = {
        "availability": "available" if any(item["data_availability"]["availability"] == "available" for item in base["symbols"]) else "unavailable",
        "provider": "KIS REST",
        "history_store": f"{history_dir}/raw/<symbol>.jsonl",
        "training_store": f"{history_dir}/training/<symbol>.jsonl",
        "history_storage": "durable premarket-history branch in GitHub Actions; local directory otherwise",
        "maximum_raw_history_records_per_symbol": maximum_raw_records,
        "maximum_training_records_per_symbol": maximum_training_records,
        "same_time_baseline_sessions": baseline_period,
        "minimum_required_baseline_samples": minimum_baseline_samples,
    }
    return base
