from pathlib import Path

import pytest
from pydantic import ValidationError

from vsf.config import load_config
from vsf.config.hashing import config_hash

ROOT = Path(__file__).parents[2]


def test_base_config_loads() -> None:
    config = load_config(ROOT / "configs/base.yaml")
    assert config.scope.default_deny is True


def test_hash_is_stable_and_mode_is_semantic() -> None:
    config = load_config(ROOT / "configs/base.yaml")
    first = config_hash(config.model_dump(mode="json"))
    second = config_hash(config.model_dump(mode="json"))
    release = config.model_copy(update={"mode": "release", "comparable": True})
    assert first == second
    assert first != config_hash(release.model_dump(mode="json"))


def test_unknown_config_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("unknown: true\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)
