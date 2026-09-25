from __future__ import annotations

from decimal import Decimal
from math import ceil
from typing import Any

from trace.records import BLOCK_REASONS, TraceRecord


def _result(value: Any, size: int) -> dict[str, Any]:
    return {"value": value, "sample_size": size}


def _percentile(values: list[int], percentile: float) -> dict[str, Any]:
    if not values:
        return _result(None, 0)
    ordered = sorted(values)
    return _result(ordered[ceil(percentile * len(ordered)) - 1], len(ordered))


def compute_metrics(records: list[TraceRecord]) -> dict[str, Any]:
    """Derive metrics solely from trace records; never trust a controller success label."""
    completed = [r for r in records if r.record_type == "query_completed"]
    finals = {r.query_id: r.payload.get("result_event_id") for r in completed}
    results = {r.event_id: r for r in records if r.record_type == "result"}
    jumps = [r for r in records if r.record_type == "round" and r.payload.get("action") == "jump"]
    executed = [r for r in jumps if r.payload.get("outcome") == "executed"]
    successful = []
    for jump in executed:
        final = results.get(finals.get(jump.query_id))
        if final and jump.event_id in final.payload.get("supporting_round_event_ids", []):
            successful.append(jump)
    no_gain = [r for r in executed if not r.payload.get("constraints_gained") and not r.payload.get("new_anchor_ids")]
    used = {clue for jump in executed for clue in jump.payload.get("used_clue_ids", [])}
    useful = {clue for jump in successful for clue in jump.payload.get("used_clue_ids", [])}
    latencies = [r.payload["total_latency_ms"] for r in completed]
    call_latencies = [r.payload["latency_ms"] for r in records if r.record_type == "llm_call"]
    costs: dict[str, Decimal] = {r.query_id: Decimal(0) for r in completed}
    for r in records:
        if r.record_type == "llm_call":
            costs[r.query_id] = costs.get(r.query_id, Decimal(0)) + Decimal(r.payload["cost_usd"])
    denominator = len(executed)
    rounds = [r for r in records if r.record_type == "round"]
    searches = {"retrieve_lexical", "retrieve_dense", "retrieve_visual", "filter_metadata"}
    blocked = {reason: 0 for reason in BLOCK_REASONS}
    for jump in jumps:
        if jump.payload.get("outcome") == "blocked":
            blocked[jump.payload["block_reason"]] += 1
    return {
        "jump_success_rate": _result(len(successful) / denominator if denominator else None, denominator),
        "no_gain_jump_rate": _result(len(no_gain) / denominator if denominator else None, denominator),
        "useful_clue_precision": _result(len(useful) / len(used) if used else None, len(used)),
        "average_jumps_per_query": _result(len(executed) / len(completed) if completed else None, len(completed)),
        "loop_prevention_count": blocked,
        "average_rounds": _result(len(rounds) / len(completed) if completed else None, len(completed)),
        "search_calls": _result(sum(r.payload.get("action") in searches for r in rounds) if completed else None, len(completed)),
        "llm_calls": _result(sum(r.record_type == "llm_call" and r.payload.get("role") != "vlm_extract" for r in records) if completed else None, len(completed)),
        "vlm_calls": _result(sum(r.record_type == "llm_call" and r.payload.get("role") == "vlm_extract" for r in records) if completed else None, len(completed)),
        "system_latency_p50": _percentile(latencies, 0.5),
        "system_latency_p95": _percentile(latencies, 0.95),
        "per_call_latency_p50": _percentile(call_latencies, 0.5),
        "per_call_latency_p95": _percentile(call_latencies, 0.95),
        "cost_per_query": _result({key: str(value) for key, value in costs.items()} if completed else None, len(completed)),
    }
