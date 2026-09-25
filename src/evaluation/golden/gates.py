"""Deterministic Phase 2 release gates; authoring remains outside this module."""

import hashlib
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

HARD_FAIL_PATTERNS = (
    re.compile(r"[Ss]ession\s*\d+"),
    re.compile(r"\b\w*\d+\.(?:png|jpe?g)\b", re.IGNORECASE),
    re.compile(r"session\d+/", re.IGNORECASE),
)
FLAG_FOR_REVIEW_PATTERNS = (re.compile(r"\bIn this conversation,", re.IGNORECASE),)


class ReleaseGateError(ValueError):
    """Raised when a Phase 2 / Phase 2.5 release gate fails closed."""


def validate_multi_hop_judgment(
    judgment_records: list[dict[str, Any]],
    golden_rows: list[dict[str, Any]],
    messages: dict[str, Any] | None = None,
) -> None:
    """Validate multi_hop_judgment records against release rows and message corpus.

    Enforces:
    1. Exact multiset equality between judgment query_ids and multi_hop_evidence rows.
    2. Every judgment verdict must be 'sufficient'. Any non-sufficient verdict fails closed.
    3. Output hash integrity: rec['output_hash'] == row['output_hash'].
    4. Evidence message hash bindings match exact canonical bytes in message corpus.
    """
    mh_rows = [r for r in golden_rows if r.get("stratum") == "multi_hop_evidence"]
    if len(judgment_records) != len(mh_rows):
        raise ReleaseGateError(
            f"Multi-hop judgment count mismatch: {len(judgment_records)} records vs {len(mh_rows)} multi_hop_evidence rows"
        )

    j_qids = [r.get("query_id") for r in judgment_records]
    mh_qids = [r.get("query_id") for r in mh_rows]
    if len(set(j_qids)) != len(j_qids):
        raise ReleaseGateError(
            "Duplicate query_ids found in multi-hop judgment records"
        )
    if Counter(j_qids) != Counter(mh_qids):
        raise ReleaseGateError(
            "Multi-hop judgment query_id multiset mismatch between judgments and release rows"
        )

    rows_by_qid = {r["query_id"]: r for r in mh_rows}
    for rec in judgment_records:
        qid = rec.get("query_id")
        verdict = rec.get("verdict")
        if verdict != "sufficient":
            raise ReleaseGateError(
                f"Multi-hop judgment release gate failed: query {qid} has non-sufficient verdict '{verdict}'"
            )

        row = rows_by_qid.get(qid)
        if not row:
            raise ReleaseGateError(f"Multi-hop judgment references unknown query_id {qid}")

        if rec.get("output_hash") != row.get("output_hash"):
            raise ReleaseGateError(
                f"Output hash mismatch for {qid}: judgment {rec.get('output_hash')} != row {row.get('output_hash')}"
            )

        bindings = rec.get("evidence_bindings", [])
        gold_mids = row.get("gold_evidence_message_ids", [])
        if len(bindings) != len(gold_mids):
            raise ReleaseGateError(
                f"Evidence binding count mismatch for {qid}: {len(bindings)} bindings vs {len(gold_mids)} evidence message IDs"
            )

        for b, mid in zip(bindings, gold_mids, strict=True):
            if b.get("message_id") != mid:
                raise ReleaseGateError(
                    f"Evidence binding message_id mismatch for {qid}: {b.get('message_id')} != {mid}"
                )
            if messages is not None:
                m_obj = messages.get(mid)
                if m_obj is None:
                    raise ReleaseGateError(f"Evidence message {mid} not found in message corpus")
                m_bytes = json.dumps(m_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
                expected_hash = hashlib.sha256(m_bytes).hexdigest()
                if b.get("message_hash") != expected_hash:
                    raise ReleaseGateError(
                        f"Evidence message hash mismatch for {qid} on {mid}: {b.get('message_hash')} != {expected_hash}"
                    )


def validate_multi_hop_audit(
    audit_records: Iterable[dict[str, Any]],
    golden_rows: Iterable[dict[str, Any]],
    messages: dict[str, Any] | None = None,
    expected_reviewer: str = "codex",
    expected_gold_sha256: str | None = None,
) -> None:
    """Validate Codex G11d multi-hop audit records against released golden rows and corpus messages (F-149).

    Enforces:
    1. Exact multiset equality between audit query_ids and multi_hop_evidence rows.
    2. Every audit verdict must be 'sufficient'. Any non-sufficient verdict fails closed.
    3. Reviewer key must match expected_reviewer (default 'codex').
    4. Output hash integrity: rec['output_hash'] == row['output_hash'].
    5. Evidence message hash bindings match exact canonical bytes in message corpus.
    6. Candidate gold_sha256 binding: if rec carries gold_sha256, it must match expected_gold_sha256.
    """
    records_list = list(audit_records)
    mh_rows = [r for r in golden_rows if r.get("stratum") == "multi_hop_evidence"]
    if len(records_list) != len(mh_rows):
        raise ReleaseGateError(
            f"Multi-hop audit count mismatch: {len(records_list)} records vs {len(mh_rows)} multi_hop_evidence rows"
        )

    a_qids = [r.get("query_id") for r in records_list]
    mh_qids = [r.get("query_id") for r in mh_rows]
    if len(set(a_qids)) != len(a_qids):
        raise ReleaseGateError("Duplicate query_ids found in multi-hop audit records")
    if Counter(a_qids) != Counter(mh_qids):
        raise ReleaseGateError(
            "Multi-hop audit query_id multiset mismatch between audit and release rows"
        )

    rows_by_qid = {r["query_id"]: r for r in mh_rows}
    for rec in records_list:
        qid = rec.get("query_id")
        verdict = rec.get("verdict")
        if verdict != "sufficient":
            raise ReleaseGateError(
                f"Multi-hop audit release gate failed: query {qid} has non-sufficient verdict '{verdict}'"
            )

        if expected_reviewer and rec.get("reviewer_key") != expected_reviewer:
            raise ReleaseGateError(
                f"Multi-hop audit reviewer mismatch for {qid}: expected '{expected_reviewer}', got '{rec.get('reviewer_key')}'"
            )

        row = rows_by_qid.get(qid)
        if not row:
            raise ReleaseGateError(f"Multi-hop audit references unknown query_id {qid}")

        if rec.get("output_hash") != row.get("output_hash"):
            raise ReleaseGateError(
                f"Output hash mismatch in audit for {qid}: audit {rec.get('output_hash')} != row {row.get('output_hash')}"
            )

        if expected_gold_sha256 is not None:
            rec_gold_sha = rec.get("gold_sha256")
            if not rec_gold_sha:
                raise ReleaseGateError(
                    f"Candidate gold_sha256 binding missing in audit for {qid}: expected '{expected_gold_sha256}'"
                )
            if not isinstance(rec_gold_sha, str) or rec_gold_sha != expected_gold_sha256:
                raise ReleaseGateError(
                    f"Gold SHA-256 mismatch in audit for {qid}: audit has '{rec_gold_sha}', expected '{expected_gold_sha256}'"
                )

        bindings = rec.get("evidence_bindings", [])
        gold_mids = row.get("gold_evidence_message_ids", [])
        if len(bindings) != len(gold_mids):
            raise ReleaseGateError(
                f"Evidence binding count mismatch in audit for {qid}: {len(bindings)} bindings vs {len(gold_mids)} evidence message IDs"
            )

        for b, mid in zip(bindings, gold_mids, strict=True):
            if b.get("message_id") != mid:
                raise ReleaseGateError(
                    f"Evidence binding message_id mismatch in audit for {qid}: {b.get('message_id')} != {mid}"
                )
            if messages is not None:
                m_obj = messages.get(mid)
                if m_obj is None:
                    raise ReleaseGateError(f"Evidence message {mid} not found in message corpus")
                m_bytes = json.dumps(m_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
                expected_hash = hashlib.sha256(m_bytes).hexdigest()
                if b.get("message_hash") != expected_hash:
                    raise ReleaseGateError(
                        f"Evidence message hash mismatch in audit for {qid} on {mid}: {b.get('message_hash')} != {expected_hash}"
                    )




def validate_gold_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enforce the text-answer/media-answer split before a gold release is frozen."""

    values = list(rows)
    seen: set[str] = set()
    for row in values:
        query_id = row.get("query_id")
        if not isinstance(query_id, str) or not query_id or query_id in seen:
            raise ValueError("gold rows require unique query_id values")
        seen.add(query_id)
        action = row.get("expected_action")
        media = row.get("gold_media_ids", [])
        answer = row.get("gold_answer", "")
        is_media = row.get("answer_is_media", False)
        if action == "return_result":
            if is_media and (not media or answer):
                raise ValueError("media answers require media gold and an empty gold_answer")
            if not is_media and (media or not isinstance(answer, str) or not answer.strip()):
                raise ValueError("text answers require one non-empty gold_answer and no media gold")
        elif action == "clarify":
            if media or answer or not row.get("acceptable_clarification_targets"):
                raise ValueError("clarify rows require clarification targets only")
        elif action == "no_result":
            if media or answer:
                raise ValueError("no_result rows must have empty answer and media gold")
        else:
            raise ValueError("gold row has an unknown expected_action")
    return values


def vocabulary_violations(rows: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    """Return hard-fail identifiers found in released query and answer text."""

    violations: dict[str, list[str]] = {}
    for row in rows:
        query_id = str(row.get("query_id", "<missing>"))
        text = "\n".join(str(row.get(field, "")) for field in ("query", "gold_answer"))
        matches = [pattern.pattern for pattern in HARD_FAIL_PATTERNS if pattern.search(text)]
        if matches:
            violations[query_id] = matches
    return violations


def vocabulary_flags(rows: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    """Report naturalness concerns without turning them into release blockers."""

    flags: dict[str, list[str]] = {}
    for row in rows:
        query_id = str(row.get("query_id", "<missing>"))
        text = "\n".join(str(row.get(field, "")) for field in ("query", "gold_answer"))
        matches = [pattern.pattern for pattern in FLAG_FOR_REVIEW_PATTERNS if pattern.search(text)]
        if matches:
            flags[query_id] = matches
    return flags


def run_vocabulary_gate(path: str | Path) -> int:
    """Run the hard-fail vocabulary gate over a JSONL release artifact."""

    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]
    violations = vocabulary_violations(rows)
    if violations:
        print(json.dumps({"violations": violations}, sort_keys=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m evaluation.golden.gates PATH.jsonl")
    raise SystemExit(run_vocabulary_gate(sys.argv[1]))
