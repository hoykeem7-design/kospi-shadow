from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kospi_shadow.decision_coach import (
    apply_previous_states,
    build_data_lab,
    build_decision_card,
    build_decision_coach,
    build_shadow_snapshots,
    candidate_transition,
    news_available_at,
    normalize_and_deduplicate_news,
    normalize_news_item,
    resolve_decision_phase,
    sanitize_symbol_for_phase,
)
from kospi_shadow.premarket_data import _build_aftermarket_summary


SEOUL = ZoneInfo("Asia/Seoul")


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (7, 30, "overnight_brief"),
        (8, 0, "nxt_premarket"),
        (8, 50, "opening_auction"),
        (9, 0, "opening_confirmation"),
        (9, 5, "entry_decision"),
        (9, 30, "intraday_management"),
        (15, 20, "intraday_management"),
        (15, 30, "closing_review"),
        (15, 40, "nxt_aftermarket"),
        (20, 0, "nxt_aftermarket"),
        (20, 5, "next_day_watch"),
    ],
)
def test_official_decision_phase_boundaries(hour, minute, expected):
    result = resolve_decision_phase(datetime(2026, 8, 4, hour, minute, tzinfo=SEOUL))
    assert result["phase"] == expected
    assert result["timezone"] == "Asia/Seoul"
    assert result["generated_at"] != result["scheduled_at"] or result["schedule_delay_seconds"] == 0


def test_utc_is_converted_to_asia_seoul_at_boundary():
    utc_value = datetime(2026, 8, 4, 0, 5, tzinfo=timezone.utc)
    assert resolve_decision_phase(utc_value)["phase"] == "entry_decision"


def test_news_exact_time_converts_to_kst():
    item = normalize_news_item(
        {"title": "Company update", "published_at": "2026-08-03T22:12:00-04:00", "source_timezone": "America/New_York"},
        now=datetime(2026, 8, 4, 12, 0, tzinfo=SEOUL),
    )
    assert item["published_at_kst"].startswith("2026-08-04T11:12:00")
    assert item["time_precision"] == "minute"
    assert item["session_bucket"] == "regular_session"


def test_date_only_news_never_gets_midnight():
    item = normalize_news_item(
        {"title": "공시", "published_at": "2026-08-03", "time_precision": "date_only"},
        now=datetime(2026, 8, 4, 8, 0, tzinfo=SEOUL),
    )
    assert item["published_at_kst"] == "2026-08-03"
    assert "00:00" not in item["date_label"]
    assert item["age_minutes"] is None
    assert item["freshness_label"] == "시간 미제공"


def test_unknown_article_time_stays_unknown():
    item = normalize_news_item(
        {"title": "시간 없는 기사", "published_at": None},
        now=datetime(2026, 8, 4, 8, 0, tzinfo=SEOUL),
    )
    assert item["published_at_kst"] is None
    assert item["time_precision"] == "unknown"


def test_news_freshness_and_new_checkpoint_flag():
    item = normalize_news_item(
        {"title": "새 기사", "published_at": "2026-08-04T07:50:00+09:00"},
        now=datetime(2026, 8, 4, 8, 10, tzinfo=SEOUL),
        last_checkpoint_at=datetime(2026, 8, 4, 7, 45, tzinfo=SEOUL),
    )
    assert item["freshness_label"] == "새 기사"
    assert item["is_new_since_last_checkpoint"] is True
    assert item["session_bucket"] == "before_premarket"


def test_duplicate_news_is_grouped_and_official_source_has_priority():
    items = normalize_and_deduplicate_news(
        [
            {"title": "A사 공급계약 체결", "published_at": "2026-08-04T07:10:00+09:00", "source_name": "언론", "source_type": "news"},
            {"title": "A사 공급계약 체결", "published_at": "2026-08-04", "source_name": "OpenDART", "source_type": "official_disclosure", "official_disclosure": True},
        ],
        now=datetime(2026, 8, 4, 8, 0, tzinfo=SEOUL),
    )
    assert len(items) == 1
    assert items[0]["official_disclosure"] is True
    assert items[0]["source_count"] == 2
    assert len(items[0]["related_articles"]) == 1


def test_news_is_sorted_by_actual_time():
    items = normalize_and_deduplicate_news(
        [
            {"title": "older", "published_at": "2026-08-04T06:00:00+09:00"},
            {"title": "newer", "published_at": "2026-08-04T07:00:00+09:00"},
        ],
        now=datetime(2026, 8, 4, 8, 0, tzinfo=SEOUL),
    )
    assert [item["title"] for item in items] == ["newer", "older"]


def test_future_news_and_same_day_date_only_are_not_used_at_cutoff():
    items = normalize_and_deduplicate_news(
        [
            {"title": "before", "published_at": "2026-08-04T08:59:00+09:00"},
            {"title": "after", "published_at": "2026-08-04T09:01:00+09:00"},
            {"title": "unknown today", "published_at": "2026-08-04"},
            {"title": "known prior date", "published_at": "2026-08-03"},
        ],
        now=datetime(2026, 8, 4, 10, 0, tzinfo=SEOUL),
    )
    result = news_available_at(items, datetime(2026, 8, 4, 9, 0, tzinfo=SEOUL))
    assert {item["title"] for item in result} == {"before", "known prior date"}


def _symbol(*, available=True, opening_complete=True, opening_observed="2026-08-04T09:05:00+09:00"):
    pre = {
        "availability": "available" if available else "unavailable",
        "unavailable_reason": None if available else "nxt_snapshot_not_received",
        "nxt_return": 0.01 if available else None,
        "nxt_final_price": 101.0 if available else None,
        "cumulative_turnover": 1_000_000.0 if available else None,
        "relative_volume": {"relative_value": 1.2, "baseline_available": True} if available else {},
        "relative_turnover": {"relative_value": 1.4, "baseline_available": True} if available else {},
        "bid_ask_spread": 0.001 if available else None,
        "orderbook_imbalance": 0.1 if available else None,
        "execution_imbalance": -0.05 if available else None,
        "last_5m_return": 0.003 if available else None,
        "observed_at": "2026-08-04T08:49:00+09:00" if available else None,
        "data_quality": "good" if available else "unavailable",
    }
    return {
        "symbol": "005930",
        "name": "삼성전자",
        "premarket_summary": pre,
        "opening_auction_summary": {
            "availability": "available",
            "direction_matches_nxt": True,
            "expected_volume_change": 0.2,
            "observed_at": "2026-08-04T08:59:00+09:00",
            "data_quality": "good",
        },
        "opening_five_minute_summary": {
            "availability": "available" if opening_complete else "unavailable",
            "data_complete": opening_complete,
            "actual_open": 100.0,
            "current_price": 102.0,
            "open_held": True,
            "open_recovery": False,
            "current_vs_approximate_vwap": 0.005,
            "observed_at": opening_observed,
            "data_quality": "good" if opening_complete else "unavailable",
        },
        "labels": {"open_to_0930_up": None, "open_to_close_up": None},
    }


@pytest.mark.parametrize(
    ("auction", "expected"),
    [
        ({"availability": "unavailable"}, "not_received"),
        ({"availability": "available", "data_quality": "stale"}, "excluded"),
        ({"availability": "available", "direction_matches_nxt": False}, "weakened"),
        ({"availability": "available", "direction_matches_nxt": True, "expected_volume_change": 0.1}, "strengthened"),
        ({"availability": "available", "direction_matches_nxt": True}, "maintained"),
    ],
)
def test_opening_auction_candidate_transitions(auction, expected):
    assert candidate_transition({}, auction)["code"] == expected


def test_untrained_model_never_creates_entry_candidate():
    phase = resolve_decision_phase(datetime(2026, 8, 4, 9, 5, tzinfo=SEOUL))
    phase["next_checkpoint_at"] = "09:30"
    card = build_decision_card(_symbol(), rank=1, phase=phase, signal_enabled=True, model_trained=False, news=[])
    assert card["action_state"] == "WAIT"
    assert card["probability"] is None
    assert card["probability_available"] is False
    assert card["entry_conditions_met"] is False


def test_missing_required_market_data_is_data_insufficient():
    phase = resolve_decision_phase(datetime(2026, 8, 4, 8, 10, tzinfo=SEOUL))
    phase["next_checkpoint_at"] = "08:50"
    card = build_decision_card(_symbol(available=False, opening_complete=False), rank=1, phase=phase, signal_enabled=False, model_trained=False, news=[])
    assert card["action_state"] == "DATA_INSUFFICIENT"


def test_decision_card_contains_entry_chase_invalidation_and_exit_conditions():
    phase = resolve_decision_phase(datetime(2026, 8, 4, 9, 5, tzinfo=SEOUL))
    phase["next_checkpoint_at"] = "09:30"
    card = build_decision_card(_symbol(), rank=1, phase=phase, signal_enabled=False, model_trained=False, news=[])
    assert card["entry_trigger_conditions"]
    assert card["do_not_chase_conditions"]
    assert card["invalidation_conditions"]
    assert card["reduce_conditions"]
    assert card["exit_conditions"]
    assert card["model_confidence"] == "low"


def test_first_five_minute_features_removed_before_0905():
    sanitized = sanitize_symbol_for_phase(_symbol(), datetime(2026, 8, 4, 9, 4, 59, tzinfo=SEOUL))
    assert sanitized["opening_five_minute_summary"]["data_complete"] is False
    assert sanitized["opening_five_minute_summary"]["unavailable_reason"] == "opening_confirmation_in_progress"


def test_post_0905_future_observation_is_removed():
    sanitized = sanitize_symbol_for_phase(
        _symbol(opening_observed="2026-08-04T09:06:00+09:00"),
        datetime(2026, 8, 4, 9, 10, tzinfo=SEOUL),
    )
    assert sanitized["opening_five_minute_summary"]["data_complete"] is False
    assert sanitized["opening_five_minute_summary"]["unavailable_reason"] == "opening_feature_after_0905_cutoff"


def test_shadow_trade_not_created_when_entry_conditions_not_enabled():
    phase = resolve_decision_phase(datetime(2026, 8, 4, 9, 5, tzinfo=SEOUL))
    snapshots = build_shadow_snapshots(
        [{
            "symbol": "005930", "action_state": "WAIT", "entry_conditions_met": False,
            "result_prices": {
                "actual_open": 100.0, "session_high": 103.0, "session_low": 98.0,
                "price_at_0930": 101.0, "close_price": 102.0, "aftermarket_final_price": 102.5,
            },
        }],
        phase,
        {"decision_coach": {"paper_fee_bps_per_side": 5, "paper_slippage_bps_per_side": 7}},
    )
    assert snapshots[0]["hypothetical_trade_created"] is False
    assert snapshots[0]["hypothetical_entry_price"] is None
    assert snapshots[0]["fee_bps_per_side"] == 5
    assert snapshots[0]["slippage_bps_per_side"] == 7
    assert snapshots[0]["price_at_0930"] == 101.0
    assert snapshots[0]["close_price"] == 102.0
    assert snapshots[0]["max_favorable_excursion"] == pytest.approx(0.03)
    assert snapshots[0]["max_adverse_excursion"] == pytest.approx(-0.02)


def test_intraday_state_update_reads_previous_shadow_snapshot(tmp_path: Path):
    path = tmp_path / "history" / "decisions" / "005930.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"action_state":"WATCH","generated_at":"2026-08-04T08:50:00+09:00"}\n', encoding="utf-8")
    card = {"symbol": "005930", "action_state": "WAIT", "state_update": {"previous_state": None, "change_reason": None}}
    apply_previous_states({"premarket": {"history_dir": "history"}}, tmp_path, [card])
    assert card["state_update"]["previous_state"] == "WATCH"
    assert "WAIT" in card["state_update"]["change_reason"]


def test_data_lab_counts_real_history_and_separates_stage_samples(tmp_path: Path):
    raw = tmp_path / "history" / "raw" / "005930.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_text(
        '{"collected_at":"2026-08-03T08:50:00+09:00","premarket_summary":{},"auction_snapshot":{}}\n'
        '{"collected_at":"2026-08-04T09:05:00+09:00","opening_five_minute_summary":{},"labels":{"open_to_0930_up":true,"open_to_close_up":false}}\n',
        encoding="utf-8",
    )
    lab = build_data_lab(
        {"premarket": {"history_dir": "history", "minimum_model_samples": 252}},
        tmp_path,
        [{"symbol": "005930", "name": "삼성전자"}],
    )
    row = lab["symbols"][0]
    assert row["collected_trading_days"] == 2
    assert row["premarket_sample_count"] == 1
    assert row["opening_auction_sample_count"] == 1
    assert row["opening_five_minute_sample_count"] == 1
    assert row["label_0930_count"] == 1
    assert row["close_label_count"] == 1
    assert lab["models"]["premarket_prediction"]["brier_score"] is None


def test_full_v5_response_has_integrity_gate_and_unavailable_aftermarket(tmp_path: Path):
    experiment = {
        "generated_at": "2026-08-04T09:05:00+09:00",
        "symbols": [_symbol()],
        "production_truth": {"stock_model_trained": False},
    }
    result = build_decision_coach(
        settings_raw={"premarket": {"history_dir": "history"}, "decision_coach": {}},
        project_root=tmp_path,
        now=datetime(2026, 8, 4, 9, 5, tzinfo=SEOUL),
        premarket_experiment=experiment,
        news=[],
        events=[],
        market={"kospi": None, "kospi200_futures": None, "factors": []},
        index_signal_enabled=True,
        persist_history=False,
    )
    assert result["feature_name"] == "time_based_decision_coach_v5"
    assert result["signal_gate"]["stock_signal_enabled"] is False
    assert result["signal_gate"]["probability"] is None
    assert result["entry_candidates"] == []
    assert result["nxt_aftermarket"]["availability"] == "unavailable"
    assert result["next_day_watchlist"] == []
    assert result["integrity"]["future_news_cutoff_applied"] is True


def test_aftermarket_gap_uses_real_krx_close_and_missing_baseline_stays_null():
    result = _build_aftermarket_summary(
        {
            "availability": "available",
            "current_price": 102.0,
            "high": 103.0,
            "low": 99.0,
            "cumulative_volume": 1000.0,
            "cumulative_turnover": 101_000.0,
            "bid_ask_spread": 0.002,
            "observed_at": "2026-08-04T16:00:00+09:00",
            "received_at": "2026-08-04T16:00:01+09:00",
            "data_delay_seconds": 1,
            "stale": False,
            "data_quality": "good",
            "source": "KIS NXT REST",
        },
        [],
        now=datetime(2026, 8, 4, 16, 0, tzinfo=SEOUL),
        krx_close=100.0,
        baseline_period=20,
        minimum_baseline_samples=20,
        same_time_tolerance_minutes=5,
    )
    assert result["krx_close_return"] == pytest.approx(0.02)
    assert result["relative_volume"]["relative_value"] is None
    assert result["relative_turnover"]["relative_value"] is None
    assert result["liquidity_status"] == "observed_without_validated_threshold"


def test_actual_aftermarket_data_can_create_next_day_watch_but_not_entry(tmp_path: Path):
    symbol = _symbol()
    symbol["aftermarket_summary"] = {
        "availability": "available",
        "krx_close": 100.0,
        "current_price": 101.0,
        "krx_close_return": 0.01,
        "cumulative_turnover": 1_000_000.0,
        "data_quality": "good",
    }
    result = build_decision_coach(
        settings_raw={"premarket": {"history_dir": "history"}, "decision_coach": {}},
        project_root=tmp_path,
        now=datetime(2026, 8, 4, 20, 5, tzinfo=SEOUL),
        premarket_experiment={
            "generated_at": "2026-08-04T20:05:00+09:00",
            "symbols": [symbol],
            "production_truth": {"stock_model_trained": False},
        },
        news=[], events=[], market={"factors": []}, index_signal_enabled=False,
        persist_history=False,
    )
    assert result["nxt_aftermarket"]["availability"] == "available"
    assert result["next_day_watchlist"][0]["status"] == "프리마켓 재확인 필요"
    assert result["entry_candidates"] == []


def test_pwa_contains_no_direct_recommendation_and_has_data_lab():
    root = Path(__file__).resolve().parents[1]
    app_js = (root / "app" / "app.js").read_text(encoding="utf-8")
    index = (root / "app" / "index.html").read_text(encoding="utf-8")
    assert "매수 추천" not in app_js
    assert "매수 추천" not in index
    assert 'id="data-lab"' in index
    assert "screenRefreshButton" in app_js


def test_production_source_has_no_random_or_mock_market_generation():
    root = Path(__file__).resolve().parents[1] / "src" / "kospi_shadow"
    production = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "random.random" not in production
    assert "mock_market" not in production
