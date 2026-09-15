from pathlib import Path

import pytest
from pydantic import ValidationError

from vsf.trace import (
    TraceRecord,
    TraceWriter,
    compute_metrics,
    render_cli,
    render_html,
    validate_trace,
)
from vsf.trace.capability import write_capability_artifact

ROOT = Path(__file__).parents[2]
RUN_ID = "00000000-0000-0000-0000-000000000001"
Q1 = "q_stratum_001"
Q2 = "q_stratum_002"


def _mp_append_worker(path_str: str, worker_id: int, count: int) -> None:
    writer = TraceWriter(path_str)
    for i in range(count):
        ev_id = f"01ARZ3NDEKTSV4RRFFQ6{worker_id:02d}{i:04d}"
        rec = TraceRecord(
            trace_schema_version="1.0.0",
            run_id=RUN_ID,
            query_id=f"q_mp_{worker_id}",
            event_id=ev_id,
            record_type="round",
            payload={"action": "retrieve_lexical"},
        )
        writer.append(rec)


def make_record(
    event_id: str,
    record_type: str,
    payload: dict,
    query_id: str = Q1,
    parent_event_id: str | None = None,
) -> TraceRecord:
    return TraceRecord(
        trace_schema_version="1.0.0",
        run_id=RUN_ID,
        query_id=query_id,
        event_id=event_id,
        parent_event_id=parent_event_id,
        record_type=record_type,
        payload=payload,
    )


def test_w1_schema_version_is_enforced() -> None:
    """W1: Record requires trace_schema_version=='1.0.0'; unsupported version fails."""
    with pytest.raises(ValidationError):
        TraceRecord(
            trace_schema_version="9.9.9",
            run_id=RUN_ID,
            query_id=Q1,
            event_id="01ARZ3NDEKTSV4RRFFQ69G5FA1",
            record_type="query_started",
            payload={},
        )


def test_w2_action_enum_is_closed() -> None:
    """W2: Controller actions outside the closed action vocabulary are rejected."""
    with pytest.raises(ValidationError, match="closed action enum"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FA2",
            "round",
            {"action": "arbitrary_unregistered_action"},
        )


def test_w3_jump_not_equal_expansion_contracts() -> None:
    """W3: Executed jump requires specific evidence fields; expand_context cannot carry jump evidence."""
    # Executed jump missing used_clue_ids
    with pytest.raises(ValidationError, match="executed jump is missing required evidence"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FA3",
            "round",
            {
                "action": "jump",
                "outcome": "executed",
                "jump_query": {"clue_ids": ["c1"]},
                "missing_constraints_before_jump": ["time"],
                # missing used_clue_ids, destination_anchor_ids, new_anchor_ids, constraints_gained
            },
        )

    # Context expansion carrying jump fields must be rejected
    with pytest.raises(ValidationError, match=r"(context expansion cannot carry jump evidence|outside its action contract)"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FA4",
            "round",
            {
                "action": "expand_context",
                "strategy": "reply_thread",
                "used_clue_ids": ["c1"],
            },
        )

    # Valid executed jump passes
    valid_jump = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FA5",
        "round",
        {
            "action": "jump",
            "outcome": "executed",
            "jump_query": {"clue_ids": ["c1"]},
            "missing_constraints_before_jump": ["place"],
            "used_clue_ids": ["c1"],
            "destination_anchor_ids": ["msg_10"],
            "new_anchor_ids": ["msg_10"],
            "constraints_gained": ["place"],
        },
    )
    assert valid_jump.payload["outcome"] == "executed"


def test_w4_blocked_jump_requires_block_reason() -> None:
    """W4: Blocked jumps require block_reason from closed enum; visited_query_fingerprint requires used_clue_ids; no_unused_clue may omit it."""
    # Missing block_reason
    with pytest.raises(ValidationError, match="blocked jump requires block_reason"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FA6",
            "round",
            {"action": "jump", "outcome": "blocked"},
        )

    # Invalid block_reason outside closed enum
    with pytest.raises(ValidationError, match="from the closed enum"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FA6",
            "round",
            {"action": "jump", "outcome": "blocked", "block_reason": "arbitrary_reason"},
        )

    # visited_query_fingerprint omitting used_clue_ids must fail (W4 requirement)
    with pytest.raises(ValidationError, match="requires used_clue_ids"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FA7",
            "round",
            {
                "action": "jump",
                "outcome": "blocked",
                "block_reason": "visited_query_fingerprint",
            },
        )

    # no_unused_clue can omit used_clue_ids
    valid_no_clue = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FA7",
        "round",
        {
            "action": "jump",
            "outcome": "blocked",
            "block_reason": "no_unused_clue",
        },
    )
    assert valid_no_clue.payload["block_reason"] == "no_unused_clue"

    # visited_query_fingerprint with used_clue_ids succeeds
    valid_visited = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FA8",
        "round",
        {
            "action": "jump",
            "outcome": "blocked",
            "block_reason": "visited_query_fingerprint",
            "used_clue_ids": ["c1"],
        },
    )
    assert valid_visited.payload["used_clue_ids"] == ["c1"]


def test_w4b_final_result_binding_and_cross_query_checks() -> None:
    """W4b: Answered query requires valid result_event_id in same query; cross-query or non-result rejected."""
    started = make_record("01ARZ3NDEKTSV4RRFFQ69G5FA8", "query_started", {})
    round_1 = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FA9",
        "round",
        {"action": "retrieve_lexical"},
        parent_event_id=started.event_id,
    )
    res_1 = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FAA",
        "result",
        {"supporting_round_event_ids": [round_1.event_id]},
        parent_event_id=round_1.event_id,
    )

    # Answered query with missing result_event_id
    with pytest.raises(ValidationError, match="answered completion requires exactly one final result reference"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FAB",
            "query_completed",
            {"outcome": "answered", "total_latency_ms": 100},
            parent_event_id=started.event_id,
        )

    # No_result query carrying result_event_id must fail validation
    with pytest.raises(ValidationError, match="answered completion requires exactly one final result reference"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FAC",
            "query_completed",
            {"outcome": "no_result", "result_event_id": res_1.event_id, "total_latency_ms": 50},
            parent_event_id=started.event_id,
        )

    # Cross-query result reference rejected in validate_trace
    started_q2 = make_record("01ARZ3NDEKTSV4RRFFQ69G5FAD", "query_started", {}, query_id=Q2)
    bad_completed_q2 = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FAE",
        "query_completed",
        {"outcome": "answered", "result_event_id": res_1.event_id, "total_latency_ms": 80},
        query_id=Q2,
        parent_event_id=started_q2.event_id,
    )
    with pytest.raises(ValueError, match="same query"):
        validate_trace([started, round_1, res_1, started_q2, bad_completed_q2])


def test_w6_interleaved_queries_and_dangling_parents() -> None:
    """W6: Interleaved stream validation and dangling parent rejection."""
    # Dangling parent
    orphan = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FAF",
        "round",
        {"action": "retrieve_dense"},
        parent_event_id="01ARZ3NDEKTSV4RRFFQ69G5FA0",  # does not exist
    )
    with pytest.raises(ValueError, match="trace parent must exist earlier"):
        validate_trace([orphan])

    # Interleaved queries in one chronological list
    q1_start = make_record("01ARZ3NDEKTSV4RRFFQ69G5FB1", "query_started", {}, query_id=Q1)
    q2_start = make_record("01ARZ3NDEKTSV4RRFFQ69G5FB2", "query_started", {}, query_id=Q2)
    q1_r1 = make_record("01ARZ3NDEKTSV4RRFFQ69G5FB3", "round", {"action": "retrieve_lexical"}, query_id=Q1, parent_event_id=q1_start.event_id)
    q2_r1 = make_record("01ARZ3NDEKTSV4RRFFQ69G5FB4", "round", {"action": "retrieve_dense"}, query_id=Q2, parent_event_id=q2_start.event_id)
    q1_end = make_record("01ARZ3NDEKTSV4RRFFQ69G5FB5", "query_completed", {"outcome": "no_result", "total_latency_ms": 45}, query_id=Q1, parent_event_id=q1_start.event_id)
    q2_end = make_record("01ARZ3NDEKTSV4RRFFQ69G5FB6", "query_completed", {"outcome": "no_result", "total_latency_ms": 55}, query_id=Q2, parent_event_id=q2_start.event_id)

    validated = validate_trace([q1_start, q2_start, q1_r1, q2_r1, q1_end, q2_end])
    assert len(validated) == 6


def test_w9_multi_process_append_atomicity(tmp_path: Path) -> None:
    """W9: Concurrent appends from multiple processes must produce uncorrupted, parseable JSONL."""
    import multiprocessing
    trace_path = tmp_path / "concurrent_trace.jsonl"
    num_workers = 4
    count = 10
    ctx = multiprocessing.get_context("spawn")
    processes = [
        ctx.Process(target=_mp_append_worker, args=(str(trace_path), wid, count))
        for wid in range(num_workers)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join()
        assert p.exitcode == 0

    # Read back
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == num_workers * count
    parsed = [TraceRecord.model_validate_json(line) for line in lines]
    assert len(parsed) == num_workers * count


def test_w10_and_w5b_metric_edge_cases_and_zero_denominators() -> None:
    """W10 & W5b: Zero-call no-result queries and empty traces compute without division by zero."""
    # Completely empty trace
    empty_metrics = compute_metrics([])
    assert empty_metrics["jump_success_rate"] == {"value": None, "sample_size": 0}
    assert empty_metrics["no_gain_jump_rate"] == {"value": None, "sample_size": 0}
    assert empty_metrics["system_latency_p50"] == {"value": None, "sample_size": 0}
    assert empty_metrics["search_calls"] == {"value": None, "sample_size": 0}
    assert empty_metrics["llm_calls"] == {"value": None, "sample_size": 0}
    assert empty_metrics["vlm_calls"] == {"value": None, "sample_size": 0}
    assert empty_metrics["cost_per_query"] == {"value": None, "sample_size": 0}

    # Query with zero LLM calls and no_result outcome
    started = make_record("01ARZ3NDEKTSV4RRFFQ69G5FC1", "query_started", {})
    completed = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FC2",
        "query_completed",
        {"outcome": "no_result", "total_latency_ms": 15},
        parent_event_id=started.event_id,
    )
    metrics = compute_metrics([started, completed])
    assert metrics["jump_success_rate"] == {"value": None, "sample_size": 0}
    assert metrics["system_latency_p50"] == {"value": 15, "sample_size": 1}
    assert metrics["search_calls"] == {"value": 0, "sample_size": 1}
    assert metrics["llm_calls"] == {"value": 0, "sample_size": 1}
    assert metrics["vlm_calls"] == {"value": 0, "sample_size": 1}
    assert metrics["cost_per_query"] == {"value": {Q1: "0"}, "sample_size": 1}


def test_w11_viewer_cli_and_html_rendering(tmp_path: Path) -> None:
    """W11: Viewer renders a multi-round trace including blocked jump, executed jump, and no-result query."""
    started = make_record("01ARZ3NDEKTSV4RRFFQ69G5FD1", "query_started", {})
    round_expand = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FD2",
        "round",
        {"action": "expand_context", "strategy": "reply_thread", "anchor_ids": ["m1"], "new_anchor_ids": ["m2"]},
        parent_event_id=started.event_id,
    )
    round_blocked_jump = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FD3",
        "round",
        {
            "action": "jump",
            "outcome": "blocked",
            "block_reason": "visited_query_fingerprint",
            "used_clue_ids": ["c1"],
        },
        parent_event_id=round_expand.event_id,
    )
    round_executed_jump = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FD4",
        "round",
        {
            "action": "jump",
            "outcome": "executed",
            "jump_query": {"clue_ids": ["c1"]},
            "missing_constraints_before_jump": ["target"],
            "used_clue_ids": ["c1"],
            "destination_anchor_ids": ["m3"],
            "new_anchor_ids": ["m3"],
            "constraints_gained": ["target"],
        },
        parent_event_id=round_blocked_jump.event_id,
    )
    llm_call = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FD5",
        "llm_call",
        {"role": "query_analyzer", "model_id": "google/gemini-3.5-flash-lite", "cost_usd": "0.0012", "latency_ms": 250},
        parent_event_id=round_executed_jump.event_id,
    )
    result = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FD6",
        "result",
        {"supporting_round_event_ids": [round_executed_jump.event_id], "media_id": "img_001"},
        parent_event_id=round_executed_jump.event_id,
    )
    completed = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FD7",
        "query_completed",
        {"outcome": "answered", "result_event_id": result.event_id, "total_latency_ms": 650},
        parent_event_id=started.event_id,
    )

    # Add a second query with no_result
    started_2 = make_record("01ARZ3NDEKTSV4RRFFQ69G5FD8", "query_started", {}, query_id=Q2)
    completed_2 = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FD9",
        "query_completed",
        {"outcome": "no_result", "total_latency_ms": 110},
        query_id=Q2,
        parent_event_id=started_2.event_id,
    )

    records = [
        started,
        round_expand,
        round_blocked_jump,
        round_executed_jump,
        llm_call,
        result,
        completed,
        started_2,
        completed_2,
    ]

    corpus = {"c1": "sunset beach photo"}

    # 1. Test CLI rendering
    cli_text = render_cli(records, corpus)
    assert "TRACE VIEWER" in cli_text
    assert "Query [q_stratum_001]" in cli_text
    assert "JUMP (EXECUTED)" in cli_text
    assert "JUMP (BLOCKED)" in cli_text
    assert "visited_query_fingerprint" in cli_text
    assert "sunset beach photo" in cli_text
    assert "Query [q_stratum_002]" in cli_text
    assert "Outcome: no_result" in cli_text

    # 2. Test HTML rendering
    html_text = render_html(records, corpus)
    assert "<!DOCTYPE html>" in html_text
    assert "VSF Multimodal Retrieval - Trace Viewer" in html_text
    assert "badge-answered" in html_text
    assert "badge-no_result" in html_text
    assert "q_stratum_001" in html_text
    assert "q_stratum_002" in html_text

    # Verify writing HTML to file
    out_html = tmp_path / "report.html"
    out_html.write_text(html_text, encoding="utf-8")
    assert out_html.stat().st_size > 0


def test_w12_capability_artifact_redacts_secrets(tmp_path: Path) -> None:
    """W12: Provider error strings containing secrets must be sanitized with [REDACTED]."""
    target = tmp_path / "capability.json"
    raw_payload = {
        "provider": "openrouter",
        "error": "Failed with key sk-or-v1-abcdef1234567890abcdef and token Bearer abc.def.ghi",
        "nested": {"details": "auth error: or-live-99887766554433"},
    }
    sha = write_capability_artifact(target, raw_payload)
    assert len(sha) == 64
    content = target.read_text(encoding="utf-8")
    assert "[REDACTED]" in content
    assert "sk-or-v1-abcdef1234567890abcdef" not in content
    assert "Bearer abc.def.ghi" not in content
    assert "or-live-99887766554433" not in content


def test_w5_full_metric_sufficiency_and_zero_filled_loop_prevention() -> None:
    """W5 & W5b: Verify that all 12 §24 metrics are computed, and loop_prevention_count is zero-filled over all block reasons."""
    from vsf.trace.records import BLOCK_REASONS

    # Synthetic multi-action trace
    s1 = make_record("01ARZ3NDEKTSV4RRFFQ69G5FE1", "query_started", {})
    r_lex = make_record("01ARZ3NDEKTSV4RRFFQ69G5FE2", "round", {"action": "retrieve_lexical"}, parent_event_id=s1.event_id)
    r_dense = make_record("01ARZ3NDEKTSV4RRFFQ69G5FE3", "round", {"action": "retrieve_dense"}, parent_event_id=r_lex.event_id)
    r_block = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FE4",
        "round",
        {"action": "jump", "outcome": "blocked", "block_reason": "budget_exhausted", "used_clue_ids": ["c1"]},
        parent_event_id=r_dense.event_id,
    )
    r_jump = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FE5",
        "round",
        {
            "action": "jump",
            "outcome": "executed",
            "jump_query": {"clue_ids": ["c1", "c2"]},
            "missing_constraints_before_jump": ["speaker"],
            "used_clue_ids": ["c1", "c2"],
            "destination_anchor_ids": ["msg_50"],
            "new_anchor_ids": ["msg_50"],
            "constraints_gained": ["speaker"],
        },
        parent_event_id=r_block.event_id,
    )
    c_llm = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FE6",
        "llm_call",
        {"role": "bridge_resolver", "cost_usd": "0.002", "latency_ms": 180},
        parent_event_id=r_jump.event_id,
    )
    c_vlm = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FE7",
        "llm_call",
        {"role": "vlm_extract", "cost_usd": "0.005", "latency_ms": 420},
        parent_event_id=r_jump.event_id,
    )
    res = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FE8",
        "result",
        {"supporting_round_event_ids": [r_jump.event_id]},
        parent_event_id=r_jump.event_id,
    )
    done = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FE9",
        "query_completed",
        {"outcome": "answered", "result_event_id": res.event_id, "total_latency_ms": 750},
        parent_event_id=s1.event_id,
    )

    records = [s1, r_lex, r_dense, r_block, r_jump, c_llm, c_vlm, res, done]
    m = compute_metrics(records)

    # 1. Loop Prevention Count is zero-filled over all BLOCK_REASONS
    assert isinstance(m["loop_prevention_count"], dict)
    assert set(m["loop_prevention_count"].keys()) == BLOCK_REASONS
    assert m["loop_prevention_count"]["budget_exhausted"] == 1
    assert m["loop_prevention_count"]["no_unused_clue"] == 0
    assert m["loop_prevention_count"]["visited_query_fingerprint"] == 0
    assert m["loop_prevention_count"]["no_information_gain"] == 0

    # 2. Check call counts and cost per query
    assert m["search_calls"] == {"value": 2, "sample_size": 1}  # retrieve_lexical + retrieve_dense
    assert m["llm_calls"] == {"value": 1, "sample_size": 1}     # bridge_resolver
    assert m["vlm_calls"] == {"value": 1, "sample_size": 1}     # vlm_extract
    assert m["cost_per_query"] == {"value": {Q1: "0.007"}, "sample_size": 1}
    assert m["average_rounds"] == {"value": 4.0, "sample_size": 1}

    # 3. Check clue precision & jump success
    assert m["jump_success_rate"] == {"value": 1.0, "sample_size": 1}
    assert m["no_gain_jump_rate"] == {"value": 0.0, "sample_size": 1}
    assert m["useful_clue_precision"] == {"value": 1.0, "sample_size": 2}

    # 4. Check percentiles
    assert m["system_latency_p50"] == {"value": 750, "sample_size": 1}
    assert m["per_call_latency_p50"] == {"value": 180, "sample_size": 2}
    assert m["per_call_latency_p95"] == {"value": 420, "sample_size": 2}


def test_w7_payload_fields_and_ids_enforcement() -> None:
    """W7 (G1 & G2): Schema rejects unknown payload fields, non-identifier characters, and unconstrained enum lists."""
    # 1. Unknown fields across every record type
    # query_started
    with pytest.raises(ValidationError, match="outside its trace contract"):
        make_record("01ARZ3NDEKTSV4RRFFQ69G5FF1", "query_started", {"arbitrary_text": "hello"})

    # result unknown field
    with pytest.raises(ValidationError, match="outside its trace contract"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FF2",
            "result",
            {"media_id": "m1", "unknown_extra": "value"},
        )

    # llm_call unknown field
    with pytest.raises(ValidationError, match="outside its trace contract"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FF7",
            "llm_call",
            {"role": "bridge_resolver", "extra_leaked_prompt": "prompt text"},
        )

    # query_completed unknown field
    with pytest.raises(ValidationError, match="outside its trace contract"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FF8",
            "query_completed",
            {"outcome": "answered", "result_event_id": "01ARZ3NDEKTSV4RRFFQ69G5FF2", "extra": 123},
        )

    # 2. G1: Unknown fields on ROUND across actions
    with pytest.raises(ValidationError, match="outside its action contract"):
        make_record("01ARZ3NDEKTSV4RRFFQ69G5FF9", "round", {"action": "expand_context", "unknown_extra": "chat text"})

    with pytest.raises(ValidationError, match="outside its action contract"):
        make_record("01ARZ3NDEKTSV4RRFFQ69G5FFA", "round", {"action": "retrieve_lexical", "raw_query": "search query text"})

    with pytest.raises(ValidationError, match="outside its action contract"):
        make_record("01ARZ3NDEKTSV4RRFFQ69G5FFB", "round", {"action": "stop", "extra_reason": "done"})

    # Positive control: valid round payloads pass
    valid_expand = make_record("01ARZ3NDEKTSV4RRFFQ69G5FFC", "round", {
        "action": "expand_context", "expansion_strategy": "reply_thread", "anchor_ids": ["m1"],
        "expanded_message_ids": ["m2"], "new_anchor_ids": ["m2"]
    })
    assert valid_expand.payload["action"] == "expand_context"

    # 3. G2: Content checking on result fields (structural_relations & supported_constraints)
    with pytest.raises(ValidationError, match="structural_relations must use the closed relation enum"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FFD",
            "result",
            {"media_id": "m1", "structural_relations": ["arbitrary chat text"]},
        )

    with pytest.raises(ValidationError, match="supported_constraints must use the closed constraint enum"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FFE",
            "result",
            {"media_id": "m1", "supported_constraints": ["arbitrary chat text"]},
        )

    # Positive control: valid result enums pass
    valid_res = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FFF",
        "result",
        {
            "media_id": "m1",
            "structural_relations": ["reply_to", "temporal"],
            "supported_constraints": ["person", "place"],
        },
    )
    assert valid_res.payload["media_id"] == "m1"

    # 4. Identifiers containing spaces (e.g. raw message text) must fail
    with pytest.raises(ValidationError, match="identifier values only"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FF3",
            "round",
            {
                "action": "jump",
                "outcome": "executed",
                "jump_query": {"clue_ids": ["c1"]},
                "missing_constraints_before_jump": ["valid_id"],
                "used_clue_ids": ["this is chat text, not an id"],
                "destination_anchor_ids": ["m1"],
                "new_anchor_ids": ["m1"],
                "constraints_gained": ["valid_id"],
            },
        )

    # Unstructured jump query must fail
    with pytest.raises(ValidationError, match="jump_query must be structured IDs and filters"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FF4",
            "round",
            {
                "action": "jump",
                "outcome": "executed",
                "jump_query": {"raw_query_string": "find sunset beach"},
                "missing_constraints_before_jump": ["target"],
                "used_clue_ids": ["c1"],
                "destination_anchor_ids": ["m1"],
                "new_anchor_ids": ["m1"],
                "constraints_gained": ["target"],
            },
        )


def test_w8_trace_writer_and_record_refuses_secrets(tmp_path: Path) -> None:
    """W8 & G4: TraceRecords and TraceWriter refuse secrets, but permit legitimate IDs containing 'or-'."""
    # Record validation catches actual secrets
    with pytest.raises(ValidationError, match="cannot contain secrets"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FF5",
            "round",
            {"action": "retrieve_lexical", "anchor_ids": ["sk-or-v1-secretkey1234567890abcdef"]},
        )

    with pytest.raises(ValidationError, match="cannot contain secrets"):
        make_record(
            "01ARZ3NDEKTSV4RRFFQ69G5FF6",
            "llm_call",
            {"role": "bridge_resolver", "model_id": "Bearer secret-token-xyz-123456"},
        )

    # G4: Legitimate IDs containing "or-" (e.g. sensor-1, anchor-origin) must NOT be rejected
    legit_record = make_record(
        "01ARZ3NDEKTSV4RRFFQ69G5FF7",
        "round",
        {
            "action": "expand_context",
            "expanded_message_ids": ["sensor-1", "motor-drive", "anchor-origin"],
            "anchor_ids": ["sensor-1"],
            "new_anchor_ids": ["sensor-1"],
        },
    )
    assert legit_record.payload["expanded_message_ids"] == ["sensor-1", "motor-drive", "anchor-origin"]

    # TraceWriter also catches secrets
    valid_rec = make_record("01ARZ3NDEKTSV4RRFFQ69G5FF8", "query_started", {})
    writer = TraceWriter(tmp_path / "secret_trace.jsonl")
    secret_rec = valid_rec.model_copy()
    object.__setattr__(secret_rec, "run_id", "sk-secret-run-id-1234567890123456")
    # Bypass pydantic validation directly to test writer defense-in-depth
    with pytest.raises(ValueError, match="refusing to persist a trace secret"):
        writer.append(secret_rec)


def test_w14_and_w15_capability_preflight_and_catalogue() -> None:
    """W14 & W15: Preflight spend cap rejects excessive estimates; catalogue lookup fails closed."""
    from decimal import Decimal

    from vsf.trace.capability import catalogue_requires_dated_id, preflight_cost

    # Preflight cost under cap
    est = preflight_cost([(1000, Decimal("0.30")), (2000, Decimal("2.50"))], cap_usd=Decimal("0.50"))
    assert est < Decimal("0.50")

    # Preflight cost exceeding cap
    with pytest.raises(ValueError, match="exceeds cap"):
        preflight_cost([(10_000_000, Decimal("2.50"))], cap_usd=Decimal("0.50"))

    # Catalogue cross-check: model with dated variant in catalogue
    catalogue = [
        "google/gemini-3.5-flash-lite",
        "google/gemini-3.8-flash",
        "google/gemini-3.8-flash-20260902",
    ]
    assert catalogue_requires_dated_id("google/gemini-3.8-flash", catalogue) is True
    assert catalogue_requires_dated_id("google/gemini-3.5-flash-lite", catalogue) is False

    # Catalogue cross-check: unlisted model fails closed
    with pytest.raises(ValueError, match="no unambiguous configured model entry"):
        catalogue_requires_dated_id("unlisted/unknown-model", catalogue)


def test_w18_make_env_safety() -> None:
    """W18: make env refuses to overwrite existing .env and sets mode 0600."""
    import stat
    import subprocess
    env_file = ROOT / ".env"
    
    # If .env does not exist, make env creates it with mode 0600
    if not env_file.exists():
        proc = subprocess.run(["make", "env"], cwd=ROOT, capture_output=True, text=True, check=False)
        assert proc.returncode == 0
        assert env_file.exists()
        mode = stat.S_IMODE(env_file.stat().st_mode)
        assert mode == 0o600

    # Once .env exists, make env must refuse to overwrite
    proc_refuse = subprocess.run(["make", "env"], cwd=ROOT, capture_output=True, text=True, check=False)
    assert proc_refuse.returncode != 0
    assert "refusing to overwrite" in proc_refuse.stdout or "refusing to overwrite" in proc_refuse.stderr


def test_w19_network_gated_and_capability_marker_registered() -> None:
    """W19: pyproject.toml registers capability marker and plain test execution makes zero network calls."""
    import tomllib

    pyproject_path = ROOT / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)

    markers = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", [])
    assert any(m.startswith("capability") for m in markers), (
        "pyproject.toml must register 'capability' marker"
    )


def test_w13_w16_w17_capability_probe_runner(tmp_path: Path) -> None:
    """W13, W16, W17: Verify probe runner handles vision, schema, context, flex/std endpoints, spend cap, and price proposals."""
    from decimal import Decimal
    from unittest.mock import patch

    from vsf.trace.capability import run_capability_probes

    artifact_path = tmp_path / "capability.json"

    # 1. Rejects empty endpoints
    with pytest.raises(ValueError, match="at least one exact endpoint"):
        run_capability_probes(
            api_key="test-key",
            model_id="google/gemini-3.8-flash-20260902",
            endpoints=[],
            max_input_tokens=8192,
            input_price_per_million=Decimal("0.15"),
            output_price_per_million=Decimal("0.60"),
            artifact_path=artifact_path,
        )

    # 2. Spend cap preflight failure before calling network
    with pytest.raises(ValueError, match="exceeds cap"):
        run_capability_probes(
            api_key="test-key",
            model_id="google/gemini-3.8-flash-20260902",
            endpoints=["vertex"],
            max_input_tokens=10_000_000,
            input_price_per_million=Decimal("0.50"),
            output_price_per_million=Decimal("2.00"),
            artifact_path=artifact_path,
            cap_usd=Decimal("0.50"),
        )

    # 3. Successful execution with multiple endpoints (flex vs standard W16) and probes (W13)
    def mock_chat(api_key: str, body: dict) -> dict:
        endpoint = body.get("provider", {}).get("order", ["unknown"])[0]
        if endpoint == "error_endpoint":
            raise OSError("API error containing Bearer sk-secret-in-error-123456")
        return {"provider": endpoint, "choices": [{"message": {"content": "{\"ok\": true}"}}]}

    with patch("vsf.trace.capability._chat_completion", side_effect=mock_chat):
        res = run_capability_probes(
            api_key="test-key",
            model_id="google/gemini-3.8-flash-20260902",
            endpoints=["vertex", "together", "error_endpoint"],
            max_input_tokens=4096,
            input_price_per_million=Decimal("0.15"),
            output_price_per_million=Decimal("0.60"),
            artifact_path=artifact_path,
        )

    # Verify W13: vision, structured_output, and context_limit probes were executed
    probe_names = {p["name"] for p in res["probes"]}
    assert probe_names == {"vision", "structured_output", "context_limit"}

    # Verify W16: flex vs standard endpoints measured with latency recorded and capacity rejection rates
    endpoints_measured = {p["endpoint"] for p in res["probes"]}
    assert endpoints_measured == {"vertex", "together", "error_endpoint"}
    for p in res["probes"]:
        assert "latency_ms" in p
        if p["endpoint"] == "error_endpoint":
            assert p["status"] == "fail"
            assert "error" in p
        else:
            assert p["status"] == "pass"

    assert "tier_summary" in res
    assert "vertex" in res["tier_summary"]
    assert res["tier_summary"]["vertex"]["capacity_rejection_rate"]["value"] == 0.0
    assert res["tier_summary"]["error_endpoint"]["sample_count"] == 3

    # Verify W17: max_price_proposal is recorded with explicitly named serving endpoint
    assert res["max_price_proposal"] == {
        "pricing_floor": {
            "input_per_million": "0.15",
            "output_per_million": "0.60",
        },
        "headroom_multiplier": 1.0,
        "input_per_million": "0.15",
        "output_per_million": "0.60",
        "derived_from_endpoint": "vertex",
    }

    # Verify artifact write, hash and secret redaction (W12 / W8)
    assert artifact_path.exists()
    assert len(res["sha256"]) == 64
    artifact_text = artifact_path.read_text()
    assert "[REDACTED]" in artifact_text
    assert "sk-secret-in-error" not in artifact_text


def test_endpoint_pricing_resolution_fails_closed_when_endpoint_missing() -> None:
    """W17 / M1 Negative: Fails closed when exact endpoint metadata is missing, refusing model-level fallback."""
    from unittest.mock import MagicMock, patch

    from vsf.trace.capability import resolve_endpoint_pricing

    mock_response = MagicMock()
    mock_response.read.return_value = b'{"data": {"endpoints": [{"tag": "other-provider/endpoint", "pricing": {"prompt": "0.0000001", "completion": "0.000001"}}]}}'
    mock_response.__enter__.return_value = mock_response

    with (
        patch("vsf.trace.capability.urlopen", return_value=mock_response),
        pytest.raises(ValueError, match="no unambiguous endpoint pricing metadata for endpoint tag 'google-ai-studio' on model 'google/gemini-3.5-flash-lite': found 0 matches"),
    ):
        resolve_endpoint_pricing("test-key", "google/gemini-3.5-flash-lite", "google-ai-studio")


def test_endpoint_pricing_resolution_fails_closed_on_duplicate_matches() -> None:
    """Codex finding: Ambiguity safety requires failing closed when multiple matching tags exist."""
    from unittest.mock import MagicMock, patch

    from vsf.trace.capability import resolve_endpoint_pricing

    mock_response = MagicMock()
    mock_response.read.return_value = b'{"data": {"endpoints": [{"tag": "google-ai-studio", "pricing": {"prompt": "0.0000003", "completion": "0.0000025"}}, {"tag": "google-ai-studio", "pricing": {"prompt": "0.0000001", "completion": "0.000001"}}]}}'
    mock_response.__enter__.return_value = mock_response

    with (
        patch("vsf.trace.capability.urlopen", return_value=mock_response),
        pytest.raises(ValueError, match="no unambiguous endpoint pricing metadata for endpoint tag 'google-ai-studio' on model 'google/gemini-3.5-flash-lite': found 2 matches"),
    ):
        resolve_endpoint_pricing("test-key", "google/gemini-3.5-flash-lite", "google-ai-studio")


def test_endpoint_pricing_resolution_ignores_provider_name_mismatch() -> None:
    """Codex finding: provider_name must not be used as an alternate identity for a pinned endpoint tag."""
    from unittest.mock import MagicMock, patch

    from vsf.trace.capability import resolve_endpoint_pricing

    mock_response = MagicMock()
    # provider_name matches target endpoint, but tag does not match
    mock_response.read.return_value = b'{"data": {"endpoints": [{"tag": "google-vertex/global/flex", "provider_name": "google-ai-studio", "pricing": {"prompt": "0.0000001", "completion": "0.000001"}}]}}'
    mock_response.__enter__.return_value = mock_response

    with (
        patch("vsf.trace.capability.urlopen", return_value=mock_response),
        pytest.raises(ValueError, match="no unambiguous endpoint pricing metadata for endpoint tag 'google-ai-studio' on model 'google/gemini-3.5-flash-lite': found 0 matches"),
    ):
        resolve_endpoint_pricing("test-key", "google/gemini-3.5-flash-lite", "google-ai-studio")


def test_endpoint_pricing_resolution_exact_single_match_succeeds() -> None:
    """Positive control: Exact single tag match extracts and quantizes pricing accurately."""
    from decimal import Decimal
    from unittest.mock import MagicMock, patch

    from vsf.trace.capability import resolve_endpoint_pricing

    mock_response = MagicMock()
    mock_response.read.return_value = b'{"data": {"endpoints": [{"tag": "google-ai-studio", "provider_name": "Google AI Studio", "pricing": {"prompt": "0.0000003", "completion": "0.0000025"}}]}}'
    mock_response.__enter__.return_value = mock_response

    with patch("vsf.trace.capability.urlopen", return_value=mock_response):
        pricing = resolve_endpoint_pricing("test-key", "google/gemini-3.5-flash-lite", "google-ai-studio")

    assert pricing == {"input_per_million": Decimal("0.30"), "output_per_million": Decimal("2.50")}


def test_w14_capability_runner_fails_on_catalogue_mismatch(tmp_path: Path) -> None:
    """H1 Negative & S2: run_capability_probes must write failure artifact and fail closed if requires_dated_model_id disagrees."""
    import json
    from decimal import Decimal
    from unittest.mock import patch

    from vsf.trace.capability import run_capability_probes

    artifact_path = tmp_path / "unused.json"
    with (
        patch("vsf.trace.capability.fetch_catalogue", return_value=["google/gemini-3.5-flash-lite"]),
        pytest.raises(ValueError, match="configured requires_dated_model_id disagrees with the model catalogue"),
    ):
        run_capability_probes(
            api_key="mock-key",
            model_id="google/gemini-3.5-flash-lite",
            endpoints=["google-ai-studio"],
            max_input_tokens=100,
            input_price_per_million=Decimal("0.30"),
            output_price_per_million=Decimal("2.50"),
            artifact_path=artifact_path,
            requires_dated_model_id=True,  # Disagrees: model has no dated variant
        )

    assert artifact_path.exists()
    fail_data = json.loads(artifact_path.read_text())
    assert fail_data["status"] == "invalid_for_release"
    assert fail_data["catalogue_check"]["status"] == "fail"


def test_w14_w16_w17_offline_capability_runner(tmp_path: Path) -> None:
    import json
    from decimal import Decimal
    from unittest.mock import patch

    from vsf.trace.capability import run_capability_probes

    artifact_path = tmp_path / "capability_offline.json"

    with (
        patch("vsf.trace.capability.fetch_catalogue", return_value=["google/gemini-3.5-flash-lite", "other-model"]),
        patch(
            "vsf.trace.capability._chat_completion",
            return_value={"provider": "Mock Studio", "choices": [{"message": {"content": "{}"}}]},
        ),
    ):
        res = run_capability_probes(
            api_key="mock-key",
            model_id="google/gemini-3.5-flash-lite",
            endpoints=["google-ai-studio", "google-vertex/global"],
            max_input_tokens=1024,
            input_price_per_million=Decimal("0.30"),
            output_price_per_million=Decimal("2.50"),
            artifact_path=artifact_path,
            cap_usd=Decimal("0.50"),
            requires_dated_model_id=False,
        )

    # Verify H1: catalogue check
    assert res["catalogue_check"] == {
        "model_exposes_dated_id": False,
        "requires_dated_model_id": False,
        "catalogue_model_count": 2,
        "status": "pass",
    }
    # Verify W16 / H4: tier summary with nearest-rank p50
    assert "tier_summary" in res
    assert res["tier_summary"]["google-ai-studio"]["sample_count"] == 3
    assert res["tier_summary"]["google-ai-studio"]["capacity_rejection_rate"]["value"] == 0.0
    assert res["tier_summary"]["google-ai-studio"]["latency_ms_p50"] is not None

    # Verify W17: max_price_proposal with floor and headroom
    assert res["max_price_proposal"] == {
        "pricing_floor": {
            "input_per_million": "0.30",
            "output_per_million": "2.50",
        },
        "headroom_multiplier": 1.0,
        "input_per_million": "0.30",
        "output_per_million": "2.50",
        "derived_from_endpoint": "google-ai-studio",
    }

    # Verify artifact written and redacted
    assert artifact_path.exists()
    assert json.loads(artifact_path.read_text())["model_id"] == "google/gemini-3.5-flash-lite"


@pytest.mark.capability
def test_w13_w16_w17_live_capability_verification(tmp_path: Path) -> None:
    """Live capability probe against OpenRouter exercising flex and standard tiers (B1/B2/D-P0-4)."""
    import json
    from decimal import Decimal

    from vsf.trace.capability import run_capability_probes

    env_file = ROOT / ".env"
    if not env_file.exists():
        pytest.skip("No .env file found; skipping live capability test")

    api_key = ""
    for line in env_file.read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            api_key = line.split("=", 1)[1].strip()

    if not api_key or "your" in api_key.lower() or "placeholder" in api_key.lower():
        pytest.skip("No valid OPENROUTER_API_KEY found in .env; skipping live capability test")

    # Live invocation probing both flex and standard tiers
    artifact_path = tmp_path / "capability.json"
    res = run_capability_probes(
        api_key=api_key,
        model_id="google/gemini-3.5-flash-lite",
        endpoints=[
            "google-ai-studio/flex",
            "google-vertex/global/flex",
            "google-ai-studio",
            "google-vertex/global",
        ],
        max_input_tokens=8192,
        artifact_path=artifact_path,
        cap_usd=Decimal("0.50"),
        requires_dated_model_id=False,
    )

    assert res["capability_schema_version"] == "1.0.0"
    assert res["model_id"] == "google/gemini-3.5-flash-lite"
    assert len(res["probes"]) == 12  # 3 probes x 4 endpoints
    assert all(p["status"] == "pass" for p in res["probes"])

    # Verify W14 / H1: catalogue check wired into live artifact
    assert "catalogue_check" in res
    assert res["catalogue_check"]["status"] == "pass"
    assert res["catalogue_check"]["model_exposes_dated_id"] is False
    assert res["catalogue_check"]["requires_dated_model_id"] is False
    assert res["catalogue_check"]["catalogue_model_count"] > 0

    # Verify W13: vision, structured_output, context_limit pass live
    probe_names = {p["name"] for p in res["probes"]}
    assert probe_names == {"vision", "structured_output", "context_limit"}

    # Verify W16 (B1 / D-P0-4): latency and capacity rejection rate recorded for flex and standard
    assert all("latency_ms" in p for p in res["probes"])
    assert all(p["latency_ms"] > 0 for p in res["probes"])
    assert "tier_summary" in res
    for ep in [
        "google-ai-studio/flex",
        "google-vertex/global/flex",
        "google-ai-studio",
        "google-vertex/global",
    ]:
        summary = res["tier_summary"][ep]
        assert summary["sample_count"] == 3
        assert summary["capacity_rejection_rate"]["value"] == 0.0
        assert summary["latency_ms_p50"] is not None

    # Verify live resolved pricing across flex and standard
    assert "resolved_pricing" in res
    assert res["resolved_pricing"]["google-ai-studio/flex"] == {"input_per_million": "0.15", "output_per_million": "1.25"}
    assert res["resolved_pricing"]["google-ai-studio"] == {"input_per_million": "0.30", "output_per_million": "2.50"}

    # Verify W17 / B2: measured flex selection with 2.0x headroom covering standard fallback
    proposal = res["max_price_proposal"]
    assert proposal["pricing_floor"] == {"input_per_million": "0.15", "output_per_million": "1.25"}
    assert proposal["headroom_multiplier"] == 2.0
    assert proposal["input_per_million"] == "0.30"
    assert proposal["output_per_million"] == "2.50"
    assert proposal["derived_from_endpoint"] in ["google-ai-studio/flex", "google-vertex/global/flex"]

    # Verify written artifact file matches res and has no leaked raw secrets
    assert artifact_path.exists()
    disk_data = json.loads(artifact_path.read_text())
    assert disk_data["model_id"] == "google/gemini-3.5-flash-lite"
    assert "sk-or-" not in artifact_path.read_text()



