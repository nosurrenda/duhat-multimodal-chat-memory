from pathlib import Path

import pytest

from trace import TraceReader, TraceRecord, TraceWriter, compute_metrics, validate_trace
from trace.capability import write_capability_artifact

RUN = "123e4567-e89b-12d3-a456-426614174000"
QUERY = "q_direct_1"


def event(event_id: str, record_type: str, payload: dict, parent: str | None = None) -> TraceRecord:
    return TraceRecord(
        trace_schema_version="1.0.0", run_id=RUN, query_id=QUERY, event_id=event_id,
        parent_event_id=parent, record_type=record_type, payload=payload,
    )


def test_trace_round_trip_and_final_jump_metric(tmp_path: Path) -> None:
    started = event("01ARZ3NDEKTSV4RRFFQ69G5FAV", "query_started", {})
    jump = event("01ARZ3NDEKTSV4RRFFQ69G5FAW", "round", {
        "action": "jump", "outcome": "executed", "jump_query": {"clue_ids": ["c_1"]},
        "missing_constraints_before_jump": ["place"], "used_clue_ids": ["c_1"],
        "destination_anchor_ids": ["m_1"], "new_anchor_ids": ["m_1"], "constraints_gained": ["place"],
    }, started.event_id)
    result = event("01ARZ3NDEKTSV4RRFFQ69G5FAX", "result", {"supporting_round_event_ids": [jump.event_id]}, jump.event_id)
    completed = event("01ARZ3NDEKTSV4RRFFQ69G5FAY", "query_completed", {"outcome": "answered", "result_event_id": result.event_id, "total_latency_ms": 12}, started.event_id)
    writer = TraceWriter(tmp_path / "trace.jsonl")
    for record in [started, jump, result, completed]:
        writer.append(record)
    records = TraceReader(tmp_path / "trace.jsonl").read()
    assert compute_metrics(records)["jump_success_rate"] == {"value": 1.0, "sample_size": 1}


def test_trace_rejects_cross_query_parent() -> None:
    first = event("01ARZ3NDEKTSV4RRFFQ69G5FAV", "query_started", {})
    other = first.model_copy(update={"query_id": "q_direct_2", "event_id": "01ARZ3NDEKTSV4RRFFQ69G5FAW"})
    with pytest.raises(ValueError, match="same query"):
        validate_trace([first, other.model_copy(update={"parent_event_id": first.event_id})])


def test_capability_artifact_is_redacted(tmp_path: Path) -> None:
    output = tmp_path / "capability.json"
    checksum = write_capability_artifact(output, {"error": "Bearer sk-secret-token-123456"})
    assert len(checksum) == 64
    assert "[REDACTED]" in output.read_text()
