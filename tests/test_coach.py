from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from kospi_shadow.coach import build_coaching, resolve_session_context

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
