from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from kospi_shadow.theme_radar import build_theme_supply_radar, update_theme_radar_ledger


SEOUL = ZoneInfo("Asia/Seoul")


def _symbol(symbol: str, name: str, *, nxt_return: float, relative_turnover: float) -> dict:
    return {
        "symbol": symbol,
        "name": name,
        "premarket_summary": {
            "availability": "available",
            "previous_close": 100.0,
            "nxt_return": nxt_return,
            "cumulative_turnover": 1_000_000_000 * relative_turnover,
            "relative_turnover": {"relative_value": relative_turnover, "baseline_available": True},
            "relative_volume": {"relative_value": relative_turnover - 0.1, "baseline_available": True},
            "observed_at": "2026-08-05T08:49:00+09:00",
            "data_quality": "good",
        },
        "opening_auction_summary": {
            "availability": "available",
            "direction_matches_nxt": True,
            "observed_at": "2026-08-05T08:50:00+09:00",
        },
        "opening_five_minute_summary": {
            "availability": "unavailable",
            "data_complete": False,
        },
    }


def _gate(status: str = "WAIT", allowed: bool = False) -> dict:
    return {"status": status, "stock_entries_allowed": allowed}


def _phase() -> dict:
    return {"phase": "opening_auction", "next_checkpoint_at": "09:05"}


def test_theme_radar_combines_point_in_time_news_supply_and_us_alignment():
    radar = build_theme_supply_radar(
        now=datetime(2026, 8, 5, 8, 50, tzinfo=SEOUL),
        phase=_phase(),
        symbols=[
            _symbol("005930", "삼성전자", nxt_return=0.03, relative_turnover=2.2),
            _symbol("000660", "SK하이닉스", nxt_return=0.02, relative_turnover=1.6),
        ],
        news=[{
            "title": "엔비디아 HBM 수요 확대",
            "related_symbols": ["005930", "000660"],
            "published_at_kst": "2026-08-05T08:10:00+09:00",
            "freshness_label": "새 기사",
            "is_new_since_last_checkpoint": True,
            "source_name": "테스트뉴스",
        }],
        market={
            "factors": [
                {"key": "nasdaq", "name": "NASDAQ", "change_rate": 0.01, "date": "2026-08-04"},
                {"key": "sox", "name": "SOX", "change_rate": 0.02, "date": "2026-08-04"},
            ],
            "kospi": {"price": 3100, "change_rate": 0.003},
        },
        market_gate=_gate(),
    )

    assert radar["availability"] == "available"
    assert radar["entry_signal_enabled"] is False
    assert radar["probability"] is None
    assert radar["depends_on_kospi_gate"] is True
    assert radar["source_availability"]["direct_query_rank"] == "unavailable"
    assert radar["source_availability"]["weather_observation_or_forecast"] == "unavailable"
    assert radar["integrity"]["can_override_kospi_gate"] is False
    assert radar["checkpoint"] == "08:50"

    theme = radar["themes"][0]
    assert theme["key"] == "semiconductor_ai"
    assert theme["supply"]["state"] == "BROAD_POSITIVE"
    assert theme["supply"]["member_count"] == 2
    assert theme["global_alignment"]["state"] == "POSITIVE"
    assert theme["members"][0]["symbol"] == "005930"
    assert theme["members"][0]["role"] == "LEADER_OBSERVATION"
    assert theme["attention"]["direct_query_rank_available"] is False
    assert radar["candidate_annotations"]["005930"]["primary_theme"] == "반도체·AI"


def test_single_fast_mover_is_chase_review_not_theme_confirmation():
    radar = build_theme_supply_radar(
        now=datetime(2026, 8, 5, 8, 20, tzinfo=SEOUL),
        phase={"phase": "nxt_premarket", "next_checkpoint_at": "08:50"},
        symbols=[_symbol("005930", "삼성전자", nxt_return=0.09, relative_turnover=3.0)],
        news=[{
            "title": "반도체 관련주 강세",
            "related_symbols": ["005930"],
            "published_at_kst": "2026-08-05T08:05:00+09:00",
            "freshness_label": "새 기사",
        }],
        market={"factors": []},
        market_gate=_gate("SELECTIVE", True),
    )

    theme = radar["themes"][0]
    assert theme["supply"]["state"] == "SINGLE_NAME"
    assert theme["action"] == "CHASE_REVIEW"
    assert theme["chase_risk"]["active"] is True
    assert theme["entry_signal_enabled"] is False
    assert radar["abstention"]["active"] is True


def test_weather_news_is_labeled_proxy_and_never_fabricated_as_forecast():
    radar = build_theme_supply_radar(
        now=datetime(2026, 8, 5, 7, 30, tzinfo=SEOUL),
        phase={"phase": "overnight_brief", "next_checkpoint_at": "08:00"},
        symbols=[],
        news=[{
            "title": "전국 폭염에 냉방 수요 증가",
            "related_symbols": [],
            "published_at_kst": "2026-08-05T07:00:00+09:00",
            "freshness_label": "새 기사",
        }],
        market={"factors": []},
        market_gate=_gate(),
    )

    theme = next(row for row in radar["themes"] if row["key"] == "cooling_weather")
    assert theme["weather_event"]["availability"] == "news_proxy"
    assert theme["weather_event"]["temperature_c"] is None
    assert theme["weather_event"]["trading_signal"] is False
    assert radar["integrity"]["weather_signal_fabricated"] is False


def test_empty_inputs_fail_closed():
    radar = build_theme_supply_radar(
        now=datetime(2026, 8, 5, 9, 5, tzinfo=SEOUL),
        phase={"phase": "entry_decision", "next_checkpoint_at": "09:30"},
        symbols=[],
        news=[],
        market={},
        market_gate=_gate("UNAVAILABLE", False),
    )
    assert radar["availability"] == "unavailable"
    assert radar["themes"] == []
    assert radar["entry_signal_enabled"] is False
    assert radar["universe"]["market_wide_scanner_available"] is False


def test_ai_keyword_requires_a_whole_token():
    radar = build_theme_supply_radar(
        now=datetime(2026, 8, 5, 7, 30, tzinfo=SEOUL),
        phase={"phase": "overnight_brief", "next_checkpoint_at": "08:00"},
        symbols=[],
        news=[{"title": "Chairman said ordinary business continues", "related_symbols": []}],
        market={"factors": []},
        market_gate=_gate(),
    )
    assert not any(theme["key"] == "semiconductor_ai" for theme in radar["themes"])


def test_publisher_suffix_does_not_create_false_shipbuilding_theme():
    radar = build_theme_supply_radar(
        now=datetime(2026, 8, 5, 9, 5, tzinfo=SEOUL),
        phase={"phase": "entry_decision", "next_checkpoint_at": "09:30"},
        symbols=[],
        news=[{
            "title": "코스피 상승 출발 - 조선일보",
            "source_name": "조선일보",
            "theme_tags": ["조선"],
            "related_symbols": [],
        }],
        market={"factors": []},
        market_gate=_gate(),
    )
    assert not any(theme["key"] == "shipbuilding" for theme in radar["themes"])


def test_market_rank_slice_fills_mapped_theme_supply_without_query_rank():
    radar = build_theme_supply_radar(
        now=datetime(2026, 8, 5, 11, 0, tzinfo=SEOUL),
        phase={"phase": "intraday_management", "next_checkpoint_at": "12:00"},
        symbols=[],
        news=[],
        market={
            "factors": [],
            "stock_attention": {
                "availability": "available",
                "market": "KRX",
                "note": "실제 거래 순위",
                "leaders": [
                    {"symbol": "005930", "name": "삼성전자", "current_return": 0.03, "previous_close": 100,
                     "cumulative_turnover": 5_000_000_000, "ranks": {"turnover": 1},
                     "ranking_sources": ["turnover"], "observed_at": "2026-08-05T11:00:00+09:00", "data_quality": "good"},
                    {"symbol": "000660", "name": "SK하이닉스", "current_return": 0.02, "previous_close": 100,
                     "cumulative_turnover": 4_000_000_000, "ranks": {"turnover": 2},
                     "ranking_sources": ["turnover"], "observed_at": "2026-08-05T11:00:00+09:00", "data_quality": "good"},
                ],
            },
        },
        market_gate=_gate(),
        config={"themes": [{"key": "semiconductor_ai", "symbols": ["005930", "000660"]}]},
    )
    theme = next(row for row in radar["themes"] if row["key"] == "semiconductor_ai")
    assert radar["availability"] == "available"
    assert radar["source_availability"]["market_turnover_ranking"] == "available"
    assert radar["market_attention"]["direct_query_rank_available"] is False
    assert theme["supply"]["state"] == "BROAD_POSITIVE"
    assert theme["attention"]["state"] == "MARKET_FLOW"
    assert theme["previous_close_context"]["availability"] == "available"
    assert theme["members"][0]["market_attention_ranks"]["turnover"] == 1


def test_hot_weather_forecast_creates_context_not_a_trade_signal():
    radar = build_theme_supply_radar(
        now=datetime(2026, 8, 5, 7, 30, tzinfo=SEOUL),
        phase={"phase": "overnight_brief", "next_checkpoint_at": "08:00"},
        symbols=[],
        news=[],
        market={"factors": [], "weather": {
            "availability": "available", "location": "서울", "source": "forecast",
            "temperature_c": 31.0, "maximum_temperature_c": 35.0,
            "maximum_apparent_temperature_c": 38.0, "alerts": [],
        }},
        market_gate=_gate(),
        config={"weather_heat_threshold_c": 30.0},
    )
    theme = next(row for row in radar["themes"] if row["key"] == "cooling_weather")
    assert theme["weather_event"]["maximum_temperature_c"] == 35.0
    assert theme["weather_event"]["trading_signal"] is False
    assert theme["entry_signal_enabled"] is False


def test_shadow_ledger_persists_observations_without_trades(tmp_path: Path):
    radar = build_theme_supply_radar(
        now=datetime(2026, 8, 5, 8, 50, tzinfo=SEOUL),
        phase=_phase(),
        symbols=[_symbol("005930", "삼성전자", nxt_return=0.02, relative_turnover=1.5)],
        news=[{"title": "반도체 수주", "related_symbols": ["005930"]}],
        market={"factors": []},
        market_gate=_gate(),
    )
    ledger = update_theme_radar_ledger(
        project_root=tmp_path,
        radar=radar,
        persist=True,
        maximum_records=10,
    )
    assert ledger["record_count"] == 1
    assert ledger["actual_orders_enabled"] is False
    assert ledger["outcome_labels_complete"] is False
    assert ledger["records"][0]["entry_signal_enabled"] is False
    assert (tmp_path / "app_state" / "theme_supply_radar.jsonl").is_file()
