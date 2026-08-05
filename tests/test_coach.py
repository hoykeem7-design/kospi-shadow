from __future__ import annotations

from datetime import datetime
import json
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kospi_shadow.coach import build_coaching, resolve_session_context
from kospi_shadow.config import Settings

SEOUL = ZoneInfo("Asia/Seoul")


def test_session_context_matches_key_market_windows():
    assert resolve_session_context(datetime(2026, 8, 4, 7, 50, tzinfo=SEOUL)).code == "PREOPEN_BRIEF"
    assert resolve_session_context(datetime(2026, 8, 4, 8, 5, tzinfo=SEOUL)).code == "NXT_PRE"
    assert resolve_session_context(datetime(2026, 8, 4, 8, 50, tzinfo=SEOUL)).code == "FUTURES_PREOPEN"
    assert resolve_session_context(datetime(2026, 8, 4, 9, 5, tzinfo=SEOUL)).code == "KRX_OPEN_DISCOVERY"
    assert resolve_session_context(datetime(2026, 8, 4, 15, 25, tzinfo=SEOUL)).code == "CLOSE_WINDOW"
    assert resolve_session_context(datetime(2026, 8, 4, 19, 0, tzinfo=SEOUL)).code == "NXT_AFTER_NIGHT_FUTURES"
    assert resolve_session_context(datetime(2026, 8, 4, 21, 0, tzinfo=SEOUL)).code == "POST_MARKET"


def test_closed_promotion_gate_always_coaches_wait():
    session = resolve_session_context(datetime(2026, 8, 4, 8, 12, tzinfo=SEOUL))
    result = build_coaching(
        prediction={"probability_intraday_up": 0.68, "research_direction": "LONG"},
        promotion={"signal_enabled": False},
        session=session,
        index={"change_rate": 0.01},
        futures={"change_rate": 0.012},
    )
    assert result["action"] == "WAIT"
    assert result["headline"] == "관망 우선"


def test_open_discovery_requires_confirmation_when_live_market_disagrees():
    session = resolve_session_context(datetime(2026, 8, 4, 9, 3, tzinfo=SEOUL))
    result = build_coaching(
        prediction={"probability_intraday_up": 0.66, "research_direction": "LONG"},
        promotion={"signal_enabled": True},
        session=session,
        index={"change_rate": -0.008},
        futures={"change_rate": -0.012},
    )
    assert result["action"] == "WAIT_CONFIRMATION"


def test_missing_probability_is_not_replaced_with_neutral_fallback():
    session = resolve_session_context(datetime(2026, 8, 4, 9, 10, tzinfo=SEOUL))
    result = build_coaching(
        prediction={"probability_intraday_up": None, "probability_available": False},
        promotion={"signal_enabled": True},
        session=session,
        index={"change_rate": 0.01},
        futures={"change_rate": 0.01},
    )
    assert result["action"] == "WAIT"
    assert result["headline"] == "확률 산출 불가"
    assert result["alignment"]["base_direction"] == "unavailable"
    assert result["alignment"]["aligned"] is None


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
    def json(self):
        return self._payload


def test_kis_index_snapshot_uses_index_api(monkeypatch):
    from kospi_shadow import coach
    seen = {}
    def fake_get(url, *, params=None, headers=None, timeout=30, retries=4):
        seen.update(url=url, params=params, headers=headers)
        return _FakeResponse({"rt_cd":"0","output":{
            "bstp_nmix_prpr":"3,250.20","bstp_nmix_prdy_ctrt":"0.45",
            "bstp_nmix_oprc":"3240","bstp_nmix_hgpr":"3260","bstp_nmix_lwpr":"3230",
            "acml_vol":"123456","ascn_issu_cnt":"500","down_issu_cnt":"350"
        }})
    monkeypatch.setenv("KIS_APP_KEY","key")
    monkeypatch.setenv("KIS_APP_SECRET","secret")
    monkeypatch.setattr(coach,"_retry_get",fake_get)
    result=coach.fetch_kis_index_snapshot(timeout=3,retries=1,token="token")
    assert seen["params"] == {"FID_COND_MRKT_DIV_CODE":"U","FID_INPUT_ISCD":"0001"}
    assert seen["headers"]["tr_id"] == "FHPUP02100000"
    assert result["price"] == 3250.2
    assert result["change_rate"] == pytest.approx(0.0045)


def test_kis_futures_snapshot_uses_board_api(monkeypatch):
    from kospi_shadow import coach
    seen = {}
    def fake_get(url, *, params=None, headers=None, timeout=30, retries=4):
        seen.update(url=url, params=params, headers=headers)
        return _FakeResponse({"rt_cd":"0","output1":[{
            "hts_kor_isnm":"KOSPI200 F 2609","futs_shrn_iscd":"101W6000",
            "futs_prpr":"430.25","futs_prdy_ctrt":"-0.35","basis":"0.42",
            "futs_askp":"430.30","futs_bidp":"430.20","acml_vol":"98765"
        }]})
    monkeypatch.setenv("KIS_APP_KEY","key")
    monkeypatch.setenv("KIS_APP_SECRET","secret")
    monkeypatch.setattr(coach,"_retry_get",fake_get)
    result=coach.fetch_kis_futures_snapshot(timeout=3,retries=1,token="token")
    assert seen["headers"]["tr_id"] == "FHPIF05030200"
    assert seen["params"]["FID_COND_MRKT_DIV_CODE"] == "F"
    assert result["price"] == 430.25
    assert result["change_rate"] == pytest.approx(-0.0035)


def test_kis_market_attention_normalizes_real_rank_fields(monkeypatch):
    from kospi_shadow import coach

    calls = []
    def fake_get(url, *, params=None, headers=None, timeout=30, retries=4):
        calls.append(dict(params or {}))
        return _FakeResponse({"rt_cd": "0", "output": [{
            "hts_kor_isnm": "삼성전자",
            "mksc_shrn_iscd": "005930",
            "data_rank": "1",
            "stck_prpr": "81200",
            "prdy_ctrt": "2.50",
            "acml_vol": "1234567",
            "acml_tr_pbmn": "100000000000",
            "vol_inrt": "180.0",
            "tr_pbmn_tnrt": "1.2",
        }]})

    monkeypatch.setenv("KIS_APP_KEY", "key")
    monkeypatch.setenv("KIS_APP_SECRET", "secret")
    monkeypatch.setattr(coach, "_retry_get", fake_get)
    result = coach.fetch_kis_market_attention(
        now_seoul=datetime(2026, 8, 4, 11, 0, tzinfo=SEOUL),
        timeout=1,
        retries=1,
        token="token",
    )
    assert len(calls) == 2
    assert {row["FID_BLNG_CLS_CODE"] for row in calls} == {"1", "3"}
    assert all(row["FID_INPUT_ISCD"] == "0001" for row in calls)
    leader = result["leaders"][0]
    assert leader["name"] == "삼성전자"
    assert leader["current_return"] == pytest.approx(0.025)
    assert leader["volume_growth_rate"] == pytest.approx(1.8)
    assert leader["previous_close"] == pytest.approx(81200 / 1.025)
    assert set(leader["ranking_sources"]) == {"turnover", "volume_growth"}
    assert result["direct_query_rank_available"] is False
    assert result["trading_signal"] is False


def test_weather_snapshot_is_labeled_model_forecast(monkeypatch):
    from kospi_shadow import coach

    monkeypatch.setattr(coach, "_retry_get", lambda *args, **kwargs: _FakeResponse({
        "current": {"time": "2026-08-04T11:00", "temperature_2m": 33.2, "apparent_temperature": 36.1},
        "daily": {"temperature_2m_max": [35.0], "apparent_temperature_max": [38.0], "weather_code": [1]},
    }))
    result = coach.fetch_weather_snapshot(timeout=1, retries=1)
    assert result["availability"] == "available"
    assert result["maximum_temperature_c"] == 35.0
    assert result["official_warning_available"] is False
    assert result["data_quality"] == "forecast_model"
    assert result["trading_signal"] is False


def test_nxt_pre_never_recommends_entry_without_realtime_nxt_feed():
    session = resolve_session_context(datetime(2026, 8, 4, 8, 20, tzinfo=SEOUL))
    result = build_coaching(
        prediction={"probability_intraday_up": 0.68, "research_direction": "LONG"},
        promotion={"signal_enabled": True},
        session=session,
        index={"change_rate": 0.01},
        futures=None,
    )
    assert result["action"] == "WAIT_CONFIRMATION"


def test_opendart_is_optional_when_key_is_not_configured(monkeypatch):
    from kospi_shadow import coach
    monkeypatch.delenv("DART_API_KEY", raising=False)
    rows, status = coach.fetch_opendart_disclosures(
        symbols=["005930"], start="2026-08-03", end="2026-08-04", timeout=3, retries=1
    )
    assert rows == []
    assert status["unavailable_reason"] == "DART_API_KEY_NOT_CONFIGURED"


def test_opendart_keeps_receipt_date_without_fake_time(monkeypatch):
    from kospi_shadow import coach

    def fake_get(url, *, params=None, headers=None, timeout=30, retries=4):
        return _FakeResponse({
            "status": "000",
            "list": [
                {"stock_code": "005930", "report_nm": "주요사항보고", "rcept_no": "202608040001", "rcept_dt": "20260804"},
                {"stock_code": "000660", "report_nm": "다른 종목", "rcept_no": "202608040002", "rcept_dt": "20260804"},
            ],
        })

    real_getenv = coach.os.getenv
    monkeypatch.setattr(coach.os, "getenv", lambda name, default="": "configured" if name == "DART_API_KEY" else real_getenv(name, default))
    monkeypatch.setattr(coach, "_retry_get", fake_get)
    rows, status = coach.fetch_opendart_disclosures(
        symbols=["005930"], start="2026-08-03", end="2026-08-04", timeout=3, retries=1
    )
    assert status["availability"] == "available"
    assert len(rows) == 1
    assert rows[0]["published_at"] == "2026-08-04"
    assert rows[0]["time_precision"] == "date_only"
    assert "00:00" not in rows[0]["published_at"]


def test_generated_dashboard_keeps_old_fields_and_adds_v5_schema(monkeypatch, tmp_path):
    from kospi_shadow import coach

    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / "app", tmp_path / "app")
    (tmp_path / "outputs").mkdir()
    (tmp_path / "outputs" / "metrics.json").write_text(json.dumps({
        "latest_prediction": {
            "candidate_target_date": "2026-08-05",
            "probability_intraday_up": None,
            "probability_available": False,
            "research_direction": "FLAT",
        },
        "promotion": {"signal_enabled": False},
        "data_manifest": {
            "target_provider": "KRX",
            "target_official": True,
            "target_latest_source": "KRX",
            "target_date_max": "2026-08-04",
            "collection_warnings": [],
        },
        "classification": {},
        "strategy_proxy": {},
    }), encoding="utf-8")
    settings = Settings(raw={
        "project": {"timezone": "Asia/Seoul"},
        "data": {"cache_dir": "data/cache", "request_timeout_seconds": 1, "request_retries": 1},
        "model": {}, "promotion": {}, "coach": {},
        "premarket": {"symbols": [], "history_dir": "history"},
        "decision_coach": {},
    }, config_path=tmp_path / "config.yml")
    monkeypatch.setattr(coach, "fetch_kis_access_token", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("unavailable")))
    monkeypatch.setattr(coach, "fetch_market_news", lambda **kwargs: [])
    monkeypatch.setattr(coach, "fetch_fred_release_calendar", lambda **kwargs: [])
    monkeypatch.setattr(coach, "fetch_weather_snapshot", lambda **kwargs: {
        "availability": "unavailable", "trading_signal": False,
    })
    monkeypatch.delenv("DART_API_KEY", raising=False)
    dashboard = coach.generate_coach_app(
        settings, tmp_path, now_seoul=datetime(2026, 8, 4, 9, 5, tzinfo=SEOUL)
    )
    assert dashboard["schema_version"] == 7
    assert dashboard["app_version"] == "5.3.1"
    assert "prediction" in dashboard
    assert "premarket_experiment" in dashboard
    assert dashboard["decision_coach_v5"]["phase"]["phase"] == "entry_decision"
    assert dashboard["decision_coach_v5"]["signal_gate"]["probability"] is None
    assert dashboard["decision_coach_v5"]["kospi_market_gate"]["status"] == "UNAVAILABLE"
    assert dashboard["decision_coach_v5"]["theme_supply_radar"]["entry_signal_enabled"] is False
    assert (tmp_path / "site" / "data" / "dashboard.json").is_file()
