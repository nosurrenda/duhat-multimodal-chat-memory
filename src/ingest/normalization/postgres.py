from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def load_postgres(processed_dir: str | Path, dsn: str, schema_path: str | Path) -> None:
    """Load deterministic Phase 1 artifacts into Postgres after the build has validated locally."""
    try:
        import psycopg
    except ImportError as error:  # pragma: no cover - dependency is intentionally optional for offline builds.
        raise RuntimeError("install the project's psycopg dependency before loading Postgres") from error

    root = Path(processed_dir)
    schema = Path(schema_path).read_text(encoding="utf-8")
    with psycopg.connect(dsn) as connection, connection.cursor() as cursor:
        # Canonical writes are replaced as one transaction, preventing a partially rebuilt corpus.
        cursor.execute(schema)
        cursor.execute("TRUNCATE channel_memberships, media, messages, senders, channels RESTART IDENTITY")
        cursor.executemany(
            "INSERT INTO channels (channel_id, source_family, source_dialogue) VALUES (%s, %s, %s)",
            [(row["channel_id"], row["source_family"], row["source_dialogue"]) for row in _rows(root / "channels.jsonl")],
        )
        cursor.executemany(
            "INSERT INTO senders (sender_id, display_name) VALUES (%s, %s)",
            [(row["sender_id"], row["display_name"]) for row in _rows(root / "senders.jsonl")],
        )
        cursor.executemany(
            """INSERT INTO messages
                (message_id, channel_id, sender_id, body, occurred_at, timestamp_source, source_turn_index,
                 session_index, source_session_id, chronological_rank, temporal_order_conflict,
                 reply_to_message_id, thread_id)
                VALUES (%(message_id)s, %(channel_id)s, %(sender_id)s, %(text)s, %(timestamp)s,
                        %(timestamp_source)s, %(source_turn_index)s, %(session_index)s,
                        %(source_session_id)s, %(chronological_rank)s, %(temporal_order_conflict)s,
                        %(reply_to_message_id)s, %(thread_id)s)""",
            _rows(root / "messages.jsonl"),
        )
        cursor.executemany(
            """INSERT INTO media (media_id, parent_message_id, channel_id, content_sha256, storage_object_ref)
                VALUES (%(media_id)s, %(parent_message_id)s, %(channel_id)s, %(content_sha256)s,
                        %(storage_object_ref)s)""",
            _rows(root / "media.jsonl"),
        )
        cursor.executemany(
            """INSERT INTO channel_memberships (caller_id, channel_id, role, valid_from, valid_to)
                VALUES (%(caller_id)s, %(channel_id)s, %(role)s, %(valid_from)s, %(valid_to)s)""",
            _rows(root / "memberships.jsonl"),
        )
