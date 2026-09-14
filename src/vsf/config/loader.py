from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from vsf.config.schema import AppConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        value = yaml.safe_load(source) or {}
    if not isinstance(value, dict):
        raise TypeError(f"Expected a mapping in {path}")
    return value


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    raw = _read_yaml(config_path)
    llm_file = raw.pop("llm_contract_file", None)
    if llm_file:
        raw["llm"] = _read_yaml(config_path.parent / llm_file)
    return AppConfig.model_validate(raw)
