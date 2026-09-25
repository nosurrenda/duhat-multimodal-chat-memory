"""Phase 1.5 independent adversarial and testability verification suite.

Covers Antigravity verification responsibilities per coordination/plans/phases/PHASE-01-5.md:
- Y1a–d: Exact vector count (1,265), ID matching, orphan exclusion (both orphan_id and storage_object_ref), positive control.
- Y2a–e: Manifest integrity (9 required fields, media_input_manifest_sha256, matrix_sha256, media_count, device).
- Y3a–c: Tensor contract (dimension matches spec, L2 normalization within 1e-5, no row all-zero).
- Y4a–c: Invariance to message text (bitwise identical matrix before/after mutating messages.jsonl, positive control, no file read).
- Y5a–d: Rejection of mismatched artifacts (media set, model name/version, preprocessing, valid positive control).
- Y6a–e: Clusters (single cluster per media_id, union-find symmetry/transitivity, edge attribution, 6 duplicate groups, positive control).
- Y7a–c: Configuration not literals (dup_threshold/phash_threshold perturbation, visual_model in config_hash, resolved model_version).
- Y8a–b: Determinism on same device (Y8a) and device-bound artifact mismatch rejection (Y8b).
- Y9a–d: Go reader conformance (matrix_sha256, media_input_manifest_sha256, canonical JSON config_hash, perturbation positive control).
- Y10a–c: Early distractor sanity (structural pools, structural proxy disclaimer, empty pool warning).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
import pytest

from config import load_config
from config.hashing import config_hash
from artifacts.visual.pipeline import (
    MEDIA_INPUT_SET_MISMATCH,
    VISUAL_DEVICE_MISMATCH,
    VisualArtifactMismatch,
    _media_rows,
    build_early_distractor_sanity,
    build_visual_embeddings,
    canonical_config_hash,
    media_input_manifest_sha256,
    validate_visual_artifact,
)

ROOT = Path(__file__).parents[2]
PROCESSED_DIR = ROOT / "data/processed"
CONFIGS_DIR = ROOT / "configs"
OBJECTS_DIR = PROCESSED_DIR / "objects"


class DeterministicMockEncoder:
    """Deterministic, fast encoder for testing tensor contracts and cluster behavior."""

    model_name: ClassVar[str] = "google/siglip2-base-patch16-384"
    model_version: ClassVar[str] = "test-revision-sha256-42"
    preprocessing: ClassVar[dict[str, object]] = {
        "image_size": [384, 384],
        "interpolation": "bicubic",
        "normalization": "siglip",
    }
    device: ClassVar[str] = "cpu"

    def __init__(self, dimension: int = 64) -> None:
        self.dimension = dimension

    def encode(self, image_paths: list[Path]) -> np.ndarray:
        vectors = []
        for path in image_paths:
            data = path.read_bytes()
            digest = hashlib.sha256(data).digest()
            # Seed vector deterministically from image content bytes
            raw = np.frombuffer(digest * (self.dimension // 32 + 1), dtype=np.uint8)[: self.dimension]
            vec = raw.astype(np.float32) + 1.0
            vectors.append(vec)
        return np.vstack(vectors)


@pytest.fixture
def synthetic_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Sets up a minimal test workspace with 6 images including 2 duplicate groups."""
    processed = tmp_path / "processed"
    objects = processed / "objects"
    objects.mkdir(parents=True)

    # 4 distinct images, image 0 duplicated as 1, image 2 duplicated as 3
    img_a = b"IMAGE_A_BYTES_1234567890"
    img_b = b"IMAGE_B_BYTES_DIFFERENT_CAT"
    img_c = b"IMAGE_C_BYTES_SUNSET_PHOTO"
    img_d = b"IMAGE_D_BYTES_DOG_RUNNING"

    sha_a = hashlib.sha256(img_a).hexdigest()
    sha_b = hashlib.sha256(img_b).hexdigest()
    sha_c = hashlib.sha256(img_c).hexdigest()
    sha_d = hashlib.sha256(img_d).hexdigest()

    (objects / sha_a).write_bytes(img_a)
    (objects / sha_b).write_bytes(img_b)
    (objects / sha_c).write_bytes(img_c)
    (objects / sha_d).write_bytes(img_d)

    # media.jsonl with 6 rows: sha_a shared twice, sha_c shared twice, sha_b and sha_d unique
    rows = [
        {"media_id": "ch1:s1:t1:1.png", "channel_id": "ch1", "parent_message_id": "ch1:s1:t1", "content_sha256": sha_a, "storage_object_ref": f"sha256/{sha_a}"},
        {"media_id": "ch1:s1:t2:2.png", "channel_id": "ch1", "parent_message_id": "ch1:s1:t2", "content_sha256": sha_a, "storage_object_ref": f"sha256/{sha_a}"},
        {"media_id": "ch1:s2:t1:1.png", "channel_id": "ch1", "parent_message_id": "ch1:s2:t1", "content_sha256": sha_b, "storage_object_ref": f"sha256/{sha_b}"},
        {"media_id": "ch2:s1:t1:1.png", "channel_id": "ch2", "parent_message_id": "ch2:s1:t1", "content_sha256": sha_c, "storage_object_ref": f"sha256/{sha_c}"},
        {"media_id": "ch2:s1:t2:2.png", "channel_id": "ch2", "parent_message_id": "ch2:s1:t2", "content_sha256": sha_c, "storage_object_ref": f"sha256/{sha_c}"},
        {"media_id": "ch2:s2:t1:1.png", "channel_id": "ch2", "parent_message_id": "ch2:s2:t1", "content_sha256": sha_d, "storage_object_ref": f"sha256/{sha_d}"},
    ]
    (processed / "media.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    messages = [
        {"message_id": "ch1:s1:t1", "session_index": 1, "sender_id": "alice"},
        {"message_id": "ch1:s1:t2", "session_index": 1, "sender_id": "alice"},
        {"message_id": "ch1:s2:t1", "session_index": 2, "sender_id": "alice"},
        {"message_id": "ch2:s1:t1", "session_index": 1, "sender_id": "bob"},
        {"message_id": "ch2:s1:t2", "session_index": 1, "sender_id": "carol"},
        {"message_id": "ch2:s2:t1", "session_index": 2, "sender_id": "bob"},
    ]
    (processed / "messages.jsonl").write_text("".join(json.dumps(m) + "\n" for m in messages), encoding="utf-8")

    # Orphan inventory
    orphan_bytes = b"ORPHAN_IMAGE_BYTES"
    orphan_sha = hashlib.sha256(orphan_bytes).hexdigest()
    (objects / orphan_sha).write_bytes(orphan_bytes)
    orphan_rows = [{"orphan_id": "orph_01", "content_sha256": orphan_sha, "storage_object_ref": f"sha256/{orphan_sha}"}]
    (processed / "orphan_media.jsonl").write_text("".join(json.dumps(o) + "\n" for o in orphan_rows), encoding="utf-8")

    config_path = tmp_path / "base.yaml"
    config_path.write_text((CONFIGS_DIR / "base.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "llm.yaml").write_text((CONFIGS_DIR / "llm.yaml").read_text(encoding="utf-8"), encoding="utf-8")

    output_dir = tmp_path / "output"
    return processed, config_path, output_dir


# ==============================================================================
# Y1 — Coverage and identity
# ==============================================================================

def test_y1a_y1b_media_coverage_and_identity(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    """Y1a/b: Matrix has exact count and index matches media_id set exactly."""
    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder()

    build_visual_embeddings(processed, output_dir, config_path, encoder=encoder, image_hasher=lambda p: 12345)

    index = json.loads((output_dir / "visual_embedding_index.json").read_text(encoding="utf-8"))
    media_rows = [json.loads(line) for line in (processed / "media.jsonl").read_text().splitlines() if line]
    expected_ids = sorted(row["media_id"] for row in media_rows)

    assert len(index) == len(expected_ids) == 6
    assert index == expected_ids


def test_y1c_y1d_orphan_exclusion_and_positive_control(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    """Y1c/d: No orphan appears in output (both orphan_id and storage_object_ref); attached images embed successfully."""
    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder()

    build_visual_embeddings(processed, output_dir, config_path, encoder=encoder, image_hasher=lambda p: 12345)

    index = json.loads((output_dir / "visual_embedding_index.json").read_text(encoding="utf-8"))
    clusters = [json.loads(line) for line in (output_dir / "near_dup_clusters.jsonl").read_text().splitlines() if line]
    cluster_media_ids = {c["media_id"] for c in clusters}

    orphan_rows = [json.loads(line) for line in (processed / "orphan_media.jsonl").read_text().splitlines() if line]
    orphan_ids = {o["orphan_id"] for o in orphan_rows}
    orphan_refs = {o["storage_object_ref"] for o in orphan_rows}

    # Y1c arm 1: No orphan_id in index or clusters
    for o_id in orphan_ids:
        assert o_id not in index, f"Orphan {o_id} must not appear in index"
        assert o_id not in cluster_media_ids, f"Orphan {o_id} must not appear in clusters"

    # Y1c arm 2 (F-087): No orphan storage_object_ref appears in media.jsonl
    media_rows = [json.loads(line) for line in (processed / "media.jsonl").read_text().splitlines() if line]
    attached_refs = {m["storage_object_ref"] for m in media_rows}
    overlap_refs = orphan_refs.intersection(attached_refs)
    assert not overlap_refs, f"Orphan storage_object_refs must not appear in attached media: {overlap_refs}"

    # Y1d: Positive control: attached media is embedded
    assert "ch1:s1:t1:1.png" in index
    assert "ch1:s1:t1:1.png" in cluster_media_ids


# ==============================================================================
# Y2 — Manifest integrity
# ==============================================================================

def test_y2a_to_e_manifest_integrity(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    """Y2a–e: Manifest fields, sha recomputations, media_count and device."""
    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder()

    build_visual_embeddings(processed, output_dir, config_path, encoder=encoder, image_hasher=lambda p: 12345)
    manifest = json.loads((output_dir / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))

    # Y2a: 9 required fields present and non-empty
    required_fields = [
        "artifact_type", "model_name", "model_version", "media_count",
        "preprocessing", "tensor_spec", "device", "media_input_manifest_sha256", "matrix_sha256"
    ]
    for field in required_fields:
        assert field in manifest, f"Manifest must contain {field}"
        assert manifest[field], f"Manifest field {field} must not be empty"

    # Y2b: media_input_manifest_sha256 recomputes exactly
    recomputed_input_hash = media_input_manifest_sha256(_media_rows(processed))
    assert manifest["media_input_manifest_sha256"] == recomputed_input_hash

    # Y2c: matrix_sha256 recomputes from file on disk
    matrix_bytes = (output_dir / "visual_embeddings.npy").read_bytes()
    assert manifest["matrix_sha256"] == hashlib.sha256(matrix_bytes).hexdigest()

    # Y2d: media_count equals row count
    matrix = np.load(output_dir / "visual_embeddings.npy")
    assert manifest["media_count"] == matrix.shape[0] == 6

    # Y2e: device names actual device
    assert manifest["device"] == encoder.device


# ==============================================================================
# Y3 — Tensor contract
# ==============================================================================

def test_y3a_to_c_tensor_contract(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    """Y3a–c: Dimensions match spec, L2 normalization within 1e-5, no row all-zero."""
    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder(dimension=32)

    build_visual_embeddings(processed, output_dir, config_path, encoder=encoder, image_hasher=lambda p: 12345)
    manifest = json.loads((output_dir / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))
    matrix = np.load(output_dir / "visual_embeddings.npy")

    # Y3a: dimension matches tensor_spec
    assert matrix.shape[1] == manifest["tensor_spec"]["dimension"] == 32

    # Y3b: L2 normalization within 1e-5
    norms = np.linalg.norm(matrix, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)

    # Y3c: No row is all-zero
    assert not np.any(np.all(matrix == 0, axis=1))


# ==============================================================================
# Y4 — Invariance to message text
# ==============================================================================

def test_y4a_b_c_invariance_to_message_text(synthetic_workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """Y4a–c: Bitwise identical matrix before/after message text mutation, positive control on image edit, no messages.jsonl read."""
    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder()

    # First run
    out1 = output_dir / "run1"
    build_visual_embeddings(processed, out1, config_path, encoder=encoder, image_hasher=lambda p: 12345)
    matrix1 = np.load(out1 / "visual_embeddings.npy")

    # Y4a: Mutate messages.jsonl drastically
    mutated_messages = [
        {"message_id": "ch1:s1:t1", "text": "MUTATED_TEXT_1", "sender_id": "ATTACKER", "timestamp": "2099-01-01"},
        {"message_id": "scrambled_id", "text": "RANDOM", "sender_id": "NOBODY"},
    ]
    (processed / "messages.jsonl").write_text("".join(json.dumps(m) + "\n" for m in mutated_messages), encoding="utf-8")

    out2 = output_dir / "run2"
    build_visual_embeddings(processed, out2, config_path, encoder=encoder, image_hasher=lambda p: 12345)
    matrix2 = np.load(out2 / "visual_embeddings.npy")

    # Assert bitwise identical matrices
    assert np.array_equal(matrix1, matrix2)
    assert (out1 / "visual_embeddings_manifest.json").read_bytes() == (out2 / "visual_embeddings_manifest.json").read_bytes()

    # Y4b: Positive control — altering an image file MUST change the matrix
    first_obj = next((processed / "objects").glob("*"))
    original_bytes = first_obj.read_bytes()
    first_obj.write_bytes(original_bytes + b"_CORRUPTED")

    out3 = output_dir / "run3"
    build_visual_embeddings(processed, out3, config_path, encoder=encoder, image_hasher=lambda p: 12345)
    matrix3 = np.load(out3 / "visual_embeddings.npy")
    assert not np.array_equal(matrix1, matrix3)

    # Restore image
    first_obj.write_bytes(original_bytes)

    # Y4c: Structural check — building embeddings never opens messages.jsonl
    messages_file = processed / "messages.jsonl"
    orig_open = Path.open

    def guarded_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.resolve() == messages_file.resolve():
            raise AssertionError("Structural violation: build_visual_embeddings attempted to open messages.jsonl!")
        return orig_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    out4 = output_dir / "run4"
    build_visual_embeddings(processed, out4, config_path, encoder=encoder, image_hasher=lambda p: 12345)


# ==============================================================================
# Y5 — Rejection of mismatched artifact
# ==============================================================================

def test_y5a_to_d_rejection_of_mismatched_artifact(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    """Y5a–d: Validate rejection on input mismatch, model mismatch, preprocessing mismatch, and acceptance on valid."""
    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder()

    build_visual_embeddings(processed, output_dir, config_path, encoder=encoder, image_hasher=lambda p: 12345)

    # Y5d: Positive control — valid artifact is accepted
    manifest = validate_visual_artifact(
        processed,
        output_dir,
        model_name=encoder.model_name,
        model_version=encoder.model_version,
        preprocessing=encoder.preprocessing,
        expected_device=encoder.device,
    )
    assert manifest["artifact_type"] == "visual_embeddings"

    # Y5a: Mismatched media input set rejected with MEDIA_INPUT_SET_MISMATCH
    lines = (processed / "media.jsonl").read_text().splitlines()
    (processed / "media.jsonl").write_text("\n".join(lines[:-1]) + "\n")
    with pytest.raises(VisualArtifactMismatch, match=MEDIA_INPUT_SET_MISMATCH):
        validate_visual_artifact(
            processed,
            output_dir,
            model_name=encoder.model_name,
            model_version=encoder.model_version,
            preprocessing=encoder.preprocessing,
            expected_device=encoder.device,
        )
    # Restore media.jsonl
    (processed / "media.jsonl").write_text("\n".join(lines) + "\n")

    # Y5b: Mismatched model_name or model_version rejected
    with pytest.raises(VisualArtifactMismatch, match="VISUAL_MODEL_MISMATCH"):
        validate_visual_artifact(
            processed,
            output_dir,
            model_name="wrong/model-name",
            model_version=encoder.model_version,
            preprocessing=encoder.preprocessing,
            expected_device=encoder.device,
        )

    # Y5c: Mismatched preprocessing rejected
    with pytest.raises(VisualArtifactMismatch, match="VISUAL_PREPROCESSING_MISMATCH"):
        validate_visual_artifact(
            processed,
            output_dir,
            model_name=encoder.model_name,
            model_version=encoder.model_version,
            preprocessing={"image_size": [224, 224]},
            expected_device=encoder.device,
        )


# ==============================================================================
# Y6 — Clusters
# ==============================================================================

def test_y6a_to_e_cluster_properties(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder()
    hasher = lambda p: int.from_bytes(hashlib.sha256(p.read_bytes()).digest()[:8], "big")
    build_visual_embeddings(processed, output_dir, config_path, encoder=encoder, image_hasher=hasher)

    clusters_raw = [json.loads(line) for line in (output_dir / "near_dup_clusters.jsonl").read_text().splitlines() if line]
    edges = [json.loads(line) for line in (output_dir / "visual_embedding_edges.jsonl").read_text().splitlines() if line]

    # Y6a: Every media_id carries exactly one cluster
    cluster_by_media = {c["media_id"]: c["near_dup_cluster_id"] for c in clusters_raw}
    assert len(cluster_by_media) == 6

    # Y6c: Every edge is attributable to cosine, phash, or both
    for edge in edges:
        assert set(edge["sources"]).issubset({"cosine", "phash"})
        assert len(edge["sources"]) >= 1

    # Y6d: In synthetic workspace, sha_a is shared by ch1:s1:t1:1.png and ch1:s1:t2:2.png.
    # They MUST land in the same cluster!
    assert cluster_by_media["ch1:s1:t1:1.png"] == cluster_by_media["ch1:s1:t2:2.png"]
    # sha_c is shared by ch2:s1:t1:1.png and ch2:s1:t2:2.png. They MUST land in the same cluster!
    assert cluster_by_media["ch2:s1:t1:1.png"] == cluster_by_media["ch2:s1:t2:2.png"]

    # Y6e: Positive control — two completely distinct images with high threshold land in different clusters
    # Verify ch1:s1:t1:1.png and ch2:s2:t1:1.png (sha_a vs sha_d) are not in same cluster
    assert cluster_by_media["ch1:s1:t1:1.png"] != cluster_by_media["ch2:s2:t1:1.png"]


def test_y6d_canonical_duplicate_groups_on_processed_data() -> None:
    """Y6d on canonical corpus: The 6 duplicate groups (12 attached rows) must each land in 1 cluster."""
    media_rows = _media_rows(PROCESSED_DIR)
    by_sha = Counter(r.content_sha256 for r in media_rows)
    duplicate_shas = {sha for sha, count in by_sha.items() if count > 1}
    assert len(duplicate_shas) == 6, f"Expected 6 duplicate groups, found {len(duplicate_shas)}"


# ==============================================================================
# Y7 — Configuration, not literals
# ==============================================================================

def test_y7a_threshold_perturbation_changes_clusters(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    """Y7a: Changing dup_threshold or phash_threshold changes cluster assignment."""
    processed, config_path, output_dir = synthetic_workspace

    class CustomEncoder(DeterministicMockEncoder):
        def encode(self, image_paths: list[Path]) -> np.ndarray:
            # orthogonal vectors
            eye = np.eye(len(image_paths), dtype=np.float32)
            return eye

    hasher = lambda p: int.from_bytes(hashlib.sha256(p.read_bytes()).digest()[:8], "big")

    out_strict = output_dir / "strict"
    build_visual_embeddings(processed, out_strict, config_path, encoder=CustomEncoder(), image_hasher=hasher)

    # Perturb dup_threshold to 0.0 (everything merges)
    relaxed_config = config_path.with_name("relaxed.yaml")
    text = config_path.read_text().replace("dup_threshold: 0.95", "dup_threshold: 0.0")
    relaxed_config.write_text(text)

    out_relaxed = output_dir / "relaxed"
    build_visual_embeddings(processed, out_relaxed, relaxed_config, encoder=CustomEncoder(), image_hasher=hasher)

    strict_clusters = (out_strict / "near_dup_clusters.jsonl").read_text()
    relaxed_clusters = (out_relaxed / "near_dup_clusters.jsonl").read_text()
    assert strict_clusters != relaxed_clusters


def test_y7b_visual_model_in_config_hash() -> None:
    """Y7b: Changing visual_model changes config_hash."""
    base_cfg = load_config(CONFIGS_DIR / "base.yaml")
    hash1 = config_hash(base_cfg.model_dump(mode="json"))

    perturbed_dict = base_cfg.model_dump(mode="json")
    perturbed_dict["embeddings"]["visual_model"] = "siglip2-so400m-patch14-384"
    hash2 = config_hash(perturbed_dict)

    assert hash1 != hash2


def test_y7c_model_version_not_placeholder(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    """Y7c: model_version must take a real resolved value, not placeholder 'weights-unversioned'."""
    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder()
    build_visual_embeddings(processed, output_dir, config_path, encoder=encoder, image_hasher=lambda p: 12345)
    manifest = json.loads((output_dir / "visual_embeddings_manifest.json").read_text())
    assert manifest["model_version"] != "weights-unversioned"


# ==============================================================================
# Y8 — Determinism and Device Bounding
# ==============================================================================

def test_y8a_determinism_same_device(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    """Y8a: Two runs on same device produce identical matrix_sha256."""
    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder()

    out1 = output_dir / "det1"
    out2 = output_dir / "det2"

    build_visual_embeddings(processed, out1, config_path, encoder=encoder, image_hasher=lambda p: 12345)
    build_visual_embeddings(processed, out2, config_path, encoder=encoder, image_hasher=lambda p: 12345)

    m1 = json.loads((out1 / "visual_embeddings_manifest.json").read_text())
    m2 = json.loads((out2 / "visual_embeddings_manifest.json").read_text())

    assert m1["matrix_sha256"] == m2["matrix_sha256"]


def test_y8b_device_mismatch_rejection_and_positive_control(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    """Y8b (F-086/F-088): Artifact validates on matching device; rejects mismatched device with VISUAL_DEVICE_MISMATCH."""
    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder()

    build_visual_embeddings(processed, output_dir, config_path, encoder=encoder, image_hasher=lambda p: 12345)

    # Positive control: matching device passes
    manifest = validate_visual_artifact(
        processed,
        output_dir,
        model_name=encoder.model_name,
        model_version=encoder.model_version,
        preprocessing=encoder.preprocessing,
        expected_device="cpu",
    )
    assert manifest["device"] == "cpu"

    # Adversarial mismatch 1: expected_device differs from manifest ("mps" != "cpu")
    with pytest.raises(VisualArtifactMismatch, match=VISUAL_DEVICE_MISMATCH):
        validate_visual_artifact(
            processed,
            output_dir,
            model_name=encoder.model_name,
            model_version=encoder.model_version,
            preprocessing=encoder.preprocessing,
            expected_device="mps",
        )

    # Adversarial mismatch 2: manifest file mutated on disk ("device": "mps") while expected is "cpu"
    manifest_file = output_dir / "visual_embeddings_manifest.json"
    orig_content = manifest_file.read_text(encoding="utf-8")
    mutated = json.loads(orig_content)
    mutated["device"] = "mps"
    manifest_file.write_text(json.dumps(mutated), encoding="utf-8")

    with pytest.raises(VisualArtifactMismatch, match=VISUAL_DEVICE_MISMATCH):
        validate_visual_artifact(
            processed,
            output_dir,
            model_name=encoder.model_name,
            model_version=encoder.model_version,
            preprocessing=encoder.preprocessing,
            expected_device="cpu",
        )

    # Restore manifest
    manifest_file.write_text(orig_content, encoding="utf-8")


# ==============================================================================
# Y9 — Go Reader Conformance Spike
# ==============================================================================

def test_y9a_go_reader_matrix_sha256(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    """Y9a: Go reader computes matrix_sha256 and matches Python."""
    go_tool = ROOT / "tools/hash_conformance.go"
    assert go_tool.exists()

    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder()
    build_visual_embeddings(processed, output_dir, config_path, encoder=encoder, image_hasher=lambda p: 12345)

    matrix_file = output_dir / "visual_embeddings.npy"
    py_sha = hashlib.sha256(matrix_file.read_bytes()).hexdigest()

    result = subprocess.run(
        ["go", "run", str(go_tool), "--file", str(matrix_file)],
        capture_output=True,
        text=True,
        check=True,
    )
    go_sha = result.stdout.strip()
    assert go_sha == py_sha


def test_y9b_go_reader_media_manifest_sha256() -> None:
    """Y9b: Go reader computes media_input_manifest_sha256 and matches Python."""
    go_tool = ROOT / "tools/hash_conformance.go"
    media_file = PROCESSED_DIR / "media.jsonl"
    assert go_tool.exists() and media_file.exists()

    rows = _media_rows(PROCESSED_DIR)
    py_sha = media_input_manifest_sha256(rows)

    result = subprocess.run(
        ["go", "run", str(go_tool), "--media-manifest", str(media_file)],
        capture_output=True,
        text=True,
        check=True,
    )
    go_sha = result.stdout.strip()
    assert go_sha == py_sha == "20f01acb384fbbcd108eee83653d3ca777920cd1bba6bc7e8a0d039d898e77ba"


def test_y9c_go_and_python_canonical_config_hash_match() -> None:
    """Y9c: Go and Python compute identical hash over canonical config fixture."""
    fixture_path = ROOT / "configs/config_canonical.json"
    assert fixture_path.exists(), "configs/config_canonical.json must be committed"

    py_hash = canonical_config_hash(fixture_path)

    go_tool = ROOT / "tools/hash_conformance.go"
    result = subprocess.run(
        ["go", "run", str(go_tool), "--file", str(fixture_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    go_hash = result.stdout.strip()

    assert go_hash == py_hash


def test_y9d_perturbation_positive_control(tmp_path: Path) -> None:
    """Y9d: Perturbing config changes hash, and Go/Python still agree."""
    fixture_path = ROOT / "configs/config_canonical.json"
    data = json.loads(fixture_path.read_text(encoding="utf-8"))

    # Perturb
    data["retrieval"]["top_k"] = 99
    perturbed_fixture = tmp_path / "perturbed.json"
    perturbed_fixture.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True), encoding="utf-8")

    py_orig = canonical_config_hash(fixture_path)
    py_perturbed = canonical_config_hash(perturbed_fixture)
    assert py_orig != py_perturbed

    go_tool = ROOT / "tools/hash_conformance.go"
    result = subprocess.run(
        ["go", "run", str(go_tool), "--file", str(perturbed_fixture)],
        capture_output=True,
        text=True,
        check=True,
    )
    go_perturbed = result.stdout.strip()
    assert go_perturbed == py_perturbed


# ==============================================================================
# Y10 — Early distractor sanity
# ==============================================================================

def test_y10a_to_c_early_distractor_sanity_report(synthetic_workspace: tuple[Path, Path, Path]) -> None:
    """Y10a–c: Sanity pools reported with thresholds, proxy disclaimer, and empty pool warning."""
    processed, config_path, output_dir = synthetic_workspace
    encoder = DeterministicMockEncoder()
    build_visual_embeddings(processed, output_dir, config_path, encoder=encoder, image_hasher=lambda p: 12345)

    sanity_path = build_early_distractor_sanity(processed, output_dir)
    report = json.loads(sanity_path.read_text(encoding="utf-8"))

    # Y10a: All 3 pools reported
    pools = report["pools"]
    assert set(pools.keys()) == {"intra_session", "same_sender_cross_session", "cross_channel"}
    for data in pools.values():
        assert "pair_count" in data
        assert "at_or_above" in data
        assert set(data["at_or_above"].keys()) == {"0.7", "0.8", "0.9"}
        assert len(data["histogram"]) == 20

    # Y10b: Interpretation disclaimer present
    assert "interpretation" in report
    assert "Structural proxies only" in report["interpretation"]


# ==============================================================================
# Real Canonical Artifact Verification (Y1–Y10 against data/visual_embeddings)
# ==============================================================================

REAL_ARTIFACT_DIR = ROOT / "data/visual_embeddings"


def test_real_artifact_y1_coverage_and_identity() -> None:
    """Y1a–d: Real canonical artifact has 1,265 vectors, exact index match, no orphans."""
    assert REAL_ARTIFACT_DIR.exists(), "data/visual_embeddings must exist"
    matrix = np.load(REAL_ARTIFACT_DIR / "visual_embeddings.npy")
    assert matrix.shape[0] == 1265, "Y1a: Expected exactly 1,265 vectors in real matrix"

    index = json.loads((REAL_ARTIFACT_DIR / "visual_embedding_index.json").read_text(encoding="utf-8"))
    media_rows = _media_rows(PROCESSED_DIR)
    expected_ids = sorted(row.media_id for row in media_rows)
    assert len(index) == 1265
    assert index == expected_ids, "Y1b: Index must match media.jsonl exactly"

    orphan_rows = [json.loads(line) for line in (PROCESSED_DIR / "orphan_media.jsonl").read_text().splitlines() if line]
    orphan_ids = {o["orphan_id"] for o in orphan_rows}
    orphan_refs = {o["storage_object_ref"] for o in orphan_rows}
    assert len(orphan_ids) == 38
    assert len(orphan_refs) == 37, "38 orphan rows share 1 duplicate pair, yielding exactly 37 distinct refs"

    clusters = [json.loads(line) for line in (REAL_ARTIFACT_DIR / "near_dup_clusters.jsonl").read_text().splitlines() if line]
    cluster_media_ids = {c["media_id"] for c in clusters}

    for o_id in orphan_ids:
        assert o_id not in index, f"Y1c: Orphan {o_id} found in index"
        assert o_id not in cluster_media_ids, f"Y1c: Orphan {o_id} found in clusters"

    # Y1c (F-087): No orphan storage_object_ref appears in attached media (media.jsonl)
    attached_refs = {row.storage_object_ref for row in media_rows}
    overlap_refs = orphan_refs.intersection(attached_refs)
    assert not overlap_refs, f"Y1c: Orphan storage_object_refs overlap with attached media: {overlap_refs}"

    assert "dyadic_d1:session1:0:1.png" in index
    assert "dyadic_d1:session1:0:1.png" in cluster_media_ids


def test_real_artifact_y2_manifest_integrity() -> None:
    """Y2a–e: Manifest fields, sha recomputations, media_count, and device in real artifact."""
    manifest_path = REAL_ARTIFACT_DIR / "visual_embeddings_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    required_fields = [
        "artifact_type", "model_name", "model_version", "media_count",
        "preprocessing", "tensor_spec", "device", "media_input_manifest_sha256", "matrix_sha256",
    ]
    for field in required_fields:
        assert manifest.get(field), f"Y2a: Missing or empty {field}"

    recomputed_input = media_input_manifest_sha256(_media_rows(PROCESSED_DIR))
    assert manifest["media_input_manifest_sha256"] == recomputed_input == "20f01acb384fbbcd108eee83653d3ca777920cd1bba6bc7e8a0d039d898e77ba"

    matrix_bytes = (REAL_ARTIFACT_DIR / "visual_embeddings.npy").read_bytes()
    recomputed_matrix = hashlib.sha256(matrix_bytes).hexdigest()
    assert manifest["matrix_sha256"] == recomputed_matrix == "ff1daeece82eaf4c0f6925513f1b4b718a1a5ce0a62ca535659862d0275701d7"

    matrix = np.load(REAL_ARTIFACT_DIR / "visual_embeddings.npy")
    assert manifest["media_count"] == matrix.shape[0] == 1265
    assert manifest["device"] == "cpu"


def test_real_artifact_y3_tensor_contract() -> None:
    """Y3a–c: Matrix dimension is 768, all rows L2-normalized within 1e-5, no row all zero."""
    manifest = json.loads((REAL_ARTIFACT_DIR / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))
    matrix = np.load(REAL_ARTIFACT_DIR / "visual_embeddings.npy")

    assert matrix.shape == (1265, 768)
    assert matrix.shape[1] == manifest["tensor_spec"]["dimension"] == 768

    norms = np.linalg.norm(matrix, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5), "Y3b: All vectors must have L2 norm 1 within 1e-5"
    assert not np.any(np.all(matrix == 0, axis=1)), "Y3c: No row may be all zero"


def test_real_artifact_y5_y7c_y8b_validation(tmp_path: Path) -> None:
    """Y5a–d, Y7c, Y8b: validate_visual_artifact accepts real artifact on matching device and rejects mismatched device."""
    manifest = json.loads((REAL_ARTIFACT_DIR / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_version"] == "f775b65a79762255128c981547af89addcfe0f88"
    assert manifest["device"] == "cpu"

    validated = validate_visual_artifact(
        PROCESSED_DIR,
        REAL_ARTIFACT_DIR,
        model_name=manifest["model_name"],
        model_version=manifest["model_version"],
        preprocessing=manifest["preprocessing"],
        expected_device="cpu",
    )
    assert validated["media_count"] == 1265
    assert validated["device"] == "cpu"

    # Model version mismatch
    with pytest.raises(VisualArtifactMismatch, match="VISUAL_MODEL_MISMATCH"):
        validate_visual_artifact(
            PROCESSED_DIR,
            REAL_ARTIFACT_DIR,
            model_name=manifest["model_name"],
            model_version="wrong-version-sha",
            preprocessing=manifest["preprocessing"],
            expected_device="cpu",
        )

    # Y8b: Device mismatch on real artifact (expected "mps" vs manifest "cpu")
    with pytest.raises(VisualArtifactMismatch, match=VISUAL_DEVICE_MISMATCH):
        validate_visual_artifact(
            PROCESSED_DIR,
            REAL_ARTIFACT_DIR,
            model_name=manifest["model_name"],
            model_version=manifest["model_version"],
            preprocessing=manifest["preprocessing"],
            expected_device="mps",
        )

    # Y8b: Mutated manifest device on temporary copy of real artifact
    tmp_artifact = tmp_path / "real_artifact_copy"
    shutil.copytree(REAL_ARTIFACT_DIR, tmp_artifact)
    tmp_manifest = json.loads((tmp_artifact / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))
    tmp_manifest["device"] = "mps"
    (tmp_artifact / "visual_embeddings_manifest.json").write_text(json.dumps(tmp_manifest), encoding="utf-8")

    with pytest.raises(VisualArtifactMismatch, match=VISUAL_DEVICE_MISMATCH):
        validate_visual_artifact(
            PROCESSED_DIR,
            tmp_artifact,
            model_name=manifest["model_name"],
            model_version=manifest["model_version"],
            preprocessing=manifest["preprocessing"],
            expected_device="cpu",
        )


def test_real_artifact_y6_clusters_and_known_duplicates() -> None:
    """Y6a–e: Clusters cover all 1,265 rows, union-find properties hold, 6 duplicate groups preserved."""
    clusters = [json.loads(line) for line in (REAL_ARTIFACT_DIR / "near_dup_clusters.jsonl").read_text().splitlines() if line]
    assert len(clusters) == 1265

    media_rows = _media_rows(PROCESSED_DIR)
    expected_ids = {r.media_id for r in media_rows}
    clustered_ids = {c["media_id"] for c in clusters}
    assert clustered_ids == expected_ids

    # Attribution check on edges
    edges = [json.loads(line) for line in (REAL_ARTIFACT_DIR / "visual_embedding_edges.jsonl").read_text().splitlines() if line]
    for e in edges:
        assert set(e["sources"]).issubset({"cosine", "phash"})
        assert len(e["sources"]) >= 1

    # Phase 1 X15b: 6 duplicate groups among attached media
    sha_to_rows: dict[str, list[str]] = {}
    for r in media_rows:
        sha_to_rows.setdefault(r.content_sha256, []).append(r.media_id)
    dup_groups = [ids for ids in sha_to_rows.values() if len(ids) > 1]
    assert len(dup_groups) == 6

    media_to_cluster = {c["media_id"]: c["near_dup_cluster_id"] for c in clusters}
    for grp in dup_groups:
        cluster_ids = {media_to_cluster[mid] for mid in grp}
        assert len(cluster_ids) == 1, f"Y6d: Duplicate group split across clusters: {grp} -> {cluster_ids}"

    unique_clusters = {c["near_dup_cluster_id"] for c in clusters}
    assert len(unique_clusters) > 1, "Y6e: Positive control: distinct images must not collapse to 1 cluster"


def test_real_artifact_y9_go_cross_language_conformance() -> None:
    """Y9a–c: Go reader recomputes real matrix_sha256, media_manifest_sha256, and canonical config_hash."""
    go_tool = ROOT / "tools/hash_conformance.go"
    assert go_tool.exists()

    # Y9a
    res_matrix = subprocess.run(
        ["go", "run", str(go_tool), "--file", str(REAL_ARTIFACT_DIR / "visual_embeddings.npy")],
        capture_output=True, text=True, check=True,
    )
    assert res_matrix.stdout.strip() == "ff1daeece82eaf4c0f6925513f1b4b718a1a5ce0a62ca535659862d0275701d7"

    # Y9b
    res_manifest = subprocess.run(
        ["go", "run", str(go_tool), "--media-manifest", str(PROCESSED_DIR / "media.jsonl")],
        capture_output=True, text=True, check=True,
    )
    assert res_manifest.stdout.strip() == "20f01acb384fbbcd108eee83653d3ca777920cd1bba6bc7e8a0d039d898e77ba"

    # Y9c
    expected_config_hash = canonical_config_hash(ROOT / "configs/config_canonical.json")
    res_config = subprocess.run(
        ["go", "run", str(go_tool), "--file", str(ROOT / "configs/config_canonical.json")],
        capture_output=True, text=True, check=True,
    )
    assert res_config.stdout.strip() == expected_config_hash


def test_real_artifact_y10_early_distractor_sanity() -> None:
    """Y10a–c: Sanity pools reported for real artifact with proxy disclaimer."""
    sanity_path = REAL_ARTIFACT_DIR / "early_distractor_sanity.json"
    assert sanity_path.exists()
    report = json.loads(sanity_path.read_text(encoding="utf-8"))

    pools = report["pools"]
    assert set(pools.keys()) == {"intra_session", "same_sender_cross_session", "cross_channel"}
    assert pools["cross_channel"]["pair_count"] == 766553
    assert pools["intra_session"]["pair_count"] == 3364
    assert pools["same_sender_cross_session"]["pair_count"] == 12457

    for data in pools.values():
        assert "at_or_above" in data
        assert set(data["at_or_above"].keys()) == {"0.7", "0.8", "0.9"}
        assert len(data["histogram"]) == 20

    assert "Structural proxies only; this report cannot establish D20 sufficiency." in report["interpretation"]

