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
KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_TOKEN_ENDPOINT = "/oauth2/tokenP"
KIS_INDEX_DAILY_ENDPOINT = "/uapi/domestic-stock/v1/quotations/inquire-index-daily-price"
KIS_INDEX_DAILY_TR_ID = "FHPUP02120000"
_KIS_TOKEN_CACHE: dict[str, tuple[str, datetime]] = {}


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


def _retry_post_json(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 30,
    retries: int = 4,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"transient HTTP {response.status_code}")
            response.raise_for_status()
            return response
        except Exception as exc:
            last_error = exc
            if attempt + 1 < retries:
                time.sleep(min(2**attempt, 8))
    raise RuntimeError(f"POST failed after {retries} attempts: {url}: {last_error}")


def _kis_credentials() -> tuple[str, str]:
    return os.getenv("KIS_APP_KEY", "").strip(), os.getenv("KIS_APP_SECRET", "").strip()


def fetch_kis_access_token(
    *,
    timeout: int,
    retries: int,
    base_url: str = KIS_BASE_URL,
    force_refresh: bool = False,
) -> str:
    app_key, app_secret = _kis_credentials()
    if not app_key or not app_secret:
        raise RuntimeError("KIS_APP_KEY and KIS_APP_SECRET must both be set")
    cached = _KIS_TOKEN_CACHE.get(app_key)
    now_utc = datetime.now(timezone.utc)
    if not force_refresh and cached is not None and cached[1] > now_utc + timedelta(minutes=2):
        return cached[0]
    response = _retry_post_json(
        f"{base_url}{KIS_TOKEN_ENDPOINT}",
        payload={
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret,
        },
        headers={"Content-Type": "application/json", "Accept": "text/plain", "charset": "UTF-8"},
        timeout=timeout,
        retries=retries,
    )
    payload = response.json()
    token = str(payload.get("access_token", "")).strip()
    if not token:
        raise RuntimeError(f"KIS token response missing access_token: {json.dumps(payload, ensure_ascii=False)[:300]}")
    expires_at = now_utc + timedelta(hours=23)
    expiry_text = str(payload.get("access_token_token_expired", "")).strip()
    if expiry_text:
        try:
            parsed = pd.Timestamp(expiry_text)
            if parsed.tzinfo is None:
                parsed = parsed.tz_localize(ZoneInfo("Asia/Seoul"))
            expires_at = parsed.tz_convert("UTC").to_pydatetime()
        except Exception:
            pass
    _KIS_TOKEN_CACHE[app_key] = (token, expires_at)
    return token


def _parse_kis_index_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    parsed = pd.DataFrame({
        "Date": [r.get("stck_bsop_date") for r in rows],
        "Open": [r.get("bstp_nmix_oprc") for r in rows],
        "High": [r.get("bstp_nmix_hgpr") for r in rows],
        "Low": [r.get("bstp_nmix_lwpr") for r in rows],
        "Close": [r.get("bstp_nmix_prpr") for r in rows],
        "Volume": [r.get("acml_vol", 0) for r in rows],
    })
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        parsed[col] = parsed[col].astype(str).str.replace(",", "", regex=False).replace({"-": np.nan, "": np.nan})
    return _normalise_ohlcv(parsed, "kis-index-daily", strict_ohlc=True)


def fetch_kis_kospi_recent(
    *,
    as_of_date: str,
    timeout: int,
    retries: int,
    market_code: str = "U",
    index_code: str = "0001",
    base_url: str = KIS_BASE_URL,
) -> pd.DataFrame:
    app_key, app_secret = _kis_credentials()
    if not app_key or not app_secret:
        raise RuntimeError("KIS_APP_KEY and KIS_APP_SECRET must both be set")
    token = fetch_kis_access_token(timeout=timeout, retries=retries, base_url=base_url)
    response = _retry_get(
        f"{base_url}{KIS_INDEX_DAILY_ENDPOINT}",
        params={
            "FID_PERIOD_DIV_CODE": "D",
            "FID_COND_MRKT_DIV_CODE": market_code,
            "FID_INPUT_ISCD": index_code,
            "FID_INPUT_DATE_1": pd.Timestamp(as_of_date).strftime("%Y%m%d"),
        },
        headers={
            "Content-Type": "application/json",
            "Accept": "text/plain",
            "charset": "UTF-8",
            "authorization": f"Bearer {token}",
            "appkey": app_key,
            "appsecret": app_secret,
            "tr_id": KIS_INDEX_DAILY_TR_ID,
            "custtype": "P",
        },
        timeout=timeout,
        retries=retries,
    )
    payload = response.json()
    if str(payload.get("rt_cd", "")) != "0":
        raise RuntimeError(
            f"KIS index API failed [{payload.get('msg_cd', '')}]: {payload.get('msg1', 'unknown error')}"
        )
    rows = payload.get("output2")
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list):
        rows = []
    return _parse_kis_index_rows(rows)


def _merge_provisional_rows(
    base: pd.DataFrame,
    provisional: pd.DataFrame,
    *,
    max_date: pd.Timestamp,
) -> tuple[pd.DataFrame, list[str]]:
    if provisional.empty:
        return base, []
    base_max = pd.to_datetime(base["Date"]).max() if not base.empty else pd.Timestamp.min
    fresh = provisional[(provisional["Date"] > base_max) & (provisional["Date"] <= max_date)].copy()
    if fresh.empty:
        return base, []
    merged = _merge_cached(base, fresh, "target-with-kis-provisional")
    dates = fresh["Date"].dt.strftime("%Y-%m-%d").tolist()
    return merged, dates


def _normalise_ohlcv(df: pd.DataFrame, source: str, *, strict_ohlc: bool = True) -> pd.DataFrame:
    out = df.copy()
    # yfinance may return an index named ``Date`` while callers also provide a
    # concrete ``Date`` column. Pandas then treats ``sort_values("Date")`` as
    # ambiguous. Once a real Date column exists the index is only positional,
    # so discard the colliding index name before any column operations.
    if "Date" in out.columns and "Date" in set(out.index.names):
        out = out.reset_index(drop=True)
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
            # Use arrays rather than indexed Series so pandas cannot retain a
            # Date-named index alongside the Date column. This was the source of
            # the production error: "Date is both an index level and a column".
            close_values = pd.to_numeric(part["Close"], errors="coerce").to_numpy()
            volume_values = (
                pd.to_numeric(part["Volume"], errors="coerce").to_numpy()
                if "Volume" in part.columns
                else np.zeros(len(part), dtype=float)
            )
            close = pd.DataFrame({
                "Date": pd.to_datetime(part.index).to_numpy(),
                "Open": close_values,
                "High": close_values,
                "Low": close_values,
                "Close": close_values,
                "Volume": volume_values,
            })
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
    recheck_recent_business_days: int = 5,
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

    # A date can be checked before KRX publishes its final daily row. Never
    # permanently suppress the most recent sessions: re-query a small rolling
    # business-day window so a previously empty current-day response is filled
    # on the next run. Older known dates remain skipped for speed.
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    all_business_dates = pd.bdate_range(start=start_ts, end=end_ts)
    recent_days = max(0, int(recheck_recent_business_days))
    recent_cutoff = (
        end_ts - pd.offsets.BDay(recent_days - 1)
        if recent_days > 0
        else end_ts + pd.offsets.BDay(1)
    )
    requested = pd.DatetimeIndex([
        ts for ts in all_business_dates
        if ts.strftime("%Y%m%d") not in known_dates or ts >= recent_cutoff
    ])
    if len(requested):
        _log(
            f"KRX incremental scan: {len(requested)} business dates "
            f"from {requested.min().date()} to {requested.max().date()} "
            f"(recent recheck={recent_days})"
        )
    else:
        latest_known = max(known_dates) if known_dates else "none"
        _log(f"KRX incremental scan: 0 dates; cache/checked current through {latest_known}")

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


def collect_data(
    config: dict[str, Any],
    project_root: Path,
    *,
    allow_provisional: bool = True,
) -> DataBundle:
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
                recheck_recent_business_days=int(config.get("krx_recheck_recent_business_days", 5)),
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

    base_target_provider = target_provider
    provisional_dates: list[str] = []
    if allow_provisional and bool(config.get("kis_provisional_fallback", True)):
        app_key, app_secret = _kis_credentials()
        if bool(app_key) != bool(app_secret):
            errors.append("KIS provisional fallback: only one of KIS_APP_KEY/KIS_APP_SECRET is set")
        elif app_key and app_secret:
            try:
                seoul_now = datetime.now(ZoneInfo("Asia/Seoul"))
                cutoff_text = str(config.get("kis_current_day_cutoff_time", "15:45"))
                cutoff_hour, cutoff_minute = [int(x) for x in cutoff_text.split(":", 1)]
                current_day_allowed = (seoul_now.hour, seoul_now.minute) >= (cutoff_hour, cutoff_minute)
                max_provisional_date = pd.Timestamp(seoul_today)
                if not current_day_allowed:
                    max_provisional_date -= pd.Timedelta(days=1)
                _log(
                    f"KIS provisional check through {max_provisional_date.date()} "
                    f"(current-day cutoff={cutoff_text})"
                )
                kis_recent = fetch_kis_kospi_recent(
                    as_of_date=seoul_today.isoformat(),
                    timeout=timeout,
                    retries=retries,
                    market_code=str(config.get("kis_market_code", "U")),
                    index_code=str(config.get("kis_index_code", "0001")),
                )
                target, provisional_dates = _merge_provisional_rows(
                    target, kis_recent, max_date=max_provisional_date
                )
                if provisional_dates:
                    target_provider = f"{base_target_provider}_plus_kis_provisional"
                    _log(f"KIS provisional rows added: {', '.join(provisional_dates)}")
                else:
                    _log("KIS provisional overlay: no newer eligible row")
            except Exception as exc:
                errors.append(f"KIS provisional fallback: {exc}")

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
        "target_base_provider": base_target_provider,
        "target_official": base_target_provider.startswith("krx_official") and not provisional_dates,
        "target_latest_source": "kis_provisional" if provisional_dates else base_target_provider,
        "target_provisional_dates": provisional_dates,
        "target_rows": int(len(target)),
        "target_date_min": target["Date"].min().strftime("%Y-%m-%d"),
        "target_date_max": target["Date"].max().strftime("%Y-%m-%d"),
        "target_sha256": sha256_file(target_path),
        "factor_names": sorted(factors),
        "collection_warnings": errors,
    }
    _log(f"collection complete in {manifest['collection_seconds']}s; factors={sorted(factors)}")
    return DataBundle(target=target, factors=factors, manifest=manifest)
