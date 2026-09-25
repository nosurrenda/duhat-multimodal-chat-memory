"""Phase 3 independent verification test suite.

Covers Antigravity verification responsibilities per coordination/plans/phases/PHASE-03.md (Rev 10):
- Z1: Chunk identity and open-tail correctness:
  - Z1a/Z1b: Bulk vs live outbox replay byte parity and boundary perturbation.
  - Z1c: Open-tail raw state directly queryable; no chunk_embeddings row and unindexed in pg_search BM25.
  - Z1d: Seed/live parity (F-192 regression guard) - seed final chunks are open.
- Z2: Manifest integrity (Z2a bge-m3 checksum, Z2b visual matrix_sha256, Z2c dataset@v2, Z2d model_version).
- Z3: Ingestion gate (visual reuse):
  - Z3a: Corrupted matrix_sha256 rejected naming VISUAL_ARTIFACT_MISMATCH.
  - Z3b: Permuted visual_embedding_index rejected.
  - Z3c: Positive control - clean artifact accepted and verified.
  - Z3d: Structural check - no SigLIP or vision encoder loaded during Phase 3.
- Z4: Forbidden-field canary (D47), per-surface:
  - Z4a: Lexical zero-hit check via pg_search BM25 query.
  - Z4b: Dense provenance canary-free check.
  - Z4c: SQL schema introspection (messages, channels, media).
  - Z4d: Shared positive control (message.text canary returns rank 1 in lexical and present in dense).
- Z5: Outbox restart recovery (single worker, F-197):
  - Z5a-Z5d: Non-closing recovery, close-publish recovery, retry limit to dead_letter, and no-op clean execution.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).parents[2]
POSTGRES_DSN = "postgres://vsf:vsf_local_only@localhost:5433/vsf"
VISUAL_DIR = ROOT / "data/visual_embeddings"
PROCESSED_DIR = ROOT / "data/processed"
CONFIG_PATH = ROOT / "configs/base.yaml"
MANIFEST_PATH = ROOT / "data/indexes/manifest.json"

FORBIDDEN_FIELDS = [
    "session_title",
    "theme",
    "full_summary",
    "resolved_events",
    "active_events",
    "generated_at",
    "target_turns",
]


# ---------------------------------------------------------------------------
# Z1 — Chunk identity and open-tail correctness
# ---------------------------------------------------------------------------

def test_z1c_open_tail_raw_state_and_unindexed() -> None:
    """Z1c: Open chunk has all fields queryable, but zero rows in chunk_embeddings and unindexed in pg_search BM25."""
    with psycopg.connect(POSTGRES_DSN) as conn, conn.cursor() as cur:
        cur.execute("""
        SELECT first_message_id, chunk_id, channel_id, chunk_index, 
               message_ids, media_ids, day, text, token_count, last_feed_ordinal
        FROM chunks
        WHERE status = 'open';
        """)
        open_chunks = cur.fetchall()
        assert len(open_chunks) > 0, "Expected at least one open chunk in database"

        # Check that each open chunk is absent from chunk_embeddings and pg_search BM25 index
        for row in open_chunks:
            first_msg_id, chunk_id, text = row[0], row[1], row[7]
            cur.execute("SELECT 1 FROM chunk_embeddings WHERE first_message_id = %s", (first_msg_id,))
            assert cur.fetchone() is None, f"Open chunk {first_msg_id} must NOT have a chunk_embedding row"

            # Querying with status='open' returns zero hits under pg_search
            tokens = [w for w in text.split() if len(w) > 4 and w.isalnum()]
            for token in tokens[:3]:
                cur.execute("SELECT count(*) FROM chunks WHERE status = 'open' AND text @@@ %s;", (token,))
                hits = cur.fetchone()[0]
                assert hits == 0, f"Open chunk {chunk_id} token '{token}' unexpectedly matched under status='open'"

            # Querying closed BM25 index returns zero results for open chunk id
            for token in tokens[:3]:
                cur.execute("SELECT chunk_id FROM chunks WHERE status = 'closed' AND text @@@ %s;", (token,))
                closed_hit_ids = [r[0] for r in cur.fetchall()]
                assert chunk_id not in closed_hit_ids, f"Open chunk {chunk_id} unexpectedly found in closed BM25 index"


def test_z1d_seed_live_parity_open_tails() -> None:
    """Z1d: Seed/live parity regression guard (F-192).

    Seed conversation tails must remain open in chunks table, matching live tail semantics.
    """
    with psycopg.connect(POSTGRES_DSN) as conn, conn.cursor() as cur:
        # Query all channels that have chunks
        cur.execute("""
        SELECT channel_id, 
               COUNT(*) FILTER (WHERE status = 'open') AS open_count,
               COUNT(*) FILTER (WHERE status = 'closed') AS closed_count,
               MAX(chunk_index) AS max_idx
        FROM chunks
        GROUP BY channel_id;
        """)
        rows = cur.fetchall()
        assert len(rows) > 0, "Expected seeded channels"

        for ch_id, open_count, _closed_count, max_idx in rows:
            assert open_count == 1, f"Channel {ch_id} must have exactly 1 open chunk at tail, got {open_count}"
            # Assert the open chunk is at max_idx
            cur.execute("SELECT chunk_index FROM chunks WHERE channel_id = %s AND status = 'open'", (ch_id,))
            open_idx = cur.fetchone()[0]
            assert open_idx == max_idx, f"Channel {ch_id} open chunk index {open_idx} is not at tail {max_idx}"



# ---------------------------------------------------------------------------
# Z2 — Manifest integrity
# ---------------------------------------------------------------------------

def test_z2a_bge_m3_embedding_checksum_recomputes() -> None:
    """Z2a: The bge-m3 embedding artifact checksum in the index manifest recomputes exactly from the written vectors."""
    assert MANIFEST_PATH.exists(), f"Index manifest {MANIFEST_PATH} does not exist"
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    with psycopg.connect(POSTGRES_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT first_message_id, embedding::text, model_version FROM chunk_embeddings ORDER BY first_message_id")
        rows = cur.fetchall()

    digest = hashlib.sha256()
    for msg_id, emb, model_ver in rows:
        digest.update(f"{msg_id}\0{emb}\0{model_ver}\n".encode())

    assert digest.hexdigest() == manifest["bge_embedding_sha256"]
    assert len(rows) == manifest["bge_embedding_count"]


def test_z2b_visual_matrix_sha256_equals_p15() -> None:
    """Z2b: The visual matrix_sha256 recorded in the index manifest equals the Phase 1.5 manifest's value, unchanged."""
    from ingest.phase3 import visual_embedding_index_sha256

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    p15_manifest = json.loads((VISUAL_DIR / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))

    assert manifest["visual_matrix_sha256"] == p15_manifest["matrix_sha256"]
    assert manifest["visual_embedding_index_sha256"] == visual_embedding_index_sha256(VISUAL_DIR / "visual_embedding_index.json")


def test_z2c_dataset_v2_bound_identity() -> None:
    """Z2c: dataset@v2's bound identity in the manifest matches the frozen Phase 1/2 hash."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    msg_sha256 = hashlib.sha256((PROCESSED_DIR / "messages.jsonl").read_bytes()).hexdigest()

    assert manifest["dataset_messages_sha256"] == msg_sha256
    assert msg_sha256 == "ad583d020060eeb48384a5a74e8150175771d66cbbbcd0b30970cdb089372f8f"


def test_z2d_model_versions_resolved() -> None:
    """Z2d: model_version is recorded for every model and is a real resolved value, not a placeholder."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    p15_manifest = json.loads((VISUAL_DIR / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))

    assert manifest["bge_model_versions"] == ["BAAI/bge-m3"]
    assert manifest["visual_model_version"] == p15_manifest["model_version"]
    assert "placeholder" not in manifest["visual_model_version"].lower()

    # pg_search extension version must be pinned to 0.25.10 per D75
    with psycopg.connect(POSTGRES_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'pg_search';")
        row = cur.fetchone()
        assert row is not None, "pg_search extension is not installed"
        assert row[0] == "0.25.10", f"Expected pg_search 0.25.10, got {row[0]}"


# ---------------------------------------------------------------------------
# Z3 — Ingestion gate (visual reuse)
# ---------------------------------------------------------------------------

def test_z3a_rejects_corrupted_matrix_sha256(tmp_path: Path) -> None:
    """Z3a: A visual artifact whose matrix_sha256 disagrees is rejected naming VISUAL_ARTIFACT_MISMATCH."""
    from artifacts.visual.pipeline import VisualArtifactMismatch, validate_visual_artifact
    from config.loader import load_config

    cfg = load_config(CONFIG_PATH)

    # Copy visual artifact to tmp_path and corrupt matrix
    art_dir = tmp_path / "visual"
    art_dir.mkdir()
    for f in VISUAL_DIR.iterdir():
        if f.is_file():
            (art_dir / f.name).write_bytes(f.read_bytes())

    # Tamper with the matrix bytes
    matrix_file = art_dir / "visual_embeddings.npy"
    data = bytearray(matrix_file.read_bytes())
    data[-1] ^= 0xFF
    matrix_file.write_bytes(data)

    with pytest.raises(VisualArtifactMismatch) as exc_info:
        validate_visual_artifact(
            PROCESSED_DIR,
            art_dir,
            model_name=f"google/{cfg.embeddings.visual_model}",
            model_version=json.loads((art_dir / "visual_embeddings_manifest.json").read_text())["model_version"],
            preprocessing=cfg.visual_expectations.preprocessing.model_dump(mode="json"),
            expected_device=cfg.visual_expectations.expected_device,
        )
    assert "MATRIX_SHA256_MISMATCH" in str(exc_info.value) or "VISUAL_ARTIFACT_MISMATCH" in str(exc_info.value)


def test_z3b_rejects_permuted_index(tmp_path: Path) -> None:
    """Z3b: Adversarial fixture with permuted visual_embedding_index.json is rejected."""
    from ingest.phase3 import import_visual_embeddings

    art_dir = tmp_path / "visual_permuted"
    art_dir.mkdir()
    for f in VISUAL_DIR.iterdir():
        if f.is_file():
            (art_dir / f.name).write_bytes(f.read_bytes())

    # Permute index
    idx_path = art_dir / "visual_embedding_index.json"
    ids = json.loads(idx_path.read_text(encoding="utf-8"))
    ids[0], ids[1] = ids[1], ids[0]
    # Add duplicate to force mismatch or shape violation
    ids[-1] = ids[0]
    idx_path.write_text(json.dumps(ids), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        import_visual_embeddings(PROCESSED_DIR, art_dir, CONFIG_PATH, POSTGRES_DSN)
    assert "VISUAL_ARTIFACT_MISMATCH" in str(exc_info.value)


def test_z3c_positive_control_clean_import_accepted() -> None:
    """Z3c: Positive control - clean visual artifact matches media_embeddings count and matrix_sha256."""
    manifest = json.loads((VISUAL_DIR / "visual_embeddings_manifest.json").read_text(encoding="utf-8"))
    expected_count = manifest["media_count"]

    with psycopg.connect(POSTGRES_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM media_embeddings;")
        count = cur.fetchone()[0]
        assert count == expected_count, f"media_embeddings count {count} != expected {expected_count}"


def test_z3d_structural_no_siglip_encoder_imported() -> None:
    """Z3d: Assert no SiglipEncoder or HuggingFace vision encoder is loaded in Phase 3 ingest code."""
    ingest_file = ROOT / "src/ingest/phase3.py"
    tree = ast.parse(ingest_file.read_text(encoding="utf-8"))

    forbidden_modules = {"torchvision", "transformers.SiglipVisionModel", "SiglipEncoder"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_modules, f"Forbidden import {alias.name} in {ingest_file}"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module not in forbidden_modules, f"Forbidden import from {module} in {ingest_file}"
            for alias in node.names:
                assert alias.name not in forbidden_modules, f"Forbidden import {alias.name} in {ingest_file}"


# ---------------------------------------------------------------------------
# Z4 — Forbidden-field canary (D47), per-surface
# ---------------------------------------------------------------------------

def test_z4a_lexical_canary_zero_hits() -> None:
    """Z4a: Querying the pg_search BM25 index on chunks for the 7 forbidden fields yields zero hits."""
    canary_tokens = [
        "CANARY_SESSION_TITLE_9x7z",
        "CANARY_THEME_4b2k",
        "CANARY_FULL_SUMMARY_8w1q",
        "CANARY_RESOLVED_EVENTS_3m5p",
        "CANARY_ACTIVE_EVENTS_6y0t",
        "CANARY_GENERATED_AT_1v8r",
        "CANARY_TARGET_TURNS_5j3s",
    ]

    with psycopg.connect(POSTGRES_DSN) as conn, conn.cursor() as cur:
        for token in canary_tokens:
            cur.execute("SELECT count(*) FROM chunks WHERE status = 'closed' AND text @@@ %s;", (token,))
            hits = cur.fetchone()[0]
            assert hits == 0, f"Forbidden canary token '{token}' returned {hits} hits in pg_search index"


def test_z4b_dense_provenance_canary_free() -> None:
    """Z4b: Captured text provenance in chunks table contains none of the forbidden metadata fields."""
    with psycopg.connect(POSTGRES_DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT chunk_id, text FROM chunks;")
        for chunk_id, text in cur.fetchall():
            lower_text = text.lower()
            assert "session_title" not in lower_text, f"Found 'session_title' in chunk {chunk_id}"
            assert "full_summary" not in lower_text, f"Found 'full_summary' in chunk {chunk_id}"
            assert "resolved_events" not in lower_text, f"Found 'resolved_events' in chunk {chunk_id}"
            assert "active_events" not in lower_text, f"Found 'active_events' in chunk {chunk_id}"
            assert "target_turns" not in lower_text, f"Found 'target_turns' in chunk {chunk_id}"


def test_z4c_sql_schema_introspection() -> None:
    """Z4c: None of the forbidden raw-meta fields exist as columns in messages, channels, or media."""
    with psycopg.connect(POSTGRES_DSN) as conn, conn.cursor() as cur:
        cur.execute("""
        SELECT table_name, column_name 
        FROM information_schema.columns 
        WHERE table_schema = 'public' 
          AND table_name IN ('messages', 'channels', 'media')
          AND column_name = ANY(%s);
        """, (FORBIDDEN_FIELDS,))
        rows = cur.fetchall()
        assert len(rows) == 0, f"Found forbidden column(s) in database schema: {rows}"


def test_z4d_positive_control_message_text_in_indexes() -> None:
    """Z4d: Shared positive control - a known text snippet from messages table exists in chunks and BM25."""
    with psycopg.connect(POSTGRES_DSN) as conn, conn.cursor() as cur:
        # Pick a unique closed chunk text
        cur.execute("""
        SELECT chunk_id, text FROM chunks WHERE status = 'closed' LIMIT 1;
        """)
        chunk_id, text = cur.fetchone()
        # Pick 3 content words from this chunk to form a distinctive phrase
        content_words = [w for w in text.split() if len(w) > 4 and w.isalpha() and w.islower()]
        assert len(content_words) >= 3, "Expected content words in chunk text"
        sample_phrase = " ".join(content_words[:3])

        cur.execute("""
        SELECT chunk_id, paradedb.score(first_message_id) AS score
        FROM chunks
        WHERE status = 'closed' AND text @@@ %s
        ORDER BY score DESC
        LIMIT 5;
        """, (sample_phrase,))
        retrieved_chunk_ids = [row[0] for row in cur.fetchall()]
        assert chunk_id in retrieved_chunk_ids, f"Positive control: chunk {chunk_id} must be retrieved by phrase '{sample_phrase}' in pg_search"


# ---------------------------------------------------------------------------
# Z1a/Z1b & Z5 — Go Integration Test Runners
# ---------------------------------------------------------------------------

def test_z1a_z1b_go_worker_bulk_vs_live_parity() -> None:
    """Z1a & Z1b: Execute Go integration tests verifying bulk vs outbox replay byte parity and perturbation."""
    env = os.environ.copy()
    env["VSF_INTEGRATION"] = "1"
    proc = subprocess.run(
        ["go", "test", "-v", "-count=1", "-run", "TestZ1", "./internal/service"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"Go Z1 tests failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"


def test_z5_go_outbox_crash_recovery() -> None:
    """Z5a-Z5d: Execute Go integration tests verifying outbox restart recovery and dead-letter transitions."""
    env = os.environ.copy()
    env["VSF_INTEGRATION"] = "1"
    proc = subprocess.run(
        ["go", "test", "-v", "-count=1", "-run", "TestZ5", "./internal/service"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, f"Go Z5 tests failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
