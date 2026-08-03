from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_settings
from .pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run KOSPI SHADOW v2 pipeline")
    parser.add_argument("--config", default="config/default.yml")
    parser.add_argument("--project-root", default=".")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    settings = load_settings(root / args.config)
    metrics = run_pipeline(settings, root)
    print(json.dumps({
        "status": metrics["promotion"]["status"],
        "signal_enabled": metrics["promotion"]["signal_enabled"],
        "brier": metrics["classification"]["brier"],
        "baseline_brier": metrics["classification"]["baseline_brier"],
        "oos_n": metrics["classification"]["n"],
        "candidate_target_date": metrics["latest_prediction"]["candidate_target_date"],
        "probability_intraday_up": metrics["latest_prediction"]["probability_intraday_up"],
        "research_direction": metrics["latest_prediction"]["research_direction"],
        "actionable": metrics["latest_prediction"]["actionable"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
