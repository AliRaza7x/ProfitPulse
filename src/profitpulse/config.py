"""YAML configuration loading (rules + analytics parameters)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .settings import get_settings


def _load(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    path = (config_dir or get_settings().config_dir) / name
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=None)
def rules_config() -> dict[str, Any]:
    return _load("rules.yaml")


@lru_cache(maxsize=None)
def analytics_config() -> dict[str, Any]:
    cfg = _load("analytics.yaml")
    weights = sum(c["weight"] for c in cfg["branch_score"]["components"].values())
    if abs(weights - 1.0) > 1e-9:
        raise ValueError(f"branch_score weights must sum to 1.0, got {weights}")
    return cfg
