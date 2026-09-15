from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TRACE_SCHEMA_VERSION = "1.0.0"
ACTIONS = {
    "retrieve_lexical", "retrieve_dense", "retrieve_visual", "filter_metadata",
    "expand_context", "extract_clues", "resolve_bridge", "jump", "vlm_extract", "stop",
}
BLOCK_REASONS = {"no_unused_clue", "visited_query_fingerprint", "no_information_gain", "budget_exhausted"}
ID_LIST_FIELDS = {"context_message_ids", "destination_anchor_ids", "new_anchor_ids", "used_clue_ids", "supporting_round_event_ids", "missing_constraints_before_jump", "constraints_gained"}
RELATIONS = {"reply_to", "same_sender", "participant", "temporal", "semantic", "inferred"}
CONSTRAINTS = {"person", "place", "time", "object", "event", "target"}
PAYLOAD_FIELDS = {
    "query_started": set(), "result": {"media_id", "parent_message_id", "visual_score", "context_message_ids", "structural_relations", "supported_constraints", "supporting_round_event_ids"},
    "llm_call": {"role", "model_id", "resolved_provider", "resolved_endpoint", "prompt_version", "input_tokens", "output_tokens", "cost_usd", "latency_ms"},
    "query_completed": {"outcome", "result_event_id", "total_latency_ms"},
}
ROUND_FIELDS = {
    "retrieve_lexical": {"action", "anchor_ids", "new_anchor_ids"},
    "retrieve_dense": {"action", "anchor_ids", "new_anchor_ids"},
    "retrieve_visual": {"action", "anchor_ids", "new_anchor_ids"},
    "filter_metadata": {"action", "anchor_ids", "new_anchor_ids"},
    "expand_context": {"action", "expansion_strategy", "strategy", "anchor_ids", "expanded_message_ids", "new_anchor_ids"},
    "extract_clues": {"action", "clue_ids"}, "resolve_bridge": {"action", "anchor_ids", "new_anchor_ids"},
    "vlm_extract": {"action", "media_ids"}, "stop": {"action"},
    "jump": {"action", "outcome", "jump_query", "missing_constraints_before_jump", "used_clue_ids", "destination_anchor_ids", "new_anchor_ids", "constraints_gained", "block_reason", "evaluated_anchors"},
}
SECRET = re.compile(r"(?:^|[^a-z0-9])(?:sk-|or-)[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE)


def _ids(value: object, field: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.replace("_", "").replace("-", "").isalnum() for item in value):
        raise ValueError(f"{field} must contain identifier values only")


def _forbid_secret(value: object) -> None:
    if isinstance(value, str) and SECRET.search(value):
        raise ValueError("trace records cannot contain secrets")
    if isinstance(value, dict):
        for item in value.values(): _forbid_secret(item)
    if isinstance(value, list):
        for item in value: _forbid_secret(item)


class TraceRecord(BaseModel):
    """Closed trace envelope; payload fields are validated by record/action-specific rules."""

    model_config = ConfigDict(extra="forbid")
    trace_schema_version: Literal[TRACE_SCHEMA_VERSION]
    run_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    query_id: str = Field(pattern=r"^(q_[a-z0-9_]+|[0-9a-f-]{36})$")
    event_id: str = Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    parent_event_id: str | None = Field(default=None, pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    record_type: Literal["query_started", "round", "result", "llm_call", "query_completed"]
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_contract(self) -> TraceRecord:
        p = self.payload
        _forbid_secret(p)
        allowed = PAYLOAD_FIELDS.get(self.record_type)
        if allowed is not None and set(p) - allowed:
            raise ValueError("payload contains fields outside its trace contract")
        for field in ID_LIST_FIELDS.intersection(p):
            _ids(p[field], field)
        if self.record_type == "round":
            action = p.get("action")
            if action not in ACTIONS:
                raise ValueError("round action must be in the closed action enum")
            if set(p) - ROUND_FIELDS[action]:
                raise ValueError("round payload contains fields outside its action contract")
            if action == "jump":
                outcome = p.get("outcome")
                if outcome not in {"executed", "blocked"}:
                    raise ValueError("jump requires executed or blocked outcome")
                if outcome == "executed":
                    required = {"jump_query", "missing_constraints_before_jump", "used_clue_ids", "destination_anchor_ids", "new_anchor_ids", "constraints_gained"}
                    if not required.issubset(p):
                        raise ValueError("executed jump is missing required evidence")
                    _ids(p["used_clue_ids"], "used_clue_ids")
                    query = p["jump_query"]
                    if not isinstance(query, dict) or set(query) - {"clue_ids", "filters"}:
                        raise ValueError("jump_query must be structured IDs and filters")
                    _ids(query.get("clue_ids"), "jump_query.clue_ids")
                else:
                    reason = p.get("block_reason")
                    if reason not in BLOCK_REASONS:
                        raise ValueError("blocked jump requires block_reason from the closed enum")
                    if reason != "no_unused_clue" and not p.get("used_clue_ids"):
                        raise ValueError("blocked jump reason requires used_clue_ids")
            if action == "expand_context" and {"jump_query", "used_clue_ids"}.intersection(p):
                raise ValueError("context expansion cannot carry jump evidence")
        if self.record_type == "result":
            if not set(p.get("structural_relations", [])).issubset(RELATIONS):
                raise ValueError("structural_relations must use the closed relation enum")
            if not set(p.get("supported_constraints", [])).issubset(CONSTRAINTS):
                raise ValueError("supported_constraints must use the closed constraint enum")
        if self.record_type == "query_completed":
            answered = p.get("outcome") == "answered"
            if answered != bool(p.get("result_event_id")):
                raise ValueError("answered completion requires exactly one final result reference")
        return self


def validate_trace(records: Iterable[TraceRecord]) -> list[TraceRecord]:
    """Validate causal references after the complete append-only stream is available."""
    values = list(records)
    seen: dict[str, TraceRecord] = {}
    for record in values:
        if record.event_id in seen:
            raise ValueError("duplicate trace event id")
        if record.parent_event_id:
            parent = seen.get(record.parent_event_id)
            if parent is None or parent.query_id != record.query_id:
                raise ValueError("trace parent must exist earlier in the same query")
        seen[record.event_id] = record
    for record in values:
        if record.record_type == "query_completed" and (result_id := record.payload.get("result_event_id")):
            result = seen.get(result_id)
            if result is None or result.record_type != "result" or result.query_id != record.query_id:
                raise ValueError("final result reference must target a result in the same query")
    return values
