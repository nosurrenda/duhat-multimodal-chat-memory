from __future__ import annotations

import fcntl
import os
from pathlib import Path

from trace.records import TraceRecord, validate_trace


class TraceWriter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, record: TraceRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # O_APPEND plus flock prevents competing processes from interleaving JSONL records.
        encoded = (record.model_dump_json() + "\n").encode()
        if b"sk-" in encoded or b"or-" in encoded or b"Bearer " in encoded:
            raise ValueError("refusing to persist a trace secret")
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


class TraceReader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read(self) -> list[TraceRecord]:
        records = [TraceRecord.model_validate_json(line) for line in self.path.read_text().splitlines() if line]
        return validate_trace(records)
