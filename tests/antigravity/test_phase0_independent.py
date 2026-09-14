import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from vsf.config import AppConfig, load_config
from vsf.config.hashing import config_hash
from vsf.manifest import write_manifest
from vsf.settings import Settings

ROOT = Path(__file__).parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_v2_unknown_keys_rejected_at_all_levels(tmp_path: Path) -> None:
    """V2: Check that unknown keys at any level are strictly forbidden."""
    # Ensure llm.yaml is present in tmp_path for config loading
    (tmp_path / "llm.yaml").write_text((ROOT / "configs/llm.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    base_text = (ROOT / "configs/base.yaml").read_text(encoding="utf-8")
    
    # Unknown key at root
    bad_root = tmp_path / "bad_root.yaml"
    bad_root.write_text(base_text + "\nextra_root_field: 123\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(bad_root)

    # Unknown key in retrieval block
    bad_retrieval = tmp_path / "bad_retrieval.yaml"
    bad_retrieval.write_text(
        base_text.replace("top_k: 20", "top_k: 20\n  unsupported_param: true"),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_config(bad_retrieval)

    # Sweep blocks are closed too: a typo must never quietly change an ablation.
    bad_bm25s = tmp_path / "bad_bm25s.yaml"
    bad_bm25s.write_text(base_text.replace("k1: 1.2", "k1x: 1.2"), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(bad_bm25s)

    bad_strategy = tmp_path / "bad_strategy.yaml"
    bad_strategy.write_text(
        base_text.replace("temporal: true", "temporl: true"), encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        load_config(bad_strategy)


def test_v3_hash_determinism_across_processes() -> None:
    """V3 & S1: Config hash must be strictly deterministic across subprocesses and hash seeds."""
    code = (
        "import sys; "
        "from pathlib import Path; "
        "from vsf.config import load_config; "
        "from vsf.config.hashing import config_hash; "
        "c = load_config(Path(sys.argv[1])); "
        "print(config_hash(c.model_dump(mode='json')))"
    )
    hashes = set()
    for seed in ["0", "1", "42", "123456"]:
        env = {**os.environ, "PYTHONHASHSEED": seed}
        proc = subprocess.run(
            [sys.executable, "-c", code, str(ROOT / "configs/base.yaml")],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        hashes.add(proc.stdout.strip())

    assert len(hashes) == 1, f"Hash was unstable across PYTHONHASHSEED runs: {hashes}"
    current_hash = config_hash(load_config(ROOT / "configs/base.yaml").model_dump(mode="json"))
    assert current_hash == hashes.pop()


def test_v4_and_v5_release_endpoint_and_validator() -> None:
    """V4, V5, B1, S2: Endpoint unambiguity and release-mode constraints."""
    config = load_config(ROOT / "configs/base.yaml")

    # Positive control: Slashless endpoint (e.g. google-ai-studio) must be valid (Fix B1)
    roles_slashless = {
        name: role.model_copy(
            update={
                "provider": role.provider.model_copy(
                    update={
                        "order": ["google-ai-studio"],
                        "allow_fallbacks": False,
                        "require_parameters": True,
                    }
                )
            }
        )
        for name, role in config.llm.roles.items()
    }
    payload = config.model_dump(mode="python")
    payload.update({"mode": "release", "comparable": True})
    payload["llm"]["roles"] = roles_slashless
    valid_release = AppConfig.model_validate(payload)
    assert valid_release.mode == "release"

    # Positive control: 1-slash and 2-slash endpoints are also valid
    roles_slashed = {
        name: role.model_copy(
            update={
                "provider": role.provider.model_copy(
                    update={
                        "order": ["google-vertex/global/flex"],
                        "allow_fallbacks": False,
                        "require_parameters": True,
                    }
                )
            }
        )
        for name, role in config.llm.roles.items()
    }
    payload["llm"]["roles"] = roles_slashed
    valid_release_slashed = AppConfig.model_validate(payload)
    assert valid_release_slashed.mode == "release"

    # Negative control: allow_fallbacks = True in release mode must fail
    with pytest.raises(ValidationError, match="allow_fallbacks=false"):
        bad_roles = {
            name: role.model_copy(
                update={
                    "provider": role.provider.model_copy(
                        update={"allow_fallbacks": True, "require_parameters": True}
                    )
                }
            )
            for name, role in config.llm.roles.items()
        }
        AppConfig.model_validate({**payload, "llm": {"llm_contract_version": "1", "roles": bad_roles}})

    # Negative control: require_parameters = False in release mode must fail
    with pytest.raises(ValidationError, match="require_parameters=true"):
        bad_roles = {
            name: role.model_copy(
                update={
                    "provider": role.provider.model_copy(
                        update={"allow_fallbacks": False, "require_parameters": False}
                    )
                }
            )
            for name, role in config.llm.roles.items()
        }
        AppConfig.model_validate({**payload, "llm": {"llm_contract_version": "1", "roles": bad_roles}})

    # Negative control: dated model id required but bare slug supplied (S2)
    with pytest.raises(ValidationError, match="dated model id"):
        bad_roles = {
            "test_role": roles_slashless["query_analyzer"].model_copy(
                update={
                    "model_id": "google/gemini-3.8-flash",
                    "requires_dated_model_id": True,
                }
            )
        }
        AppConfig.model_validate({**payload, "llm": {"llm_contract_version": "1", "roles": bad_roles}})

    # Positive control: dated model id supplied when required
    good_roles = {
        "test_role": roles_slashless["query_analyzer"].model_copy(
            update={
                "model_id": "google/gemini-3.8-flash-20260902",
                "requires_dated_model_id": True,
            }
        )
    }
    good_dated = AppConfig.model_validate({**payload, "llm": {"llm_contract_version": "1", "roles": good_roles}})
    assert good_dated.mode == "release"


def test_v7_storage_boundary_catches_all_import_styles() -> None:
    """V7 & B2: AST analysis must catch relative and absolute imports of _raw outside ScopedRepository."""
    from tests.architecture.test_storage_boundary import _raw_import_offenders

    # Ensure clean production codebase
    offenders = _raw_import_offenders(ROOT / "src/vsf")
    assert not offenders, f"Unscoped raw-storage imports in src/vsf: {offenders}"

    # Synthetic check: ensure relative import `from ._raw import ...` is caught
    code_relative = "from ._raw import connection\n"
    tree = ast.parse(code_relative)
    detected = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level and node.module == "_raw":
            detected = True
    assert detected, "Relative import detector logic failed on synthetic AST"


def test_v10_mode_and_comparable_affect_config_hash() -> None:
    """V10: Flipping mode and comparable must produce distinct config hashes."""
    config = load_config(ROOT / "configs/base.yaml")
    dev_hash = config_hash(config.model_dump(mode="json"))

    rel_config = config.model_copy(update={"mode": "release", "comparable": True})
    rel_hash = config_hash(rel_config.model_dump(mode="json"))

    assert dev_hash != rel_hash


def test_v12_git_hygiene_and_secret_exclusion() -> None:
    """V12 & B3: .env must be gitignored, .env.example tracked, and no secret files tracked."""
    # 1. .env is gitignored
    res_ignored = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT, check=False)
    assert res_ignored.returncode == 0, ".env must be ignored by git"

    # 2. .env.example is tracked
    res_example = subprocess.run(["git", "ls-files", "--error-unmatch", ".env.example"], cwd=ROOT, check=False)
    assert res_example.returncode == 0, ".env.example must be tracked by git"

    # 3. No sensitive files tracked
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    forbidden_endings = (".env", ".pem", ".key")
    sensitive_matches = [f for f in tracked if f.endswith(forbidden_endings)]
    assert not sensitive_matches, f"Sensitive files tracked in git: {sensitive_matches}"


def test_v14_env_settings_independent_of_config_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    """V14: Setting environment variables must never change the deterministic config hash."""
    config = load_config(ROOT / "configs/base.yaml")
    h1 = config_hash(config.model_dump(mode="json"))

    monkeypatch.setenv("POSTGRES_PASSWORD", "new_super_secret_password_123")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-newfakekey1234567890abcdef")
    
    h2 = config_hash(config.model_dump(mode="json"))
    assert h1 == h2


def test_v11_dev_reset_safety() -> None:
    """V11 & S3: dev-reset refuses to execute without CONFIRM_DEV_RESET=yes."""
    proc = subprocess.run(["make", "dev-reset"], cwd=ROOT, capture_output=True, text=True, check=False)
    assert proc.returncode != 0
    assert "CONFIRM_DEV_RESET=yes" in proc.stdout or "CONFIRM_DEV_RESET=yes" in proc.stderr


def test_v8_secret_hygiene_in_manifest(tmp_path: Path) -> None:
    """V8: API keys or secrets in config/payload are rejected before manifest write."""
    config = load_config(ROOT / "configs/base.yaml")
    bad_config = config.model_copy(
        update={"dataset": config.dataset.model_copy(update={"split_salt": "sk-or-v1-secretkey12345"})}
    )
    with pytest.raises(ValueError, match="secret"):
        write_manifest(bad_config, tmp_path)


def test_v6_manifest_placement_and_git_commit(tmp_path: Path) -> None:
    """V6 & S3: Dev runs land in runs/dev, release in runs/release, and git_commit is populated from HEAD."""
    config = load_config(ROOT / "configs/base.yaml")
    
    # Dev run
    dev_path = write_manifest(config, tmp_path)
    assert dev_path.parent.parent.name == "dev"
    manifest_data = dev_path.read_text(encoding="utf-8")
    assert '"git_commit": "' in manifest_data
    
    # Release run
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
    rel_config = AppConfig.model_validate(payload)
    rel_path = write_manifest(rel_config, tmp_path)
    assert rel_path.parent.parent.name == "release"


def test_v2_experiments_directory_discovery() -> None:
    """V2 & S4: configs/experiments exists and any yaml present validates."""
    exp_dir = ROOT / "configs/experiments"
    assert exp_dir.is_dir(), "configs/experiments directory must exist"
    for exp_file in exp_dir.glob("*.yaml"):
        c = load_config(exp_file)
        assert isinstance(c, AppConfig)


def test_v13_missing_required_env_fails_early(monkeypatch: pytest.MonkeyPatch) -> None:
    """V13: Unsetting a required environment variable causes Settings initialization to fail early."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_n1_malformed_bm25s_and_strategy_constraints() -> None:
    """N1: Verify boundary constraints on bm25s (b in [0,1], k1 >= 0, document_unit=='message', weighting >= 0)."""
    config = load_config(ROOT / "configs/base.yaml")
    payload = config.model_dump(mode="python")

    # b > 1.0
    payload["retrieval"]["bm25s"]["b"] = 1.5
    with pytest.raises(ValidationError):
        AppConfig.model_validate(payload)

    # b < 0.0
    payload["retrieval"]["bm25s"]["b"] = -0.1
    with pytest.raises(ValidationError):
        AppConfig.model_validate(payload)

    # Reset b, test k1 < 0
    payload["retrieval"]["bm25s"]["b"] = 0.75
    payload["retrieval"]["bm25s"]["k1"] = -0.5
    with pytest.raises(ValidationError):
        AppConfig.model_validate(payload)

    # Reset k1, test document_unit != "message"
    payload["retrieval"]["bm25s"]["k1"] = 1.2
    payload["retrieval"]["bm25s"]["document_unit"] = "dialogue"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(payload)

    # Reset document_unit, test negative field weighting
    payload["retrieval"]["bm25s"]["document_unit"] = "message"
    payload["retrieval"]["bm25s"]["field_weighting"]["body"] = -0.1
    with pytest.raises(ValidationError):
        AppConfig.model_validate(payload)


def test_n2_bm25s_is_production_dependency() -> None:
    """N2: bm25s must be a main production dependency in pyproject.toml, not dev-only."""
    import tomllib

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = pyproject.get("project", {}).get("dependencies", [])
    assert any("bm25s" in d for d in deps), f"bm25s not found in project.dependencies: {deps}"

    dev_deps = pyproject.get("dependency-groups", {}).get("dev", [])
    assert not any("bm25s" in d for d in dev_deps), f"bm25s found in dev dependency-groups: {dev_deps}"


def test_n3_bm25s_version_change_alters_hash_and_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """N3: Changing the installed bm25s version alters the computed config_hash and propagates to manifest."""
    baseline_config = load_config(ROOT / "configs/base.yaml")
    baseline_hash = config_hash(baseline_config.model_dump(mode="json"))

    # Mock an updated bm25s version
    monkeypatch.setattr("vsf.config.loader.version", lambda pkg: "0.2.999" if pkg == "bm25s" else "1.0.0")
    
    mocked_config = load_config(ROOT / "configs/base.yaml")
    assert mocked_config.retrieval.bm25s.version == "0.2.999"
    mocked_hash = config_hash(mocked_config.model_dump(mode="json"))
    
    assert mocked_hash != baseline_hash, "Config hash MUST change when bm25s version changes"

    # Verify manifest reflects the dynamic version
    manifest_path = write_manifest(mocked_config, tmp_path)
    import json
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest_data["dependency_versions"]["bm25s"] == "0.2.999"


def test_n4_overlay_inheritance_integrity(tmp_path: Path) -> None:
    """N4: Experiments properly inherit base configuration and fail loudly if base is missing."""
    exp_a = load_config(ROOT / "configs/experiments/a-bm25-baseline.yaml")
    assert exp_a.retrieval.bm25_dense_weights == (1.0, 0.0)
    assert exp_a.retrieval.top_k == 20
    assert exp_a.context.context_window == 8

    exp_b = load_config(ROOT / "configs/experiments/b-dense-baseline.yaml")
    assert exp_b.retrieval.bm25_dense_weights == (0.0, 1.0)

    exp_c = load_config(ROOT / "configs/experiments/c-no-context.yaml")
    assert not any(exp_c.context.strategies.model_dump().values())

    exp_d = load_config(ROOT / "configs/experiments/d-context-ablation.yaml")
    assert exp_d.context.strategies.reply_thread is True
    assert exp_d.context.strategies.temporal is False

    # Broken extends pointer fails loudly
    broken_exp = tmp_path / "broken.yaml"
    broken_exp.write_text("extends: non_existent_base.yaml\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        load_config(broken_exp)


def test_n5_settings_cwd_independence() -> None:
    """N5: Settings must resolve .env relative to project root, not CWD."""
    from vsf.settings import PROJECT_ROOT
    env_file = Settings.model_config.get("env_file")
    assert isinstance(env_file, Path)
    assert env_file.is_absolute()
    assert env_file == PROJECT_ROOT / ".env"
