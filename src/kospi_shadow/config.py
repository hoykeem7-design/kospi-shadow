from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    config_path: Path

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name)
        if not isinstance(value, dict):
            raise ValueError(f"Missing or invalid config section: {name}")
        return value


def load_settings(path: str | Path) -> Settings:
    config_path = Path(path).resolve()
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError("Configuration root must be a mapping")
    for required in ("project", "data", "model", "promotion"):
        if required not in raw:
            raise ValueError(f"Missing config section: {required}")
    return Settings(raw=raw, config_path=config_path)
