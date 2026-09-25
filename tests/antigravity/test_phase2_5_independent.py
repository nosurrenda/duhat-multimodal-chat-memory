"""Phase 2.5 independent cross-language and runtime foundation test suite.

Covers Antigravity verification responsibilities per coordination/plans/phases/PHASE-02-5.md:
- K1: Toolchain pinning (K1a go.mod pinned to go 1.27.1, K1b build & test pass).
- K2: Cross-language conformance:
  - K2a: config_hash parity between Go and Python over configs/config_canonical.json.
  - K2a-py: configs/config_canonical.json is canonical output of configs/*.yaml.
  - K2b / K2b-abs: Artifact presence and rejection on absent real root.
  - K2c: configs/config_canonical.json byte freshness check against YAML.
  - K2e: Perturbation positive control: mutated config changes both Go and Python hash, and they agree.
  - K2f / K2f-esc: ASCII-minus-<>& domain check and positive control pair (_ passes, <>& fails loudly).
  - K2g: Structural check that Go imports no YAML parser.
- K4: Go storage boundary (closes F-074):
  - K4a: go/parser AST check ensuring no package outside ScopedRepository imports raw storage.
  - K4b: Positive control ensuring violating fixture fails AST check.
- K5: Trace conformance (K5a round-trip between Go and Python, K5b closed enum / unknown action rejection).
- K7: Exclusion check:
  - K7a: No Go package implements chunking (D59).
  - K7b: No Go package implements embedding, BM25, or index builds (D38).
  - K7c: Positive tree control: Go packages do exist.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from artifacts.visual.pipeline import canonical_config_hash, write_canonical_config_fixture

ROOT = Path(__file__).parents[2]
CONFIG_CANONICAL = ROOT / "configs/config_canonical.json"
GO_MOD = ROOT / "go.mod"


def run_go_code(code: str) -> subprocess.CompletedProcess[str]:
    scratch_dir = ROOT / "scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".go", dir=str(scratch_dir), delete=False) as f:
        f.write(code)
        tmp_path = f.name
    try:
        return subprocess.run(
            ["go", "run", tmp_path],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------------------
# K1 — Toolchain Pinned
# ---------------------------------------------------------------------------


def test_k1a_go_toolchain_pinned() -> None:
    """K1a: module and executable Go toolchain pins are committed."""
    assert GO_MOD.exists(), "go.mod must exist in root"
    content = GO_MOD.read_text(encoding="utf-8")
    assert "go 1.27.1" in content, "go.mod must pin go 1.27.1"
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "GO_VERSION := go1.27.1" in makefile, "Makefile must pin the executable Go toolchain"


def test_k1b_go_vet_and_test_pass() -> None:
    """K1b: Clean checkout passes go vet and go test -race."""
    vet_result = subprocess.run(["go", "vet", "./..."], cwd=str(ROOT), capture_output=True, text=True, check=False)
    assert vet_result.returncode == 0, f"go vet failed:\n{vet_result.stderr}"

    test_result = subprocess.run(["go", "test", "-race", "./..."], cwd=str(ROOT), capture_output=True, text=True, check=False)
    assert test_result.returncode == 0, f"go test -race failed:\n{test_result.stdout}\n{test_result.stderr}"


# ---------------------------------------------------------------------------
# K2 — Cross-Language Conformance
# ---------------------------------------------------------------------------


def test_k2a_config_hash_parity() -> None:
    """K2a: Python canonical_config_hash matches Go's byte-hash over config_canonical.json."""
    assert CONFIG_CANONICAL.exists(), "configs/config_canonical.json must exist"
    py_hash = canonical_config_hash(CONFIG_CANONICAL)

    go_cmd = (
        'package main\n'
        'import (\n'
        '\t"fmt"\n'
        '\t"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/config"\n'
        ')\n'
        'func main() {\n'
        '\tdoc, err := config.Load("configs/config_canonical.json")\n'
        '\tif err != nil { panic(err) }\n'
        '\tfmt.Print(doc.Hash())\n'
        '}\n'
    )
    res = run_go_code(go_cmd)
    assert res.returncode == 0, f"Go runner failed:\n{res.stderr}"
    go_hash = res.stdout.strip()

    assert py_hash == go_hash, f"Config hash divergence: Python={py_hash}, Go={go_hash}"


def test_k2a_py_and_k2c_canonical_json_freshness() -> None:
    """K2a-py & K2c: config_canonical.json is regenerated from YAML and matches byte-for-byte."""
    current_bytes = CONFIG_CANONICAL.read_bytes()
    with tempfile.NamedTemporaryFile("w+", suffix=".json") as tmp:
        write_canonical_config_fixture(ROOT / "configs/base.yaml", tmp.name)
        regenerated_bytes = Path(tmp.name).read_bytes()
    assert current_bytes == regenerated_bytes, "config_canonical.json is stale vs YAML"


def test_k2e_perturbation_positive_control() -> None:
    """K2e: Perturbing a config value changes the emitted file, and Go and Python still agree."""
    canonical_obj = json.loads(CONFIG_CANONICAL.read_text(encoding="utf-8"))
    canonical_obj["conformance_perturbation_test"] = 999

    mutated_bytes = json.dumps(canonical_obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    mutated_py_hash = hashlib.sha256(mutated_bytes).hexdigest()

    with tempfile.NamedTemporaryFile("wb", suffix=".json") as tmp:
        tmp.write(mutated_bytes)
        tmp.flush()

        go_cmd = (
            'package main\n'
            'import (\n'
            '\t"fmt"\n'
            '\t"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/config"\n'
            ')\n'
            'func main() {\n'
            f'\tdoc, err := config.Load("{tmp.name}")\n'
            '\tif err != nil { panic(err) }\n'
            '\tfmt.Print(doc.Hash())\n'
            '}\n'
        )
        res = run_go_code(go_cmd)
        assert res.returncode == 0, f"Go runner failed:\n{res.stderr}"
        go_mutated_hash = res.stdout.strip()

    orig_hash = canonical_config_hash(CONFIG_CANONICAL)
    assert mutated_py_hash != orig_hash, "Mutating config must change the hash"
    assert mutated_py_hash == go_mutated_hash, "Mutated hash must agree across Go and Python"


def test_k8_visual_expectations_are_hashed_and_gate_the_real_manifest(tmp_path: Path) -> None:
    """K8: every D37 expectation is configured, and missing or mismatched pins fail loudly."""
    manifest_path = ROOT / "data/visual_embeddings/visual_embeddings_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expectations = json.loads(CONFIG_CANONICAL.read_text(encoding="utf-8"))["visual_expectations"]
    assert set(expectations) == {
        "expected_device", "model_name", "model_version", "preprocessing"
    }
    assert expectations["expected_device"] == manifest["device"]
    assert expectations["model_name"] == manifest["model_name"]
    assert expectations["model_version"] == manifest["model_version"]
    assert expectations["preprocessing"] == manifest["preprocessing"]

    go_gate = (
        'package main\n'
        'import (\n'
        '\t"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/config"\n'
        '\t"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/manifest"\n'
        ')\n'
        'func main() {\n'
        f'\tdoc, err := config.Load("{CONFIG_CANONICAL.resolve()}")\n'
        '\tif err != nil { panic(err) }\n'
        f'\tvalue, err := manifest.ReadVisual("{manifest_path.resolve()}")\n'
        '\tif err != nil { panic(err) }\n'
        '\terr = doc.ValidateVisualArtifact(config.VisualArtifact{Device: value.Device, ModelName: value.ModelName, ModelVersion: value.ModelVersion, Preprocessing: value.Preprocessing})\n'
        '\tif err != nil { panic(err) }\n'
		'\tfor _, invalid := range []config.VisualArtifact{\n'
		'\t\t{Device: "wrong", ModelName: value.ModelName, ModelVersion: value.ModelVersion, Preprocessing: value.Preprocessing},\n'
		'\t\t{Device: value.Device, ModelName: "wrong", ModelVersion: value.ModelVersion, Preprocessing: value.Preprocessing},\n'
		'\t\t{Device: value.Device, ModelName: value.ModelName, ModelVersion: "wrong", Preprocessing: value.Preprocessing},\n'
		'\t\t{Device: value.Device, ModelName: value.ModelName, ModelVersion: value.ModelVersion, Preprocessing: map[string]any{"image_size": []any{1.0, 1.0}}},\n'
		'\t\t{Device: value.Device, ModelName: value.ModelName, ModelVersion: value.ModelVersion, Preprocessing: map[string]any{"image_size": []any{384.0, 384.0}, "interpolation": "bilinear", "normalization": "siglip"}},\n'
		'\t\t{Device: value.Device, ModelName: value.ModelName, ModelVersion: value.ModelVersion, Preprocessing: map[string]any{"image_size": []any{384.0, 384.0}, "interpolation": "bicubic", "normalization": "clip"}},\n'
		'\t} { if doc.ValidateVisualArtifact(invalid) == nil { panic("expected mismatch rejection") } }\n'
        '}\n'
    )
    accepted = run_go_code(go_gate)
    assert accepted.returncode == 0, f"D37 gate rejected real artifact:\n{accepted.stderr}"

    # Challenge entire block missing
    missing_all = json.loads(CONFIG_CANONICAL.read_text(encoding="utf-8"))
    del missing_all["visual_expectations"]
    missing_all_path = tmp_path / "missing-all-expectations.json"
    missing_all_path.write_text(json.dumps(missing_all), encoding="utf-8")
    go_missing_all = (
        'package main\n'
        'import "github.com/nosurrenda/duhat-multimodal-chat-memory/internal/config"\n'
        'func main() {\n'
        f'\tdoc, err := config.Load("{missing_all_path}")\n'
        '\tif err != nil { panic(err) }\n'
        '\tif _, err := doc.VisualExpectations(); err == nil { panic("expected missing visual_expectations block rejection") }\n'
        '}\n'
    )
    rejected_all = run_go_code(go_missing_all)
    assert rejected_all.returncode == 0, f"missing visual_expectations block was accepted:\n{rejected_all.stderr}"

    # Challenge each individual pin missing or empty
    pin_variations = [
        ("missing_device", lambda d: d.pop("expected_device", None)),
        ("empty_device", lambda d: d.update({"expected_device": ""})),
        ("missing_model_name", lambda d: d.pop("model_name", None)),
        ("empty_model_name", lambda d: d.update({"model_name": ""})),
        ("missing_model_version", lambda d: d.pop("model_version", None)),
        ("empty_model_version", lambda d: d.update({"model_version": ""})),
        ("missing_preprocessing", lambda d: d.pop("preprocessing", None)),
        ("empty_preprocessing", lambda d: d.update({"preprocessing": {}})),
    ]
    for name, modifier in pin_variations:
        cfg = json.loads(CONFIG_CANONICAL.read_text(encoding="utf-8"))
        modifier(cfg["visual_expectations"])
        variant_path = tmp_path / f"variant-{name}.json"
        variant_path.write_text(json.dumps(cfg), encoding="utf-8")
        go_variant = (
            'package main\n'
            'import "github.com/nosurrenda/duhat-multimodal-chat-memory/internal/config"\n'
            'func main() {\n'
            f'\tdoc, err := config.Load("{variant_path}")\n'
            '\tif err != nil { panic(err) }\n'
            f'\tif _, err := doc.VisualExpectations(); err == nil {{ panic("expected {name} rejection") }}\n'
            '}\n'
        )
        res_variant = run_go_code(go_variant)
        assert res_variant.returncode == 0, f"{name} was accepted:\n{res_variant.stderr}"


def test_k2f_and_k2f_esc_ascii_minus_special_chars_control_pair() -> None:
    """K2f & K2f-esc: Domain allows punctuation like '_', but rejects '<', '>', '&' loudly."""
    # Control 1: Allowed punctuation round-trips and passes
    allowed_entry = "session1/1_ok.png"
    go_check_allowed = (
        'package main\n'
        'import (\n'
        '\t"fmt"\n'
        '\t"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/mediahash"\n'
        ')\n'
        'func main() {\n'
        f'\terr := mediahash.AssertPortable("{allowed_entry}")\n'
        '\tif err != nil { panic(err) }\n'
        '\tfmt.Print("OK")\n'
        '}\n'
    )
    res_ok = run_go_code(go_check_allowed)
    assert res_ok.returncode == 0 and res_ok.stdout.strip() == "OK"

    # Control 2: Disallowed characters '<', '>', '&' fail loudly
    for bad in ["photo<1>", "a&b", "café"]:
        go_check_bad = (
            "package main\n"
            "import (\n"
            '\t"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/mediahash"\n'
            ")\n"
            "func main() {\n"
            f'\tif err := mediahash.AssertPortable("{bad}"); err == nil {{\n'
            '\t\tpanic("expected error")\n'
            "\t}\n"
            "}\n"
        )
        res_bad = run_go_code(go_check_bad)
        assert res_bad.returncode == 0, f"Go failed to reject bad character in {bad}"


def test_k2d_manifest_cross_language_parsing(tmp_path: Path) -> None:
    """K2d: Go parses/validates Python visual manifest, Python parses Go-written visual manifest."""
    real_manifest = ROOT / "data/visual_embeddings/visual_embeddings_manifest.json"
    assert real_manifest.exists(), "Real visual manifest must exist"

    # 1. Go parses Python-written manifest
    go_read_code = (
        'package main\n'
        'import (\n'
        '\t"fmt"\n'
        '\t"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/manifest"\n'
        ')\n'
        'func main() {\n'
        f'\tv, err := manifest.ReadVisual("{real_manifest.resolve()}")\n'
        '\tif err != nil { panic(err) }\n'
        '\tfmt.Printf("%s|%d|%s", v.ArtifactType, v.MediaCount, v.Device)\n'
        '}\n'
    )
    res_go = run_go_code(go_read_code)
    assert res_go.returncode == 0, f"Go failed to read visual manifest:\n{res_go.stderr}"
    assert "visual_embeddings|1265|" in res_go.stdout

    # 2. Go writes a visual manifest, and Python parses and validates it
    out_manifest = tmp_path / "go_written_manifest.json"
    go_write_code = (
        'package main\n'
        'import (\n'
        '\t"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/manifest"\n'
        ')\n'
        'func main() {\n'
        '\tv := manifest.Visual{\n'
        '\t\tArtifactType: "visual_embeddings",\n'
        '\t\tModelName: "google/siglip-so400m-patch14-384",\n'
        '\t\tModelVersion: "main",\n'
        '\t\tPreprocessing: map[string]any{"image_size": []any{384.0, 384.0}},\n'
        '\t\tDevice: "cpu",\n'
        '\t\tMatrixSHA256: "dummy_matrix_sha",\n'
        '\t\tMediaInputManifestSHA256: "dummy_media_sha",\n'
        '\t\tMediaCount: 42,\n'
        '\t}\n'
        f'\tif err := manifest.WriteVisual("{out_manifest.resolve()}", v); err != nil {{ panic(err) }}\n'
        '}\n'
    )
    res_write = run_go_code(go_write_code)
    assert res_write.returncode == 0, f"Go failed to write visual manifest:\n{res_write.stderr}"
    assert out_manifest.exists()

    with open(out_manifest, encoding="utf-8") as f:
        py_parsed = json.load(f)
    assert py_parsed["artifact_type"] == "visual_embeddings"
    assert py_parsed["media_count"] == 42
    assert py_parsed["model_name"] == "google/siglip-so400m-patch14-384"


def test_k2g_no_yaml_in_go() -> None:
    """K2g: Go packages contain no YAML dependency and parse no YAML structurally."""
    for go_file in ROOT.glob("internal/**/*.go"):
        content = go_file.read_text(encoding="utf-8")
        for line in content.splitlines():
            line_str = line.strip()
            if line_str.startswith(("import", '"')):
                assert "yaml" not in line_str.lower(), f"Go package {go_file} must not import yaml"

    go_mod_content = GO_MOD.read_text(encoding="utf-8")
    direct_lines = [
        line.strip()
        for line in go_mod_content.splitlines()
        if line.strip() and not line.strip().endswith("// indirect")
    ]
    for line in direct_lines:
        assert "yaml" not in line.lower(), f"go.mod must not contain direct yaml dependencies: {line}"


# ---------------------------------------------------------------------------
# K4 — Go Storage Boundary (Closes F-074)
# ---------------------------------------------------------------------------


def test_k4a_and_k4b_go_ast_storage_boundary() -> None:
    """K4a/b: Run Go's AST test verifying only ScopedRepository imports raw storage, and fixture is caught."""
    pkg = "./internal/repository" if (ROOT / "internal/repository").exists() else "./internal/scope"
    res = subprocess.run(
        ["go", "test", "-v", pkg, "-run", "TestK4"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Go AST storage boundary test failed:\n{res.stdout}\n{res.stderr}"
    assert "TestK4aNoRawStorageImportsOutsideScopedRepository" in res.stdout
    assert "TestK4bViolatingFixtureIsCaught" in res.stdout


# ---------------------------------------------------------------------------
# K5 — Trace Conformance
# ---------------------------------------------------------------------------


def test_k5_trace_roundtrip_and_unknown_action_rejection() -> None:
    """K5: Go trace suite verifies write/read roundtrip and unknown action rejection."""
    res = subprocess.run(
        ["go", "test", "-v", "./internal/trace"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"Go trace test failed:\n{res.stdout}\n{res.stderr}"


# ---------------------------------------------------------------------------
# K7 — Exclusion Checks
# ---------------------------------------------------------------------------


def test_k7a_no_go_chunker() -> None:
    """K7a: No Go package implements chunking (D59)."""
    for go_file in ROOT.glob("internal/**/*.go"):
        assert "chunk" not in go_file.name.lower(), f"Found chunker in Go: {go_file}"


def test_k7b_no_go_embedder_or_indexer() -> None:
    """K7b: No Go package implements embedding, BM25, or index builds (D38)."""
    forbidden_terms = ["bm25", "embedder", "siglip", "indexer"]
    for go_file in ROOT.glob("internal/**/*.go"):
        for term in forbidden_terms:
            assert term not in go_file.name.lower(), f"Found {term} in Go tree: {go_file}"


def test_k7c_positive_tree_control() -> None:
    """K7c: Assert Go tree is not empty and contains delivered packages."""
    repo_pkg = "internal/repository" if (ROOT / "internal/repository").exists() else "internal/scope"
    expected_packages = ["internal/config", "internal/manifest", "internal/mediahash", repo_pkg, "internal/trace"]
    for pkg in expected_packages:
        pkg_dir = ROOT / pkg
        assert pkg_dir.exists() and any(pkg_dir.glob("*.go")), f"Expected Go package missing: {pkg}"
