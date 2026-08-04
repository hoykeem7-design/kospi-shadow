from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, time as dtime
from pathlib import Path

from .config import load_settings
from .premarket import SEOUL
from .premarket_data import build_premarket_experiment


def smoke_target_and_count(result: dict, now_seoul: datetime) -> tuple[str, int]:
    """Count live responses for the session the smoke run claims to test."""
    current = now_seoul.astimezone(SEOUL) if now_seoul.tzinfo else now_seoul.replace(tzinfo=SEOUL)
    t = current.timetz().replace(tzinfo=None)
    symbols = result.get("symbols") or []
    if t >= dtime(15, 40):
        target = "nxt_aftermarket"
        count = sum(
            item.get("aftermarket_summary", {}).get("availability") == "available"
            for item in symbols
        )
    elif t < dtime(9, 0):
        target = "nxt_premarket"
        count = sum(
            item.get("premarket_summary", {}).get("availability") == "available"
            for item in symbols
        )
    else:
        target = "krx_post_open"
        count = sum(
            item.get("opening_five_minute_summary", {}).get("data_complete") is True
            for item in symbols
        )
    return target, count


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Collect point-in-time NXT/KRX stock snapshots")
    parser.add_argument("--config", default="config/default.yml")
    parser.add_argument("--project-root", default=".")
    parser.add_argument(
        "--require-symbols",
        action="store_true",
        help="fail when no PREMARKET_SYMBOLS/config symbols are configured",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="live provider smoke mode; requires symbols and at least one available response",
    )
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve()
    settings = load_settings(project_root / args.config)
    now_seoul = datetime.now(SEOUL)
    result = build_premarket_experiment(
        settings,
        project_root,
        now_seoul=now_seoul,
        market_snapshot=None,
        persist_history=not args.smoke,
    )
    smoke_target, smoke_available_count = smoke_target_and_count(result, now_seoul)
    status = {
        "generated_at": result.get("generated_at"),
        "market_phase": result.get("market_phase"),
        "configured_symbol_count": result.get("configured_symbol_count", 0),
        "available_symbol_count": sum(
            item.get("data_availability", {}).get("availability") == "available"
            for item in result.get("symbols", [])
        ),
        "smoke_target": smoke_target,
        "smoke_available_symbol_count": smoke_available_count,
        "data_availability": result.get("data_availability", {}).get("availability"),
        "unavailable_reason": result.get("data_availability", {}).get("unavailable_reason"),
        "stock_model_trained": False,
        "backtest_completed": False,
    }
    output = project_root / "outputs" / "premarket_collection_status.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    if status["configured_symbol_count"] == 0:
        print(
            "warning: no premarket symbols configured; set PREMARKET_SYMBOLS or premarket.symbols",
            file=sys.stderr,
        )
        return 2 if args.require_symbols or args.smoke else 0
    if args.smoke and status["smoke_available_symbol_count"] == 0:
        print(f"smoke failed: no configured symbol returned {smoke_target} live data", file=sys.stderr)
        return 3
    print(
        "premarket collection complete: "
        f"phase={status['market_phase']} configured={status['configured_symbol_count']} "
        f"available={status['available_symbol_count']} smoke_target={smoke_target} "
        f"smoke_available={status['smoke_available_symbol_count']}"
    )
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
