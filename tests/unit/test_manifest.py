import json
from importlib.metadata import version
from pathlib import Path

import pytest

from vsf.config import load_config
from vsf.config.schema import AppConfig
from vsf.manifest import write_manifest

ROOT = Path(__file__).parents[2]


def test_manifest_writes_required_placeholders(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs/base.yaml")
    output = write_manifest(config, tmp_path)
    content = output.read_text(encoding="utf-8")
    assert '"evaluation_release_id": null' in content
    assert '"resolved_endpoint": null' in content
    assert '"git_commit": "' in content
    assert output.parent.parent.name == "dev"
    assert json.loads(content)["dependency_versions"]["bm25s"] == version("bm25s")


def test_manifest_rejects_secret_values(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs/base.yaml")
    changed = config.model_copy(update={"dataset": config.dataset.model_copy(update={"split_salt": "or-secret-value-1234567890"})})
    with pytest.raises(ValueError, match="secret"):
        write_manifest(changed, tmp_path)


def test_release_manifest_uses_release_directory(tmp_path: Path) -> None:
    config = load_config(ROOT / "configs/base.yaml")
    roles = {
        name: role.model_copy(
            update={
                "provider": role.provider.model_copy(
                    update={"allow_fallbacks": False, "require_parameters": True}
                )
            }
        )
        for name, role in config.llm.roles.items()
    }
    payload = config.model_dump(mode="python")
    payload.update({"mode": "release", "comparable": True})
    payload["llm"]["roles"] = roles
    output = write_manifest(AppConfig.model_validate(payload), tmp_path)
    assert output.parent.parent.name == "release"
