from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

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


def _log(message: str) -> None:
    print(f"[data] {message}", flush=True)


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
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"GET failed after {retries} attempts: {url}: {last_error}")


def _normalise_ohlcv(df: pd.DataFrame, source: str, *, strict_ohlc: bool = True) -> pd.DataFrame:
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
            if match is not None:
                out = out.rename(columns={match: col})
            elif col == "Volume":
                out[col] = 0.0
            elif "Close" in out.columns and not strict_ohlc:
                out[col] = out["Close"]
            else:
                raise ValueError(f"{source}: missing {col}")
    out["Date"] = pd.to_datetime(out["Date"], errors="raise").dt.tz_localize(None).dt.normalize()
    for col in expected:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out[["Date", *expected]].dropna(subset=["Date", "Close"])
    if not strict_ohlc:
        for col in ("Open", "High", "Low"):
            out[col] = out[col].fillna(out["Close"])
    out = out.dropna(subset=["Open", "High", "Low"]).sort_values("Date")
    out = out.drop_duplicates("Date", keep="last").reset_index(drop=True)
    if strict_ohlc:
        bad = (out["High"] < out[["Open", "Close"]].max(axis=1)) | (
            out["Low"] > out[["Open", "Close"]].min(axis=1)
        )
        if bad.any():
            raise ValueError(f"{source}: OHLC consistency failure on {int(bad.sum())} rows")
    return out


def _merge_cached(existing: pd.DataFrame | None, fresh: pd.DataFrame, source: str) -> pd.DataFrame:
    if existing is None or existing.empty:
        combined = fresh
    else:
        combined = pd.concat([existing, fresh], ignore_index=True)
    return _normalise_ohlcv(combined, source, strict_ohlc=False)


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
        repair=False,
        progress=False,
        threads=False,
        timeout=30,
        multi_level_index=False,
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"Yahoo returned no data for {ticker}")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    return _normalise_ohlcv(raw, f"yahoo:{ticker}", strict_ohlc=True)


def fetch_yahoo_factors_batch(
    tickers: dict[str, str],
    *,
    start: str,
    end: str | None,
    cache_dir: Path,
    overlap_days: int = 10,
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Download factor closes in one Yahoo request and cache each series.

    Factor features only consume Close. Synthesising O/H/L from Close avoids false
    consistency failures in repaired FX data while preserving the information used.
    """
    if not tickers:
        return {}, []
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance is required for Yahoo provider") from exc

    cached: dict[str, pd.DataFrame] = {}
    latest_dates: list[pd.Timestamp] = []
    for name in tickers:
        path = cache_dir / f"yahoo_{name}.csv"
        if path.exists():
            frame = _normalise_ohlcv(pd.read_csv(path), f"cache:{name}", strict_ohlc=False)
            cached[name] = frame
            if not frame.empty:
                latest_dates.append(frame["Date"].max())

    fetch_start = pd.Timestamp(start)
    if latest_dates:
        fetch_start = max(fetch_start, min(latest_dates) - pd.Timedelta(days=overlap_days))
    _log(f"Yahoo batch: {len(tickers)} factors from {fetch_start.date()}")
    raw = yf.download(
        list(tickers.values()),
        start=fetch_start.strftime("%Y-%m-%d"),
        end=end,
        interval="1d",
        auto_adjust=False,
        actions=False,
        repair=False,
        progress=False,
        threads=True,
        timeout=30,
        group_by="ticker",
        multi_level_index=True,
    )

    factors: dict[str, pd.DataFrame] = {}
    warnings: list[str] = []
    for name, ticker in tickers.items():
        try:
            if raw is None or raw.empty:
                raise RuntimeError("empty batch response")
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker in raw.columns.get_level_values(0):
                    part = raw[ticker].copy()
                elif ticker in raw.columns.get_level_values(-1):
                    part = raw.xs(ticker, axis=1, level=-1).copy()
                else:
                    raise RuntimeError("ticker absent from batch response")
            else:
                if len(tickers) != 1:
                    raise RuntimeError("unexpected flat columns for multi-ticker response")
                part = raw.copy()
            if "Close" not in part.columns:
                raise RuntimeError("Close missing")
            close = pd.DataFrame({"Date": part.index, "Close": part["Close"]})
            close["Open"] = close["Close"]
            close["High"] = close["Close"]
            close["Low"] = close["Close"]
            close["Volume"] = part["Volume"] if "Volume" in part.columns else 0.0
            fresh = _normalise_ohlcv(close, f"yahoo-factor:{ticker}", strict_ohlc=False)
            merged = _merge_cached(cached.get(name), fresh, f"yahoo-factor:{ticker}")
            path = cache_dir / f"yahoo_{name}.csv"
            merged.to_csv(path, index=False)
            factors[name] = merged
        except Exception as exc:
            if name in cached and not cached[name].empty:
                factors[name] = cached[name]
                warnings.append(f"Yahoo factor {name}/{ticker}: fresh fetch failed; used cache: {exc}")
            else:
                warnings.append(f"Yahoo factor {name}/{ticker}: {exc}")
    return factors, warnings


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
    api_key = os.getenv("KRX_AUTH_KEY", "").strip() or os.getenv("KRX_API_KEY", "").strip()
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
    known_dates = cached_dates | checked_dates
    scan_start = pd.Timestamp(start)
    if known_dates:
        scan_start = max(scan_start, pd.Timestamp(max(known_dates)) + pd.Timedelta(days=1))
    requested = pd.bdate_range(start=scan_start, end=end)
    _log(f"KRX incremental scan: {len(requested)} business dates from {scan_start.date()} to {end}")

    new_frames: list[pd.DataFrame] = []
    for i, ts in enumerate(requested, start=1):
        date_key = ts.strftime("%Y%m%d")
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
        if i % 100 == 0 or i == len(requested):
            _log(f"KRX progress: {i}/{len(requested)}")
        time.sleep(max(0.0, pause_seconds))

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    checked_path.write_text("\n".join(sorted(checked_dates)) + "\n", encoding="utf-8")
    combined = pd.concat([cached, *new_frames], ignore_index=True) if new_frames else cached
    combined = _normalise_ohlcv(combined, "krx-combined")
    combined.to_csv(cache_path, index=False)
    return combined


def fetch_fred_series(
    series_id: str,
    start: str,
    timeout: int,
    retries: int,
    cache_path: Path | None = None,
    overlap_days: int = 14,
) -> pd.DataFrame:
    api_key = os.getenv("FRED_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("FRED_API_KEY is not set")
    cached: pd.DataFrame | None = None
    fetch_start = pd.Timestamp(start)
    if cache_path is not None and cache_path.exists():
        cached = pd.read_csv(cache_path)
        cached["Date"] = pd.to_datetime(cached["Date"]).dt.normalize()
        cached["value"] = pd.to_numeric(cached["value"], errors="coerce")
        cached = cached.dropna().sort_values("Date").drop_duplicates("Date", keep="last")
        if not cached.empty:
            fetch_start = max(fetch_start, cached["Date"].max() - pd.Timedelta(days=overlap_days))
    response = _retry_get(
        FRED_OBSERVATIONS_ENDPOINT,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": fetch_start.strftime("%Y-%m-%d"),
            "sort_order": "asc",
        },
        timeout=timeout,
        retries=retries,
    )
    payload = response.json()
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise RuntimeError(f"FRED malformed response for {series_id}")
    fresh = pd.DataFrame({
        "Date": [x.get("date") for x in observations],
        "value": [x.get("value") for x in observations],
    })
    fresh["Date"] = pd.to_datetime(fresh["Date"], errors="raise").dt.normalize()
    fresh["value"] = pd.to_numeric(fresh["value"].replace(".", np.nan), errors="coerce")
    fresh = fresh.dropna().sort_values("Date").drop_duplicates("Date", keep="last")
    combined = fresh if cached is None else pd.concat([cached, fresh], ignore_index=True)
    combined = combined.dropna().sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(cache_path, index=False)
    return combined


def collect_data(config: dict[str, Any], project_root: Path) -> DataBundle:
    started = time.perf_counter()
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

    external_tickers = {str(k): str(v) for k, v in dict(config.get("external_tickers", {})).items()}
    factors, yahoo_warnings = fetch_yahoo_factors_batch(
        external_tickers,
        start=start,
        end=end,
        cache_dir=cache_dir,
        overlap_days=int(config.get("factor_cache_overlap_days", 10)),
    )
    errors.extend(yahoo_warnings)

    for name, series_id in dict(config.get("fred_series", {})).items():
        try:
            _log(f"FRED {name}/{series_id}")
            factors[name] = fetch_fred_series(
                str(series_id),
                start=start,
                timeout=timeout,
                retries=retries,
                cache_path=cache_dir / f"fred_{name}.csv",
                overlap_days=int(config.get("fred_cache_overlap_days", 14)),
            )
        except Exception as exc:
            errors.append(f"FRED factor {name}/{series_id}: {exc}")

    target_path = cache_dir / "target_snapshot.csv"
    target.to_csv(target_path, index=False)
    manifest = {
        "collected_at_utc": datetime.now(timezone.utc).isoformat(),
        "collection_seconds": round(time.perf_counter() - started, 3),
        "target_provider": target_provider,
        "target_official": target_provider.startswith("krx_official"),
        "target_rows": int(len(target)),
        "target_date_min": target["Date"].min().strftime("%Y-%m-%d"),
        "target_date_max": target["Date"].max().strftime("%Y-%m-%d"),
        "target_sha256": sha256_file(target_path),
        "factor_names": sorted(factors),
        "collection_warnings": errors,
    }
    _log(f"collection complete in {manifest['collection_seconds']}s; factors={sorted(factors)}")
    return DataBundle(target=target, factors=factors, manifest=manifest)
