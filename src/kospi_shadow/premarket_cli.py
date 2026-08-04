from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .config import load_settings
from .premarket import SEOUL
from .premarket_data import build_premarket_experiment


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
    result = build_premarket_experiment(
        settings,
        project_root,
        now_seoul=datetime.now(SEOUL),
        market_snapshot=None,
        persist_history=not args.smoke,
    )
    status = {
        "generated_at": result.get("generated_at"),
        "market_phase": result.get("market_phase"),
        "configured_symbol_count": result.get("configured_symbol_count", 0),
        "available_symbol_count": sum(
            item.get("data_availability", {}).get("availability") == "available"
            for item in result.get("symbols", [])
        ),
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
    if args.smoke and status["available_symbol_count"] == 0:
        print("smoke failed: no configured symbol returned available live data", file=sys.stderr)
        return 3
    print(
        "premarket collection complete: "
        f"phase={status['market_phase']} configured={status['configured_symbol_count']} "
        f"available={status['available_symbol_count']}"
    )
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
