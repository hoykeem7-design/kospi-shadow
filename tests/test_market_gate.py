from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kospi_shadow.market_gate import (
    assert_market_gate_invariants,
    build_kospi_market_gate,
    build_market_breadth,
    update_live_prediction_ledger,
)


SEOUL = ZoneInfo("Asia/Seoul")


def _prediction(probability: float | None = 0.64) -> dict:
    return {
        "candidate_target_date": "2026-08-05",
        "probability_intraday_up": probability,
        "probability_threshold": 0.57,
        "prediction_scope": "preopen_full_session",
    }


def _market(*, index_rate=0.006, futures_rate=0.004, advancers=600, decliners=300) -> dict:
    return {
        "kospi": {
            "change_rate": index_rate,
            "advancers": advancers,
            "decliners": decliners,
        },
        "kospi200_futures": {"change_rate": futures_rate},
        "factors": [],
    }


def _experiment(returns=(0.01, 0.005, -0.002)) -> dict:
    return {
        "symbols": [
            {
                "symbol": str(index),
                "premarket_summary": {"availability": "available", "nxt_return": value},
            }
            for index, value in enumerate(returns)
        ]
    }


def _gate(*, when: datetime, signal_enabled=True, probability=0.64, market=None, experiment=None):
    return build_kospi_market_gate(
        now=when,
        prediction=_prediction(probability),
        promotion={"signal_enabled": signal_enabled, "status": "VALIDATED_SHADOW", "checks": {"minimum_oos": True}},
        validation={"oos_n": 300, "brier_improvement": 0.01},
        market=market if market is not None else _market(),
        premarket_experiment=experiment if experiment is not None else _experiment(),
        config={"trade_ok_probability": 0.57, "selective_probability": 0.54, "risk_off_probability": 0.43},
    )


def test_trade_ok_requires_enabled_model_and_0905_confirmation():
    gate = _gate(when=datetime(2026, 8, 5, 9, 5, tzinfo=SEOUL))
    assert gate["status"] == "TRADE_OK"
    assert gate["stock_entries_allowed"] is True
    assert gate["integrity"]["trade_ok_when_signal_disabled"] is False
    assert_market_gate_invariants([gate])


def test_signal_disabled_can_never_emit_trade_ok():
    gate = _gate(
        when=datetime(2026, 8, 5, 9, 5, tzinfo=SEOUL),
        signal_enabled=False,
    )
    assert gate["status"] == "WAIT"
    assert gate["stock_entries_allowed"] is False
    assert gate["abstention"]["active"] is True


def test_0850_is_selective_even_when_confirmations_are_positive():
    gate = _gate(when=datetime(2026, 8, 5, 8, 50, tzinfo=SEOUL))
    assert gate["checkpoint"]["at"] == "08:50"
    assert gate["status"] == "SELECTIVE"


def test_negative_probability_and_live_market_emit_risk_off():
    gate = _gate(
        when=datetime(2026, 8, 5, 9, 5, tzinfo=SEOUL),
        probability=0.39,
        market=_market(index_rate=-0.01, futures_rate=-0.012, advancers=250, decliners=650),
    )
    assert gate["status"] == "RISK_OFF"
    assert gate["abstention"]["active"] is True


def test_missing_or_wrong_date_probability_is_unavailable():
    gate = build_kospi_market_gate(
        now=datetime(2026, 8, 5, 9, 5, tzinfo=SEOUL),
        prediction={"candidate_target_date": "2026-08-06", "probability_intraday_up": 0.65},
        promotion={"signal_enabled": True},
        validation={},
        market=_market(),
        premarket_experiment=_experiment(),
    )
    assert gate["status"] == "UNAVAILABLE"
    assert gate["session_close_up_probability"]["probability"] is None


def test_remaining_session_probability_is_not_fabricated():
    gate = _gate(when=datetime(2026, 8, 5, 12, 0, tzinfo=SEOUL))
    remaining = gate["current_to_close_up_probability"]
    assert remaining["availability"] == "unavailable"
    assert remaining["probability"] is None
    assert gate["integrity"]["remaining_session_probability_fabricated"] is False


def test_breadth_and_large_cap_concentration_proxy_are_labeled_truthfully():
    breadth = build_market_breadth({"change_rate": 0.01, "advancers": 300, "decliners": 600})
    assert breadth["advancer_ratio"] == pytest.approx(1 / 3)
    assert breadth["large_cap_concentration"]["risk"] is True
    assert breadth["large_cap_concentration"]["direct_constituent_weight_data"] is False


def test_live_prediction_ledger_persists_and_deduplicates(tmp_path: Path):
    gate = _gate(when=datetime(2026, 8, 5, 9, 5, tzinfo=SEOUL), signal_enabled=False)
    first = update_live_prediction_ledger(
        settings_raw={"premarket": {"history_dir": "history"}},
        project_root=tmp_path,
        gate=gate,
        persist=True,
    )
    second = update_live_prediction_ledger(
        settings_raw={"premarket": {"history_dir": "history"}},
        project_root=tmp_path,
        gate=gate,
        persist=True,
    )
    assert first["record_count"] == 1
    assert second["record_count"] == 1
    assert second["records"][0]["gate_status"] == "WAIT"
    assert (tmp_path / "history" / "kospi" / "live_prediction_ledger.jsonl").is_file()


def test_pwa_has_market_gate_model_lab_and_ledger_surfaces():
    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "index.html").read_text(encoding="utf-8")
    javascript = (root / "app" / "app.js").read_text(encoding="utf-8")
    source = (root / "src" / "kospi_shadow" / "market_gate.py").read_text(encoding="utf-8")
    assert "오늘 KOSPI 매매 판단" in html
    assert "KOSPI Model Lab" in html
    assert "라이브 예측 원장" in html
    assert "renderKospiMarketGate" in javascript
    assert all(status in source for status in ("TRADE_OK", "SELECTIVE", "WAIT", "RISK_OFF", "UNAVAILABLE"))
