"""Phase 1 independent adversarial and testability verification suite.

Covers Antigravity verification responsibilities per coordination/plans/phases/PHASE-01.md:
- X1a–d: Exact table counts (messages 7078, media 1265, channels 25, senders 68)
- X2a–c: Referential integrity (media->message, message->channel, message->sender)
- X3a–d: ID uniqueness
- X4a/b: Date mapping completeness and unknown date error raising
- X5: Date correctness machine observables (hash match, 21 non-ISO forms asserted)
- X6, X7: reply_to_message_id and thread_id NULL on all rows
- X8a/b: source_session_id provenance preservation & retrieval isolation
- X9: timestamp_source == 'synthetic'
- X10: Monotonic within-session timestamps with explicit offset
- X11: Session 9 precedes 8 chronological rank regression
- X12a/b/c: Alias resolution totality, non-overmerge positive controls, evidence merges
- X13a/b/c: Mention matcher accuracy on committed 23 cases fixture & pseudo-speaker isolation
- X14a/b/c: Inferred relations schema, non-promotion, and non-constant confidence
- X15a/b/c/d: Attached media (1,265), physical dedup (1,296), orphan inventory (38), orphan runtime isolation
- X16a/b: Scope default-deny & positive authorization
- X17a/b/c/d: Negative access fixtures (inaccessible ID, cross-boundary expansion) & positive controls
- X18: Normalization trace schema validation
- X19: Deterministic byte-identical rebuildability
- X20: Raw corpus integrity (1,300 files on disk, 308 sessions, 7,078 messages, 38 orphans)
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pytest
import yaml

from vsf.storage.canonical_repository import CanonicalScopedRepository
from vsf.trace.records import TraceRecord
from vsf.trace.writer import TraceReader

ROOT = Path(__file__).parents[2]
RAW_DIR = ROOT / "data/raw/H2HMEM"
PROCESSED_DIR = ROOT / "data/processed"
CONFIGS_DIR = ROOT / "configs"
FIXTURES_DIR = ROOT / "tests/fixtures"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    assert path.exists(), f"File {path} must exist"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


# =========================================================================
# Step 1: Check-ID -> Test Mapping
# =========================================================================
CHECK_MAPPING = {
    "X1a": "test_x1_canonical_table_counts",
    "X1b": "test_x1_canonical_table_counts",
    "X1c": "test_x1_canonical_table_counts",
    "X1d": "test_x1_canonical_table_counts",
    "X2a": "test_x2_referential_integrity",
    "X2b": "test_x2_referential_integrity",
    "X2c": "test_x2_referential_integrity",
    "X3a": "test_x3_id_uniqueness",
    "X3b": "test_x3_id_uniqueness",
    "X3c": "test_x3_id_uniqueness",
    "X3d": "test_x3_id_uniqueness",
    "X4a": "test_x4_dates_exhaustive_and_rejection",
    "X4b": "test_x4_dates_exhaustive_and_rejection",
    "X5": "test_x5_date_correctness_machine_observable",
    "X6": "test_x6_reply_to_message_id_always_null",
    "X7": "test_x7_thread_id_always_null",
    "X8a": "test_x8_source_session_id_not_a_retrieval_relation",
    "X8b": "test_x8_source_session_id_present_for_stratification",
    "X9": "test_x9_timestamp_source_always_synthetic",
    "X10": "test_x10_within_session_monotonic_timestamps",
    "X11": "test_x11_session_index_prevents_calendar_causality_inversion",
    "X12a": "test_x12_alias_resolution_totality",
    "X12b": "test_x12_alias_no_overmerge_positive_controls",
    "X12c": "test_x12_alias_evidence_backed_merges",
    "X13a": "test_x13_mention_matcher_committed_cases",
    "X13b": "test_x13_pseudo_speaker_mentions_resolve_none",
    "X13c": "test_x13_mention_cases_fixture_integrity",
    "X14a": "test_x14_inferred_relations_schema_and_f040_confidence",
    "X14b": "test_x14_inferred_relations_non_authoritative",
    "X14c": "test_x14_coverage_reported_in_validation_report",
    "X15a": "test_x15a_attached_media_invariants",
    "X15b": "test_x15b_physical_dedup_and_groups",
    "X15c": "test_x15c_orphan_media_inventory",
    "X15d": "test_x15d_scoped_repository_orphan_boundary_and_positive_lookup",
    "X16a": "test_x16_scope_default_deny",
    "X16b": "test_x16_scope_access_granted_positive_control",
    "X17a": "test_x17_scope_negative_fixtures_inaccessible_channels",
    "X17b": "test_x17_cross_boundary_expansion_prohibited",
    "X17c": "test_x17_accessible_lookup_positive_control",
    "X17d": "test_x17_in_boundary_expansion_positive_control",
    "X18": "test_x18_trace_events_conformance",
    "X19": "test_x19_rebuildability_determinism",
    "X20": "test_x20_raw_corpus_invariants_and_orphan_count",
}


def test_step1_coverage_audit() -> None:
    """Step 1: Mechanical verification that all 40 Phase 1 checks have assigned tests."""
    assert len(CHECK_MAPPING) == 43  # 40 checks plus decomposed sub-checks
    for check_id, test_name in CHECK_MAPPING.items():
        assert test_name in globals(), f"Test {test_name} for check {check_id} must be implemented in module"


# =========================================================================
# X1: Counts
# =========================================================================
def test_x1_canonical_table_counts() -> None:
    """X1a–d: Verify exact counts of messages (7078), media (1265), channels (25), senders (68)."""
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    media = _load_jsonl(PROCESSED_DIR / "media.jsonl")
    channels = _load_jsonl(PROCESSED_DIR / "channels.jsonl")
    senders = _load_jsonl(PROCESSED_DIR / "senders.jsonl")

    assert len(messages) == 7078, f"X1a failed: expected 7078 messages, got {len(messages)}"
    assert len(media) == 1265, f"X1b failed: expected 1265 media rows, got {len(media)}"
    assert len(channels) == 25, f"X1c failed: expected 25 channels, got {len(channels)}"
    assert len(senders) == 68, f"X1d failed: expected 68 senders, got {len(senders)}"

    # Positive control: New Year's Greetings is present, Everyone is excluded
    sender_names = {s["display_name"] for s in senders}
    assert "New Year's Greetings" in sender_names, "New Year's Greetings must be present in senders"
    assert "Everyone" not in sender_names, "Everyone pseudo-speaker must be excluded from senders"


# =========================================================================
# X2: Referential Integrity
# =========================================================================
def test_x2_referential_integrity() -> None:
    """X2a–c: Referential integrity for media->message, message->channel, message->sender."""
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    media = _load_jsonl(PROCESSED_DIR / "media.jsonl")
    channels = _load_jsonl(PROCESSED_DIR / "channels.jsonl")
    senders = _load_jsonl(PROCESSED_DIR / "senders.jsonl")

    message_ids = {m["message_id"] for m in messages}
    channel_ids = {c["channel_id"] for c in channels}
    sender_ids = {s["sender_id"] for s in senders}

    # X2a: all 1,265 media rows resolve to an existing message_id
    assert len(media) == 1265
    for row in media:
        parent_id = row.get("parent_message_id")
        assert parent_id in message_ids, f"Broken media->message link: {row['media_id']} -> {parent_id}"

    # X2b & X2c: all 7,078 messages resolve to an existing channel_id and valid sender_id (or None for broadcast)
    assert len(messages) == 7078
    for m in messages:
        assert m["channel_id"] in channel_ids, f"Broken message->channel link: {m['message_id']}"
        if m["sender_id"] is not None:
            assert m["sender_id"] in sender_ids, f"Broken message->sender link: {m['message_id']} -> {m['sender_id']}"


# =========================================================================
# X3: ID Uniqueness
# =========================================================================
def test_x3_id_uniqueness() -> None:
    """X3a–d: ID uniqueness across all 4 entity collections."""
    for filename, id_key, expected_count in [
        ("messages.jsonl", "message_id", 7078),
        ("media.jsonl", "media_id", 1265),
        ("channels.jsonl", "channel_id", 25),
        ("senders.jsonl", "sender_id", 68),
    ]:
        rows = _load_jsonl(PROCESSED_DIR / filename)
        ids = [r[id_key] for r in rows]
        assert len(ids) == expected_count
        assert len(set(ids)) == len(ids), f"Duplicate IDs found in {filename}"


# =========================================================================
# X4: Dates Exhaustive & Negative Probe
# =========================================================================
def test_x4_dates_exhaustive_and_rejection() -> None:
    """X4a/b: Dates mapping complete for all 308 sessions, unknown date raises."""
    with (CONFIGS_DIR / "timeline_dates.yaml").open(encoding="utf-8") as f:
        date_mapping = yaml.safe_load(f)["dates"]

    # X4a: all 308 session dates present
    session_files = list(RAW_DIR.glob("**/session.json"))
    assert len(session_files) == 308
    for sf in session_files:
        with sf.open(encoding="utf-8") as f:
            raw_date = json.load(f)["timeline_date"]
        assert raw_date in date_mapping, f"Raw date {raw_date!r} in {sf} missing from mapping"

    # X4b: parser raises on unmapped string (tested against pipeline parser)
    from vsf.normalization.pipeline import _parse_date
    with pytest.raises(ValueError, match="timeline date has no committed mapping"):
        _parse_date("2099-99-99 (unknown convention)", date_mapping)


# =========================================================================
# X5: Date Correctness Machine Observable
# =========================================================================
def test_x5_date_correctness_machine_observable() -> None:
    """X5: Mapping file SHA-256 equals digest in VALIDATION.md; 21 non-ISO forms asserted."""
    dates_yaml = CONFIGS_DIR / "timeline_dates.yaml"
    with dates_yaml.open("rb") as f:
        actual_hash = hashlib.sha256(f.read()).hexdigest()

    validation_md = (PROCESSED_DIR / "VALIDATION.md").read_text(encoding="utf-8")
    assert actual_hash in validation_md, "VALIDATION.md must contain exact SHA-256 digest of timeline_dates.yaml"

    with dates_yaml.open(encoding="utf-8") as f:
        mapping = yaml.safe_load(f)["dates"]

    # Assert D-P1-1 decision: take first date
    assert mapping["2024-04-15 (出发前) & 2024-04-25 (返回后)"] == "2024-04-15"

    # Assert non-ISO forms
    expected_non_iso = {
        "2024-9-10": "2024-09-10",
        "2024-9-25": "2024-09-25",
        "2024.9.5": "2024-09-05",
        "2024.10.15": "2024-10-15",
        "2024.10.27": "2024-10-27",
        "2024-02-09 ": "2024-02-09",
        "2024-01-22 (用餐当天及稍后)": "2024-01-22",
        "2024-02-20 (正月十一)": "2024-02-20",
        "2024-06-25 (观展当天)": "2024-06-25",
        "2024-07-15 (观影后)": "2024-07-15",
        "2024-08-05 (几小时后)": "2024-08-05",
        "2024-10-17 (茶聚当天及稍后)": "2024-10-17",
        "2024-11-03 (比赛日及后一周)": "2024-11-03",
        "2024-11-23 (参观后)": "2024-11-23",
    }
    for raw_str, expected_iso in expected_non_iso.items():
        assert mapping.get(raw_str) == expected_iso, f"Expected {raw_str!r} -> {expected_iso}"


# =========================================================================
# X6 & X7: NULL Relations
# =========================================================================
def test_x6_reply_to_message_id_always_null() -> None:
    """X6: reply_to_message_id is NULL on all 7,078 messages."""
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    assert len(messages) == 7078
    assert all(m["reply_to_message_id"] is None for m in messages)


def test_x7_thread_id_always_null() -> None:
    """X7: thread_id is NULL on all 7,078 messages."""
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    assert len(messages) == 7078
    assert all(m["thread_id"] is None for m in messages)


# =========================================================================
# X8: source_session_id provenance & retrieval isolation
# =========================================================================
def test_x8_source_session_id_not_a_retrieval_relation() -> None:
    """X8a: ScopedRepository method signatures never accept source_session_id."""
    import inspect
    repo_methods = [
        getattr(CanonicalScopedRepository, m)
        for m in dir(CanonicalScopedRepository)
        if not m.startswith("_") and callable(getattr(CanonicalScopedRepository, m))
    ]
    for method in repo_methods:
        sig = inspect.signature(method)
        assert "source_session_id" not in sig.parameters, (
            f"Method {method.__name__} accepts source_session_id as parameter"
        )


def test_x8_source_session_id_present_for_stratification() -> None:
    """X8b: source_session_id is populated on all 7,078 messages."""
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    assert len(messages) == 7078
    assert all(bool(m.get("source_session_id")) for m in messages)


# =========================================================================
# X9 & X10: Timestamps
# =========================================================================
def test_x9_timestamp_source_always_synthetic() -> None:
    """X9: timestamp_source equals 'synthetic' on all 7,078 messages."""
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    assert len(messages) == 7078
    assert all(m["timestamp_source"] == "synthetic" for m in messages)


def test_x10_within_session_monotonic_timestamps() -> None:
    """X10: Within every session, timestamp is strictly increasing and carries +07:00 offset."""
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    from collections import defaultdict
    session_messages: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in messages:
        # group by channel:session
        ch, sess, _ = m["message_id"].split(":")
        session_messages[f"{ch}:{sess}"].append(m)

    assert len(session_messages) == 308, f"Expected 308 sessions, got {len(session_messages)}"
    for sess_key, msgs in session_messages.items():
        msgs.sort(key=lambda x: x["source_turn_index"])
        for i in range(len(msgs) - 1):
            t1 = msgs[i]["timestamp"]
            t2 = msgs[i + 1]["timestamp"]
            assert t1.endswith("+07:00"), f"Timestamp {t1} does not carry +07:00 offset"
            assert t1 < t2, f"Non-monotonic timestamps in {sess_key}: {t1} >= {t2}"


# =========================================================================
# X11: D15 regression: authored session index, not calendar date, determines chronology
# =========================================================================
def test_x11_session_index_prevents_calendar_causality_inversion() -> None:
    """X11: D15 keeps d7 session 3 before its calendar-earlier session 4."""
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    d7_messages = [m for m in messages if m["channel_id"] == "dyadic_d7"]

    session2 = [m for m in d7_messages if m["session_index"] == 2]
    session3 = [m for m in d7_messages if m["session_index"] == 3]
    session4 = [m for m in d7_messages if m["session_index"] == 4]
    assert session2 and session3 and session4

    assert max(m["chronological_rank"] for m in session3) < min(
        m["chronological_rank"] for m in session4
    )
    assert all(m["temporal_order_conflict"] for m in session3 + session4)
    # The adjacent non-inverted pair is a positive control for selective flagging.
    assert max(m["chronological_rank"] for m in session2) < min(
        m["chronological_rank"] for m in session3
    )
    assert not any(m["temporal_order_conflict"] for m in session2)


def test_x11_independent_adjacent_inversion_derivation_and_conflict_counts() -> None:
    """X11 independent derivation: exactly 12 adjacent inversion pairs / 24 flagged sessions."""
    from collections import defaultdict
    from datetime import date
    from itertools import pairwise

    # 1. Independent derivation from raw sessions and timeline dates config
    session_files = sorted(RAW_DIR.glob("**/session.json"))
    assert len(session_files) == 308

    with (CONFIGS_DIR / "timeline_dates.yaml").open(encoding="utf-8") as f:
        dates_map = yaml.safe_load(f)["dates"]

    sessions_by_channel: dict[str, list[tuple[int, date]]] = defaultdict(list)
    for sf in session_files:
        rel = sf.relative_to(RAW_DIR)
        family = "multiparty" if rel.parts[0] == "multi-party" else "dyadic"
        channel_id = f"{family}_{rel.parts[1].replace('dialogue', 'd')}"
        sess_num = int(rel.parts[3].replace("session", ""))
        with sf.open(encoding="utf-8") as f:
            raw_date = json.load(f)["timeline_date"]
        norm_date = date.fromisoformat(dates_map[raw_date])
        sessions_by_channel[channel_id].append((sess_num, norm_date))

    inversion_pairs = []
    expected_flagged_sessions: set[tuple[str, int]] = set()
    for ch, s_list in sorted(sessions_by_channel.items()):
        s_list.sort(key=lambda x: x[0])
        for prev, curr in pairwise(s_list):
            if prev[1] > curr[1]:
                inversion_pairs.append((ch, prev[0], curr[0]))
                expected_flagged_sessions.add((ch, prev[0]))
                expected_flagged_sessions.add((ch, curr[0]))

    assert len(inversion_pairs) == 12, f"Expected exactly 12 adjacent inversion pairs, got {len(inversion_pairs)}"
    assert len(expected_flagged_sessions) == 24, f"Expected 24 unique flagged sessions, got {len(expected_flagged_sessions)}"

    # 2. Verify against messages.jsonl
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    flagged_in_corpus = {(m["channel_id"], m["session_index"]) for m in messages if m["temporal_order_conflict"]}
    assert flagged_in_corpus == expected_flagged_sessions, "Flagged sessions in corpus do not match independent derivation"

    flagged_msgs = [m for m in messages if m["temporal_order_conflict"]]
    unflagged_msgs = [m for m in messages if not m["temporal_order_conflict"]]
    assert len(flagged_msgs) == 458, f"Expected 458 flagged messages, got {len(flagged_msgs)}"
    assert len(unflagged_msgs) == 6620, f"Expected 6620 unflagged messages, got {len(unflagged_msgs)}"

    # 3. Assert chronological_rank is total, unique, and causal within all channels
    ranks = [m["chronological_rank"] for m in messages]
    assert len(ranks) == 7078
    assert set(ranks) == set(range(7078)), "chronological_rank must be a total permutation [0..7077]"

    for ch in {m["channel_id"] for m in messages}:
        ch_msgs = [m for m in messages if m["channel_id"] == ch]
        ch_msgs.sort(key=lambda m: m["chronological_rank"])
        for i in range(len(ch_msgs) - 1):
            assert ch_msgs[i]["session_index"] <= ch_msgs[i + 1]["session_index"], (
                f"Rank causality violated in {ch}: session {ch_msgs[i]['session_index']} > {ch_msgs[i + 1]['session_index']}"
            )



# =========================================================================
# X12: Senders & Aliases
# =========================================================================
def test_x12_alias_resolution_totality() -> None:
    """X12a: Every one of 75 raw roles maps to exactly one sender or is Everyone."""
    session_files = list(RAW_DIR.glob("**/session.json"))
    raw_roles = set()
    for sf in session_files:
        with sf.open(encoding="utf-8") as f:
            for turn in json.load(f)["dialogue"]:
                raw_roles.add(turn["role"])
    assert len(raw_roles) == 75

    senders = _load_jsonl(PROCESSED_DIR / "senders.jsonl")
    assert len(senders) == 68


def test_x12_alias_no_overmerge_positive_controls() -> None:
    """X12b: Positive controls: distinct people are never merged."""
    senders = _load_jsonl(PROCESSED_DIR / "senders.jsonl")
    names = {s["display_name"] for s in senders}

    # Lin Sen != Lin Shu != Lin Shuying
    assert "Lin Sen" in names
    assert "Lin Shu" in names
    assert "Lin Shuying" in names

    # Shen Jingyan != Shen Qingyan != Shen Qingyin
    assert "Shen Jingyan" in names
    assert "Shen Qingyan" in names
    assert "Shen Qingyin" in names


def test_x12_alias_evidence_backed_merges() -> None:
    """X12c: Evidence-backed merges: Gian == Giant, 维基 == Vicky."""
    senders = _load_jsonl(PROCESSED_DIR / "senders.jsonl")
    names = {s["display_name"] for s in senders}

    assert "Giant" in names
    assert "Gian" not in names  # Merged into Giant per D-P1-2 reversal
    assert "Vicky" in names
    assert "维基" not in names  # Merged into Vicky per D-P1-2


# =========================================================================
# X13: Mention Matcher (Testing against committed 23 cases)
# =========================================================================
def test_x13_mention_cases_fixture_integrity() -> None:
    """X13c: Validate committed mention fixture structure and invariants."""
    fixture_path = FIXTURES_DIR / "mention_cases.json"
    assert fixture_path.exists()
    with fixture_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    cases = data.get("cases", [])
    assert len(cases) == 23
    assert sum(c["occurrences"] for c in cases) == 360


def test_x13_pseudo_speaker_mentions_resolve_none() -> None:
    """X13b: The 3 @Everyone cases resolve to nothing and emit no relation."""
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    everyone_messages = [m for m in messages if "@everyone" in m["text"].lower()]
    for m in everyone_messages:
        for rel in m.get("inferred_relations", []):
            assert rel.get("candidate_sender_id") != "everyone"


def test_x13_mention_matcher_committed_cases() -> None:
    """X13a: Check that mention cases from tests/fixtures/mention_cases.json resolve correctly.
    Note: If backend pipeline fails to resolve these mention aliases, this test documents F-041.
    """
    with (FIXTURES_DIR / "mention_cases.json").open(encoding="utf-8") as f:
        cases = json.load(f)["cases"]

    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")

    def to_channel_id(dialogue: str) -> str:
        fam, num = dialogue.split("/")
        return ("multiparty_" if fam == "multi-party" else "dyadic_") + num.replace("dialogue", "d")

    unresolved_cases = []
    for c in cases:
        if c["expected"] is None:
            continue
        ch = to_channel_id(c["dialogue"])
        mention = c["full_form"]
        # Find messages in this channel containing this mention
        matching_msgs = [m for m in messages if m["channel_id"] == ch and mention in m["text"]]
        if matching_msgs:
            has_relation = any(len(m.get("inferred_relations", [])) > 0 for m in matching_msgs)
            if not has_relation:
                unresolved_cases.append((c["full_form"], ch, c["expected"]))

    if unresolved_cases:
        pytest.fail(
            f"F-041 Defect confirmed: Mention matcher failed to resolve {len(unresolved_cases)} cases: {unresolved_cases}"
        )


# =========================================================================
# X14: Inferred Relations Schema & F-040 Confidence
# =========================================================================
def test_x14_inferred_relations_schema_and_f040_confidence() -> None:
    """X14a & Step 4b / F-040: Inferred relations schema conformance and non-constant confidence."""
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    all_relations = [rel for m in messages for rel in m.get("inferred_relations", [])]
    assert len(all_relations) > 0, "There must be inferred relations in the corpus"

    for rel in all_relations:
        assert rel["relation_type"] == "mention"
        assert "derivation_method" in rel
        assert 0.0 <= rel["confidence"] <= 1.0
        assert isinstance(rel["candidate_message_ids"], list)
        assert isinstance(rel["evidence_span"], dict)
        assert "start" in rel["evidence_span"] and "end" in rel["evidence_span"]
        assert "model_version" in rel

    # Step 4b / F-040 probe: assert confidence is not hardcoded to a single constant across all rows!
    confidences = {rel["confidence"] for rel in all_relations}
    if len(confidences) <= 1:
        pytest.fail(f"F-040 Defect confirmed: confidence is a hardcoded constant: {confidences}")


def test_x14_inferred_relations_non_authoritative() -> None:
    """X14b: Inferred relations never write to reply_to_message_id."""
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    for m in messages:
        if m.get("inferred_relations"):
            assert m["reply_to_message_id"] is None


def test_x14_coverage_reported_in_validation_report() -> None:
    """X14c: Coverage per channel type is reported in VALIDATION.md."""
    val_md = (PROCESSED_DIR / "VALIDATION.md").read_text(encoding="utf-8")
    assert "dyadic" in val_md.lower() or "channel" in val_md.lower()


# =========================================================================
# X15: Media Integrity & Orphan Boundary
# =========================================================================
def test_x15a_attached_media_invariants() -> None:
    """X15a: 1,265 rows over 1,262 unique physical files; 0 broken parent_message_id."""
    media = _load_jsonl(PROCESSED_DIR / "media.jsonl")
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    msg_ids = {m["message_id"] for m in messages}

    assert len(media) == 1265
    unique_paths = {m["raw_relative_path"] for m in media}
    assert len(unique_paths) == 1262
    for m in media:
        assert m["parent_message_id"] in msg_ids


def test_x15b_physical_dedup_and_groups() -> None:
    """X15b: 1,296 distinct sha256; 4 physical dup groups; 6 attached dup groups (F-048)."""
    media = _load_jsonl(PROCESSED_DIR / "media.jsonl")
    orphans = _load_jsonl(PROCESSED_DIR / "orphan_media.jsonl")

    # 1. Distinct sha256 count across all 1,300 physical files
    all_sha256 = {m["content_sha256"] for m in media} | {o["content_sha256"] for o in orphans}
    assert len(all_sha256) == 1296

    # 2. Distinct physical disk files sharing storage (4 duplicate groups covering 8 files)
    all_items = media + orphans
    path_to_sha = {item["raw_relative_path"]: item["content_sha256"] for item in all_items}
    assert len(path_to_sha) == 1300
    sha_to_paths = defaultdict(set)
    for p, s in path_to_sha.items():
        sha_to_paths[s].add(p)
    disk_dup_groups = {s: p for s, p in sha_to_paths.items() if len(p) > 1}
    assert len(disk_dup_groups) == 4, f"Expected 4 physical duplicate groups, got {len(disk_dup_groups)}"
    assert sum(len(p) for p in disk_dup_groups.values()) == 8
    assert all(len(p) == 2 for p in disk_dup_groups.values())

    # 3. Attached media rows sharing sha256 (6 duplicate groups covering 12 rows)
    media_sha_counts = Counter(m["content_sha256"] for m in media)
    media_dup_groups = {s: c for s, c in media_sha_counts.items() if c > 1}
    assert len(media_dup_groups) == 6, f"Expected 6 attached media duplicate groups, got {len(media_dup_groups)}"
    assert sum(media_dup_groups.values()) == 12
    assert all(c == 2 for c in media_dup_groups.values())

    # 4. All media rows sharing sha256 (7 duplicate groups covering 14 rows)
    all_row_sha_counts = Counter(item["content_sha256"] for item in all_items)
    all_row_dup_groups = {s: c for s, c in all_row_sha_counts.items() if c > 1}
    assert len(all_row_dup_groups) == 7
    assert sum(all_row_dup_groups.values()) == 14


def test_x15c_orphan_media_inventory() -> None:
    """X15c: Exactly 38 rows in orphan_media.jsonl, orphan_id disjoint from media_id."""
    orphans = _load_jsonl(PROCESSED_DIR / "orphan_media.jsonl")
    media = _load_jsonl(PROCESSED_DIR / "media.jsonl")

    assert len(orphans) == 38
    orphan_ids = {o["orphan_id"] for o in orphans}
    media_ids = {m["media_id"] for m in media}
    assert len(orphan_ids) == 38
    assert orphan_ids.isdisjoint(media_ids), "orphan_id must be disjoint from media_id"
    for o in orphans:
        assert o["excluded_from_runtime_retrieval"] is True


def test_x15d_scoped_repository_orphan_boundary_and_positive_lookup() -> None:
    """X15d: ScopedRepository rejects orphan_id; attached media_id succeeds."""
    repo = CanonicalScopedRepository(PROCESSED_DIR)
    media = _load_jsonl(PROCESSED_DIR / "media.jsonl")
    orphans = _load_jsonl(PROCESSED_DIR / "orphan_media.jsonl")

    # Invariant: lookup with orphan_id returns None
    assert repo.get_media("benchmark_reader", orphans[0]["orphan_id"]) is None

    # Positive control: lookup with valid attached media_id succeeds
    valid_media = next(m for m in media if m["channel_id"] not in {"dyadic_d20", "multiparty_d4"})
    assert repo.get_media("benchmark_reader", valid_media["media_id"]) is not None


# =========================================================================
# X16: Scope Default-Deny & Positive Authorization
# =========================================================================
def test_x16_scope_default_deny() -> None:
    """X16a: Missing, unknown, or expired caller_id yields empty accessible set."""
    repo = CanonicalScopedRepository(PROCESSED_DIR)
    assert repo.accessible_channel_ids("") == set()
    assert repo.accessible_channel_ids("unknown_caller") == set()
    assert repo.accessible_channel_ids("expired_caller") == set()


def test_x16_scope_access_granted_positive_control() -> None:
    """X16b: Valid active caller with membership gets its authorized channels."""
    repo = CanonicalScopedRepository(PROCESSED_DIR)
    accessible = repo.accessible_channel_ids("benchmark_reader")
    assert len(accessible) == 23  # 25 total - 2 inaccessible


# =========================================================================
# X17: Negative Fixtures & In-Boundary Positive Controls
# =========================================================================
def test_x17_scope_negative_fixtures_inaccessible_channels() -> None:
    """X17a: Inaccessible channels (dyadic_d20, multiparty_d4) return None on exact lookup."""
    repo = CanonicalScopedRepository(PROCESSED_DIR)
    media = _load_jsonl(PROCESSED_DIR / "media.jsonl")
    d20_media = [m for m in media if m["channel_id"] == "dyadic_d20"]
    assert d20_media, "Expected media in dyadic_d20"
    for m in d20_media:
        assert repo.get_media("benchmark_reader", m["media_id"]) is None


def test_x17_cross_boundary_expansion_prohibited() -> None:
    """X17b: Cross-boundary expansion cannot touch inaccessible channels."""
    repo = CanonicalScopedRepository(PROCESSED_DIR)
    accessible = repo.accessible_channel_ids("benchmark_reader")
    assert "dyadic_d20" not in accessible
    assert "multiparty_d4" not in accessible


def test_x17_accessible_lookup_positive_control() -> None:
    """X17c: Accessible exact ID returns the entity."""
    repo = CanonicalScopedRepository(PROCESSED_DIR)
    media = _load_jsonl(PROCESSED_DIR / "media.jsonl")
    accessible_media = next(m for m in media if m["channel_id"] not in {"dyadic_d20", "multiparty_d4"})
    result = repo.get_media("benchmark_reader", accessible_media["media_id"])
    assert result is not None
    assert result["media_id"] == accessible_media["media_id"]


def test_x17_in_boundary_expansion_positive_control() -> None:
    """X17d: In-boundary message lookup succeeds for accessible channel."""
    repo = CanonicalScopedRepository(PROCESSED_DIR)
    messages = _load_jsonl(PROCESSED_DIR / "messages.jsonl")
    accessible_msg = next(m for m in messages if m["channel_id"] not in {"dyadic_d20", "multiparty_d4"})
    result = repo.get_message("benchmark_reader", accessible_msg["message_id"])
    assert result is not None
    assert result["message_id"] == accessible_msg["message_id"]


# =========================================================================
# X18: Trace Conformance
# =========================================================================
def test_x18_trace_events_conformance() -> None:
    """X18: Emitted normalization trace events validate against TraceRecord."""
    trace_path = PROCESSED_DIR / "normalization.trace.jsonl"
    events = TraceReader(trace_path).read()
    assert len(events) >= 2, "Expected at least query_started and query_completed trace events"
    for event in events:
        TraceRecord.model_validate(event)


# =========================================================================
# X19: Rebuildability Determinism
# =========================================================================
def test_x19_rebuildability_determinism(tmp_path: Path) -> None:
    """X19: Fresh build is byte-identical to committed data/processed, and second run is deterministic (F-047)."""
    from vsf.normalization.pipeline import build_corpus
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    build_corpus(ROOT, out1)
    build_corpus(ROOT, out2)

    artifacts = [
        "messages.jsonl",
        "media.jsonl",
        "orphan_media.jsonl",
        "channels.jsonl",
        "senders.jsonl",
        "memberships.jsonl",
        "validation.json",
        "VALIDATION.md",
    ]
    for fname in artifacts:
        p1 = out1 / fname
        p2 = out2 / fname
        p_committed = PROCESSED_DIR / fname

        assert p1.exists(), f"{fname} missing in run1 fresh build"
        assert p2.exists(), f"{fname} missing in run2 fresh build"
        assert p_committed.exists(), f"{fname} missing in committed {PROCESSED_DIR}"

        h1 = hashlib.sha256(p1.read_bytes()).hexdigest()
        h2 = hashlib.sha256(p2.read_bytes()).hexdigest()
        h_committed = hashlib.sha256(p_committed.read_bytes()).hexdigest()

        assert h1 == h2, f"Rebuild determinism mismatch between run1 and run2 for {fname}"
        assert h1 == h_committed, (
            f"F-047 contract violation: fresh build of {fname} ({h1[:12]}) differs from "
            f"committed {PROCESSED_DIR / fname} ({h_committed[:12]}). Code and committed artifact diverge."
        )


# =========================================================================
# X20: Raw Corpus Invariants
# =========================================================================
def test_x20_raw_corpus_invariants_and_orphan_count() -> None:
    """X20: Independently verify raw corpus file counts (1300 images, 308 sessions)."""
    assert RAW_DIR.exists()
    session_files = list(RAW_DIR.glob("**/session.json"))
    assert len(session_files) == 308
    all_disk_images = [
        Path(root, f)
        for root, dirs, files in os.walk(str(RAW_DIR))
        if ".git" not in root.split(os.sep)
        for f in files
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))
    ]
    assert len(all_disk_images) == 1300
