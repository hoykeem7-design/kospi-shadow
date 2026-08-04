from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from .config import load_settings
from .premarket import SEOUL
from .premarket_data import build_premarket_experiment


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect point-in-time NXT/KRX stock snapshots")
    parser.add_argument("--config", default="config/default.yml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    settings = load_settings(project_root / args.config)
    result = build_premarket_experiment(
        settings,
        project_root,
        now_seoul=datetime.now(SEOUL),
        market_snapshot=None,
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
    print(
        "premarket collection complete: "
        f"phase={status['market_phase']} configured={status['configured_symbol_count']} "
        f"available={status['available_symbol_count']}"
    )


if __name__ == "__main__":
    main()
