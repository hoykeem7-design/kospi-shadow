from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from kospi_shadow import data as data_module


class FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def test_parse_krx_kospi_schema():
    rows = [
        {
            "BAS_DD": "20260730",
            "IDX_NM": "코스피",
            "OPNPRC_IDX": "3,100.00",
            "HGPRC_IDX": "3,140.00",
            "LWPRC_IDX": "3,080.00",
            "CLSPRC_IDX": "3,125.00",
            "ACC_TRDVOL": "500,000,000",
        },
        {
            "BAS_DD": "20260730",
            "IDX_NM": "코스피 200",
            "OPNPRC_IDX": "400",
            "HGPRC_IDX": "405",
            "LWPRC_IDX": "398",
            "CLSPRC_IDX": "402",
            "ACC_TRDVOL": "1",
        },
    ]
    frame = data_module._parse_krx_rows(rows, ["코스피", "KOSPI"])
    assert len(frame) == 1
    assert frame.loc[0, "Close"] == 3125.0
    assert frame.loc[0, "Low"] == 3080.0


def test_fetch_krx_uses_header_and_cache(monkeypatch, tmp_path: Path):
    calls = []

    def fake_get(url, *, params=None, headers=None, timeout=30, retries=4):
        calls.append({"url": url, "params": params, "headers": headers})
        return FakeResponse({
            "OutBlock_1": [{
                "BAS_DD": params["basDd"],
                "IDX_NM": "코스피",
                "OPNPRC_IDX": "3000",
                "HGPRC_IDX": "3020",
                "LWPRC_IDX": "2990",
                "CLSPRC_IDX": "3010",
                "ACC_TRDVOL": "1000",
            }]
        })

    monkeypatch.delenv("KRX_API_KEY", raising=False)
    monkeypatch.setenv("KRX_AUTH_KEY", "secret-test-key")
    monkeypatch.setattr(data_module, "_retry_get", fake_get)
    cache = tmp_path / "krx.csv"
    first = data_module.fetch_krx_kospi(
        start="2026-07-30", end="2026-07-30", cache_path=cache,
        names=["코스피"], timeout=3, retries=1, pause_seconds=0,
    )
    assert len(first) == 1
    assert calls[0]["headers"] == {"AUTH_KEY": "secret-test-key"}
    assert calls[0]["params"] == {"basDd": "20260730"}
    calls.clear()
    second = data_module.fetch_krx_kospi(
        start="2026-07-30", end="2026-07-30", cache_path=cache,
        names=["코스피"], timeout=3, retries=1, pause_seconds=0,
    )
    assert len(second) == 1
    assert calls == []


def test_fetch_fred_parses_missing_values(monkeypatch):
    payload = {
        "observations": [
            {"date": "2026-07-28", "value": "4.12"},
            {"date": "2026-07-29", "value": "."},
            {"date": "2026-07-30", "value": "4.10"},
        ]
    }

    def fake_get(url, *, params=None, headers=None, timeout=30, retries=4):
        assert params["series_id"] == "DGS10"
        assert params["api_key"] == "fred-test-key"
        return FakeResponse(payload)

    monkeypatch.setenv("FRED_API_KEY", "fred-test-key")
    monkeypatch.setattr(data_module, "_retry_get", fake_get)
    frame = data_module.fetch_fred_series("DGS10", "2026-07-01", timeout=3, retries=1)
    assert frame["value"].tolist() == [4.12, 4.10]
