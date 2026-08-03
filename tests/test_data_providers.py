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
        recheck_recent_business_days=0,
    )
    assert len(first) == 1
    assert calls[0]["headers"] == {"AUTH_KEY": "secret-test-key"}
    assert calls[0]["params"] == {"basDd": "20260730"}
    calls.clear()
    second = data_module.fetch_krx_kospi(
        start="2026-07-30", end="2026-07-30", cache_path=cache,
        names=["코스피"], timeout=3, retries=1, pause_seconds=0,
        recheck_recent_business_days=0,
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


def test_yahoo_batch_handles_date_index_and_date_column(monkeypatch, tmp_path: Path):
    import sys
    import types
    import numpy as np

    dates = pd.DatetimeIndex(["2026-07-30", "2026-07-31"], name="Date")
    columns = pd.MultiIndex.from_product([["^GSPC", "KRW=X"], ["Close", "Volume"]])
    raw = pd.DataFrame(
        [
            [6300.0, 100.0, 1380.0, 200.0],
            [6320.0, 110.0, 1375.0, 210.0],
        ],
        index=dates,
        columns=columns,
    )

    fake_yf = types.SimpleNamespace(download=lambda *args, **kwargs: raw)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()

    factors, warnings = data_module.fetch_yahoo_factors_batch(
        {"sp500": "^GSPC", "usdk_rw": "KRW=X"},
        start="2026-07-01",
        end="2026-08-01",
        cache_dir=cache_dir,
    )

    assert warnings == []
    assert set(factors) == {"sp500", "usdk_rw"}
    assert factors["sp500"]["Date"].tolist() == list(dates)
    assert np.allclose(factors["usdk_rw"]["Close"], [1380.0, 1375.0])


def test_fetch_krx_rechecks_recent_checked_date(monkeypatch, tmp_path: Path):
    calls = []

    def fake_get(url, *, params=None, headers=None, timeout=30, retries=4):
        calls.append(params["basDd"])
        return FakeResponse({
            "OutBlock_1": [{
                "BAS_DD": params["basDd"],
                "IDX_NM": "코스피",
                "OPNPRC_IDX": "3200",
                "HGPRC_IDX": "3220",
                "LWPRC_IDX": "3190",
                "CLSPRC_IDX": "3210",
                "ACC_TRDVOL": "1000",
            }]
        })

    monkeypatch.setenv("KRX_AUTH_KEY", "secret-test-key")
    monkeypatch.setattr(data_module, "_retry_get", fake_get)
    cache = tmp_path / "krx.csv"
    pd.DataFrame({
        "Date": ["2026-07-31"],
        "Open": [3100], "High": [3120], "Low": [3090], "Close": [3110], "Volume": [1000],
    }).to_csv(cache, index=False)
    cache.with_suffix(".checked_dates.txt").write_text("20260731\n20260803\n", encoding="utf-8")

    result = data_module.fetch_krx_kospi(
        start="2026-07-31",
        end="2026-08-03",
        cache_path=cache,
        names=["코스피"],
        timeout=3,
        retries=1,
        pause_seconds=0,
        recheck_recent_business_days=1,
    )

    assert calls == ["20260803"]
    assert result["Date"].max() == pd.Timestamp("2026-08-03")


def test_parse_kis_index_rows():
    rows = [{
        "stck_bsop_date": "20260803",
        "bstp_nmix_oprc": "3,210.10",
        "bstp_nmix_hgpr": "3,240.20",
        "bstp_nmix_lwpr": "3,200.00",
        "bstp_nmix_prpr": "3,230.50",
        "acml_vol": "456,789,000",
    }]
    frame = data_module._parse_kis_index_rows(rows)
    assert frame.loc[0, "Date"] == pd.Timestamp("2026-08-03")
    assert frame.loc[0, "Close"] == 3230.5
    assert frame.loc[0, "Volume"] == 456789000


def test_fetch_kis_kospi_recent_uses_official_headers(monkeypatch):
    calls = []

    def fake_post(url, *, payload, headers=None, timeout=30, retries=4):
        calls.append(("post", url, payload, headers))
        return FakeResponse({"access_token": "token-value"})

    def fake_get(url, *, params=None, headers=None, timeout=30, retries=4):
        calls.append(("get", url, params, headers))
        return FakeResponse({
            "rt_cd": "0",
            "msg_cd": "MCA00000",
            "msg1": "ok",
            "output2": [{
                "stck_bsop_date": "20260803",
                "bstp_nmix_oprc": "3210.10",
                "bstp_nmix_hgpr": "3240.20",
                "bstp_nmix_lwpr": "3200.00",
                "bstp_nmix_prpr": "3230.50",
                "acml_vol": "456789000",
            }],
        })

    monkeypatch.setenv("KIS_APP_KEY", "app-key")
    monkeypatch.setenv("KIS_APP_SECRET", "app-secret")
    monkeypatch.setattr(data_module, "_retry_post_json", fake_post)
    monkeypatch.setattr(data_module, "_retry_get", fake_get)
    frame = data_module.fetch_kis_kospi_recent(
        as_of_date="2026-08-03", timeout=3, retries=1
    )
    assert len(frame) == 1
    _, _, params, headers = calls[1]
    assert params["FID_COND_MRKT_DIV_CODE"] == "U"
    assert params["FID_INPUT_ISCD"] == "0001"
    assert params["FID_INPUT_DATE_1"] == "20260803"
    assert headers["tr_id"] == "FHPUP02120000"
    assert headers["authorization"] == "Bearer token-value"


def test_merge_provisional_rows_only_adds_new_eligible_dates():
    official = pd.DataFrame({
        "Date": pd.to_datetime(["2026-07-31"]),
        "Open": [3100.0], "High": [3120.0], "Low": [3090.0], "Close": [3110.0], "Volume": [1000.0],
    })
    kis = pd.DataFrame({
        "Date": pd.to_datetime(["2026-07-31", "2026-08-03", "2026-08-04"]),
        "Open": [3101.0, 3200.0, 3300.0],
        "High": [3121.0, 3220.0, 3320.0],
        "Low": [3091.0, 3190.0, 3290.0],
        "Close": [3111.0, 3210.0, 3310.0],
        "Volume": [1001.0, 2000.0, 3000.0],
    })
    merged, dates = data_module._merge_provisional_rows(
        official, kis, max_date=pd.Timestamp("2026-08-03")
    )
    assert dates == ["2026-08-03"]
    assert merged["Date"].max() == pd.Timestamp("2026-08-03")
    assert merged.loc[merged["Date"] == pd.Timestamp("2026-07-31"), "Close"].iloc[0] == 3110.0
