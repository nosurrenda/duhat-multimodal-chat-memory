from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from config.hashing import config_hash
from config.schema import AppConfig

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


def _current_git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _verify_capability_artifact(path: str | Path, expected_sha256: str) -> Path:
    """Bind release evidence to an intact, successful capability artifact before publishing a manifest."""
    artifact_path = Path(path)
    try:
        payload = artifact_path.read_bytes()
    except FileNotFoundError as err:
        raise ValueError(f"release manifest requires an existing capability artifact file: '{artifact_path}' not found") from err
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError("capability artifact checksum does not match the release manifest")
    artifact = json.loads(payload)
    if artifact.get("status") == "invalid_for_release" or any(
        probe.get("status") != "pass" for probe in artifact.get("probes", [])
    ):
        raise ValueError("release manifest requires a successful capability artifact")
    return artifact_path


def write_manifest(
    config: AppConfig,
    run_root: str | Path,
    git_commit: str | None = None,
    capability_artifact_path: str | Path | None = None,
    capability_sha256: str | None = None,
) -> Path:
    snapshot = config.model_dump(mode="json")
    _assert_no_secret(snapshot)
    run_id = str(uuid4())
    run_kind = "release" if config.mode == "release" else "dev"
    if run_kind == "release" and (capability_artifact_path is None or capability_sha256 is None):
        raise ValueError("release manifest requires capability artifact path and checksum")
    verified_artifact = (
        _verify_capability_artifact(capability_artifact_path, capability_sha256)
        if capability_artifact_path is not None and capability_sha256 is not None
        else None
    )
    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "git_commit": git_commit or _current_git_commit(),
        "config_hash": config_hash(snapshot),
        "config": snapshot,
        "dependency_versions": {
            "bm25s": snapshot["retrieval"]["bm25s"]["version"],
            "text_embedding_model": None,
            "visual_model": None,
        },
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
        "capability_artifact_path": str(verified_artifact) if verified_artifact else None,
        "capability_sha256": capability_sha256,
        "status": "created",
    }
    _assert_no_secret(manifest)
    destination = Path(run_root) / run_kind / run_id
    destination.mkdir(parents=True, exist_ok=False)
    output = destination / "manifest.json"
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output
