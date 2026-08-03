from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests


KRX_KOSPI_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd"
FRED_OBSERVATIONS_ENDPOINT = "https://api.stlouisfed.org/fred/series/observations"


@dataclass(frozen=True)
class DataBundle:
    target: pd.DataFrame
    factors: dict[str, pd.DataFrame]
    manifest: dict[str, Any]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _retry_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    retries: int = 4,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"transient HTTP {response.status_code}")
            response.raise_for_status()
            return response
        except Exception as exc:  # requests exposes several network exception types
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"GET failed after {retries} attempts: {url}: {last_error}")


def _normalise_ohlcv(df: pd.DataFrame, source: str) -> pd.DataFrame:
    rename = {c.lower(): c for c in df.columns}
    out = df.copy()
    if "Date" not in out.columns:
        if out.index.name is not None or isinstance(out.index, pd.DatetimeIndex):
            out = out.reset_index()
        date_col = next((c for c in out.columns if str(c).lower() in {"date", "datetime"}), None)
        if date_col is None:
            raise ValueError(f"{source}: no date column")
        out = out.rename(columns={date_col: "Date"})
    expected = ["Open", "High", "Low", "Close", "Volume"]
    for col in expected:
        if col not in out.columns:
            match = next((c for c in out.columns if str(c).lower() == col.lower()), None)
            if match is None:
                if col == "Volume":
                    out[col] = 0.0
                else:
                    raise ValueError(f"{source}: missing {col}")
            else:
                out = out.rename(columns={match: col})
    out["Date"] = pd.to_datetime(out["Date"], errors="raise").dt.tz_localize(None).dt.normalize()
    for col in expected:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out[["Date", *expected]].dropna(subset=["Date", "Open", "High", "Low", "Close"])
    out = out.sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)
    bad = (out["High"] < out[["Open", "Close"]].max(axis=1)) | (out["Low"] > out[["Open", "Close"]].min(axis=1))
    if bad.any():
        raise ValueError(f"{source}: OHLC consistency failure on {int(bad.sum())} rows")
    return out


def fetch_yahoo_ohlcv(ticker: str, start: str, end: str | None = None) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is required for Yahoo provider") from exc
    raw = yf.download(
        ticker,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=False,
        actions=False,
        repair=True,
        progress=False,
        threads=False,
        timeout=30,
        multi_level_index=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"Yahoo returned no data for {ticker}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    return _normalise_ohlcv(raw, f"yahoo:{ticker}")


def _parse_krx_rows(rows: list[dict[str, Any]], names: Iterable[str]) -> pd.DataFrame:
    candidates = {str(x).strip().upper() for x in names}
    selected = [r for r in rows if str(r.get("IDX_NM", "")).strip().upper() in candidates]
    if not selected:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    parsed = pd.DataFrame(
        {
            "Date": [r.get("BAS_DD") for r in selected],
            "Open": [r.get("OPNPRC_IDX") for r in selected],
            "High": [r.get("HGPRC_IDX") for r in selected],
            "Low": [r.get("LWPRC_IDX") or r.get("WPRC_IDX") for r in selected],
            "Close": [r.get("CLSPRC_IDX") for r in selected],
            "Volume": [r.get("ACC_TRDVOL", 0) for r in selected],
        }
    )
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        parsed[col] = parsed[col].astype(str).str.replace(",", "", regex=False).replace({"-": np.nan, "": np.nan})
    return _normalise_ohlcv(parsed, "krx")


def fetch_krx_kospi(
    start: str,
    end: str,
    cache_path: Path,
    names: Iterable[str],
    timeout: int,
    retries: int,
    pause_seconds: float,
) -> pd.DataFrame:
    api_key = (os.getenv("KRX_AUTH_KEY", "").strip() or os.getenv("KRX_API_KEY", "").strip())
    if not api_key:
        raise RuntimeError("KRX_AUTH_KEY (or legacy KRX_API_KEY) is not set")

    if cache_path.exists():
        cached = _normalise_ohlcv(pd.read_csv(cache_path), "krx-cache")
    else:
        cached = pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        cached["Date"] = pd.to_datetime(cached["Date"])

    cached_dates = set(pd.to_datetime(cached["Date"]).dt.strftime("%Y%m%d"))
    checked_path = cache_path.with_suffix(".checked_dates.txt")
    checked_dates = set(checked_path.read_text(encoding="utf-8").split()) if checked_path.exists() else set()
    requested = pd.bdate_range(start=start, end=end)
    new_frames: list[pd.DataFrame] = []
    for ts in requested:
        date_key = ts.strftime("%Y%m%d")
        if date_key in cached_dates or date_key in checked_dates:
            continue
        response = _retry_get(
            KRX_KOSPI_ENDPOINT,
            params={"basDd": date_key},
            headers={"AUTH_KEY": api_key},
            timeout=timeout,
            retries=retries,
        )
        payload = response.json()
        rows = payload.get("OutBlock_1")
        if not isinstance(rows, list):
            raise RuntimeError(f"KRX malformed response for {date_key}: {json.dumps(payload, ensure_ascii=False)[:300]}")
        day = _parse_krx_rows(rows, names)
        if not day.empty:
            new_frames.append(day)
        checked_dates.add(date_key)
        time.sleep(max(0.0, pause_seconds))

    checked_path.write_text("\n".join(sorted(checked_dates)) + "\n", encoding="utf-8")
    combined = pd.concat([cached, *new_frames], ignore_index=True) if new_frames else cached
    combined = _normalise_ohlcv(combined, "krx-combined")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(cache_path, index=False)
    return combined


def fetch_fred_series(series_id: str, start: str, timeout: int, retries: int) -> pd.DataFrame:
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("FRED_API_KEY is not set")
    response = _retry_get(
        FRED_OBSERVATIONS_ENDPOINT,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start,
            "sort_order": "asc",
        },
        timeout=timeout,
        retries=retries,
    )
    payload = response.json()
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise RuntimeError(f"FRED malformed response for {series_id}")
    df = pd.DataFrame({
        "Date": [x.get("date") for x in observations],
        "value": [x.get("value") for x in observations],
    })
    df["Date"] = pd.to_datetime(df["Date"], errors="raise").dt.normalize()
    df["value"] = pd.to_numeric(df["value"].replace(".", np.nan), errors="coerce")
    return df.dropna().sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)


def collect_data(config: dict[str, Any], project_root: Path) -> DataBundle:
    start = str(config["start_date"])
    seoul_today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    end = (seoul_today + timedelta(days=1)).isoformat()
    cache_dir = project_root / str(config.get("cache_dir", "data/cache"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    timeout = int(config.get("request_timeout_seconds", 30))
    retries = int(config.get("request_retries", 4))

    provider_requested = str(config.get("target_provider", "auto")).lower()
    target: pd.DataFrame | None = None
    target_provider = ""
    errors: list[str] = []

    if provider_requested in {"auto", "krx"}:
        try:
            target = fetch_krx_kospi(
                start=start,
                end=seoul_today.isoformat(),
                cache_path=cache_dir / "kospi_krx.csv",
                names=config.get("krx_index_name_candidates", ["코스피", "KOSPI"]),
                timeout=timeout,
                retries=retries,
                pause_seconds=float(config.get("krx_pause_seconds", 0.12)),
            )
            target_provider = "krx_official_open_api"
        except Exception as exc:
            errors.append(f"KRX: {exc}")
            if provider_requested == "krx":
                raise

    if target is None:
        target = fetch_yahoo_ohlcv(str(config["yahoo_target_ticker"]), start=start, end=end)
        target_provider = "yahoo_unofficial_fallback"
        target.to_csv(cache_dir / "kospi_yahoo.csv", index=False)

    factors: dict[str, pd.DataFrame] = {}
    for name, ticker in dict(config.get("external_tickers", {})).items():
        try:
            factors[name] = fetch_yahoo_ohlcv(str(ticker), start=start, end=end)
        except Exception as exc:
            errors.append(f"Yahoo factor {name}/{ticker}: {exc}")

    for name, series_id in dict(config.get("fred_series", {})).items():
        try:
            factors[name] = fetch_fred_series(str(series_id), start=start, timeout=timeout, retries=retries)
        except Exception as exc:
            errors.append(f"FRED factor {name}/{series_id}: {exc}")

    target_path = cache_dir / "target_snapshot.csv"
    target.to_csv(target_path, index=False)
    manifest = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_provider": target_provider,
        "target_official": target_provider.startswith("krx_official"),
        "target_rows": int(len(target)),
        "target_date_min": target["Date"].min().strftime("%Y-%m-%d"),
        "target_date_max": target["Date"].max().strftime("%Y-%m-%d"),
        "target_sha256": sha256_file(target_path),
        "factor_names": sorted(factors),
        "collection_warnings": errors,
    }
    return DataBundle(target=target, factors=factors, manifest=manifest)
