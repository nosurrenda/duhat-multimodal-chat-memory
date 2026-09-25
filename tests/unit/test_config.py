from importlib.metadata import version
from pathlib import Path

import pytest
from pydantic import ValidationError

from config import AppConfig, load_config
from config.hashing import config_hash

ROOT = Path(__file__).parents[2]


def test_base_config_loads() -> None:
    config = load_config(ROOT / "configs/base.yaml")
    assert config.scope.default_deny is True
    assert config.visual_expectations.model_name == "google/siglip2-base-patch16-384"
    assert config.visual_expectations.preprocessing.image_size == (384, 384)


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


def test_sweep_blocks_reject_typos_and_invalid_types() -> None:
    config = load_config(ROOT / "configs/base.yaml")
    payload = config.model_dump(mode="python")
    payload["retrieval"]["bm25s"]["k1x"] = 1.2
    with pytest.raises(ValidationError):
        AppConfig.model_validate(payload)

    payload = config.model_dump(mode="python")
    payload["retrieval"]["bm25s"]["k1"] = "banana"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(payload)

    payload = config.model_dump(mode="python")
    payload["context"]["strategies"]["temporl"] = True
    with pytest.raises(ValidationError):
        AppConfig.model_validate(payload)


def test_bm25s_runtime_version_is_in_config_snapshot() -> None:
    config = load_config(ROOT / "configs/base.yaml")
    assert config.retrieval.bm25s.version == version("bm25s")


def test_required_sweep_overlays_load() -> None:
    experiment_dir = ROOT / "configs/experiments"
    names = {path.name for path in experiment_dir.glob("*.yaml")}
    assert names == {
        "a-bm25-baseline.yaml",
        "b-dense-baseline.yaml",
        "c-no-context.yaml",
        "d-context-ablation.yaml",
    }
    for path in experiment_dir.glob("*.yaml"):
        assert isinstance(load_config(path), AppConfig)


def test_release_accepts_slashless_endpoint_tag() -> None:
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
    release = AppConfig.model_validate(payload)
    assert release.mode == "release"


def test_release_rejects_missing_endpoint_and_required_dated_id() -> None:
    config = load_config(ROOT / "configs/base.yaml")
    role = next(iter(config.llm.roles.values()))
    bad_role = role.model_copy(
        update={
            "requires_dated_model_id": True,
            "provider": role.provider.model_copy(
                update={"order": [], "allow_fallbacks": False, "require_parameters": True}
            ),
        }
    )
    with pytest.raises(ValidationError):
        AppConfig.model_validate(
            {
                **config.model_dump(mode="python"),
                "mode": "release",
                "comparable": True,
                "llm": {"llm_contract_version": "1", "roles": {"test": bad_role}},
            }
        )
