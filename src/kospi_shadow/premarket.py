from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, time as dtime
from statistics import median, pstdev
from typing import Any, Iterable, Protocol
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")
PREDICTION_LABELS = {
    "open_to_0930_up": "09:30 price is strictly above the actual open",
    "open_to_close_up": "official close is strictly above the actual open",
    "gap_continuation_0930": "09:30 move extends the opening gap in the same direction",
}


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _seoul(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SEOUL)
    return value.astimezone(SEOUL)


def resolve_market_phase(now: datetime) -> dict[str, str]:
    """Resolve the two-stage experiment phase using explicit Korea time."""
    current = _seoul(now)
    t = current.timetz().replace(tzinfo=None)
    if t < dtime(8, 50):
        return {"phase": "premarket", "display": "프리장 예측"}
    if t < dtime(9, 0):
        return {"phase": "opening_auction", "display": "동시호가 반영 중"}
    if t < dtime(9, 5):
        return {"phase": "opening_confirmation", "display": "시초 확인 중"}
    return {"phase": "post_open_updated", "display": "확인 업데이트 완료"}


def data_timing(
    *,
    observed_at: datetime | None,
    received_at: datetime | None,
    stale_after_seconds: int,
    source: str,
    available: bool,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    observed = _seoul(observed_at) if observed_at is not None else None
    received = _seoul(received_at) if received_at is not None else None
    delay = None
    stale = None
    if observed is not None and received is not None:
        delay = max(0, int((received - observed).total_seconds()))
        stale = delay > int(stale_after_seconds)
    if not available:
        quality = "unavailable"
    elif stale is True:
        quality = "stale"
    elif observed is None:
        quality = "unknown_time"
    else:
        quality = "good"
    return {
        "availability": "available" if available else "unavailable",
        "unavailable_reason": None if available else (unavailable_reason or "data_not_received"),
        "observed_at": observed.isoformat() if observed else None,
        "received_at": received.isoformat() if received else None,
        "data_delay_seconds": delay,
        "stale": stale,
        "data_quality": quality,
        "source": source,
    }


def unavailable_metric(reason: str, source: str) -> dict[str, Any]:
    return {
        "value": None,
        **data_timing(
            observed_at=None,
            received_at=None,
            stale_after_seconds=0,
            source=source,
            available=False,
            unavailable_reason=reason,
        ),
    }


def measured_metric(
    value: Any,
    *,
    source: str,
    observed_at: datetime | None,
    received_at: datetime | None,
    stale_after_seconds: int,
    unavailable_reason: str = "data_not_received",
) -> dict[str, Any]:
    number = _finite(value)
    if number is None:
        return unavailable_metric(unavailable_reason, source)
    return {
        "value": number,
        **data_timing(
            observed_at=observed_at,
            received_at=received_at,
            stale_after_seconds=stale_after_seconds,
            source=source,
            available=True,
        ),
    }


def relative_metric(
    current_value: Any,
    baseline_values: Iterable[Any],
    *,
    minimum_sample_count: int,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    current = _finite(current_value)
    baseline = [number for value in baseline_values if (number := _finite(value)) is not None]
    baseline_median = float(median(baseline)) if baseline else None
    available = (
        current is not None
        and len(baseline) >= int(minimum_sample_count)
        and baseline_median is not None
        and baseline_median > 0
    )
    if current is None:
        reason = "current_value_missing"
    elif len(baseline) < int(minimum_sample_count):
        reason = "insufficient_same_time_history"
    elif baseline_median is None or baseline_median <= 0:
        reason = "baseline_median_not_positive"
    else:
        reason = None
    return {
        "baseline_sample_count": len(baseline),
        "minimum_required_sample_count": int(minimum_sample_count),
        "baseline_median": baseline_median,
        "current_value": current,
        "relative_value": current / baseline_median if available else None,
        "baseline_available": available,
        "unavailable_reason": reason,
        "observed_at": _seoul(observed_at).isoformat() if observed_at else None,
        "data_quality": "good" if available else "unavailable",
    }


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    top = _finite(numerator)
    bottom = _finite(denominator)
    if top is None or bottom is None or bottom == 0:
        return None
    return top / bottom


def order_book_features(
    *, ask_price: Any, bid_price: Any, ask_quantity: Any, bid_quantity: Any
) -> dict[str, float | None]:
    ask = _finite(ask_price)
    bid = _finite(bid_price)
    ask_qty = _finite(ask_quantity)
    bid_qty = _finite(bid_quantity)
    midpoint = (ask + bid) / 2.0 if ask is not None and bid is not None else None
    spread = safe_ratio(ask - bid, midpoint) if ask is not None and bid is not None else None
    quantity_sum = ask_qty + bid_qty if ask_qty is not None and bid_qty is not None else None
    imbalance = safe_ratio(bid_qty - ask_qty, quantity_sum) if quantity_sum is not None else None
    return {
        "best_ask": ask,
        "best_bid": bid,
        "bid_ask_spread": spread,
        "ask_quantity": ask_qty,
        "bid_quantity": bid_qty,
        "orderbook_imbalance": imbalance,
    }


def expected_price_stability(prices: Iterable[Any]) -> dict[str, Any]:
    values = [number for value in prices if (number := _finite(value)) is not None]
    if len(values) < 2:
        return {
            "value": None,
            "sample_count": len(values),
            "available": False,
            "unavailable_reason": "at_least_two_expected_prices_required",
        }
    centre = abs(float(median(values)))
    if centre == 0:
        return {
            "value": None,
            "sample_count": len(values),
            "available": False,
            "unavailable_reason": "expected_price_median_zero",
        }
    normalized_volatility = pstdev(values) / centre
    return {
        "value": max(0.0, 1.0 - min(1.0, normalized_volatility)),
        "normalized_volatility": normalized_volatility,
        "sample_count": len(values),
        "available": True,
        "unavailable_reason": None,
    }


def build_auction_summary(
    snapshots: list[dict[str, Any]],
    *,
    previous_close: Any,
    nxt_final_price: Any,
) -> dict[str, Any]:
    usable = [item for item in snapshots if _finite(item.get("expected_price")) is not None]
    prices = [_finite(item.get("expected_price")) for item in usable]
    quantities = [_finite(item.get("expected_volume")) for item in usable]
    last = usable[-1] if usable else {}
    last_price = _finite(last.get("expected_price"))
    last_quantity = _finite(last.get("expected_volume"))
    first_quantity = next((value for value in quantities if value is not None), None)
    price_return = None
    prev = _finite(previous_close)
    if last_price is not None and prev not in (None, 0):
        price_return = last_price / prev - 1.0
    estimated_turnover = (
        last_price * last_quantity
        if last_price is not None and last_quantity is not None
        else None
    )
    quantity_change = None
    if first_quantity not in (None, 0) and last_quantity is not None:
        quantity_change = last_quantity / first_quantity - 1.0
    nxt_price = _finite(nxt_final_price)
    nxt_difference = (
        last_price / nxt_price - 1.0
        if last_price is not None and nxt_price not in (None, 0)
        else None
    )
    price_direction = 0 if price_return in (None, 0) else (1 if price_return > 0 else -1)
    nxt_return = (nxt_price / prev - 1.0) if nxt_price is not None and prev not in (None, 0) else None
    nxt_direction = 0 if nxt_return in (None, 0) else (1 if nxt_return > 0 else -1)
    return {
        "availability": "available" if last_price is not None else "unavailable",
        "unavailable_reason": None if last_price is not None else "opening_auction_data_not_received",
        "expected_price": last_price,
        "expected_price_return": price_return,
        "expected_volume": last_quantity,
        "expected_turnover": estimated_turnover,
        "expected_price_stability": expected_price_stability(prices),
        "expected_volume_change": quantity_change,
        "expected_price_vs_nxt_final": nxt_difference,
        # Scheduled snapshots are five minutes apart. They cannot truthfully
        # represent a final-one-minute series, even when two timestamps happen
        # to fall within a minute because provider time semantics are uncertain.
        "last_1m_expected_price_range": None,
        "last_1m_expected_volume_change": None,
        "last_1m_collection_status": "unavailable",
        "last_1m_unavailable_reason": "sub_minute_auction_series_not_collected",
        "direction_matches_nxt": (
            price_direction == nxt_direction
            if price_direction and nxt_direction
            else None
        ),
        "update_count": len(usable),
        "observation_start": usable[0].get("observed_at") if usable else None,
        "observation_end": usable[-1].get("observed_at") if usable else None,
        "observed_at": last.get("observed_at"),
        "received_at": last.get("received_at"),
        "data_delay_seconds": last.get("data_delay_seconds"),
        "data_quality": last.get("data_quality", "unavailable"),
        "source": last.get("source", "KIS KRX orderbook/expected REST"),
        "experimental": True,
    }


def vwap(prices: Iterable[Any], volumes: Iterable[Any]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for raw_price, raw_volume in zip(prices, volumes):
        price = _finite(raw_price)
        volume = _finite(raw_volume)
        if price is None or volume is None or volume < 0:
            continue
        numerator += price * volume
        denominator += volume
    return numerator / denominator if denominator > 0 else None


def open_state(observations: Iterable[Any], open_price: Any, gap_direction: str | None) -> dict[str, Any]:
    """Evaluate opening-price hold using bar extremes when they are available.

    Recovery is deliberately conservative: an intrabar breach is considered
    recovered only when a *later* bar closes back across the open. Minute OHLC
    data cannot establish the ordering of a breach and recovery inside one bar.
    """
    opening = _finite(open_price)
    rows: list[dict[str, float | None]] = []
    for item in observations:
        if isinstance(item, dict):
            close = _finite(item.get("price"))
            rows.append({
                "close": close,
                "low": _finite(item.get("low")) if _finite(item.get("low")) is not None else close,
                "high": _finite(item.get("high")) if _finite(item.get("high")) is not None else close,
            })
        elif (number := _finite(item)) is not None:
            rows.append({"close": number, "low": number, "high": number})
    if opening is None or not rows or gap_direction not in {"up", "down"}:
        return {
            "open_held": None,
            "open_recovery": None,
            "unavailable_reason": "directional_gap_and_prices_required",
            "recovery_observation_limit": "minute_ohlc_sequence_unknown",
        }
    if gap_direction == "up":
        breached = [index for index, row in enumerate(rows) if row["low"] is not None and row["low"] < opening]
        held = not breached
        recovered = bool(breached and any(
            row["close"] is not None and row["close"] >= opening
            for row in rows[breached[0] + 1 :]
        ))
    else:
        breached = [index for index, row in enumerate(rows) if row["high"] is not None and row["high"] > opening]
        held = not breached
        recovered = bool(breached and any(
            row["close"] is not None and row["close"] <= opening
            for row in rows[breached[0] + 1 :]
        ))
    return {
        "open_held": held,
        "open_recovery": recovered,
        "unavailable_reason": None,
        "recovery_observation_limit": "same_minute_recovery_not_inferable_from_ohlc",
    }


def compute_labels(
    *,
    previous_close: Any,
    open_price: Any,
    price_0930: Any,
    close_price: Any,
    minimum_gap_price_unit: float,
) -> dict[str, Any]:
    previous = _finite(previous_close)
    opening = _finite(open_price)
    at_0930 = _finite(price_0930)
    closing = _finite(close_price)
    gap_direction: str | None = None
    if previous is not None and opening is not None:
        difference = opening - previous
        if abs(difference) <= float(minimum_gap_price_unit):
            gap_direction = "flat"
        else:
            gap_direction = "up" if difference > 0 else "down"

    open_to_0930 = at_0930 > opening if opening is not None and at_0930 is not None else None
    open_to_close = closing > opening if opening is not None and closing is not None else None
    continuation = None
    if gap_direction == "up" and at_0930 is not None and opening is not None and previous is not None:
        continuation = at_0930 > opening and (at_0930 - previous) > (opening - previous)
    elif gap_direction == "down" and at_0930 is not None and opening is not None and previous is not None:
        continuation = at_0930 < opening and (previous - at_0930) > (previous - opening)
    return {
        "open_to_0930_up": open_to_0930,
        "open_to_close_up": open_to_close,
        "gap_direction": gap_direction,
        "gap_continuation_0930": continuation,
        "label_definitions": PREDICTION_LABELS,
    }


def build_opening_five_minute_summary(
    bars: list[dict[str, Any]],
    *,
    previous_close: Any,
    baseline_volumes: Iterable[Any],
    baseline_turnovers: Iterable[Any],
    minimum_baseline_samples: int,
    stale_after_seconds: int = 180,
) -> dict[str, Any]:
    ordered = sorted((bar for bar in bars if bar.get("minute") is not None), key=lambda item: item["minute"])
    window = [bar for bar in ordered if "09:00" <= str(bar["minute"]) < "09:05"]
    complete = len({str(item["minute"]) for item in window}) >= 5
    if not complete:
        return {
            "availability": "unavailable",
            "unavailable_reason": "first_five_minutes_incomplete",
            "data_complete": False,
            "sample_count": len(window),
            "observed_at": None,
            "received_at": None,
            "data_delay_seconds": None,
            "stale": None,
            "data_quality": "unavailable",
        }
    opening = _finite(window[0].get("open")) or _finite(window[0].get("price"))
    prices = [_finite(item.get("price")) for item in window]
    highs = [_finite(item.get("high")) or _finite(item.get("price")) for item in window]
    lows = [_finite(item.get("low")) or _finite(item.get("price")) for item in window]
    volumes = [_finite(item.get("volume")) for item in window]
    turnovers = [_finite(item.get("turnover")) for item in window]
    valid_prices = [value for value in prices if value is not None]
    valid_highs = [value for value in highs if value is not None]
    valid_lows = [value for value in lows if value is not None]
    total_volume = sum(value for value in volumes if value is not None) if any(value is not None for value in volumes) else None
    total_turnover = sum(value for value in turnovers if value is not None) if any(value is not None for value in turnovers) else None
    if total_turnover is None and all(price is not None and volume is not None for price, volume in zip(prices, volumes)):
        total_turnover = sum(float(price) * float(volume) for price, volume in zip(prices, volumes))
    current = valid_prices[-1] if valid_prices else None
    prev = _finite(previous_close)
    if opening is not None and prev is not None:
        gap_direction = "flat" if opening == prev else ("up" if opening > prev else "down")
    else:
        gap_direction = None
    state = open_state(window, opening, gap_direction)
    first1 = safe_ratio(valid_prices[0], opening) - 1.0 if valid_prices and opening not in (None, 0) else None
    first3 = safe_ratio(valid_prices[min(2, len(valid_prices) - 1)], opening) - 1.0 if valid_prices and opening not in (None, 0) else None
    first5 = safe_ratio(current, opening) - 1.0 if current is not None and opening not in (None, 0) else None
    # KIS minute bars expose a minute close and volume, not every execution.
    # This is a close*volume approximation and must not be labelled true VWAP.
    calculated_vwap = vwap(prices, volumes)
    relative_volume = relative_metric(
        total_volume, baseline_volumes, minimum_sample_count=minimum_baseline_samples
    )
    relative_turnover = relative_metric(
        total_turnover, baseline_turnovers, minimum_sample_count=minimum_baseline_samples
    )
    observed_at = window[-1].get("observed_at")
    received_at = window[-1].get("received_at")
    delay_seconds = None
    stale = None
    try:
        delay_seconds = max(0, int((datetime.fromisoformat(str(received_at)) - datetime.fromisoformat(str(observed_at))).total_seconds()))
        stale = delay_seconds > int(stale_after_seconds)
    except (TypeError, ValueError):
        pass
    return {
        "availability": "available",
        "unavailable_reason": None,
        "data_complete": True,
        "actual_open": opening,
        "actual_gap_return": (opening / prev - 1.0) if opening is not None and prev not in (None, 0) else None,
        "gap_direction": gap_direction,
        "first_1m_return": first1,
        "first_3m_return": first3,
        "first_5m_return": first5,
        "open_held": state["open_held"],
        "open_recovery": state["open_recovery"],
        "open_recovery_observation_limit": state["recovery_observation_limit"],
        "high": max(valid_highs) if valid_highs else None,
        "low": min(valid_lows) if valid_lows else None,
        "range": (max(valid_highs) - min(valid_lows)) if valid_highs and valid_lows else None,
        "volume": total_volume,
        "relative_volume": relative_volume,
        "turnover": total_turnover,
        "relative_turnover": relative_turnover,
        "approximate_vwap": calculated_vwap,
        "vwap_method": "minute_close_times_volume",
        "vwap_is_approximate": True,
        # Backward-compatible aliases. Consumers must inspect
        # vwap_is_approximate; the PWA uses the explicit fields above.
        "vwap": calculated_vwap,
        "current_price": current,
        "current_vs_approximate_vwap": (current / calculated_vwap - 1.0) if current is not None and calculated_vwap not in (None, 0) else None,
        "current_vs_vwap": (current / calculated_vwap - 1.0) if current is not None and calculated_vwap not in (None, 0) else None,
        "current_vs_open": (current / opening - 1.0) if current is not None and opening not in (None, 0) else None,
        "execution_imbalance": None,
        "volume_concentration_interval": None,
        "market_index_direction": None,
        "sector_index_direction": None,
        "market_breadth": None,
        "market_advance_decline_ratio": None,
        "observed_at": observed_at,
        "received_at": received_at,
        "data_delay_seconds": delay_seconds,
        "stale": stale,
        "sample_count": len(window),
        "data_quality": "stale" if stale else ("good" if observed_at else "unknown_time"),
        "source": "KIS KRX one-minute bars",
    }


def unavailable_prediction(
    stage: str,
    reason: str,
    *,
    sample_count: int = 0,
    minimum_required_sample_count: int = 252,
    observed_at: str | None = None,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "open_to_0930_up_probability": None,
        "open_to_close_up_probability": None,
        "gap_continuation_probability": None,
        "probability_available": False,
        "confidence": "low",
        "signal_strength": "data_insufficient",
        "sample_count": int(sample_count),
        "experimental": True,
        "calibration_status": "unavailable",
        "calibration_reason": reason,
        "minimum_required_sample_count": int(minimum_required_sample_count),
        "observed_at": observed_at,
        "data_quality": "unavailable",
        "feature_cutoff": "09:00" if stage == "premarket_prediction" else "09:05",
        "used_data_range": "08:00-09:00 (cutoff exclusive)" if stage == "premarket_prediction" else "08:00-09:05",
        "opening_five_minutes_applied": stage == "post_open_0905_prediction",
    }


class TwoStagePredictor(Protocol):
    def predict(self, features: dict[str, Any], *, stage: str) -> dict[str, Any]: ...


class UnavailablePredictor:
    """Truthful production default until stock-level training data exists."""

    def __init__(
        self,
        reason: str = "stock_level_model_not_trained",
        *,
        minimum_required_sample_count: int = 252,
    ) -> None:
        self.reason = reason
        self.minimum_required_sample_count = int(minimum_required_sample_count)

    def predict(self, features: dict[str, Any], *, stage: str) -> dict[str, Any]:
        sample_count = max(
            [int(item.get("baseline_sample_count") or 0) for item in _timed_feature_dicts(features)]
            or [0]
        )
        result = unavailable_prediction(
            stage,
            self.reason,
            sample_count=sample_count,
            minimum_required_sample_count=self.minimum_required_sample_count,
            observed_at=features.get("observed_at"),
        )
        result["input_features"] = features
        result["framework_status"] = "two_stage_data_feature_validation_framework_model_untrained"
        return result


def enforce_feature_cutoff(
    features: Iterable[dict[str, Any]],
    cutoff: datetime,
    *,
    inclusive: bool = True,
) -> list[dict[str, Any]]:
    """Validate point-in-time features and annotate unknown timestamps.

    The premarket model uses ``inclusive=False`` at 09:00. The 09:05 model
    uses ``inclusive=True``. Missing timestamps remain usable only as unknown
    quality data; they are never silently treated as point-in-time verified.
    """
    cutoff_seoul = _seoul(cutoff)
    validated: list[dict[str, Any]] = []
    for feature in features:
        checked = deepcopy(feature)
        text = feature.get("observed_at")
        if not text:
            checked["data_quality"] = "unknown_time"
            checked["cutoff_validation"] = "timestamp_unavailable"
            validated.append(checked)
            continue
        observed = _seoul(datetime.fromisoformat(str(text)))
        invalid = observed > cutoff_seoul or (not inclusive and observed == cutoff_seoul)
        if invalid:
            raise ValueError(f"future data leakage: {feature.get('feature_name', 'unknown')} observed after cutoff")
        checked["cutoff_validation"] = "passed"
        validated.append(checked)
    return validated


def _timed_feature_dicts(value: Any, prefix: str = "feature") -> list[dict[str, Any]]:
    """Flatten timed dictionaries without manufacturing timestamps."""
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "observed_at" in value or "data_quality" in value or "availability" in value:
            result.append({"feature_name": prefix, **value})
        for key, nested in value.items():
            if isinstance(nested, (dict, list, tuple)):
                result.extend(_timed_feature_dicts(nested, f"{prefix}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            result.extend(_timed_feature_dicts(nested, f"{prefix}[{index}]"))
    return result


def stage_cutoff(trading_date: str, stage: str) -> tuple[datetime, bool]:
    if stage == "premarket_prediction":
        return datetime.fromisoformat(f"{trading_date}T09:00:00+09:00"), False
    if stage == "post_open_0905_prediction":
        return datetime.fromisoformat(f"{trading_date}T09:05:00+09:00"), True
    raise ValueError("unknown two-stage prediction stage")


def validate_stage_feature_bundle(
    bundle: dict[str, Any], *, trading_date: str, stage: str
) -> list[dict[str, Any]]:
    cutoff, inclusive = stage_cutoff(trading_date, stage)
    return enforce_feature_cutoff(
        _timed_feature_dicts(bundle), cutoff, inclusive=inclusive
    )


def build_stage_feature_bundle(
    *,
    trading_date: str,
    stage: str,
    premarket_summary: dict[str, Any],
    opening_auction_summary: dict[str, Any],
    opening_five_minute_summary: dict[str, Any] | None = None,
    market_indicators: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the actual predictor input and exclude components past cutoff."""
    cutoff, inclusive = stage_cutoff(trading_date, stage)
    components: dict[str, dict[str, Any] | None] = {
        "premarket_summary": deepcopy(premarket_summary),
        "opening_auction_summary": deepcopy(opening_auction_summary),
    }
    if stage == "post_open_0905_prediction":
        components["opening_five_minute_summary"] = deepcopy(opening_five_minute_summary)
        components["market_indicators"] = deepcopy(market_indicators)

    excluded: list[dict[str, str]] = []
    for name, component in list(components.items()):
        if component is None:
            continue
        try:
            enforce_feature_cutoff(
                _timed_feature_dicts(component, name), cutoff, inclusive=inclusive
            )
        except (TypeError, ValueError):
            components[name] = None
            excluded.append({"feature_group": name, "reason": "observed_after_stage_cutoff"})
            continue
        if not component.get("observed_at") and component.get("availability") == "available":
            component["data_quality"] = "unknown_time"
            component["cutoff_validation"] = "timestamp_unavailable"
        else:
            component["cutoff_validation"] = "passed" if component.get("observed_at") else "not_observed"

    observed_times = [
        str(component.get("observed_at"))
        for component in components.values()
        if component and component.get("observed_at")
    ]
    bundle: dict[str, Any] = {
        "stage": stage,
        "feature_cutoff": cutoff.isoformat(),
        "cutoff_inclusive": inclusive,
        **components,
        "excluded_features": excluded,
        "observed_at": max(observed_times) if observed_times else None,
        "data_quality": "unknown_time" if any(
            component and component.get("data_quality") == "unknown_time"
            for component in components.values()
        ) else "available",
    }
    validate_stage_feature_bundle(bundle, trading_date=trading_date, stage=stage)
    return bundle


def _factor(
    *, feature_name: str, display_name: str, actual_value: Any, reference_value: Any,
    direction: str, observed_at: str | None, received_at: str | None, source: str,
    data_delay_seconds: int | None = None, data_quality: str = "good",
) -> dict[str, Any]:
    return {
        "feature_name": feature_name,
        "display_name": display_name,
        "actual_value": _finite(actual_value),
        "reference_value": _finite(reference_value),
        "contribution_direction": direction,
        "contribution_value": None,
        "contribution_unit": None,
        "observed_at": observed_at,
        "received_at": received_at,
        "data_delay_seconds": data_delay_seconds,
        "data_quality": data_quality,
        "source": source,
        "experimental": True,
        "note": "설명용 참고 신호이며 모델 기여도나 인과관계가 아닙니다.",
    }


def explanation_factors(
    *, premarket_summary: dict[str, Any] | None, opening_summary: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    factors: list[dict[str, Any]] = []
    pre = premarket_summary or {}
    opening = opening_summary or {}
    observed = pre.get("observed_at")
    received = pre.get("received_at")
    source = str(pre.get("source") or "KIS NXT")
    for name, label, value, reference in (
        ("nxt_return", "NXT 프리마켓 수익률", pre.get("nxt_return"), 0.0),
        ("relative_volume", "동일 시간대 상대거래량", (pre.get("relative_volume") or {}).get("relative_value"), 1.0),
        ("relative_turnover", "동일 시간대 상대거래대금", (pre.get("relative_turnover") or {}).get("relative_value"), 1.0),
        ("orderbook_imbalance", "호가잔량 불균형", pre.get("orderbook_imbalance"), 0.0),
    ):
        numeric = _finite(value)
        if numeric is None or numeric == reference:
            continue
        factors.append(_factor(
            feature_name=name, display_name=label, actual_value=numeric, reference_value=reference,
            direction="positive" if numeric > reference else "negative",
            observed_at=observed, received_at=received, source=source,
            data_delay_seconds=pre.get("data_delay_seconds"),
        ))
    open_observed = opening.get("observed_at")
    for name, label, value, reference in (
        ("first_5m_return", "첫 5분 수익률", opening.get("first_5m_return"), 0.0),
        ("current_vs_approximate_vwap", "근사 VWAP 대비 현재가", opening.get("current_vs_approximate_vwap"), 0.0),
        ("market_breadth", "시장 상승 종목 비율", opening.get("market_breadth"), 0.5),
    ):
        numeric = _finite(value)
        if numeric is None or numeric == reference:
            continue
        factors.append(_factor(
            feature_name=name, display_name=label, actual_value=numeric, reference_value=reference,
            direction="positive" if numeric > reference else "negative",
            observed_at=open_observed, received_at=opening.get("received_at"),
            source=str(opening.get("source") or "KIS KRX"),
            data_delay_seconds=opening.get("data_delay_seconds"),
        ))
    positive = sort_explanation_factors(
        [item for item in factors if item["contribution_direction"] == "positive"]
    )
    negative = sort_explanation_factors(
        [item for item in factors if item["contribution_direction"] == "negative"]
    )
    return positive, negative


def sort_explanation_factors(factors: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort real model contributions first; preserve source order when unavailable."""
    indexed = list(enumerate(factors))
    return [
        item for _, item in sorted(
            indexed,
            key=lambda pair: (
                _finite(pair[1].get("contribution_value")) is None,
                -abs(_finite(pair[1].get("contribution_value")) or 0.0),
                pair[0],
            ),
        )
    ]


def robust_clip(values: Iterable[Any], *, iqr_multiplier: float) -> list[float | None]:
    converted = [_finite(value) for value in values]
    finite = sorted(value for value in converted if value is not None)
    if len(finite) < 4:
        return converted
    def quantile(probability: float) -> float:
        position = (len(finite) - 1) * probability
        lower_index = int(math.floor(position))
        upper_index = int(math.ceil(position))
        if lower_index == upper_index:
            return finite[lower_index]
        weight = position - lower_index
        return finite[lower_index] * (1.0 - weight) + finite[upper_index] * weight

    q1 = quantile(0.25)
    q3 = quantile(0.75)
    spread = q3 - q1
    if spread <= 0:
        return converted
    lower = q1 - float(iqr_multiplier) * spread
    upper = q3 + float(iqr_multiplier) * spread
    return [None if value is None else min(upper, max(lower, value)) for value in converted]


def low_liquidity_status(
    *, volume: Any, turnover: Any, minimum_volume: float, minimum_turnover: float
) -> dict[str, Any]:
    parsed_volume = _finite(volume)
    parsed_turnover = _finite(turnover)
    if parsed_volume is None or parsed_turnover is None:
        return {"excluded": None, "reason": "liquidity_data_missing"}
    excluded = parsed_volume < float(minimum_volume) or parsed_turnover < float(minimum_turnover)
    return {"excluded": excluded, "reason": "below_configured_liquidity_floor" if excluded else None}
