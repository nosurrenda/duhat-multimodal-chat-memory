from pathlib import Path

import pytest
from pydantic import ValidationError

from vsf.config import load_config
from vsf.settings import Settings

ROOT = Path(__file__).parents[2]


def test_env_example_contains_only_expected_settings_keys() -> None:
    example_keys = {
        line.split("=", 1)[0]
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    assert example_keys == {field.upper() for field in Settings.model_fields}


def test_missing_env_fails_early(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in Settings.model_fields:
        monkeypatch.delenv(key.upper(), raising=False)
    with pytest.raises(ValidationError, match="openrouter_api_key"):
        Settings(_env_file=None)


def test_env_fields_cannot_override_hashed_config() -> None:
    config = load_config(ROOT / "configs/base.yaml")
    assert not set(Settings.model_fields).intersection(config.model_dump())


def test_env_is_ignored_by_git() -> None:
    assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
