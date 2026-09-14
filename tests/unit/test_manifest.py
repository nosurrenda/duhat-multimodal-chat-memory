from pathlib import Path

import pytest

from vsf.config import load_config
from vsf.manifest import write_manifest

ROOT = Path(__file__).parents[2]


def test_manifest_writes_required_placeholders(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs/base.yaml")
    output = write_manifest(config, tmp_path)
    content = output.read_text(encoding="utf-8")
    assert '"evaluation_release_id": null' in content
    assert '"resolved_endpoint": null' in content


def test_manifest_rejects_secret_values(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs/base.yaml")
    changed = config.model_copy(update={"dataset": config.dataset.model_copy(update={"split_salt": "or-secret-value-1234567890"})})
    with pytest.raises(ValueError, match="secret"):
        write_manifest(changed, tmp_path)
