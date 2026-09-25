from __future__ import annotations

from typing import Protocol


class ScopedRepository(Protocol):
    """Future persistence access must carry caller scope through this boundary."""

    def accessible_channel_ids(self, caller_id: str) -> set[str]: ...
