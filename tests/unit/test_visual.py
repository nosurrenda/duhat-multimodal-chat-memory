from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from config import load_config
from artifacts.visual.pipeline import (
    MEDIA_INPUT_SET_MISMATCH,
    PHASH_VERSION,
    VISUAL_DEVICE_MISMATCH,
    VisualArtifactMismatch,
    build_early_distractor_sanity,
    build_visual_embeddings,
    canonical_config_hash,
    media_input_manifest_sha256,
    validate_visual_artifact,
    write_canonical_config_fixture,
)

ROOT = Path(__file__).parents[2]


class FakeEncoder:
    model_name: ClassVar[str] = "google/siglip2-base-patch16-384"
    model_version: ClassVar[str] = "test-weights-sha256"
    preprocessing: ClassVar[dict[str, object]] = {
        "image_size": [384, 384],
        "interpolation": "bicubic",
        "normalization": "siglip",
    }
    device: ClassVar[str] = "cpu"

    def encode(self, image_paths: list[Path]) -> np.ndarray:
        return np.asarray(
            [[float(path.read_bytes()[0]), float(index + 1), 1.0] for index, path in enumerate(image_paths)],
            dtype=np.float32,
        )


def _copy_config(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text((ROOT / "configs/base.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    (destination.parent / "llm.yaml").write_text(
        (ROOT / "configs/llm.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return destination


def _processed(tmp_path: Path) -> tuple[Path, Path]:
    processed = tmp_path / "processed"
    (processed / "objects").mkdir(parents=True)
    rows = []
    messages = []
    for index, (channel, session, sender, byte) in enumerate(
        (("alpha", 1, "a", 5), ("alpha", 1, "b", 5), ("alpha", 2, "a", 9), ("beta", 1, "c", 12))
    ):
        content = bytes([byte, index])
        digest = hashlib.sha256(content).hexdigest()
        (processed / "objects" / digest).write_bytes(content)
        message_id = f"{channel}:session{session}:{index}"
        rows.append(
            {
                "media_id": f"{message_id}:image.png",
                "channel_id": channel,
                "parent_message_id": message_id,
                "content_sha256": digest,
                "storage_object_ref": f"sha256/{digest}",
            }
        )
        messages.append({"message_id": message_id, "session_index": session, "sender_id": sender})
    (processed / "media.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    (processed / "messages.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in messages), encoding="utf-8"
    )
    return processed, _copy_config(tmp_path / "configs/base.yaml")


def test_visual_artifact_contract_and_sanity_report(tmp_path: Path) -> None:
    processed, config = _processed(tmp_path)
    artifact = build_visual_embeddings(
        processed, tmp_path / "artifact", config, encoder=FakeEncoder(), image_hasher=lambda path: path.read_bytes()[0]
    )
    manifest = json.loads((artifact / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))
    matrix = np.load(artifact / "visual_embeddings.npy", allow_pickle=False)
    index = json.loads((artifact / "visual_embedding_index.json").read_text(encoding="utf-8"))
    assert manifest["media_count"] == 4
    assert manifest["phash_version"] == PHASH_VERSION
    assert manifest["matrix_sha256"] == hashlib.sha256((artifact / "visual_embeddings.npy").read_bytes()).hexdigest()
    assert matrix.shape == (4, 3)
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0)
    assert len(index) == 4
    assert len((artifact / "near_dup_clusters.jsonl").read_text(encoding="utf-8").splitlines()) == 4
    assert build_early_distractor_sanity(processed, artifact).is_file()
    assert json.loads((artifact / "early_distractor_sanity.json").read_text(encoding="utf-8"))["pools"]


def test_embedding_build_never_opens_messages_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    processed, config = _processed(tmp_path)
    messages_path = processed / "messages.jsonl"
    original_read_text = Path.read_text

    def refuse_message_read(path: Path, *args: object, **kwargs: object) -> str:
        if path == messages_path:
            raise AssertionError("embedding path must not read messages.jsonl")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", refuse_message_read)
    build_visual_embeddings(
        processed, tmp_path / "artifact", config, encoder=FakeEncoder(), image_hasher=lambda path: path.read_bytes()[0]
    )


def test_cluster_threshold_is_read_from_config(tmp_path: Path) -> None:
    processed, config = _processed(tmp_path)
    class ThresholdEncoder(FakeEncoder):
        def encode(self, image_paths: list[Path]) -> np.ndarray:
            return np.eye(len(image_paths), dtype=np.float32)

    image_hasher = lambda path: int.from_bytes(hashlib.sha256(path.read_bytes()).digest()[:8], "big")
    strict = build_visual_embeddings(
        processed, tmp_path / "strict", config, encoder=ThresholdEncoder(), image_hasher=image_hasher
    )
    relaxed_config = config.with_name("relaxed.yaml")
    relaxed_config.write_text(
        config.read_text(encoding="utf-8").replace("dup_threshold: 0.95", "dup_threshold: 0.0"),
        encoding="utf-8",
    )
    relaxed = build_visual_embeddings(
        processed, tmp_path / "relaxed", relaxed_config, encoder=ThresholdEncoder(), image_hasher=image_hasher
    )
    assert (strict / "near_dup_clusters.jsonl").read_bytes() != (
        relaxed / "near_dup_clusters.jsonl"
    ).read_bytes()


def test_visual_artifact_rejects_changed_media_set(tmp_path: Path) -> None:
    processed, config = _processed(tmp_path)
    artifact = build_visual_embeddings(
        processed, tmp_path / "artifact", config, encoder=FakeEncoder(), image_hasher=lambda path: path.read_bytes()[0]
    )
    rows = (processed / "media.jsonl").read_text(encoding="utf-8").splitlines()
    (processed / "media.jsonl").write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(VisualArtifactMismatch, match=MEDIA_INPUT_SET_MISMATCH):
        validate_visual_artifact(
            processed,
            artifact,
            model_name=FakeEncoder.model_name,
            model_version=FakeEncoder.model_version,
            preprocessing=FakeEncoder.preprocessing,
            expected_device=FakeEncoder.device,
        )


def test_visual_artifact_rejects_changed_device(tmp_path: Path) -> None:
    processed, config = _processed(tmp_path)
    artifact = build_visual_embeddings(
        processed, tmp_path / "artifact", config, encoder=FakeEncoder(), image_hasher=lambda path: path.read_bytes()[0]
    )
    assert validate_visual_artifact(
        processed,
        artifact,
        model_name=FakeEncoder.model_name,
        model_version=FakeEncoder.model_version,
        preprocessing=FakeEncoder.preprocessing,
        expected_device=FakeEncoder.device,
    )["device"] == FakeEncoder.device

    manifest_path = artifact / "visual_embeddings_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["device"] = "mps"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(VisualArtifactMismatch, match=VISUAL_DEVICE_MISMATCH):
        validate_visual_artifact(
            processed,
            artifact,
            model_name=FakeEncoder.model_name,
            model_version=FakeEncoder.model_version,
            preprocessing=FakeEncoder.preprocessing,
            expected_device=FakeEncoder.device,
        )


def test_canonical_fixture_hash_is_stable(tmp_path: Path) -> None:
    fixture = write_canonical_config_fixture(ROOT / "configs/base.yaml", tmp_path / "config.json")
    config = load_config(ROOT / "configs/base.yaml")
    assert canonical_config_hash(fixture) == hashlib.sha256(fixture.read_bytes()).hexdigest()
    assert media_input_manifest_sha256([]) == hashlib.sha256(b"[]").hexdigest()
    assert config.embeddings.visual_model == "siglip2-base-patch16-384"
