from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

import yaml

from config.schema import AppConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        value = yaml.safe_load(source) or {}
    if not isinstance(value, dict):
        raise TypeError(f"Expected a mapping in {path}")
    return value


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge a sweep overlay without losing untouched config blocks."""
    merged = base.copy()
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    raw = _read_yaml(config_path)
    extends = raw.pop("extends", None)
    llm_base_dir = config_path.parent
    if extends:
        # Sweep files are constrained overlays so they inherit every required base knob.
        base_path = (config_path.parent / extends).resolve()
        raw = _merge(_read_yaml(base_path), raw)
        llm_base_dir = base_path.parent
    llm_file = raw.pop("llm_contract_file", None)
    if llm_file:
        raw["llm"] = _read_yaml(llm_base_dir / llm_file)
    retrieval = raw.get("retrieval")
    bm25s = retrieval.get("bm25s") if isinstance(retrieval, dict) else None
    if isinstance(bm25s, dict):
        # The installed BM25 implementation affects ranking, so its exact version is semantic.
        bm25s["version"] = version("bm25s")
    return AppConfig.model_validate(raw)
