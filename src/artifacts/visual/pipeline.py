from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from config.loader import load_config
from artifacts.visual.encoder import SiglipEncoder, VisualEncoder

MEDIA_INPUT_SET_MISMATCH = "MEDIA_INPUT_SET_MISMATCH"
VISUAL_DEVICE_MISMATCH = "VISUAL_DEVICE_MISMATCH"
PHASH_VERSION = "vsf-dct64-v1"


class VisualArtifactMismatch(ValueError):
    """An on-disk visual artifact no longer matches its input or observed model."""


@dataclass(frozen=True)
class MediaRow:
    media_id: str
    channel_id: str
    parent_message_id: str
    content_sha256: str
    storage_object_ref: str


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _media_rows(processed_dir: Path) -> list[MediaRow]:
    rows = [
        MediaRow(
            media_id=row["media_id"],
            channel_id=row["channel_id"],
            parent_message_id=row["parent_message_id"],
            content_sha256=row["content_sha256"],
            storage_object_ref=row["storage_object_ref"],
        )
        for row in _read_jsonl(processed_dir / "media.jsonl")
    ]
    return sorted(rows, key=lambda row: row.media_id)


def media_input_manifest_sha256(rows: Iterable[MediaRow]) -> str:
    """Hash the sorted logical-media identity tuple contract, not raw JSON formatting."""

    payload = [
        [row.media_id, row.content_sha256, row.storage_object_ref]
        for row in sorted(rows, key=lambda item: item.media_id)
    ]
    canonical = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] == 0:
        raise ValueError("encoder must return a non-empty two-dimensional matrix")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError("encoder returned an all-zero visual vector")
    return matrix / norms


def _dct_matrix(size: int) -> np.ndarray:
    indices = np.arange(size)
    matrix = np.cos(np.pi / size * (indices + 0.5)[:, None] * indices[None, :])
    matrix[0] *= np.sqrt(1 / size)
    matrix[1:] *= np.sqrt(2 / size)
    return matrix


def perceptual_hash(path: Path) -> int:
    """Return a stable 64-bit pHash using Pillow and a fixed 32x32 DCT transform."""

    try:
        from PIL import Image
    except ImportError as err:  # pragma: no cover - exercised by a real build environment
        raise RuntimeError("pHash generation requires Pillow") from err
    with Image.open(path) as image:
        pixels = np.asarray(image.convert("L").resize((32, 32)), dtype=np.float64)
    dct = _dct_matrix(32) @ pixels @ _dct_matrix(32).T
    low = dct[:8, :8]
    threshold = np.median(low.ravel()[1:])
    bits = low > threshold
    value = 0
    for bit in bits.ravel():
        value = (value << 1) | int(bit)
    return value


def _hamming(left: int, right: int) -> int:
    return (left ^ right).bit_count()


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[max(left_root, right_root)] = min(left_root, right_root)


def _clusters_and_edges(
    rows: list[MediaRow],
    matrix: np.ndarray,
    phashes: list[int],
    dup_threshold: float,
    phash_threshold: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    union_find = _UnionFind(row.media_id for row in rows)
    similarity = matrix @ matrix.T
    edges: list[dict[str, Any]] = []
    for left in range(len(rows)):
        for right in range(left + 1, len(rows)):
            cosine = float(similarity[left, right])
            distance = _hamming(phashes[left], phashes[right])
            sources = []
            if cosine >= dup_threshold:
                sources.append("cosine")
            if distance <= phash_threshold:
                sources.append("phash")
            if sources:
                union_find.union(rows[left].media_id, rows[right].media_id)
                edges.append(
                    {
                        "left_media_id": rows[left].media_id,
                        "right_media_id": rows[right].media_id,
                        "cosine": cosine,
                        "phash_distance": distance,
                        "sources": sources,
                    }
                )
    members: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        members[union_find.find(row.media_id)].append(row.media_id)
    cluster_id_by_media = {
        media_id: f"visual-cluster-{number:05d}"
        for number, media_ids in enumerate(sorted(members.values(), key=lambda values: min(values)), start=1)
        for media_id in sorted(media_ids)
    }
    clusters = [
        {"media_id": row.media_id, "near_dup_cluster_id": cluster_id_by_media[row.media_id]}
        for row in rows
    ]
    histogram = Counter(cluster_id_by_media.values())
    stats = {
        "cluster_size_histogram": dict(sorted(Counter(histogram.values()).items())),
        "edge_counts": {
            "cosine": sum("cosine" in edge["sources"] for edge in edges),
            "phash": sum("phash" in edge["sources"] for edge in edges),
            "both": sum(len(edge["sources"]) == 2 for edge in edges),
        },
        "singletons": sum(size == 1 for size in histogram.values()),
    }
    return clusters, edges, stats


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8"
    )


def build_visual_embeddings(
    processed_dir: str | Path,
    output_dir: str | Path,
    config_path: str | Path,
    *,
    encoder: VisualEncoder | None = None,
    image_hasher: Callable[[Path], int] = perceptual_hash,
    batch_size: int = 16,
) -> Path:
    """Create visual vectors, duplicate clusters, and their verification manifest."""

    processed = Path(processed_dir)
    output = Path(output_dir)
    rows = _media_rows(processed)
    config = load_config(config_path)
    active_encoder = encoder or SiglipEncoder(model_name=f"google/{config.embeddings.visual_model}")
    output.mkdir(parents=True, exist_ok=True)

    chunk_dir = output / ".embedding_chunks"
    chunk_dir.mkdir(exist_ok=True)
    chunk_paths: list[Path] = []
    phashes: list[int] = []
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        image_paths = [processed / "objects" / row.content_sha256 for row in batch]
        if missing := [path for path in image_paths if not path.is_file()]:
            raise FileNotFoundError(f"missing media object: {missing[0]}")
        chunk_path = chunk_dir / f"{start:06d}.npy"
        if chunk_path.exists():
            encoded = np.load(chunk_path, allow_pickle=False)
        else:
            encoded = _normalize_rows(active_encoder.encode(image_paths))
            # Persist each batch before continuing so the M1 build can resume after an interruption.
            np.save(chunk_path, encoded, allow_pickle=False)
        if encoded.shape[0] != len(batch):
            raise ValueError("encoder output count does not match its image batch")
        chunk_paths.append(chunk_path)
        phashes.extend(image_hasher(path) for path in image_paths)
    matrix = np.vstack([np.load(path, allow_pickle=False) for path in chunk_paths])
    matrix_path = output / "visual_embeddings.npy"
    np.save(matrix_path, matrix, allow_pickle=False)
    index_path = output / "visual_embedding_index.json"
    _write_json(index_path, [row.media_id for row in rows])

    clusters, edges, stats = _clusters_and_edges(
        rows,
        matrix,
        phashes,
        config.dataset.dup_threshold,
        config.dataset.phash_threshold,
    )
    _write_jsonl(output / "near_dup_clusters.jsonl", clusters)
    _write_jsonl(output / "visual_embedding_edges.jsonl", edges)
    _write_json(output / "visual_embedding_stats.json", stats)
    manifest = {
        "artifact_type": "visual_embeddings",
        "model_name": active_encoder.model_name,
        "model_version": active_encoder.model_version,
        "media_count": len(rows),
        "preprocessing": active_encoder.preprocessing,
        "tensor_spec": {
            "dimension": int(matrix.shape[1]),
            "dtype": str(matrix.dtype),
            "l2_normalized": True,
        },
        "device": active_encoder.device,
        "phash_version": PHASH_VERSION,
        "media_input_manifest_sha256": media_input_manifest_sha256(rows),
        "matrix_sha256": _sha256_file(matrix_path),
    }
    _write_json(output / "visual_embeddings_manifest.json", manifest)
    # A second operator may finish the same resumable build first; cleanup is best effort.
    shutil.rmtree(chunk_dir, ignore_errors=True)
    return output


def validate_visual_artifact(
    processed_dir: str | Path,
    artifact_dir: str | Path,
    *,
    model_name: str,
    model_version: str,
    preprocessing: dict[str, object],
    expected_device: str,
) -> dict[str, Any]:
    """Reject an artifact before a downstream phase can consume stale visual state."""

    processed, artifact = Path(processed_dir), Path(artifact_dir)
    manifest = json.loads((artifact / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))
    if manifest["media_input_manifest_sha256"] != media_input_manifest_sha256(_media_rows(processed)):
        raise VisualArtifactMismatch(MEDIA_INPUT_SET_MISMATCH)
    if manifest["model_name"] != model_name or manifest["model_version"] != model_version:
        raise VisualArtifactMismatch("VISUAL_MODEL_MISMATCH")
    if manifest["preprocessing"] != preprocessing:
        raise VisualArtifactMismatch("VISUAL_PREPROCESSING_MISMATCH")
    if manifest["device"] != expected_device:
        raise VisualArtifactMismatch(VISUAL_DEVICE_MISMATCH)
    matrix_path = artifact / "visual_embeddings.npy"
    if manifest["matrix_sha256"] != _sha256_file(matrix_path):
        raise VisualArtifactMismatch("MATRIX_SHA256_MISMATCH")
    matrix = np.load(matrix_path, allow_pickle=False)
    if matrix.shape[0] != manifest["media_count"]:
        raise VisualArtifactMismatch("MEDIA_COUNT_MISMATCH")
    return manifest


def build_early_distractor_sanity(
    processed_dir: str | Path, artifact_dir: str | Path
) -> Path:
    """Report structural similarity pools separately so embedding never reads messages.jsonl."""

    processed, artifact = Path(processed_dir), Path(artifact_dir)
    rows = _media_rows(processed)
    index = json.loads((artifact / "visual_embedding_index.json").read_text(encoding="utf-8"))
    matrix = np.load(artifact / "visual_embeddings.npy", allow_pickle=False)
    positions = {media_id: position for position, media_id in enumerate(index)}
    messages = {row["message_id"]: row for row in _read_jsonl(processed / "messages.jsonl")}
    pools: dict[str, list[float]] = {"intra_session": [], "same_sender_cross_session": [], "cross_channel": []}
    for left, left_media in enumerate(rows):
        left_message = messages[left_media.parent_message_id]
        for right in range(left + 1, len(rows)):
            right_media = rows[right]
            right_message = messages[right_media.parent_message_id]
            score = float(matrix[positions[left_media.media_id]] @ matrix[positions[right_media.media_id]])
            if left_media.channel_id != right_media.channel_id:
                pools["cross_channel"].append(score)
            elif left_message["session_index"] == right_message["session_index"]:
                pools["intra_session"].append(score)
            elif left_message["sender_id"] == right_message["sender_id"]:
                pools["same_sender_cross_session"].append(score)
    report_pools = {}
    for name, values in pools.items():
        counts = {str(threshold): sum(score >= threshold for score in values) for threshold in (0.7, 0.8, 0.9)}
        report_pools[name] = {
            "pair_count": len(values),
            "at_or_above": counts,
            "histogram": np.histogram(values, bins=20, range=(-1, 1))[0].tolist(),
            "warning": "EMPTY_STRUCTURAL_POOL" if not any(counts.values()) else None,
        }
    report = {
        "pools": report_pools,
        "interpretation": "Structural proxies only; this report cannot establish D20 sufficiency.",
    }
    destination = artifact / "early_distractor_sanity.json"
    _write_json(destination, report)
    return destination


def write_canonical_config_fixture(config_path: str | Path, destination: str | Path) -> Path:
    """Emit the bytes tested by Y9c after Python has parsed and canonicalized YAML."""

    config = load_config(config_path).model_dump(mode="json")
    from config.hashing import _canonicalize

    payload = json.dumps(_canonicalize(config), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    # This file is the exact hash input for the stdlib-only Go conformance spike.
    path.write_text(payload, encoding="utf-8")
    return path


def canonical_config_hash(path: str | Path) -> str:
    """Hash the committed canonical fixture without reparsing configuration syntax."""

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
