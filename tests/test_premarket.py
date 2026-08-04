from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kospi_shadow.config import Settings, load_settings
from kospi_shadow.premarket import (
    UnavailablePredictor,
    build_auction_summary,
    build_opening_five_minute_summary,
    build_stage_feature_bundle,
    compute_labels,
    data_timing,
    enforce_feature_cutoff,
    expected_price_stability,
    explanation_factors,
    low_liquidity_status,
    relative_metric,
    resolve_market_phase,
    robust_clip,
    sort_explanation_factors,
    vwap,
)
from kospi_shadow.premarket_backtest import BacktestRecord, evaluate_stage_backtest
from kospi_shadow.premarket_data import (
    _last_premarket_summary,
    _same_time_baseline,
    build_premarket_experiment,
    configured_symbols,
    normalize_snapshot,
    upsert_training_history,
)
from kospi_shadow.premarket_cli import run as run_premarket_cli, smoke_target_and_count


SEOUL = ZoneInfo("Asia/Seoul")


def _bar(minute: str, price: float, volume: float, *, open_price: float = 100.0) -> dict:
    return {
        "minute": minute,
        "price": price,
        "open": open_price,
        "high": price + 1,
        "low": price - 1,
        "volume": volume,
        "turnover": None,
        "observed_at": f"2026-08-04T{minute}:00+09:00",
        "received_at": f"2026-08-04T{minute}:05+09:00",
    }


def _settings(tmp_path: Path, symbols=None) -> Settings:
    raw = {
        "project": {"timezone": "Asia/Seoul"},
        "data": {"cache_dir": "data/cache", "request_timeout_seconds": 1, "request_retries": 1},
        "model": {},
        "promotion": {},
        "premarket": {"symbols": symbols or []},
    }
    return Settings(raw=raw, config_path=tmp_path / "config.yml")


def test_relative_volume_and_turnover_use_same_median_formula():
    volume = relative_metric(300, [100, 200, 10_000], minimum_sample_count=3)
    turnover = relative_metric(6000, [1000, 2000, 100_000], minimum_sample_count=3)
    assert volume["baseline_median"] == 200
    assert volume["relative_value"] == 1.5
    assert turnover["baseline_median"] == 2000
    assert turnover["relative_value"] == 3.0


def test_relative_metric_requires_samples_and_positive_denominator():
    too_short = relative_metric(100, [50], minimum_sample_count=2)
    zero = relative_metric(100, [0, 0], minimum_sample_count=2)
    missing = relative_metric(None, [10, 20], minimum_sample_count=2)
    assert too_short["relative_value"] is None
    assert too_short["unavailable_reason"] == "insufficient_same_time_history"
    assert zero["relative_value"] is None
    assert zero["unavailable_reason"] == "baseline_median_not_positive"
    assert missing["current_value"] is None
    assert missing["relative_value"] is None


def test_same_time_history_uses_one_observation_per_prior_date():
    history = [
        {"collected_at": "2026-08-01T08:30:00+09:00", "phase": "premarket", "premarket_summary": {"cumulative_volume": 10}},
        {"collected_at": "2026-08-01T08:31:00+09:00", "phase": "premarket", "premarket_summary": {"cumulative_volume": 20}},
        {"collected_at": "2026-08-02T08:30:00+09:00", "phase": "premarket", "premarket_summary": {"cumulative_volume": 30}},
        {"collected_at": "2026-08-04T08:30:00+09:00", "phase": "premarket", "premarket_summary": {"cumulative_volume": 999}},
    ]
    values = _same_time_baseline(
        history,
        now=datetime(2026, 8, 4, 8, 30, tzinfo=SEOUL),
        field="cumulative_volume",
        tolerance_minutes=2,
    )
    # 08:31 is a future observation relative to 08:30 and must be excluded.
    assert values == [30.0, 10.0]


@pytest.mark.parametrize("current_minute", [47, 50])
def test_same_time_baseline_never_uses_a_future_minute(current_minute):
    history = [
        {"collected_at": "2026-08-01T08:45:00+09:00", "phase": "premarket", "premarket_summary": {"cumulative_volume": 45}},
        {"collected_at": "2026-08-01T08:50:00+09:00", "phase": "opening_auction", "premarket_summary": {"cumulative_volume": 50}},
        {"collected_at": "2026-08-01T08:55:00+09:00", "phase": "opening_auction", "premarket_summary": {"cumulative_volume": 55}},
    ]
    result = _same_time_baseline(
        history,
        now=datetime(2026, 8, 4, 8, current_minute, tzinfo=SEOUL),
        field="cumulative_volume",
        tolerance_minutes=10,
    )
    assert result == ([45.0] if current_minute == 47 else [50.0])


def test_same_time_baseline_selects_closest_past_observation_per_date():
    history = [
        {"collected_at": "2026-08-01T08:43:00+09:00", "phase": "premarket", "premarket_summary": {"cumulative_volume": 43}},
        {"collected_at": "2026-08-01T08:46:00+09:00", "phase": "premarket", "premarket_summary": {"cumulative_volume": 46}},
        {"collected_at": "2026-08-01T08:47:00+09:00", "phase": "premarket", "premarket_summary": {"cumulative_volume": 47}},
    ]
    assert _same_time_baseline(
        history,
        now=datetime(2026, 8, 4, 8, 47, tzinfo=SEOUL),
        field="cumulative_volume",
        tolerance_minutes=10,
    ) == [47.0]


def test_same_time_baseline_is_missing_when_only_future_observations_exist():
    history = [{
        "collected_at": "2026-08-01T08:50:00+09:00",
        "phase": "opening_auction",
        "premarket_summary": {"cumulative_volume": 50},
    }]
    assert _same_time_baseline(
        history,
        now=datetime(2026, 8, 4, 8, 47, tzinfo=SEOUL),
        field="cumulative_volume",
        tolerance_minutes=10,
    ) == []


def test_post_open_only_reuses_same_trading_date_premarket_snapshot():
    history = [
        {"collected_at": "2026-08-03T08:45:00+09:00", "phase": "premarket", "premarket_summary": {"nxt_final_price": 100}},
        {"collected_at": "2026-08-04T08:45:00+09:00", "phase": "premarket", "premarket_summary": {"nxt_final_price": 101}},
    ]
    assert _last_premarket_summary(history, trading_date="2026-08-04")["nxt_final_price"] == 101
    assert _last_premarket_summary(history, trading_date="2026-08-05") is None


@pytest.mark.parametrize(
    ("hour", "minute", "phase"),
    [(8, 49, "premarket"), (8, 50, "opening_auction"), (9, 0, "opening_confirmation"), (9, 5, "post_open_updated")],
)
def test_korea_market_phase_boundaries(hour, minute, phase):
    assert resolve_market_phase(datetime(2026, 8, 4, hour, minute, tzinfo=SEOUL))["phase"] == phase


def test_utc_is_converted_to_seoul_before_phase_resolution():
    assert resolve_market_phase(datetime(2026, 8, 4, 0, 0, tzinfo=timezone.utc))["phase"] == "opening_confirmation"


def test_stale_quality_uses_observed_and_received_times():
    result = data_timing(
        observed_at=datetime(2026, 8, 4, 8, 0, tzinfo=SEOUL),
        received_at=datetime(2026, 8, 4, 8, 4, tzinfo=SEOUL),
        stale_after_seconds=180,
        source="provider",
        available=True,
    )
    assert result["data_delay_seconds"] == 240
    assert result["stale"] is True
    assert result["data_quality"] == "stale"


def test_expected_price_stability_needs_a_series_and_uses_volatility():
    assert expected_price_stability([100])["value"] is None
    stable = expected_price_stability([100, 100, 100])
    volatile = expected_price_stability([90, 100, 110])
    assert stable["value"] == 1.0
    assert volatile["value"] < stable["value"]


def test_auction_summary_missing_data_stays_missing_not_zero():
    result = build_auction_summary([], previous_close=100, nxt_final_price=101)
    assert result["availability"] == "unavailable"
    assert result["expected_price"] is None
    assert result["expected_volume"] is None
    assert result["expected_price_stability"]["value"] is None


def test_auction_summary_calculates_price_volume_and_nxt_difference():
    snapshots = [
        {"expected_price": 101, "expected_volume": 100, "observed_at": "a"},
        {"expected_price": 102, "expected_volume": 150, "observed_at": "b"},
    ]
    result = build_auction_summary(snapshots, previous_close=100, nxt_final_price=101)
    assert result["expected_price_return"] == pytest.approx(0.02)
    assert result["expected_turnover"] == 15_300
    assert result["expected_volume_change"] == pytest.approx(0.5)
    assert result["expected_price_vs_nxt_final"] == pytest.approx(102 / 101 - 1)


def test_vwap_and_zero_volume_handling():
    assert vwap([100, 110], [1, 3]) == pytest.approx(107.5)
    assert vwap([100, 110], [0, 0]) is None
    assert vwap([100, None], [1, 3]) == 100


def test_first_five_minutes_features_and_vwap_position():
    bars = [_bar(f"09:0{i}", 100 + i, 10 + i) for i in range(5)]
    result = build_opening_five_minute_summary(
        bars,
        previous_close=99,
        baseline_volumes=[50] * 20,
        baseline_turnovers=[5000] * 20,
        minimum_baseline_samples=20,
    )
    assert result["data_complete"] is True
    assert result["first_1m_return"] == 0
    assert result["first_3m_return"] == pytest.approx(0.02)
    assert result["first_5m_return"] == pytest.approx(0.04)
    assert result["approximate_vwap"] is not None
    assert result["current_vs_approximate_vwap"] > 0
    assert result["vwap_is_approximate"] is True
    assert result["relative_volume"]["baseline_available"] is True


def test_first_five_minutes_excludes_0905_and_later_future_rows():
    bars = [_bar(f"09:0{i}", 100 + i, 10) for i in range(5)] + [_bar("09:05", 999, 999)]
    result = build_opening_five_minute_summary(
        bars,
        previous_close=99,
        baseline_volumes=[],
        baseline_turnovers=[],
        minimum_baseline_samples=20,
    )
    assert result["current_price"] == 104
    assert result["volume"] == 50


def test_first_five_minutes_incomplete_data_never_becomes_complete():
    result = build_opening_five_minute_summary(
        [_bar("09:00", 100, 10), _bar("09:04", 101, 10)],
        previous_close=99,
        baseline_volumes=[],
        baseline_turnovers=[],
        minimum_baseline_samples=20,
    )
    assert result["data_complete"] is False
    assert result["availability"] == "unavailable"


@pytest.mark.parametrize(
    ("prices", "expected_held", "expected_recovery"),
    [([101, 102, 103, 104, 105], True, False), ([101, 99, 101, 102, 103], False, True)],
)
def test_open_hold_and_recovery(prices, expected_held, expected_recovery):
    bars = [_bar(f"09:0{i}", price, 10, open_price=100) for i, price in enumerate(prices)]
    result = build_opening_five_minute_summary(
        bars,
        previous_close=99,
        baseline_volumes=[],
        baseline_turnovers=[],
        minimum_baseline_samples=20,
    )
    assert result["open_held"] is expected_held
    assert result["open_recovery"] is expected_recovery


def test_open_hold_uses_intrabar_low_and_documents_recovery_limit():
    bars = [_bar(f"09:0{i}", 101, 10, open_price=100) for i in range(5)]
    bars[1]["low"] = 99
    result = build_opening_five_minute_summary(
        bars,
        previous_close=99,
        baseline_volumes=[],
        baseline_turnovers=[],
        minimum_baseline_samples=20,
    )
    assert result["open_held"] is False
    assert result["open_recovery"] is True
    assert "same_minute" in result["open_recovery_observation_limit"]


def test_gap_labels_cover_up_down_flat_and_missing():
    up = compute_labels(previous_close=100, open_price=102, price_0930=104, close_price=103, minimum_gap_price_unit=1)
    down = compute_labels(previous_close=100, open_price=98, price_0930=97, close_price=99, minimum_gap_price_unit=1)
    flat = compute_labels(previous_close=100, open_price=100.5, price_0930=101, close_price=102, minimum_gap_price_unit=1)
    missing = compute_labels(previous_close=100, open_price=102, price_0930=None, close_price=None, minimum_gap_price_unit=1)
    assert up["gap_continuation_0930"] is True
    assert down["gap_continuation_0930"] is True
    assert flat["gap_direction"] == "flat"
    assert flat["gap_continuation_0930"] is None
    assert missing["open_to_0930_up"] is None
    assert missing["open_to_close_up"] is None


def test_feature_cutoffs_reject_future_leakage_for_each_stage():
    with pytest.raises(ValueError, match="future data leakage"):
        enforce_feature_cutoff(
            [{"feature_name": "first_5m_return", "observed_at": "2026-08-04T09:04:00+09:00"}],
            datetime(2026, 8, 4, 9, 0, tzinfo=SEOUL),
        )
    enforce_feature_cutoff(
        [{"feature_name": "first_5m_return", "observed_at": "2026-08-04T09:04:59+09:00"}],
        datetime(2026, 8, 4, 9, 5, tzinfo=SEOUL),
    )


def test_missing_feature_timestamp_is_marked_unknown_quality():
    validated = enforce_feature_cutoff(
        [{"feature_name": "market_breadth", "value": 0.6}],
        datetime(2026, 8, 4, 9, 5, tzinfo=SEOUL),
    )
    assert validated[0]["data_quality"] == "unknown_time"
    assert validated[0]["cutoff_validation"] == "timestamp_unavailable"


def test_stage_bundles_keep_prior_stage_inputs_and_enforce_cutoffs():
    pre = {"availability": "available", "observed_at": "2026-08-04T08:47:00+09:00", "data_quality": "good"}
    auction = {"availability": "available", "observed_at": "2026-08-04T08:55:00+09:00", "data_quality": "good"}
    opening = {"availability": "available", "observed_at": "2026-08-04T09:04:59+09:00", "data_quality": "good"}
    market = {"availability": "available", "observed_at": "2026-08-04T09:05:00+09:00", "data_quality": "good"}
    pre_bundle = build_stage_feature_bundle(
        trading_date="2026-08-04",
        stage="premarket_prediction",
        premarket_summary=pre,
        opening_auction_summary=auction,
    )
    post_bundle = build_stage_feature_bundle(
        trading_date="2026-08-04",
        stage="post_open_0905_prediction",
        premarket_summary=pre,
        opening_auction_summary=auction,
        opening_five_minute_summary=opening,
        market_indicators=market,
    )
    assert set(pre_bundle) >= {"premarket_summary", "opening_auction_summary"}
    assert set(post_bundle) >= {
        "premarket_summary", "opening_auction_summary",
        "opening_five_minute_summary", "market_indicators",
    }


def test_premarket_bundle_excludes_observation_at_0900_or_later():
    bundle = build_stage_feature_bundle(
        trading_date="2026-08-04",
        stage="premarket_prediction",
        premarket_summary={"availability": "available", "observed_at": "2026-08-04T08:47:00+09:00"},
        opening_auction_summary={"availability": "available", "observed_at": "2026-08-04T09:00:00+09:00"},
    )
    assert bundle["opening_auction_summary"] is None
    assert bundle["excluded_features"] == [{
        "feature_group": "opening_auction_summary",
        "reason": "observed_after_stage_cutoff",
    }]


def test_explanations_are_split_without_fabricated_contribution_values():
    positive, negative = explanation_factors(
        premarket_summary={
            "nxt_return": 0.02,
            "relative_volume": {"relative_value": 2.0},
            "relative_turnover": {"relative_value": 0.5},
            "orderbook_imbalance": -0.2,
            "observed_at": "2026-08-04T08:45:00+09:00",
            "source": "provider",
        },
        opening_summary={"first_5m_return": -0.01, "current_vs_vwap": 0.01},
    )
    assert all(item["contribution_direction"] == "positive" for item in positive)
    assert all(item["contribution_direction"] == "negative" for item in negative)
    assert all(item["contribution_value"] is None and item["experimental"] for item in positive + negative)


def test_explanation_sorting_uses_absolute_model_contribution_when_available():
    factors = [
        {"feature_name": "a", "contribution_value": None},
        {"feature_name": "b", "contribution_value": -0.1},
        {"feature_name": "c", "contribution_value": 0.3},
    ]
    assert [item["feature_name"] for item in sort_explanation_factors(factors)] == ["c", "b", "a"]


def test_untrained_predictor_returns_null_probabilities_and_low_confidence():
    result = UnavailablePredictor().predict({"baseline_sample_count": 0}, stage="premarket_prediction")
    assert result["open_to_0930_up_probability"] is None
    assert result["open_to_close_up_probability"] is None
    assert result["gap_continuation_probability"] is None
    assert result["probability_available"] is False
    assert result["confidence"] == "low"
    assert result["sample_count"] == 0
    assert result["experimental"] is True


def test_outlier_clipping_and_low_liquidity_are_configurable():
    clipped = robust_clip([1, 2, 3, 4, 1000], iqr_multiplier=1.5)
    assert clipped[-1] < 1000
    assert low_liquidity_status(volume=5, turnover=100, minimum_volume=10, minimum_turnover=50)["excluded"] is True
    assert low_liquidity_status(volume=None, turnover=100, minimum_volume=10, minimum_turnover=50)["excluded"] is None


def test_no_symbols_produces_backward_compatible_optional_api_section(tmp_path, monkeypatch):
    monkeypatch.delenv("PREMARKET_SYMBOLS", raising=False)
    result = build_premarket_experiment(
        _settings(tmp_path),
        tmp_path,
        now_seoul=datetime(2026, 8, 4, 8, 30, tzinfo=SEOUL),
        market_snapshot=None,
    )
    assert result["configured"] is False
    assert result["symbols"] == []
    assert result["data_availability"]["unavailable_reason"] == "no_symbols_configured"
    assert result["production_truth"]["stock_model_trained"] is False


def test_update_prediction_is_not_created_before_0905(tmp_path, monkeypatch):
    from kospi_shadow import premarket_data

    class ProviderFixture:
        def __init__(self, **_kwargs):
            pass

        def current_price(self, _symbol, _market):
            return {
                "stck_bsop_date": "20260804",
                "stck_prpr": "104",
                "stck_prdy_clpr": "99",
                "stck_oprc": "100",
                "stck_hgpr": "105",
                "stck_lwpr": "99",
                "acml_vol": "1000",
            }

        def orderbook(self, _symbol, _market):
            return ({"aspr_acpt_hour": "090400", "askp1": "105", "bidp1": "104"}, {})

        def minute_bars(self, _symbol, _market, _hour):
            return [
                {
                    "stck_bsop_date": "20260804",
                    "stck_cntg_hour": f"090{i}00",
                    "stck_prpr": str(100 + i),
                    "stck_oprc": "100",
                    "stck_hgpr": str(101 + i),
                    "stck_lwpr": str(99 + i),
                    "cntg_vol": "10",
                }
                for i in range(5)
            ]

    same_day_history = [{
        "collected_at": "2026-08-04T08:45:00+09:00",
        "phase": "premarket",
        "premarket_summary": {
            "availability": "available",
            "previous_close": 99,
            "nxt_final_price": 101,
            "data_quality": "good",
        },
    }]
    monkeypatch.setattr(premarket_data, "KisStockProvider", ProviderFixture)
    monkeypatch.setattr(premarket_data, "load_history", lambda _path: same_day_history)
    monkeypatch.setattr(premarket_data, "append_history", lambda *_args, **_kwargs: None)
    settings = _settings(tmp_path, [{"symbol": "005930", "name": "Samsung"}])

    before = build_premarket_experiment(
        settings,
        tmp_path,
        now_seoul=datetime(2026, 8, 4, 9, 4, tzinfo=SEOUL),
        market_snapshot=None,
    )["symbols"][0]
    after = build_premarket_experiment(
        settings,
        tmp_path,
        now_seoul=datetime(2026, 8, 4, 9, 5, tzinfo=SEOUL),
        market_snapshot=None,
    )["symbols"][0]

    assert before["opening_five_minute_summary"]["data_complete"] is False
    assert before["post_open_0905_prediction"] is None
    assert before["premarket_prediction"]["feature_cutoff"] == "09:00"
    assert after["opening_five_minute_summary"]["data_complete"] is True
    assert after["post_open_0905_prediction"]["probability_available"] is False
    assert after["post_open_0905_prediction"]["opening_five_minutes_applied"] is True
    assert set(before["premarket_prediction"]["input_features"]) >= {
        "premarket_summary", "opening_auction_summary",
    }
    assert set(after["post_open_0905_prediction"]["input_features"]) >= {
        "premarket_summary", "opening_auction_summary",
        "opening_five_minute_summary", "market_indicators",
    }


def test_configured_symbols_accepts_only_explicit_valid_symbols(tmp_path, monkeypatch):
    monkeypatch.setenv("PREMARKET_SYMBOLS", "005930, invalid!, 000660,005930")
    assert configured_symbols(_settings(tmp_path)) == [
        {"symbol": "005930", "name": "005930"},
        {"symbol": "000660", "name": "000660"},
    ]


def test_snapshot_normalization_preserves_missing_and_provider_timestamps():
    result = normalize_snapshot(
        symbol="005930",
        market="NX",
        price_row={"stck_prpr": "101", "stck_prdy_clpr": "100", "acml_vol": "123", "stck_bsop_date": "20260804"},
        book_row={"aspr_acpt_hour": "083000", "askp1": "102", "bidp1": "100", "askp_rsqn1": "5", "bidp_rsqn1": "15"},
        expected_row={},
        received_at=datetime(2026, 8, 4, 8, 30, 5, tzinfo=SEOUL),
        stale_after_seconds=10,
    )
    assert result["current_price"] == 101
    assert result["cumulative_turnover"] is None
    assert result["bid_ask_spread"] == pytest.approx(2 / 101)
    assert result["orderbook_imbalance"] == 0.5
    assert result["data_delay_seconds"] == 5


def test_default_config_has_no_fabricated_watchlist_or_signal_thresholds(monkeypatch):
    monkeypatch.delenv("PREMARKET_SYMBOLS", raising=False)
    settings = load_settings(Path(__file__).parents[1] / "config" / "default.yml")
    assert settings.raw["premarket"]["symbols"] == []
    assert settings.raw["premarket"]["signal_strength_thresholds"] is None


def test_production_premarket_modules_do_not_use_random_or_mock_market_data():
    root = Path(__file__).parents[1] / "src" / "kospi_shadow"
    source = "\n".join((root / name).read_text(encoding="utf-8") for name in ("premarket.py", "premarket_data.py"))
    assert "random." not in source
    assert "mock_" not in source


def test_backtest_interface_separates_stages_and_fails_closed_on_low_sample():
    records = [BacktestRecord("005930", "2026-08-01", "premarket_prediction", 0.6, True, 0.01)]
    result = evaluate_stage_backtest(
        records,
        stage="post_open_0905_prediction",
        minimum_sample_count=1,
        transaction_cost_bps_per_side=5,
        slippage_bps_per_side=5,
    )
    assert result["status"] == "unavailable"
    assert result["sample_count"] == 0


def test_backtest_interface_reports_calibration_and_cost_metrics_when_labeled():
    records = [
        BacktestRecord("005930", "2026-08-01", "premarket_prediction", 0.8, True, 0.02),
        BacktestRecord("000660", "2026-08-01", "premarket_prediction", 0.2, False, -0.01),
    ]
    result = evaluate_stage_backtest(
        records,
        stage="premarket_prediction",
        minimum_sample_count=2,
        transaction_cost_bps_per_side=5,
        slippage_bps_per_side=5,
    )
    assert result["status"] == "available"
    assert result["brier_score"] == pytest.approx(0.04)
    assert result["roc_auc"] == 1.0
    assert result["calibration"]["expected_calibration_error"] is not None
    assert result["cost_adjusted_expected_return"] is not None


def test_backtest_dataset_rejects_future_feature_observations():
    records = [BacktestRecord(
        "005930", "2026-08-01", "premarket_prediction", 0.6, True,
        feature_bundle={"premarket_summary": {
            "availability": "available",
            "observed_at": "2026-08-01T09:00:00+09:00",
        }},
    )]
    with pytest.raises(ValueError, match="future data leakage"):
        evaluate_stage_backtest(
            records,
            stage="premarket_prediction",
            minimum_sample_count=1,
            transaction_cost_bps_per_side=5,
            slippage_bps_per_side=5,
        )


def test_normalized_training_history_upserts_one_record_per_date_and_stage(tmp_path):
    path = tmp_path / "history" / "training" / "005930.jsonl"
    upsert_training_history(path, {"record_key": "2026-08-04:premarket_prediction", "value": 1}, maximum_records=5000)
    upsert_training_history(path, {"record_key": "2026-08-04:premarket_prediction", "value": 2}, maximum_records=5000)
    upsert_training_history(path, {"record_key": "2026-08-04:post_open_0905_prediction", "value": 3}, maximum_records=5000)
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 2
    assert any('"value": 2' in row for row in rows)


def test_smoke_mode_fails_when_no_symbols_are_configured(tmp_path, monkeypatch):
    config = tmp_path / "config.yml"
    config.write_text(
        "project:\n  timezone: Asia/Seoul\ndata:\n  cache_dir: data/cache\n"
        "model: {}\npromotion: {}\npremarket:\n  symbols: []\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PREMARKET_SYMBOLS", raising=False)
    assert run_premarket_cli([
        "--config", "config.yml", "--project-root", str(tmp_path), "--smoke",
    ]) == 2
    status = (tmp_path / "outputs" / "premarket_collection_status.json").read_text(encoding="utf-8")
    assert '"configured_symbol_count": 0' in status


@pytest.mark.parametrize(
    ("when", "target", "expected"),
    [
        (datetime(2026, 8, 4, 8, 20, tzinfo=SEOUL), "nxt_premarket", 1),
        (datetime(2026, 8, 4, 9, 10, tzinfo=SEOUL), "krx_post_open", 1),
        (datetime(2026, 8, 4, 17, 0, tzinfo=SEOUL), "nxt_aftermarket", 1),
    ],
)
def test_smoke_count_is_specific_to_the_current_live_session(when, target, expected):
    result = {"symbols": [{
        "premarket_summary": {"availability": "available"},
        "opening_five_minute_summary": {"data_complete": True},
        "aftermarket_summary": {"availability": "available"},
    }]}
    assert smoke_target_and_count(result, when) == (target, expected)


def test_aftermarket_smoke_does_not_pass_on_krx_opening_data_alone():
    result = {"symbols": [{
        "premarket_summary": {"availability": "available"},
        "opening_five_minute_summary": {"data_complete": True},
        "aftermarket_summary": {"availability": "unavailable"},
    }]}
    assert smoke_target_and_count(
        result, datetime(2026, 8, 4, 17, 0, tzinfo=SEOUL)
    ) == ("nxt_aftermarket", 0)


def test_workflows_serialize_history_and_pwa_advertises_actual_deploy_times():
    root = Path(__file__).parents[1]
    collector = (root / ".github/workflows/premarket-collector.yml").read_text(encoding="utf-8")
    coach = (root / ".github/workflows/coach-app.yml").read_text(encoding="utf-8")
    app = (root / "app/app.js").read_text(encoding="utf-8")
    assert "group: kospi-shadow-live-data" in collector
    assert "group: kospi-shadow-live-data" in coach
    assert "premarket-history" in collector and "premarket-history" in coach
    assert "actions/cache" not in collector
    update_line = next(line for line in app.splitlines() if line.startswith("const AUTO_UPDATE_TIMES"))
    assert '"09:10"' in update_line
    for collector_only in ('"08:50"', '"08:55"', '"09:00"', '"09:05"'):
        assert collector_only not in update_line


def test_no_neutral_probability_fallback_remains_in_coach_or_app():
    root = Path(__file__).parents[1]
    coach = (root / "src/kospi_shadow/coach.py").read_text(encoding="utf-8")
    app = (root / "app/app.js").read_text(encoding="utf-8")
    assert 'probability_intraday_up", 0.5' not in coach
    assert "probability_intraday_up ?? .5" not in app
