from __future__ import annotations

import json
from pathlib import Path

from vsf.normalization import build_corpus
from vsf.storage import CanonicalScopedRepository
from vsf.trace.writer import TraceReader

ROOT = Path(__file__).parents[2]


def test_build_corpus_emits_phase_one_invariants(tmp_path: Path) -> None:
    output = build_corpus(ROOT, tmp_path / "processed")
    summary = json.loads((output / "validation.json").read_text(encoding="utf-8"))
    assert summary == {
        "attached_media": 1265,
        "channels": 25,
        "messages": 7078,
        "orphan_media": 38,
        "senders": 68,
        "temporal_order_conflict_sessions": 24,
        "unique_referenced_paths": 1262,
    }
    messages = [
        json.loads(line)
        for line in (output / "messages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    d7_session3 = [row for row in messages if row["message_id"].startswith("dyadic_d7:session3:")]
    d7_session4 = [row for row in messages if row["message_id"].startswith("dyadic_d7:session4:")]
    assert max(row["chronological_rank"] for row in d7_session3) < min(
        row["chronological_rank"] for row in d7_session4
    )
    assert all(row["temporal_order_conflict"] for row in d7_session3 + d7_session4)
    conflict_sessions = {
        (row["channel_id"], row["session_index"])
        for row in messages
        if row["temporal_order_conflict"]
    }
    assert len(conflict_sessions) == 24
    assert len(TraceReader(output / "normalization.trace.jsonl").read()) == 2


def test_repository_is_default_deny_and_never_loads_orphans() -> None:
    repository = CanonicalScopedRepository(ROOT / "data/processed")
    media = json.loads((ROOT / "data/processed/media.jsonl").read_text(encoding="utf-8").splitlines()[0])
    orphan = json.loads((ROOT / "data/processed/orphan_media.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert repository.accessible_channel_ids("missing") == set()
    assert repository.get_media("missing", media["media_id"]) is None
    assert repository.get_media("benchmark_reader", orphan["orphan_id"]) is None
    assert repository.get_media("benchmark_reader", media["media_id"]) is not None
