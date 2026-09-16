from __future__ import annotations

import json
from pathlib import Path


class CanonicalScopedRepository:
    """Read canonical data through caller membership; orphan inventory is never loaded as media."""

    def __init__(self, processed_dir: str | Path) -> None:
        root = Path(processed_dir)
        self._media = {row["media_id"]: row for row in self._rows(root / "media.jsonl")}
        self._messages = {row["message_id"]: row for row in self._rows(root / "messages.jsonl")}
        self._memberships = self._rows(root / "memberships.jsonl")

    @staticmethod
    def _rows(path: Path) -> list[dict[str, object]]:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def accessible_channel_ids(self, caller_id: str) -> set[str]:
        return {
            str(row["channel_id"])
            for row in self._memberships
            if row["caller_id"] == caller_id and row["valid_to"] is None
        }

    def get_media(self, caller_id: str, media_id: str) -> dict[str, object] | None:
        media = self._media.get(media_id)
        if media is None or media["channel_id"] not in self.accessible_channel_ids(caller_id):
            return None
        return media

    def get_message(self, caller_id: str, message_id: str) -> dict[str, object] | None:
        message = self._messages.get(message_id)
        if message is None or message["channel_id"] not in self.accessible_channel_ids(caller_id):
            return None
        return message
