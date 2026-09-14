from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from vsf.config.hashing import config_hash
from vsf.config.schema import AppConfig

SECRET_PATTERN = re.compile(r"(?:sk-|or-)[A-Za-z0-9_-]{16,}")


def _assert_no_secret(value: Any) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _assert_no_secret(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_secret(item)
    elif isinstance(value, str) and SECRET_PATTERN.search(value):
        raise ValueError("Refusing to write a manifest containing a secret")


def write_manifest(config: AppConfig, run_root: str | Path, git_commit: str | None = None) -> Path:
    snapshot = config.model_dump(mode="json")
    _assert_no_secret(snapshot)
    run_id = str(uuid4())
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit,
        "config_hash": config_hash(snapshot),
        "config": snapshot,
        "dependency_versions": {"bm25s": None, "text_embedding_model": None, "visual_model": None},
        "evaluation_release_id": None,
        "gold_sha256": None,
        "evaluator_version": None,
        "component_manifest": None,
        "assignment_manifest": None,
        "split_salt": config.dataset.split_salt,
        "resolved_model": None,
        "resolved_provider": None,
        "resolved_endpoint": None,
        "resolved_quantization": None,
        "status": "created",
    }
    _assert_no_secret(manifest)
    destination = Path(run_root) / run_id
    destination.mkdir(parents=True, exist_ok=False)
    output = destination / "manifest.json"
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output
