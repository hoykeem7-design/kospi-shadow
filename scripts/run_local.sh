#!/usr/bin/env bash
set -euo pipefail
python -m pip install -e ".[test]"
pytest -q
kospi-shadow --config config/default.yml --project-root .
