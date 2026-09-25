"""Phase 3 build helpers that consume frozen artifacts without recomputing them."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import psycopg

from artifacts.visual.pipeline import validate_visual_artifact
from config.loader import load_config


def visual_embedding_index_sha256(index_path: str | Path) -> str:
    """Bind matrix row order to media ids; matrix bytes alone cannot prove this."""

    return hashlib.sha256(Path(index_path).read_bytes()).hexdigest()


def _vector_literal(row: np.ndarray) -> str:
    return "[" + ",".join(format(float(value), ".9g") for value in row) + "]"


def import_visual_embeddings(
    processed_dir: str | Path,
    artifact_dir: str | Path,
    config_path: str | Path,
    postgres_dsn: str,
) -> dict[str, str | int]:
    """Verify the frozen SigLIP artifact then copy rows unchanged into pgvector.

    This function intentionally never imports a visual encoder. A Phase 3 run is
    invalid if it cannot reuse the Phase 1.5 bytes verbatim.
    """

    artifact = Path(artifact_dir)
    config = load_config(config_path)
    manifest = validate_visual_artifact(
        processed_dir,
        artifact,
        model_name=f"google/{config.embeddings.visual_model}",
        model_version=json.loads((artifact / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))["model_version"],
        preprocessing=config.visual_expectations.preprocessing.model_dump(mode="json"),
        expected_device=config.visual_expectations.expected_device,
    )
    matrix_path = artifact / "visual_embeddings.npy"
    index_path = artifact / "visual_embedding_index.json"
    matrix = np.load(matrix_path, allow_pickle=False)
    media_ids = json.loads(index_path.read_text(encoding="utf-8"))
    if matrix.ndim != 2 or matrix.shape != (manifest["media_count"], 768):
        raise ValueError("VISUAL_ARTIFACT_MISMATCH: unexpected visual matrix shape")
    if len(media_ids) != matrix.shape[0] or len(set(media_ids)) != len(media_ids):
        raise ValueError("VISUAL_ARTIFACT_MISMATCH: media index is not a one-to-one row map")
    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO media_embeddings (media_id, embedding, model_version)
            VALUES (%s, %s::public.vector, %s)
            ON CONFLICT (media_id) DO UPDATE
              SET embedding=EXCLUDED.embedding, model_version=EXCLUDED.model_version
            """,
            [(media_id, _vector_literal(matrix[index]), manifest["model_version"]) for index, media_id in enumerate(media_ids)],
        )
    return {
        "media_count": len(media_ids),
        "matrix_sha256": manifest["matrix_sha256"],
        "visual_embedding_index_sha256": visual_embedding_index_sha256(index_path),
    }


def write_index_manifest(
    processed_dir: str | Path,
    artifact_dir: str | Path,
    lexical_dir: str | Path,
    output_path: str | Path,
    postgres_dsn: str,
) -> Path:
    """Bind the observed Phase 3 index state into one reproducible artifact."""

    processed = Path(processed_dir)
    artifact = Path(artifact_dir)
    lexical = Path(lexical_dir)
    with psycopg.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT first_message_id, embedding::text, model_version FROM chunk_embeddings ORDER BY first_message_id")
        rows = cursor.fetchall()
    digest = hashlib.sha256()
    model_versions = set()
    for message_id, embedding, model_version in rows:
        digest.update(f"{message_id}\0{embedding}\0{model_version}\n".encode("utf-8"))
        model_versions.add(model_version)
    visual = json.loads((artifact / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))
    current = lexical / "current.json"
    payload = {
        "artifact_type": "phase3_index",
        "dataset_messages_sha256": hashlib.sha256((processed / "messages.jsonl").read_bytes()).hexdigest(),
        "bge_embedding_sha256": digest.hexdigest(),
        "bge_embedding_count": len(rows),
        "bge_model_versions": sorted(model_versions),
        "lexical_current_sha256": hashlib.sha256(current.read_bytes()).hexdigest(),
        "visual_matrix_sha256": visual["matrix_sha256"],
        "visual_embedding_index_sha256": visual_embedding_index_sha256(artifact / "visual_embedding_index.json"),
        "visual_model_version": visual["model_version"],
    }
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination
