from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from kospi_shadow.coach import generate_coach_app
from kospi_shadow.config import load_settings


APP_VERSION = "5.5.0"
OFFICIAL_APP_URL = "https://hoykeem7-design.github.io/kospi-shadow/"
MARKET_STALE_AFTER_MINUTES = 15
MODEL_STALE_AFTER_DAYS = 8


def _deployment_sha() -> str | None:
    return (os.getenv("SOURCE_SHA") or os.getenv("GITHUB_SHA") or "").strip() or None


def _load_base_dashboard(page_url: str) -> dict:
    url = page_url.rstrip("/") + "/data/dashboard.json"
    response = requests.get(
        url,
        params={"seed": _deployment_sha() or "local"},
        headers={"Cache-Control": "no-cache", "User-Agent": "KOSPI-Shadow-Fast-Live/1.0"},
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Base dashboard is not a JSON object")
    return payload


def _write_minimum_metrics(base: dict, output_dir: Path) -> None:
    validation = base.get("validation") or {}
    quality = base.get("data_quality") or {}
    prediction = base.get("prediction") or {}
    if not prediction:
        raise RuntimeError("Base dashboard has no prediction snapshot")

    metrics = {
        "latest_prediction": prediction,
        "promotion": base.get("promotion") or {
            "signal_enabled": False,
            "status": "RESTRICTED_SHADOW",
            "checks": {},
        },
        "data_manifest": {
            "target_provider": quality.get("target_provider"),
            "target_official": quality.get("target_official"),
            "target_latest_source": quality.get("latest_source"),
            "target_date_max": quality.get("target_date_max"),
            "collection_warnings": list(quality.get("warnings") or []),
        },
        "classification": {
            "roc_auc": validation.get("roc_auc"),
            "brier": validation.get("brier"),
            "baseline_brier": validation.get("baseline_brier"),
            "brier_improvement": validation.get("brier_improvement"),
            "n": validation.get("oos_n"),
        },
        "strategy_proxy": {
            "model": {
                "annualized_sharpe": validation.get("strategy_sharpe"),
                "max_drawdown": validation.get("max_drawdown"),
            }
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _rewrite_dashboard(project_root: Path, dashboard: dict, base_generated_at: str | None) -> None:
    market = dashboard.get("market") or {}
    index = market.get("kospi")
    futures = market.get("kospi200_futures")
    if not index and not futures:
        warnings = (dashboard.get("data_quality") or {}).get("warnings") or []
        raise RuntimeError("KIS live snapshot unavailable: " + " | ".join(map(str, warnings)))

    source_sha = _deployment_sha()
    dashboard["app_version"] = APP_VERSION
    if source_sha:
        dashboard["build_sha"] = source_sha

    dashboard["market_refresh"] = {
        "mode": "fast_live_market_only",
        "model_snapshot_reused": True,
        "model_snapshot_source_generated_at_seoul": base_generated_at,
        "source_sha": source_sha,
        "note": "현물·선물·거래순위는 현재 KIS 데이터이며 모델 확률은 마지막 검증 스냅샷을 재사용합니다.",
    }
    operations = (dashboard.get("decision_coach_v5") or {}).get("operations")
    if isinstance(operations, dict):
        operations.update({
            "app_version": APP_VERSION,
            "publish_target": "GitHub Pages",
            "last_pages_deploy": dashboard.get("generated_at_seoul"),
            "last_market_refresh": dashboard.get("generated_at_seoul"),
            "refresh_mode": "scheduled_kis_snapshot",
        })
    dashboard["operational_trust"] = {
        "schema_version": 1,
        "official_app_url": OFFICIAL_APP_URL,
        "official_host": "hoykeem7-design.github.io",
        "official_path": "/kospi-shadow/",
        "market_stale_after_minutes": MARKET_STALE_AFTER_MINUTES,
        "model_stale_after_days": MODEL_STALE_AFTER_DAYS,
        "market_timestamp_fields": ["market.kospi.received_at", "market.kospi200_futures.received_at"],
        "model_timestamp_field": "market_refresh.model_snapshot_source_generated_at_seoul",
        "news_timestamp_fields": ["news.received_at", "news.published_at_kst"],
        "trade_lock_policy": "장중 현물·선물 중 하나라도 미수신이거나 15분 초과 지연이면 DATA_STALE로 신규 매매 판단을 잠급니다.",
    }
    quality = dashboard.setdefault("data_quality", {})
    warnings = list(quality.get("warnings") or [])
    notice = (
        "KIS 시장 데이터는 갱신됐지만 모델 확률은 마지막 검증 스냅샷입니다. "
        "오래된 모델이면 매매 허가를 내리지 않습니다."
    )
    if notice not in warnings:
        warnings.append(notice)
    quality["warnings"] = warnings

    data_dir = project_root / "site" / "data"
    dashboard_path = data_dir / "dashboard.json"
    dashboard_path.write_text(
        json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    escaped = json.dumps(dashboard, ensure_ascii=False).replace("</", "<\\/")
    (data_dir / "initial-data.js").write_text(
        f"window.__INITIAL_DASHBOARD__ = {escaped};\n", encoding="utf-8"
    )


def main() -> None:
    project_root = Path(os.getenv("PROJECT_ROOT", ".")).resolve()
    page_url = os.getenv("PAGE_URL", OFFICIAL_APP_URL)
    base = _load_base_dashboard(page_url)
    _write_minimum_metrics(base, project_root / "outputs")
    settings = load_settings(project_root / "config" / "default.yml")
    dashboard = generate_coach_app(settings, project_root)
    _rewrite_dashboard(project_root, dashboard, base.get("generated_at_seoul"))

    market = dashboard.get("market") or {}
    print(
        json.dumps(
            {
                "app_version": dashboard.get("app_version"),
                "generated_at_seoul": dashboard.get("generated_at_seoul"),
                "build_sha": dashboard.get("build_sha"),
                "kospi": market.get("kospi"),
                "kospi200_futures": market.get("kospi200_futures"),
                "stock_attention_availability": (
                    market.get("stock_attention") or {}
                ).get("availability"),
                "model_snapshot_reused": True,
                "operational_trust": dashboard.get("operational_trust"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
