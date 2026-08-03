from __future__ import annotations

import argparse
import json
from pathlib import Path

from .coach import generate_coach_app
from .config import load_settings
from .pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate KOSPI Shadow Coach PWA snapshot")
    parser.add_argument("--config", default="config/default.yml")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--refresh-prediction", action="store_true", help="Run the cached prediction pipeline before building the app")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    settings = load_settings(root / args.config)
    if args.refresh_prediction:
        run_pipeline(settings, root, mode="auto")
    dashboard = generate_coach_app(settings, root)
    print(json.dumps({
        "generated_at_seoul": dashboard["generated_at_seoul"],
        "session": dashboard["session"]["code"],
        "target_date": dashboard["prediction"]["candidate_target_date"],
        "probability_intraday_up": dashboard["prediction"]["probability_intraday_up"],
        "coach_action": dashboard["coaching"]["action"],
        "coach_headline": dashboard["coaching"]["headline"],
        "warnings": len(dashboard["data_quality"]["warnings"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
