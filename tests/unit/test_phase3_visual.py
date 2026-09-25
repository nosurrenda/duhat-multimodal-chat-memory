from __future__ import annotations

import hashlib
from pathlib import Path

from ingest.phase3 import visual_embedding_index_sha256


def test_visual_embedding_index_hash_binds_exact_file_bytes(tmp_path: Path) -> None:
    index = tmp_path / "visual_embedding_index.json"
    index.write_text('["m2","m1"]\n', encoding="utf-8")
    assert visual_embedding_index_sha256(index) == hashlib.sha256(index.read_bytes()).hexdigest()
