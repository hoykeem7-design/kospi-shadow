from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_settings
from .pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run KOSPI SHADOW v3.2 pipeline")
    parser.add_argument("--config", default="config/default.yml")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--mode", choices=("auto", "full", "predict"), default="auto")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    settings = load_settings(root / args.config)
    metrics = run_pipeline(settings, root, mode=args.mode)
    print(json.dumps({
        "run_mode": metrics["run_mode"],
        "status": metrics["promotion"]["status"],
        "signal_enabled": metrics["promotion"]["signal_enabled"],
        "brier": metrics["classification"]["brier"],
        "baseline_brier": metrics["classification"]["baseline_brier"],
        "oos_n": metrics["classification"]["n"],
        "candidate_target_date": metrics["latest_prediction"]["candidate_target_date"],
        "probability_intraday_up": metrics["latest_prediction"]["probability_intraday_up"],
        "research_direction": metrics["latest_prediction"]["research_direction"],
        "timing_valid": metrics["latest_prediction"]["timing_valid_for_target"],
        "actionable": metrics["latest_prediction"]["actionable"],
        "runtime_seconds": metrics.get("runtime_seconds", {}),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
