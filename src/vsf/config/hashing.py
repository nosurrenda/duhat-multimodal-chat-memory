import hashlib
import json
from typing import Any

VOLATILE_FIELDS = {"output_path", "run_id", "created_at", "timestamp"}


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _canonicalize(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_FIELDS
        }
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, float):
        return format(value, ".12g")
    return value


def config_hash(config: dict[str, Any]) -> str:
    """Hash all behavior-affecting configuration with stable serialization."""
    canonical = _canonicalize(config)
    payload = json.dumps(canonical, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
