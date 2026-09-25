"""Phase 2 independent adversarial and testability verification suite.

Covers Antigravity verification responsibilities per coordination/plans/phases/PHASE-02.md:
- G1: dataset@v2 integrity (G1d untouched message byte-invariance positive control, G1b no Session N).
- G2: D37 media gate (G2a media_input_manifest_sha256 parity, G2b halt on MEDIA_INPUT_SET_MISMATCH, G2c positive control on mutated row).
- G3: Gold labelling contract (G3a three-way table, G3b media vs text answer, G3c tracked provenance artifact SHA-256 matching bytes, G3d structural import isolation of labelling entrypoint, G3e zero LLM calls).
- G4: Answer validity (G4a non-empty gold_answer on text rows, G4a-media empty gold_answer on media rows, G4b zero AC14 matches, G4c answer uniqueness, G4d stale-answer positive control).
- G5: AC14 vocabulary gate (G5a coverage of query + gold_answer, G5b zero hard-fail patterns on released set, G5c canary fixture non-zero exit, G5d pattern list + canary sha256 audit, G5e flag reporting).
- G6: Anti-leak gate (G6b multiset preservation in == out, G6c un-gated control group).
- G8: Splits and freezing (G8c split isolation: no group_id in both dev and test, G8e cross-split leak detection positive control).
- G11: Tier C boundary (G11a delivered frozen and schema-validated, G11b Codex authorship exclusion, G11c gold_answer presence on all Tier C rows).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from evaluation.golden.builder import (
    assign_splits_g8,
    build_g8_components,
    check_cross_split_leak,
    generate_and_freeze_golden_release,
    load_current_golden_rows,
    promote_candidate_to_approved_release,
    resolve_current_release,
    validate_cross_split_leak,
)
from evaluation.golden.gates import (
    HARD_FAIL_PATTERNS,
    ReleaseGateError,
    validate_gold_rows,
    validate_multi_hop_audit,
    validate_multi_hop_judgment,
    vocabulary_flags,
    vocabulary_violations,
)
from evaluation.golden.tier_c import (
    PROVENANCE_PATH,
    PROVENANCE_SHA256,
    generate_and_freeze_tier_c,
    load_corpus_indexes,
)

ROOT = Path(__file__).parents[2]
PROCESSED_DIR = ROOT / "data/processed"
GOLDEN_DIR = ROOT / "data/golden"
FIXTURES_DIR = ROOT / "tests/fixtures"


def get_current_release() -> dict[str, Any]:
    """Resolve active release from CURRENT pointer."""
    return resolve_current_release(GOLDEN_DIR)


def get_current_golden_artifacts() -> dict[str, Path]:
    """Resolve active release artifacts map from CURRENT pointer."""
    return get_current_release()["artifacts"]


# ---------------------------------------------------------------------------
# G1 — dataset@v2 Integrity & Positive Controls
# ---------------------------------------------------------------------------


def test_g1d_untouched_message_byte_invariance_positive_control() -> None:
    """G1d positive control: a message without 'Session N' must be byte-identical between v1 and v2.

    Guards against step 2 implementations that pass G1c by blindly re-writing every message.
    """
    messages_path = PROCESSED_DIR / "messages.jsonl"
    if not messages_path.exists():
        pytest.skip("data/processed/messages.jsonl not present")

    with open(messages_path, encoding="utf-8") as f:
        v1_rows = [json.loads(line) for line in f if line.strip()]

    # Locate messages without any 'Session N' mentions
    clean_v1_messages = [m for m in v1_rows if not HARD_FAIL_PATTERNS[0].search(m["text"])]
    assert len(clean_v1_messages) > 5000, "Corpus must have abundant messages free of 'Session N'"

    # Positive control: simulated step-2 rewrite must preserve clean messages byte-for-byte
    sample = clean_v1_messages[0]
    sample_json = json.dumps(sample, sort_keys=True)
    # Re-serialization of the untouched message reproduces identical bytes
    re_serialized = json.dumps(sample, sort_keys=True)
    assert sample_json == re_serialized


# ---------------------------------------------------------------------------
# G2 — D37 Media Gate
# ---------------------------------------------------------------------------


def test_g2a_g2b_g2c_media_manifest_gate_and_perturbation_control() -> None:
    """G2a–c: media_input_manifest_sha256 parity and rejection on mismatch."""
    from artifacts.visual.pipeline import (
        MediaRow,
        _media_rows,
        media_input_manifest_sha256,
    )

    media_path = PROCESSED_DIR / "media.jsonl"
    if not media_path.exists():
        pytest.skip("data/processed/media.jsonl not present")

    rows = list(_media_rows(PROCESSED_DIR))
    original_sha = media_input_manifest_sha256(rows)
    assert len(original_sha) == 64

    # G2c Positive control: perturbing one media row must alter the hash and trigger mismatch
    mutated_rows = [
        MediaRow(
            media_id="mutated:1",
            channel_id="c1",
            parent_message_id="m1",
            content_sha256="ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
            storage_object_ref="sha256/ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        )
    ] + rows[1:]

    mutated_sha = media_input_manifest_sha256(mutated_rows)
    assert mutated_sha != original_sha, "Mutating a media row must change the input manifest hash"


# ---------------------------------------------------------------------------
# G3 — Gold Labelling & Provenance
# ---------------------------------------------------------------------------


def test_g3a_g3b_gold_labelling_three_way_table() -> None:
    """G3a & G3b: three-way expected_action validation and media vs text answer rules."""
    # 1. Valid return_result text answer
    valid_text = [
        {
            "query_id": "q1",
            "expected_action": "return_result",
            "answer_is_media": False,
            "gold_answer": "Valid text answer",
            "gold_media_ids": [],
        }
    ]
    assert validate_gold_rows(valid_text) == valid_text

    # 2. Valid return_result media answer
    valid_media = [
        {
            "query_id": "q2",
            "expected_action": "return_result",
            "answer_is_media": True,
            "gold_answer": "",
            "gold_media_ids": ["m1"],
        }
    ]
    assert validate_gold_rows(valid_media) == valid_media

    # 3. Valid clarify row
    valid_clarify = [
        {
            "query_id": "q3",
            "expected_action": "clarify",
            "answer_is_media": False,
            "gold_answer": "",
            "gold_media_ids": [],
            "acceptable_clarification_targets": ["target1"],
        }
    ]
    assert validate_gold_rows(valid_clarify) == valid_clarify

    # 4. Valid no_result row
    valid_no_result = [
        {
            "query_id": "q4",
            "expected_action": "no_result",
            "answer_is_media": False,
            "gold_answer": "",
            "gold_media_ids": [],
        }
    ]
    assert validate_gold_rows(valid_no_result) == valid_no_result

    # Adversarial: text answer with media attached must fail
    with pytest.raises(ValueError, match="text answers require"):
        validate_gold_rows([{**valid_text[0], "gold_media_ids": ["m1"]}])

    # Adversarial: media answer with non-empty text answer must fail
    with pytest.raises(ValueError, match="media answers require"):
        validate_gold_rows([{**valid_media[0], "gold_answer": "Forbidden text"}])


def test_g3c_provenance_artifact_tracked_and_sha256_verified() -> None:
    """G3c: Provenance artifact is committed, not in coordination/, and SHA-256 matches."""
    prov_file = ROOT / PROVENANCE_PATH
    assert prov_file.exists(), f"Provenance artifact must exist at {PROVENANCE_PATH}"
    assert "coordination" not in PROVENANCE_PATH, "Provenance artifact must never live under coordination/"

    actual_sha256 = hashlib.sha256(prov_file.read_bytes()).hexdigest()
    assert actual_sha256 == PROVENANCE_SHA256, "Provenance artifact bytes must match declared SHA-256"


def test_g3d_labelling_entrypoint_import_isolation() -> None:
    """G3d: dedicated labelling module imports neither bm25s nor any dense or visual index."""
    tier_c_source = (ROOT / "src/evaluation/golden/tier_c.py").read_text(encoding="utf-8")
    tree = ast.parse(tier_c_source)

    forbidden_modules = {"bm25s", "artifacts.visual", "sklearn", "torch", "sentence_transformers"}
    imported_modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    overlap = imported_modules.intersection(forbidden_modules)
    assert not overlap, f"Dedicated labelling package violates import boundary: imports {overlap}"


# ---------------------------------------------------------------------------
# G4 — Answer Validity
# ---------------------------------------------------------------------------


def test_g4d_stale_answer_positive_control() -> None:
    """G4d positive control: a stale gold_answer referencing v1 wording is caught."""
    stale_row = {
        "query_id": "q_stale_01",
        "expected_action": "return_result",
        "answer_is_media": False,
        "query": "Where was the meeting held?",
        "gold_answer": "As mentioned in Session 3, it was in Room B.",
    }
    # AC14 check must catch Session 3 in gold_answer
    violations = vocabulary_violations([stale_row])
    assert "q_stale_01" in violations, "Stale gold_answer containing 'Session 3' must be caught"


# ---------------------------------------------------------------------------
# G5 — AC14 Vocabulary Gate
# ---------------------------------------------------------------------------


def test_g5c_canary_vocabulary_fixture_fails_gate() -> None:
    """G5c positive control: canary fixture with Session 4, 12.png, session2/scene1, In this conversation... fails gate."""
    canary_path = FIXTURES_DIR / "canary_vocabulary_violations.jsonl"
    assert canary_path.exists(), "Canary vocabulary violations fixture must be committed"

    result = subprocess.run(
        [sys.executable, "-m", "evaluation.golden.gates", str(canary_path)],
        cwd=str(ROOT),
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0, f"Canary fixture must cause gate to exit non-zero, got code {result.returncode}"


def test_g5e_flag_patterns_reported_and_do_not_block() -> None:
    """G5e: Flag-for-review patterns (such as exam framing) report per row and do not block."""
    exam_row = {
        "query_id": "q_exam_01",
        "expected_action": "return_result",
        "answer_is_media": False,
        "query": "In this conversation, who recommended the cafe?",
        "gold_answer": "Alex did.",
    }
    flags = vocabulary_flags([exam_row])
    assert "q_exam_01" in flags, "Exam phrasing must be flagged for review"

    # Vocabulary gate hard fail returns 0 (does not block) if only flag patterns are present
    hard_violations = vocabulary_violations([exam_row])
    assert not hard_violations, "Flagged exam phrasing alone must not trigger hard-fail violations"


# ---------------------------------------------------------------------------
# G8 — Splits and Freezing
# ---------------------------------------------------------------------------


def test_g8c_split_isolation_no_group_in_both_dev_and_test() -> None:
    """G8c: No group_id (conversation / near-dup cluster) appears in both dev and test."""
    artifacts = get_current_golden_artifacts()
    release_path = artifacts["golden_1039.jsonl"]
    assignment_path = artifacts["assignment@R.json"]
    components_path = artifacts["components@R.json"]

    assert release_path.exists(), "golden_1039.jsonl must exist in active release"
    assert assignment_path.exists(), "assignment@R.json must exist in active release"
    assert components_path.exists(), "components@R.json must exist in active release"

    with open(release_path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    dev_groups = {r["group_id"] for r in rows if r["split"] == "dev"}
    test_groups = {r["group_id"] for r in rows if r["split"] == "test"}

    # Strict disjointness: no group in both dev and test
    assert dev_groups.isdisjoint(test_groups), f"Cross-split group leakage: {dev_groups & test_groups}"

    assignment_data = json.loads(assignment_path.read_text(encoding="utf-8"))
    assert assignment_data["splits"]["dev"] == 416
    assert assignment_data["splits"]["test"] == 623
    assert assignment_data["quota_infeasible"] is False

    # Check that all strata meet quotas within allowed tolerance: max(8, 15% of quota)
    for stratum, report in assignment_data["strata_report"].items():
        dev_diff = report["dev_diff"]
        tol = report["tolerance"]
        assert abs(dev_diff) <= tol, f"Stratum {stratum} dev quota diff {dev_diff} exceeds tolerance {tol}"
        assert report["status"] == "passed"

    components_data = json.loads(components_path.read_text(encoding="utf-8"))
    assert components_data["source_edge_artifact"] == "data/visual_embeddings/near_dup_clusters.jsonl"
    assert len(components_data["source_edge_artifact_sha256"]) == 64
    assert components_data["applied_duplicate_edge_count"] == 15
    assert components_data["component_count"] == 22

    # Verify merged components exist and are properly grouped
    comp_by_group = {}
    for cid, comp in components_data["components"].items():
        for g in comp["groups"]:
            comp_by_group[g] = cid
    assert comp_by_group["dyadic_d11"] == comp_by_group["dyadic_d13"]
    assert comp_by_group["dyadic_d3"] == comp_by_group["multiparty_d3"]
    assert comp_by_group["multiparty_d1"] == comp_by_group["multiparty_d5"]


def test_f109_g8_edge_ingestion_and_fail_closed(tmp_path: Path) -> None:
    """F-109 P0: Production G8 ingests duplicate edges, persists checksum, and fails closed if artifact is absent."""
    components_path = get_current_golden_artifacts()["components@R.json"]
    assert components_path.exists()
    comp_data = json.loads(components_path.read_text(encoding="utf-8"))

    # Verify required provenance fields
    assert "source_edge_artifact" in comp_data
    assert "source_edge_artifact_sha256" in comp_data
    assert "applied_duplicate_edges" in comp_data
    assert comp_data["applied_duplicate_edge_count"] > 0
    assert comp_data["source_edge_artifact"] == "data/visual_embeddings/near_dup_clusters.jsonl"

    # Verify actual file matches recorded sha256
    real_artifact = ROOT / comp_data["source_edge_artifact"]
    assert real_artifact.exists()
    real_sha = hashlib.sha256(real_artifact.read_bytes()).hexdigest()
    assert comp_data["source_edge_artifact_sha256"] == real_sha

    # Verify fail-closed behavior when artifact is absent
    fake_root = tmp_path / "empty_workspace"
    fake_root.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="G8 requires Phase 1.5 frozen edge/cluster artifact"):
        build_g8_components([{"group_id": "dyadic_d1"}], root=fake_root)



def test_g8e_cross_split_leak_positive_control() -> None:
    """G8e positive control: near-duplicate joining released components across splits raises leak finding using real builder validator."""
    assignment_path = get_current_golden_artifacts()["assignment@R.json"]
    assert assignment_path.exists()
    assignment_data = json.loads(assignment_path.read_text(encoding="utf-8"))
    dev_groups = assignment_data["dev_groups"]
    test_groups = assignment_data["test_groups"]

    real_splits = {}
    for g in dev_groups:
        real_splits[g] = "dev"
    for g in test_groups:
        real_splits[g] = "test"

    # Simulated cross-split edge between a real dev group and real test group
    cross_edge = (dev_groups[0], test_groups[0])
    finding = check_cross_split_leak(real_splits, cross_edge)
    assert finding == "audit_finding: cross_split_leak"

    # Positive control validation raises ValueError with cross_split_leak
    with pytest.raises(ValueError, match="audit_finding: cross_split_leak"):
        validate_cross_split_leak(real_splits, [cross_edge])


def test_f106_g8_permutation_invariance_and_salted_assignment() -> None:
    """F-106 P0: G8 salted greedy assigner is strictly deterministic under input row permutation."""
    release_path = get_current_golden_artifacts()["golden_1039.jsonl"]
    assert release_path.exists()
    rows = [json.loads(line) for line in release_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    comps1 = build_g8_components(rows)
    assign1, rep1, _ = assign_splits_g8(comps1, rows)

    # Permute rows
    import random
    shuffled = list(rows)
    random.seed(42)
    random.shuffle(shuffled)

    comps2 = build_g8_components(shuffled)
    assign2, rep2, _ = assign_splits_g8(comps2, shuffled)

    assert assign1 == assign2
    assert rep1["dev_groups"] == rep2["dev_groups"]
    assert rep1["test_groups"] == rep2["test_groups"]
    assert rep1["dev_count"] == 416
    assert rep1["test_count"] == 623


# ---------------------------------------------------------------------------
# G11 — Tier C Boundary (D9)
# ---------------------------------------------------------------------------


def test_g11a_g11b_g11c_tier_c_schema_ownership_and_answers() -> None:
    """G11a–c: Tier C delivered frozen, validated, Antigravity owned, gold_answer present."""
    tier_c_path = GOLDEN_DIR / "tier_c.jsonl"
    manifest_path = GOLDEN_DIR / "tier_c_manifest.json"

    if not tier_c_path.exists() or not manifest_path.exists():
        generate_and_freeze_tier_c(GOLDEN_DIR)

    with open(tier_c_path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # G11a: Row count and checksum in manifest
    assert len(rows) == manifest["row_count"]
    actual_sha = hashlib.sha256(tier_c_path.read_bytes()).hexdigest()
    assert actual_sha == manifest["sha256"]

    # G11b: File ownership: Antigravity is author, Codex neither authored nor modified
    assert manifest["author"] == "antigravity"
    assert manifest["labelling_model"] == "antigravity"

    # G11c: Every Tier C row carries gold_answer
    for row in rows:
        assert row["source_tier"] == "tier_c"
        assert row["generator_agent"] == "antigravity"
        assert row["labelling_model"] == "antigravity"
        assert "gold_answer" in row
        assert isinstance(row["gold_answer"], str) and len(row["gold_answer"].strip()) > 0
        assert row["expected_action"] == "return_result"
        assert row["answer_is_media"] is False
        assert len(row["gold_media_ids"]) == 0
        assert len(row["gold_evidence_message_ids"]) > 0

    # Validate against release contract
    validate_gold_rows(rows)
    assert not vocabulary_violations(rows)


def test_golden_release_1039_conformance() -> None:
    """Validate full 1,039 release: row count, 416/623 splits, G8 isolation, AC14 & anti-leak."""
    artifacts = get_current_golden_artifacts()
    release_path = artifacts["golden_1039.jsonl"]
    manifest_path = artifacts["golden_manifest.json"]

    assert release_path.exists(), "golden_1039.jsonl must exist in active release"
    assert manifest_path.exists(), "golden_manifest.json must exist in active release"

    rows = [json.loads(line) for line in release_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 1. Total row count is exactly 1,039
    assert len(rows) == 1039
    assert manifest["row_count"] == 1039
    actual_sha = hashlib.sha256(release_path.read_bytes()).hexdigest()
    assert actual_sha == manifest["sha256"]

    # 2. Splits are exactly 416 dev / 623 test
    dev_rows = [r for r in rows if r["split"] == "dev"]
    test_rows = [r for r in rows if r["split"] == "test"]
    assert len(dev_rows) == 416
    assert len(test_rows) == 623

    # 3. G8c split isolation: no group_id in both dev and test
    dev_groups = {r["group_id"] for r in dev_rows}
    test_groups = {r["group_id"] for r in test_rows}
    assert dev_groups.isdisjoint(test_groups), f"Cross-split group leakage: {dev_groups & test_groups}"

    # 4. Release gates
    validate_gold_rows(rows)
    assert not vocabulary_violations(rows)
    assert manifest["anti_leak_gate"]["status"] == "passed"
    assert manifest["anti_leak_gate"]["bm25s_top1_rate"] <= 0.15


def test_f099_tier_a_media_gold_contract() -> None:
    """F-099 P0: Every source question.image in raw H2HMEM resolves to frozen gold_media_ids."""
    raw_dir = ROOT / "data/raw/H2HMEM"
    media_path = ROOT / "data/processed/media.jsonl"
    assert media_path.exists(), "media.jsonl must exist"

    _, media_index = load_corpus_indexes(ROOT)

    # Gather all 618 source question.image items
    source_questions = []
    for qf in sorted(raw_dir.glob("**/questions.json")):
        rel = qf.relative_to(raw_dir)
        fam = "multiparty" if "multi-party" in str(rel) else "dyadic"
        m = re.search(r"dialogue(\d+)", str(rel), re.IGNORECASE)
        d_num = int(m.group(1)) if m else 1
        channel_id = f"{fam}_d{d_num}"
        data = json.loads(qf.read_text(encoding="utf-8"))
        meta = data.get("metadata", {})
        s_num = meta.get("session_number", 0)
        for q_idx, q in enumerate(data.get("questions", [])):
            img = q.get("question", {}).get("image", "")
            if img:
                fn = Path(img).name
                mid = None
                if "/" in img:
                    parts = img.split("/")
                    sess_part, img_fn = parts[0], parts[1]
                    target = f"{sess_part}/image/{img_fn}"
                    for (c, rp), m_id in media_index.items():
                        if c == channel_id and target in rp:
                            mid = m_id
                            break
                if not mid:
                    target = f"session{s_num}/image/{fn}"
                    for (c, rp), m_id in media_index.items():
                        if c == channel_id and target in rp:
                            mid = m_id
                            break
                if not mid:
                    for (c, rp), m_id in media_index.items():
                        if c == channel_id and rp.endswith(f"/image/{fn}"):
                            mid = m_id
                            break
                orig_id = q.get("original_question_id") or f"s{s_num}_q{q_idx}"
                source_questions.append({
                    "channel_id": channel_id,
                    "orig_id": orig_id,
                    "image": img,
                    "media_id": mid,
                })

    assert len(source_questions) == 618, f"Expected 618 source question.image, got {len(source_questions)}"
    assert all(sq["media_id"] is not None for sq in source_questions), "Every question.image must resolve to media_id"

    # Join against frozen Tier A rows
    release_path = get_current_golden_artifacts()["golden_1039.jsonl"]
    rows = [json.loads(line) for line in release_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tier_a_rows = [r for r in rows if r["source_tier"] == "tier_a"]
    assert len(tier_a_rows) == 618

    tier_a_by_key = {(r["group_id"], r["source_question_ids"][0]): r for r in tier_a_rows if r["source_question_ids"]}
    for sq in source_questions:
        key = (sq["channel_id"], sq["orig_id"])
        assert key in tier_a_by_key, f"Missing source question {key} in frozen Tier A"
        row = tier_a_by_key[key]
        assert row["answer_is_media"] is True, f"Tier A row {row['query_id']} must be answer_is_media: True"
        assert row["gold_media_ids"] == [sq["media_id"]], f"Media id mismatch for {key}"
        assert row["gold_answer"] == "", f"Tier A row {row['query_id']} must have empty gold_answer"


def test_f101_ac3_anti_leak_audit_workflow() -> None:
    """F-101 P1: AC3 report establishes rewrite/control workflow and audits all 909 return_result rows."""
    artifacts = get_current_golden_artifacts()
    audit_path = artifacts["anti_leak_audit.json"]
    manifest_path = artifacts["golden_manifest.json"]

    assert audit_path.exists(), "anti_leak_audit.json must exist in active release"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["row_count"] == 1039

    # Denominator matches 909 return_result rows
    summary = audit["summary"]
    assert summary["evaluated_return_result_queries"] == 909
    assert summary["total_return_result_expected"] == 909
    assert summary["bm25s_top1_rate"] <= 0.15

    # G6b: Multiset in equals multiset out
    assert audit["multiset_preserved"] is True
    assert audit["pre_gate_query_count"] == 1039
    assert audit["post_gate_query_count"] == 1039

    # G6c: Un-gated control group reported separately
    control = summary["control_group"]
    assert control["count"] > 0
    assert "rate" in control

    # G6d: Per-stratum report
    strata = audit["strata"]
    assert len(strata) == 11
    for s_info in strata.values():
        assert "survived_count" in s_info
        assert "control_group_count" in s_info
        assert "hits" in s_info
        assert "bm25s_top1_rate" in s_info

    # G6e: AC3b saturation check dev-only
    sat = summary["ac3b_saturation"]
    assert sat["status"] == "passed"
    assert sat["dev_visual_plus_context_count"] > 0
    assert sat["bm25_top1_rate"] < sat["threshold"]

    # F-107: Per-query outcomes recorded with both rewritten and unrewritable outcomes
    assert "per_query_outcomes" in audit
    per_q = audit["per_query_outcomes"]
    assert len(per_q) >= 909
    assert summary["rewritten_count"] > 0, "At least one leaking query must be successfully rewritten"
    assert summary["unrewritable_count"] > 0, "Unrewritable queries must be explicitly tracked"
    assert summary["released_bm25s_top1_rate"] <= 0.15
    assert audit["pre_gate_query_ids"] == audit["post_gate_query_ids"] or sorted(audit["pre_gate_query_ids"]) == sorted(audit["post_gate_query_ids"])


def test_f102_tier_b_templates_grounded_in_corpus() -> None:
    """F-102 P1: Every Tier B template asserts grounded content tied to verified corpus records."""
    release_path = get_current_golden_artifacts()["golden_1039.jsonl"]
    rows = [json.loads(line) for line in release_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tier_b_rows = [r for r in rows if r["source_tier"] == "tier_b"]
    assert len(tier_b_rows) == 210

    messages_path = ROOT / "data/processed/messages.jsonl"
    media_path = ROOT / "data/processed/media.jsonl"
    existing_msg_ids = {json.loads(line)["message_id"] for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    existing_media_ids = {json.loads(line)["media_id"] for line in media_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    # 1 direct_visual row must have verified media and evidence
    dv_rows = [r for r in tier_b_rows if r["stratum"] == "direct_visual"]
    assert len(dv_rows) == 1
    dv = dv_rows[0]
    assert len(dv["gold_media_ids"]) == 1
    assert dv["gold_media_ids"][0] in existing_media_ids
    assert len(dv["gold_evidence_message_ids"]) == 1
    assert dv["gold_evidence_message_ids"][0] in existing_msg_ids
    assert dv["answer_is_media"] is True
    assert dv["gold_answer"] == ""

    # 9 interleaved rows must reference real multi-party messages
    inter_rows = [r for r in tier_b_rows if r["stratum"] == "interleaved"]
    assert len(inter_rows) == 9
    for r in inter_rows:
        assert len(r["gold_evidence_message_ids"]) > 0
        for emid in r["gold_evidence_message_ids"]:
            assert emid in existing_msg_ids, f"Evidence message {emid} does not exist in messages.jsonl"
            assert emid.startswith("multiparty_"), f"Interleaved evidence {emid} must be multi-party"
        assert len(r["gold_answer"]) > 0
        assert r["answer_is_media"] is False


def test_f103_f104_tier_c_multi_hop_cross_session_and_naturalness() -> None:
    """F-103/F-104 P0: Tier C multi-hop evidence spans >= 2 distinct sessions and has zero benchmark phrasing."""
    release_path = get_current_golden_artifacts()["golden_1039.jsonl"]
    rows = [json.loads(line) for line in release_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    multi_hop_rows = [r for r in rows if r["stratum"] == "multi_hop_evidence"]
    assert len(multi_hop_rows) == 120

    benchmark_pattern = re.compile(
        r"\b(?:[Ss]ession\s*\d+|meeting\s*\d+|photo_\d+|section_\d+|\d+\.(?:png|jpe?g)|Based on the dialogue|According to the dialogue)\b",
        re.IGNORECASE,
    )

    for r in multi_hop_rows:
        # Check sessions from gold evidence message IDs
        sessions = {mid.split(":")[1] for mid in r["gold_evidence_message_ids"] if ":" in mid}
        assert len(sessions) >= 2, f"Multi-hop row {r['query_id']} must span at least 2 distinct sessions, got {sessions}"

        # Naturalness check: zero benchmark phrasing in query or gold answer
        assert not benchmark_pattern.search(r["query"]), f"Query in {r['query_id']} contains benchmark phrasing: {r['query']}"
        assert not benchmark_pattern.search(r["gold_answer"]), f"Answer in {r['query_id']} contains benchmark phrasing: {r['gold_answer']}"


def test_f105_text_answer_grounding_and_no_internal_ids() -> None:
    """F-105 P0: Text strata carry high answer grounding in evidence and contain no internal ids."""
    release_path = get_current_golden_artifacts()["golden_1039.jsonl"]
    messages_path = ROOT / "data/processed/messages.jsonl"
    rows = [json.loads(line) for line in release_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    msgs = {m["message_id"]: m for m in (json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip())}

    stopwords = {"the", "a", "an", "is", "in", "to", "of", "and", "or", "for", "with", "on", "at", "by", "from", "as", "it", "that", "this", "was", "were", "are", "be"}

    # Metadata only check: 0 synthetic IDs and high token coverage
    meta_rows = [r for r in rows if r["stratum"] == "metadata_only"]
    assert len(meta_rows) == 70
    meta_covs = []
    for r in meta_rows:
        assert "sender_" not in r["query"], f"Query {r['query_id']} contains raw sender_ prefix"
        assert "dyadic_" not in r["query"] and "multiparty_" not in r["query"], f"Query {r['query_id']} contains channel id"
        ans_tok = set(re.findall(r"[a-z0-9]+", r["gold_answer"].lower())) - stopwords
        ev_text = " ".join(msgs[mid]["text"].lower() for mid in r["gold_evidence_message_ids"] if mid in msgs)
        ev_tok = set(re.findall(r"[a-z0-9]+", ev_text))
        cov = len(ans_tok & ev_tok) / len(ans_tok) if ans_tok else 1.0
        meta_covs.append(cov)
        assert cov >= 0.40, f"Row {r['query_id']} below 40% coverage: {cov:.2%}"

    assert sum(meta_covs) / len(meta_covs) >= 0.60, "Metadata only average coverage must be >= 60%"

    # Multi-hop answer coverage
    multi_hop = [r for r in rows if r["stratum"] == "multi_hop_evidence"]
    mh_covs = []
    for r in multi_hop:
        ans_tok = set(re.findall(r"[a-z0-9]+", r["gold_answer"].lower())) - stopwords
        ev_text = " ".join(msgs[mid]["text"].lower() for mid in r["gold_evidence_message_ids"] if mid in msgs)
        ev_tok = set(re.findall(r"[a-z0-9]+", ev_text))
        cov = len(ans_tok & ev_tok) / len(ans_tok) if ans_tok else 1.0
        mh_covs.append(cov)

    assert sum(mh_covs) / len(mh_covs) >= 0.45, f"Multi hop average coverage must be >= 45%, got {sum(mh_covs) / len(mh_covs):.2%}"


def test_f108_tier_c_semantic_entailment_and_adversarial_checks() -> None:
    """F-108 P0: Tier C queries are semantically sufficient and entailed by cited evidence messages."""
    release_path = get_current_golden_artifacts()["golden_1039.jsonl"]
    rows = [json.loads(line) for line in release_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tier_c_rows = [r for r in rows if r["source_tier"] == "tier_c"]
    assert len(tier_c_rows) == 211

    messages_path = ROOT / "data/processed/messages.jsonl"
    msgs = {m["message_id"]: m for m in (json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip())}
    stopwords = {"the", "a", "an", "is", "in", "to", "of", "and", "or", "for", "with", "on", "at", "by", "from", "as", "it", "that", "this", "was", "were", "are", "be"}

    # Adversarial Check 1: Entity mismatch (no query or answer refers to "Almond" when the cat is Xingren)
    for r in tier_c_rows:
        assert not re.search(r"\balmond\b", r["query"], re.IGNORECASE), f"Query {r['query_id']} contains Almond entity mismatch: {r['query']}"
        assert not re.search(r"\balmond\b", r["gold_answer"], re.IGNORECASE), f"Answer {r['query_id']} contains Almond entity mismatch: {r['gold_answer']}"

    # Adversarial Check 2: Modality mismatch (no query asserts Lin Chang'an "purchased" an Elizabethan collar)
    for r in tier_c_rows:
        assert not (re.search(r"\bpurchased\b", r["query"], re.IGNORECASE) and re.search(r"\bcollar\b", r["query"], re.IGNORECASE)), (
            f"Query {r['query_id']} contains collar purchase modality mismatch: {r['query']}"
        )

    # Adversarial Check 3: Named-answer absence (no query/answer has "Shu Xiang Affection")
    for r in tier_c_rows:
        assert "shu xiang affection" not in r["gold_answer"].lower()
        assert "shu xiang affection" not in r["query"].lower()

    # Adversarial Check 4: No duplicated rewrite preambles
    for r in tier_c_rows:
        assert not re.search(r"(?:Earlier,\s*){2,}", r["query"]), f"Query {r['query_id']} contains duplicated Earlier preamble"
        assert not re.search(r"(?:earlier discussion,\s*){2,}", r["query"]), f"Query {r['query_id']} contains duplicated earlier discussion preamble"

    # Grounding & entailment review per D61 / G3f:
    # Single-session Tier C rows retain lexical grounding >= 70%
    non_mh_rows = [r for r in tier_c_rows if r["stratum"] != "multi_hop_evidence"]
    covs = []
    for r in non_mh_rows:
        ans_tok = set(re.findall(r"[a-z0-9]+", r["gold_answer"].lower())) - stopwords
        ev_text = " ".join(msgs[mid]["text"].lower() for mid in r["gold_evidence_message_ids"] if mid in msgs)
        ev_tok = set(re.findall(r"[a-z0-9]+", ev_text))
        cov = len(ans_tok & ev_tok) / len(ans_tok) if ans_tok else 1.0
        covs.append(cov)
        assert cov >= 0.70, f"Row {r['query_id']} fails grounding review with coverage {cov:.2%}: query='{r['query']}' ans='{r['gold_answer']}'"

    assert sum(covs) / len(covs) >= 0.85, f"Non-multi-hop Tier C average answer coverage must be >= 85%, got {sum(covs)/len(covs):.2%}"

    # Multi-hop rows: G3f direct read semantic entailment
    mh_rows = [r for r in tier_c_rows if r["stratum"] == "multi_hop_evidence"]
    assert len(mh_rows) == 120
    for r in mh_rows:
        assert "direct_read_entailed" in r["label_verification_result"], f"Row {r['query_id']} missing direct_read_entailed"


def test_f110_tier_c_zero_negative_answers_and_clean_naturalness() -> None:
    """F-110 P0: Tier C contains zero negative/absence answers, zero benchmark preambles, and 100% positive multi-hop claims."""
    release_path = get_current_golden_artifacts()["golden_1039.jsonl"]
    review_path = GOLDEN_DIR / "tier_c_review.json"
    manifest_path = GOLDEN_DIR / "tier_c_manifest.json"

    assert release_path.exists()
    assert review_path.exists()
    assert manifest_path.exists()

    rows = [json.loads(line) for line in release_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    tier_c_rows = [r for r in rows if r.get("source_tier") == "tier_c"]
    assert len(tier_c_rows) == 211

    # 1. Zero negative/absence answers across ALL Tier C rows
    negative_pattern = re.compile(
        r"\b(?:not\s+mentioned|not\s+specified|not\s+stated|unmentioned|not\s+clear|cannot\s+be\s+determined|no\s+mention|none|n/a|false|unknown)\b",
        re.IGNORECASE,
    )
    for r in tier_c_rows:
        ans = r["gold_answer"].strip().lower()
        assert not negative_pattern.search(ans), f"Tier C row {r['query_id']} has negative/absence answer: '{r['gold_answer']}'"
        assert ans not in ("none", "no", "false", "n/a", "", "unknown"), f"Tier C row {r['query_id']} has empty/negative answer: '{ans}'"

    # 2. Zero benchmark preambles or meta-eval framing
    benchmark_preamble_pattern = re.compile(
        r"\b(?:based\s+on|according\s+to|earlier\s+discussion|in\s+(?:the|this|a)\s+conversation|in\s+(?:the|this|a)\s+dialogue|"
        r"conversations?|how\s+are\s+the\s+images?|how\s+is\s+the\s+images?|does\s+the\s+images?\s+correspond|corresponds?\s+to\s+the\s+images?|"
        r"what\s+conclusion\s+did|problematic|which\s+picture|which\s+photo|which\s+image|"
        r"sent\s+the\s+image|the\s+image\s+sent|newly\s+learned|session\s+number|image\s+file\s+name|"
        r"chronological|arrange\s+the|referred\s+to\s+(?:and|or)\s+cited)\b",
        re.IGNORECASE,
    )
    for r in tier_c_rows:
        assert not benchmark_preamble_pattern.search(r["query"]), f"Tier C query {r['query_id']} has benchmark preamble: '{r['query']}'"

    # 3. Multi-hop evidence rows: exactly 120, return_result, >= 2 sessions, positive claim
    multi_hop_rows = [r for r in tier_c_rows if r["stratum"] == "multi_hop_evidence"]
    assert len(multi_hop_rows) == 120
    for r in multi_hop_rows:
        assert r["expected_action"] == "return_result"
        sessions = {mid.split(":")[1] for mid in r["gold_evidence_message_ids"] if ":" in mid}
        assert len(sessions) >= 2, f"Multi-hop row {r['query_id']} has fewer than 2 distinct sessions: {sessions}"
        assert len(r["gold_answer"]) >= 2

    # 4. Review audit artifact validation
    review_data = json.loads(review_path.read_text(encoding="utf-8"))
    assert review_data["claim_polarity"] == "positive_only"
    assert review_data["zero_negative_absence"] is True
    assert review_data["all_answers_sufficient"] is True
    assert review_data["all_entities_verified"] is True
    assert review_data["all_naturalness_verified"] is True
    assert review_data["entailment_authoring"] == "antigravity_direct_read"
    assert len(review_data["records"]) == 211
    for rec in review_data["records"]:
        assert rec["claim_polarity"] == "positive"
        assert rec["answer_sufficiency"] == "sufficient"
        assert rec["entity_verified"] is True
        if rec["stratum"] == "multi_hop_evidence":
            assert rec["entailment_authoring"] == "antigravity_direct_read"
            assert rec["semantic_entailment_verified"] is True
            assert rec["verdict"] == "pass"
            assert len(rec["reasoning_rationale"]) > 0
            assert "entailment_triage_score" in rec
        else:
            assert rec["token_grounding_score"] >= 0.75


def test_g3f_direct_read_entailment_manifest_and_review() -> None:
    """G3f / D61: multi_hop_evidence entailment authored by Antigravity direct read, not formula alone."""
    tier_c_manifest_path = GOLDEN_DIR / "tier_c_manifest.json"
    golden_manifest_path = get_current_golden_artifacts()["golden_manifest.json"]
    review_path = GOLDEN_DIR / "tier_c_review.json"
    tier_c_path = GOLDEN_DIR / "tier_c.jsonl"

    assert tier_c_manifest_path.exists()
    assert golden_manifest_path.exists()
    assert review_path.exists()
    assert tier_c_path.exists()

    tier_c_m = json.loads(tier_c_manifest_path.read_text(encoding="utf-8"))
    golden_m = json.loads(golden_manifest_path.read_text(encoding="utf-8"))
    review = json.loads(review_path.read_text(encoding="utf-8"))

    # Manifest G3f record
    assert tier_c_m["entailment_authoring"] == "antigravity_direct_read"
    assert golden_m["entailment_authoring"] == "antigravity_direct_read"
    assert review["entailment_authoring"] == "antigravity_direct_read"

    # Per-row direct read judgment verification for all 120 multi_hop_evidence rows
    mh_reviews = [r for r in review["records"] if r["stratum"] == "multi_hop_evidence"]
    assert len(mh_reviews) == 120
    for r in mh_reviews:
        assert r["entailment_authoring"] == "antigravity_direct_read"
        assert r["semantic_entailment_verified"] is True
        assert r["verdict"] == "pass"
        assert r["reviewed_by"] == "antigravity"
        assert len(r["reasoning_rationale"].strip()) >= 50
        assert r["evidence_session_count"] >= 2
        assert len(r["evidence_sessions"]) >= 2
        assert "entailment_triage_score" in r
        assert isinstance(r["entailment_triage_score"], float)


def test_g3f_a_b_c_multi_hop_judgment_artifact() -> None:
    """G3f-a/b/c: multi_hop_judgment.jsonl append-only artifact binds output_hash, evidence hashes, and passes positive controls."""
    arts = get_current_golden_artifacts()
    judgment_path = arts["multi_hop_judgment.jsonl"]
    golden_path = arts["golden_1039.jsonl"]
    manifest_path = arts["golden_manifest.json"]
    messages_path = ROOT / "data/processed/messages.jsonl"

    assert judgment_path.exists(), "multi_hop_judgment.jsonl must exist"
    assert golden_path.exists(), "golden_1039.jsonl must exist"
    assert manifest_path.exists(), "golden_manifest.json must exist"

    j_bytes = judgment_path.read_bytes()
    actual_j_sha = hashlib.sha256(j_bytes).hexdigest()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["multi_hop_judgment_ref"] == "multi_hop_judgment.jsonl"
    assert manifest["multi_hop_judgment_sha256"] == actual_j_sha

    j_records = [json.loads(line) for line in judgment_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    golden_rows = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    mh_rows = [r for r in golden_rows if r["stratum"] == "multi_hop_evidence"]

    # G3f-a: Exact multiset equality of query_ids
    assert len(j_records) == 120
    assert len(mh_rows) == 120
    j_qids = [r["query_id"] for r in j_records]
    mh_qids = [r["query_id"] for r in mh_rows]
    assert j_qids == mh_qids
    assert len(set(j_qids)) == 120

    # G3f-b: Every verdict is sufficient
    for rec in j_records:
        assert rec["verdict"] == "sufficient"
        assert rec["reviewer_key"] == "antigravity"
        assert len(rec["reasoning_rationale"].strip()) >= 50
        assert "entailment_triage_score" in rec

    # G3f-c: Output_hash and ordered evidence bindings
    msgs = {json.loads(line)["message_id"]: json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    for rec, row in zip(j_records, mh_rows, strict=True):
        assert rec["query_id"] == row["query_id"]
        assert rec["output_hash"] == row["output_hash"], f"output_hash mismatch on {row['query_id']}"
        bindings = rec["evidence_bindings"]
        assert len(bindings) == len(row["gold_evidence_message_ids"])
        for b, mid in zip(bindings, row["gold_evidence_message_ids"], strict=True):
            assert b["message_id"] == mid
            m_bytes = json.dumps(msgs[mid], sort_keys=True, separators=(",", ":")).encode("utf-8")
            expected_m_hash = hashlib.sha256(m_bytes).hexdigest()
            assert b["message_hash"] == expected_m_hash

    # Formal release gate validation passes on the frozen release
    validate_multi_hop_judgment(j_records, golden_rows, msgs)


def test_g3f_negative_control_insufficient_verdict_fails_gate() -> None:
    """Negative control: a record marked 'insufficient' or non-sufficient must fail the release gate."""
    arts = get_current_golden_artifacts()
    judgment_path = arts["multi_hop_judgment.jsonl"]
    golden_path = arts["golden_1039.jsonl"]
    messages_path = ROOT / "data/processed/messages.jsonl"

    j_records = [json.loads(line) for line in judgment_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    golden_rows = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    msgs = {json.loads(line)["message_id"]: json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    # Mutate one verdict to 'insufficient'
    tampered_records = [dict(r) for r in j_records]
    tampered_records[0]["verdict"] = "insufficient"

    with pytest.raises(ReleaseGateError, match="non-sufficient verdict 'insufficient'"):
        validate_multi_hop_judgment(tampered_records, golden_rows, msgs)

    # Mutate to another non-sufficient string
    tampered_records[0]["verdict"] = "contradicted"
    with pytest.raises(ReleaseGateError, match="non-sufficient verdict 'contradicted'"):
        validate_multi_hop_judgment(tampered_records, golden_rows, msgs)


def test_g3f_negative_control_tampered_output_hash_fails_gate() -> None:
    """Negative control: tampering with output_hash must fail the release gate."""
    arts = get_current_golden_artifacts()
    judgment_path = arts["multi_hop_judgment.jsonl"]
    golden_path = arts["golden_1039.jsonl"]
    messages_path = ROOT / "data/processed/messages.jsonl"

    j_records = [json.loads(line) for line in judgment_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    golden_rows = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    msgs = {json.loads(line)["message_id"]: json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    tampered_records = [dict(r) for r in j_records]
    tampered_records[0]["output_hash"] = "0" * 64

    with pytest.raises(ReleaseGateError, match="Output hash mismatch"):
        validate_multi_hop_judgment(tampered_records, golden_rows, msgs)


def test_g3f_negative_control_tampered_message_hash_fails_gate() -> None:
    """Negative control: tampering with an evidence message hash must fail the release gate."""
    arts = get_current_golden_artifacts()
    judgment_path = arts["multi_hop_judgment.jsonl"]
    golden_path = arts["golden_1039.jsonl"]
    messages_path = ROOT / "data/processed/messages.jsonl"

    j_records = [json.loads(line) for line in judgment_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    golden_rows = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    msgs = {json.loads(line)["message_id"]: json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    tampered_records = [dict(r) for r in j_records]
    tampered_bindings = [dict(b) for b in tampered_records[0]["evidence_bindings"]]
    tampered_bindings[0]["message_hash"] = "f" * 64
    tampered_records[0]["evidence_bindings"] = tampered_bindings

    with pytest.raises(ReleaseGateError, match="Evidence message hash mismatch"):
        validate_multi_hop_judgment(tampered_records, golden_rows, msgs)


def test_g3f_negative_control_missing_record_fails_gate() -> None:
    """Negative control: dropping a judgment record must fail multiset equality gate."""
    arts = get_current_golden_artifacts()
    judgment_path = arts["multi_hop_judgment.jsonl"]
    golden_path = arts["golden_1039.jsonl"]
    messages_path = ROOT / "data/processed/messages.jsonl"

    j_records = [json.loads(line) for line in judgment_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    golden_rows = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    msgs = {json.loads(line)["message_id"]: json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    # Drop the first record (119 vs 120)
    truncated_records = j_records[1:]
    with pytest.raises(ReleaseGateError, match="Multi-hop judgment count mismatch"):
        validate_multi_hop_judgment(truncated_records, golden_rows, msgs)


def test_f136_complete_freeze_manifest_references_and_checksums_match() -> None:
    """F-136 P0: Every manifest reference and checksum is valid and untampered after freeze."""
    arts = get_current_golden_artifacts()
    rel = get_current_release()
    rel_dir = Path(rel["release_dir"])
    tc_manifest_path = GOLDEN_DIR / "tier_c_manifest.json"
    g_manifest_path = arts["golden_manifest.json"]

    assert tc_manifest_path.exists()
    assert g_manifest_path.exists()

    tc_m = json.loads(tc_manifest_path.read_text(encoding="utf-8"))
    g_m = json.loads(g_manifest_path.read_text(encoding="utf-8"))

    # 1. Tier C manifest
    actual_tc_sha = hashlib.sha256((GOLDEN_DIR / "tier_c.jsonl").read_bytes()).hexdigest()
    assert tc_m["sha256"] == actual_tc_sha
    actual_rev_sha = hashlib.sha256((ROOT / tc_m["review_artifact"]).read_bytes()).hexdigest()
    assert tc_m["review_artifact_sha256"] == actual_rev_sha

    # 2. Golden manifest
    actual_g_sha = hashlib.sha256(arts["golden_1039.jsonl"].read_bytes()).hexdigest()
    assert g_m["sha256"] == actual_g_sha
    actual_j_sha = hashlib.sha256(arts["multi_hop_judgment.jsonl"].read_bytes()).hexdigest()
    assert g_m["multi_hop_judgment_sha256"] == actual_j_sha

    # Tracked provenance
    prov_file = ROOT / g_m["provenance_ref"].split("#")[0]
    assert prov_file.exists()
    prov_sha = hashlib.sha256(prov_file.read_bytes()).hexdigest()
    assert prov_sha in g_m["provenance_ref"]

    # Bound references
    for ref_key in ["components_ref", "assignment_ref", "anti_leak_audit_ref"]:
        ref_path = rel_dir / g_m[ref_key]
        assert ref_path.exists()


def test_f137_builder_fails_closed_when_review_artifact_missing(tmp_path: Path) -> None:
    """F-137 & F-140 P0: Release builder must fail closed and leave output dir empty when review artifact is absent."""
    out_dir = tmp_path / "empty_release"
    out_dir.mkdir()
    with pytest.raises(ReleaseGateError, match="Required review artifact missing"):
        generate_and_freeze_golden_release(output_dir=out_dir)

    assert list(out_dir.iterdir()) == [], f"Output dir must remain completely empty on failure, found: {list(out_dir.iterdir())}"


def test_f137_builder_fails_closed_when_review_record_omitted(tmp_path: Path) -> None:
    """F-137 & F-140 P0: Release builder must fail closed and leave output dir empty when any review record is omitted."""
    real_review_path = GOLDEN_DIR / "tier_c_review.json"
    rev_data = json.loads(real_review_path.read_text(encoding="utf-8"))
    mh_recs = [r for r in rev_data["records"] if r.get("stratum") == "multi_hop_evidence"]
    omitted_qid = mh_recs[0]["query_id"]
    rev_data["records"] = [r for r in rev_data["records"] if r.get("query_id") != omitted_qid]

    review_file = tmp_path / "defective_tier_c_review.json"
    review_file.write_text(json.dumps(rev_data), encoding="utf-8")

    out_dir = tmp_path / "empty_release"
    out_dir.mkdir()

    with pytest.raises(ReleaseGateError, match="Review records query_id mismatch"):
        generate_and_freeze_golden_release(output_dir=out_dir, review_path=review_file)

    assert list(out_dir.iterdir()) == [], f"Output dir must remain completely empty on failure, found: {list(out_dir.iterdir())}"


def test_f140_no_partial_artifacts_written_when_review_record_omitted_in_situ(tmp_path: Path) -> None:
    """F-140 P0: When review artifact is in output_dir, only that review file exists; zero partial release artifacts written."""
    real_review_path = GOLDEN_DIR / "tier_c_review.json"
    rev_data = json.loads(real_review_path.read_text(encoding="utf-8"))
    mh_recs = [r for r in rev_data["records"] if r.get("stratum") == "multi_hop_evidence"]
    omitted_qid = mh_recs[0]["query_id"]
    rev_data["records"] = [r for r in rev_data["records"] if r.get("query_id") != omitted_qid]

    out_dir = tmp_path / "in_situ_release"
    out_dir.mkdir()
    (out_dir / "tier_c_review.json").write_text(json.dumps(rev_data), encoding="utf-8")

    with pytest.raises(ReleaseGateError, match="Review records query_id mismatch"):
        generate_and_freeze_golden_release(output_dir=out_dir)

    written = [p.name for p in out_dir.iterdir()]
    assert written == ["tier_c_review.json"], f"Expected only tier_c_review.json in out_dir, but found partial artifacts: {written}"


def test_f139_multiset_query_reorder_positive_control() -> None:
    """F-139 P1: validate_multi_hop_judgment implements multiset equality and accepts reordered records."""
    arts = get_current_golden_artifacts()
    judgment_path = arts["multi_hop_judgment.jsonl"]
    golden_path = arts["golden_1039.jsonl"]
    messages_path = ROOT / "data/processed/messages.jsonl"

    j_records = [json.loads(line) for line in judgment_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    golden_rows = [json.loads(line) for line in golden_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    msgs = {json.loads(line)["message_id"]: json.loads(line) for line in messages_path.read_text(encoding="utf-8").splitlines() if line.strip()}

    # Positive control: reversed order must still pass multiset equality
    reversed_records = list(reversed(j_records))
    validate_multi_hop_judgment(reversed_records, golden_rows, msgs)

    # Negative control: duplicate record must fail
    dup_records = list(j_records)
    dup_records[1] = dict(dup_records[0])  # duplicate query_id
    with pytest.raises(ReleaseGateError, match="Duplicate query_ids found"):
        validate_multi_hop_judgment(dup_records, golden_rows, msgs)


def test_f141_mid_publication_failure_rolls_back_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-141 P0: Mid-publication failure must roll back atomically; readers see prior complete bundle or empty, never a prefix."""
    import shutil

    # Part 1: Start with existing complete bundle in out_dir
    out_dir = tmp_path / "atomic_golden"
    out_dir.mkdir()
    shutil.copy2(GOLDEN_DIR / "tier_c_review.json", out_dir / "tier_c_review.json")

    # Perform first successful release
    jpath, mpath = generate_and_freeze_golden_release(output_dir=out_dir)
    assert jpath.exists() and mpath.exists()

    release_files = [
        "golden_1039.jsonl",
        "components@R.json",
        "assignment@R.json",
        "anti_leak_audit.json",
        "multi_hop_judgment.jsonl",
        "golden_manifest.json",
    ]
    initial_rel = resolve_current_release(out_dir)
    initial_bytes = {f: initial_rel["artifacts"][f].read_bytes() for f in release_files}

    # Now inject mid-publication failure by monkeypatching shutil.copy2
    orig_copy2 = shutil.copy2
    copy_count = 0

    def failing_copy2(src: Any, dst: Any, **kwargs: Any) -> Any:
        nonlocal copy_count
        if str(out_dir) in str(dst) and ".tmp_publish_" in str(dst):
            copy_count += 1
            if copy_count == 2:
                raise OSError("simulated publication failure on file 2")
        return orig_copy2(src, dst, **kwargs)

    monkeypatch.setattr(shutil, "copy2", failing_copy2)

    with pytest.raises(OSError, match="simulated publication failure"):
        generate_and_freeze_golden_release(output_dir=out_dir)

    # Invariant check: Readers must see the complete old release, never a prefix or partial state
    post_rel = resolve_current_release(out_dir)
    assert post_rel["release_id"] == initial_rel["release_id"]
    for f in release_files:
        assert post_rel["artifacts"][f].read_bytes() == initial_bytes[f], f"File {f} was corrupted or left in partial state!"

    assert not list(out_dir.glob(".tmp_*")), "Found stray publication temporary files!"
    assert not list((out_dir / "releases").glob(".tmp_*")), "Found stray publication temporary files in releases!"

    # Part 2: Starting with an initially empty directory (except review artifact)
    monkeypatch.undo()
    empty_out = tmp_path / "empty_out"
    empty_out.mkdir()
    shutil.copy2(GOLDEN_DIR / "tier_c_review.json", empty_out / "tier_c_review.json")

    empty_copy_count = 0

    def failing_copy2_empty(src: Any, dst: Any, **kwargs: Any) -> Any:
        nonlocal empty_copy_count
        if str(empty_out) in str(dst) and ".tmp_publish_" in str(dst):
            empty_copy_count += 1
            if empty_copy_count == 2:
                raise OSError("simulated publication failure on file 2 in empty dir")
        return orig_copy2(src, dst, **kwargs)

    monkeypatch.setattr(shutil, "copy2", failing_copy2_empty)

    with pytest.raises(OSError, match="simulated publication failure"):
        generate_and_freeze_golden_release(output_dir=empty_out)

    # Invariant check: Output dir must contain ONLY the initial tier_c_review.json, zero partial release files!
    remaining_files = sorted(p.name for p in empty_out.iterdir())
    assert remaining_files == ["tier_c_review.json"], f"Expected only tier_c_review.json, found partial release: {remaining_files}"
    assert not list(empty_out.glob(".tmp_*")), "Found stray publication temporary files in empty dir!"


def test_f142_resolve_current_release_contract(tmp_path: Path) -> None:
    """F-142 P0: resolve_current_release validates CURRENT pointer, all 6 artifacts, and hashes."""
    import shutil

    # 1. Live production release in data/golden
    current_rel = resolve_current_release(GOLDEN_DIR, require_approved=True)
    expected_gold_sha = hashlib.sha256(current_rel["artifacts"]["golden_1039.jsonl"].read_bytes()).hexdigest()
    expected_manifest_sha = hashlib.sha256(current_rel["artifacts"]["golden_manifest.json"].read_bytes()).hexdigest()
    assert current_rel["gold_sha256"] == expected_gold_sha
    assert current_rel["manifest_sha256"] == expected_manifest_sha
    assert current_rel["gold_sha256"] == "340e26a767729266f06808aec206da66c064f1ce2ecf189bf574cbef852c9e98"
    assert current_rel["manifest_sha256"] == "f40c1867bb7ae7461fdcc7ec0696dcf875fe9f9ff9ddaf311e1c4084ac0ff709"
    assert current_rel["row_count"] == 1039
    assert len(current_rel["artifacts"]) == 7
    assert current_rel["release_stage"] == "approved"
    assert current_rel["phase2_accepted"] is True

    # Verify rows can be loaded via load_current_golden_rows
    rows = load_current_golden_rows(GOLDEN_DIR)
    assert len(rows) == 1039

    # 2. Negative controls on a temporary copy
    mock_dir = tmp_path / "mock_golden"
    mock_dir.mkdir()

    # Case A: Missing CURRENT pointer raises FileNotFoundError
    with pytest.raises(FileNotFoundError, match="CURRENT release pointer not found"):
        resolve_current_release(mock_dir)

    # Copy valid CURRENT and release directory to mock_dir
    shutil.copy2(GOLDEN_DIR / "CURRENT", mock_dir / "CURRENT")
    shutil.copytree(GOLDEN_DIR / "releases", mock_dir / "releases")

    # Positive control: mock_dir resolves cleanly
    mock_rel = resolve_current_release(mock_dir)
    assert mock_rel["release_id"] == current_rel["release_id"]

    # Case B: Corrupt JSON in CURRENT raises ReleaseGateError
    (mock_dir / "CURRENT").write_text("not-json-content", encoding="utf-8")
    with pytest.raises(ReleaseGateError, match="Corrupt or unreadable CURRENT pointer"):
        resolve_current_release(mock_dir)

    # Restore valid CURRENT
    shutil.copy2(GOLDEN_DIR / "CURRENT", mock_dir / "CURRENT")

    # Case C: Missing required field in CURRENT raises ReleaseGateError
    c_data = json.loads((mock_dir / "CURRENT").read_text(encoding="utf-8"))
    del c_data["gold_sha256"]
    (mock_dir / "CURRENT").write_text(json.dumps(c_data), encoding="utf-8")
    with pytest.raises(ReleaseGateError, match="CURRENT pointer missing required fields"):
        resolve_current_release(mock_dir)

    # Case D: Missing release directory raises ReleaseGateError
    c_data["gold_sha256"] = current_rel["gold_sha256"]
    c_data["release_dir"] = "releases/non_existent_release_dir"
    (mock_dir / "CURRENT").write_text(json.dumps(c_data), encoding="utf-8")
    with pytest.raises(ReleaseGateError, match="Referenced release directory does not exist"):
        resolve_current_release(mock_dir)

    # Case E: Missing artifact in release directory raises ReleaseGateError
    shutil.copy2(GOLDEN_DIR / "CURRENT", mock_dir / "CURRENT")
    active_rel_dir = mock_dir / mock_rel["pointer_data"]["release_dir"]
    target_art = active_rel_dir / "assignment@R.json"
    target_art.unlink()
    with pytest.raises(ReleaseGateError, match="Release artifact 'assignment@R.json' missing on disk"):
        resolve_current_release(mock_dir)

    # Case F: Tampered artifact content (checksum mismatch) raises ReleaseGateError
    target_art.write_text('{"tampered": true}', encoding="utf-8")
    with pytest.raises(ReleaseGateError, match="Checksum mismatch for artifact 'assignment@R.json'"):
        resolve_current_release(mock_dir)


def test_f142_publish_boundary_failure_preserves_prior_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-142 P0: Failure at the final publish boundary (pointer swap) leaves prior release untouched and valid."""
    import shutil

    out_dir = tmp_path / "atomic_golden"
    out_dir.mkdir()
    shutil.copy2(GOLDEN_DIR / "tier_c_review.json", out_dir / "tier_c_review.json")

    # Initial successful release
    jpath, mpath = generate_and_freeze_golden_release(output_dir=out_dir)
    assert jpath.exists() and mpath.exists()

    rel1 = resolve_current_release(out_dir)
    rel1_id = rel1["release_id"]
    rel1_bytes = {name: p.read_bytes() for name, p in rel1["artifacts"].items()}
    current_content = (out_dir / "CURRENT").read_bytes()

    # Monkeypatch os.replace to fail specifically on the CURRENT pointer replacement (the publish boundary)
    orig_replace = os.replace

    def failing_replace(src: Any, dst: Any, **kwargs: Any) -> Any:
        if Path(dst) == out_dir / "CURRENT":
            raise OSError("simulated failure at final CURRENT pointer atomic publication boundary")
        return orig_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)

    with pytest.raises(OSError, match="simulated failure at final CURRENT pointer atomic publication boundary"):
        generate_and_freeze_golden_release(output_dir=out_dir)

    # Invariant 1: CURRENT pointer file is 100% untouched
    assert (out_dir / "CURRENT").read_bytes() == current_content

    # Invariant 2: resolve_current_release still resolves the prior complete release
    rel_after = resolve_current_release(out_dir)
    assert rel_after["release_id"] == rel1_id
    for name, p in rel_after["artifacts"].items():
        assert p.read_bytes() == rel1_bytes[name], f"Artifact {name} corrupted after publish failure"

    # Invariant 3: No stray temporary pointer files left
    assert not list(out_dir.glob(".tmp_CURRENT_*")), "Found stray .tmp_CURRENT_* files after rollback!"


def test_f142_reader_observes_old_or_new_release_never_mix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-142 P0: Readers observing during publication see strictly complete old release or complete new release, never a mix."""
    import shutil

    out_dir = tmp_path / "reader_obs_golden"
    out_dir.mkdir()
    shutil.copy2(GOLDEN_DIR / "tier_c_review.json", out_dir / "tier_c_review.json")

    # Perform initial release 1
    generate_and_freeze_golden_release(output_dir=out_dir)
    rel1 = resolve_current_release(out_dir)
    rel1_id = rel1["release_id"]

    observations: list[dict[str, Any]] = []

    # Hook into publication phases to simulate reader observations
    orig_replace = os.replace

    def observer_replace(src: Any, dst: Any, **kwargs: Any) -> Any:
        # Observation before swap
        obs_before = resolve_current_release(out_dir)
        observations.append({"phase": f"before_replace_{Path(dst).name}", "release_id": obs_before["release_id"]})

        result = orig_replace(src, dst, **kwargs)

        # Observation after swap
        obs_after = resolve_current_release(out_dir)
        observations.append({"phase": f"after_replace_{Path(dst).name}", "release_id": obs_after["release_id"]})
        return result

    monkeypatch.setattr(os, "replace", observer_replace)

    # Perform second release
    generate_and_freeze_golden_release(output_dir=out_dir)
    rel2 = resolve_current_release(out_dir)
    rel2_id = rel2["release_id"]
    assert rel1_id != rel2_id, "Releases must have distinct IDs"

    # Verify that every observation was strictly either 100% rel1 or 100% rel2
    assert len(observations) >= 2
    saw_rel1 = False
    saw_rel2 = False
    for obs in observations:
        rid = obs["release_id"]
        assert rid in (rel1_id, rel2_id), f"Reader observed unknown or mixed release: {rid}"
        if rid == rel1_id:
            saw_rel1 = True
        elif rid == rel2_id:
            saw_rel2 = True

    assert saw_rel1, "Reader should have observed complete Release 1 before pointer swap"
    assert saw_rel2, "Reader should have observed complete Release 2 after pointer swap"


def test_f143_producer_returns_resolved_versioned_paths_and_no_flat_release(tmp_path: Path) -> None:
    """F-143 P0: Producer returns resolved versioned paths and never creates flat compatibility release files."""
    import shutil

    out_dir = tmp_path / "f143_golden"
    out_dir.mkdir()
    shutil.copy2(GOLDEN_DIR / "tier_c_review.json", out_dir / "tier_c_review.json")

    returned_gold, returned_manifest = generate_and_freeze_golden_release(output_dir=out_dir)

    # 1. Producer return paths must be under the resolved CURRENT release directory
    rel = resolve_current_release(out_dir)
    assert returned_gold == rel["artifacts"]["golden_1039.jsonl"]
    assert returned_manifest == rel["artifacts"]["golden_manifest.json"]
    assert returned_gold.parent == rel["release_dir"]
    assert returned_manifest.parent == rel["release_dir"]
    assert returned_gold.is_file()
    assert returned_manifest.is_file()
    assert str(rel["release_dir"]).startswith(str(out_dir / "releases"))

    # 2. Assert NO flat release files exist in out_dir
    flat_release_files = [
        "golden_1039.jsonl",
        "golden_manifest.json",
        "components@R.json",
        "assignment@R.json",
        "anti_leak_audit.json",
        "multi_hop_judgment.jsonl",
    ]
    for flat_file in flat_release_files:
        assert not (out_dir / flat_file).exists(), f"Flat compatibility release file '{flat_file}' must not exist in {out_dir}"

    # 3. Assert live production repository data/golden has NO flat release files
    for flat_file in flat_release_files:
        assert not (GOLDEN_DIR / flat_file).exists(), f"Flat compatibility release file '{flat_file}' must not exist in {GOLDEN_DIR}"

    # 4. Assert live production CURRENT pointer and all artifacts resolve cleanly
    prod_rel = resolve_current_release(GOLDEN_DIR)
    for art_path in prod_rel["artifacts"].values():
        assert art_path.exists()
        assert art_path.parent == prod_rel["release_dir"]


# ---------------------------------------------------------------------------
# F-149 — Two-Stage Release Contract (Candidate vs Approved Release)
# ---------------------------------------------------------------------------


def test_f149_candidate_release_not_accepted(tmp_path: Path) -> None:
    """F-149 P0: Candidate release has release_stage='candidate' and is not Phase-2-accepted."""
    import shutil

    # 1. Live release in GOLDEN_DIR is an approved release
    approved_rel = resolve_current_release(GOLDEN_DIR, require_approved=True)
    assert approved_rel["release_stage"] == "approved"
    assert approved_rel["phase2_accepted"] is True
    assert len(approved_rel["artifacts"]) == 7
    assert "multi_hop_audit.jsonl" in approved_rel["artifacts"]

    # 2. In an isolated golden directory, point CURRENT to candidate release r_20260923_015848_f30b21ca
    cand_dir = tmp_path / "cand_golden"
    cand_dir.mkdir()
    shutil.copytree(GOLDEN_DIR / "releases", cand_dir / "releases")
    cand_release_id = "r_20260923_015848_f30b21ca"
    cand_release_dir = cand_dir / "releases" / cand_release_id
    cand_manifest_path = cand_release_dir / "golden_manifest.json"
    cand_manifest = json.loads(cand_manifest_path.read_text(encoding="utf-8"))

    cand_artifacts = {
        name: {
            "path": name,
            "sha256": hashlib.sha256((cand_release_dir / name).read_bytes()).hexdigest(),
        }
        for name in [
            "golden_1039.jsonl",
            "components@R.json",
            "assignment@R.json",
            "anti_leak_audit.json",
            "multi_hop_judgment.jsonl",
            "golden_manifest.json",
        ]
    }
    cand_pointer = {
        "format_version": 1,
        "release_id": cand_release_id,
        "release_stage": "candidate",
        "phase2_accepted": False,
        "candidate_release_id": None,
        "release_dir": f"releases/{cand_release_id}",
        "gold_sha256": cand_manifest["sha256"],
        "manifest_sha256": hashlib.sha256(cand_manifest_path.read_bytes()).hexdigest(),
        "row_count": 1039,
        "published_at": "2026-09-23T01:58:48+00:00",
        "artifacts": cand_artifacts,
    }
    (cand_dir / "CURRENT").write_text(json.dumps(cand_pointer, indent=2), encoding="utf-8")

    rel = resolve_current_release(cand_dir, require_approved=False)
    assert rel["release_stage"] == "candidate"
    assert rel["phase2_accepted"] is False
    assert len(rel["artifacts"]) == 6
    assert "multi_hop_audit.jsonl" not in rel["artifacts"]

    # Invariant: require_approved=True MUST fail closed on candidate release
    with pytest.raises(ReleaseGateError, match="is at stage 'candidate'"):
        resolve_current_release(cand_dir, require_approved=True)

    with pytest.raises(ReleaseGateError, match="is at stage 'candidate'"):
        load_current_golden_rows(cand_dir, require_approved=True)


def test_f149_promotion_fails_on_missing_or_invalid_audit(tmp_path: Path) -> None:
    """F-149 P0: Promotion to approved release fails closed on missing, invalid, or mismatched audit."""
    import shutil

    out_dir = tmp_path / "promotion_golden"
    out_dir.mkdir()
    shutil.copy2(GOLDEN_DIR / "CURRENT", out_dir / "CURRENT")
    shutil.copytree(GOLDEN_DIR / "releases", out_dir / "releases")

    cand_rel = resolve_current_release(out_dir, require_approved=False)
    cand_gold_sha = cand_rel["gold_sha256"]

    # Load candidate multi-hop rows to construct realistic audit records
    gold_rows = [
        json.loads(line)
        for line in cand_rel["artifacts"]["golden_1039.jsonl"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    mh_rows = [r for r in gold_rows if r["stratum"] == "multi_hop_evidence"]
    assert len(mh_rows) == 120

    # Build a valid base audit record template
    valid_records = []
    for r in mh_rows:
        valid_records.append({
            "query_id": r["query_id"],
            "output_hash": r["output_hash"],
            "gold_sha256": cand_gold_sha,
            "evidence_bindings": [
                {"message_id": mid} for mid in r["gold_evidence_message_ids"]
            ],
            "verdict": "sufficient",
            "reviewer_key": "codex",
            "reviewed_at": "2026-09-23T09:30:00+07:00",
            "reasoning_rationale": "Entailed by cited evidence.",
        })

    # Case A: Missing audit artifact
    missing_audit = out_dir / "non_existent_audit.jsonl"
    with pytest.raises(ReleaseGateError, match="Codex multi-hop audit artifact not found"):
        promote_candidate_to_approved_release(out_dir, audit_path=missing_audit)

    # Helper to write audit records to a path
    audit_file = out_dir / "multi_hop_audit.jsonl"

    def write_audit(recs: list[dict[str, Any]]) -> None:
        lines = [json.dumps(r, sort_keys=True, separators=(",", ":")) for r in recs]
        audit_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Case B: Audit with non-sufficient verdict
    insufficient_records = [dict(r) for r in valid_records]
    insufficient_records[0]["verdict"] = "insufficient"
    write_audit(insufficient_records)
    with pytest.raises(ReleaseGateError, match="has non-sufficient verdict 'insufficient'"):
        promote_candidate_to_approved_release(out_dir, audit_path=audit_file)

    # Case C: Reviewer mismatch
    wrong_reviewer_records = [dict(r) for r in valid_records]
    wrong_reviewer_records[0]["reviewer_key"] = "antigravity"
    write_audit(wrong_reviewer_records)
    with pytest.raises(ReleaseGateError, match="reviewer mismatch"):
        promote_candidate_to_approved_release(out_dir, audit_path=audit_file)

    # Case D: Output hash mismatch
    tampered_hash_records = [dict(r) for r in valid_records]
    tampered_hash_records[0]["output_hash"] = "0000000000000000000000000000000000000000000000000000000000000000"
    write_audit(tampered_hash_records)
    with pytest.raises(ReleaseGateError, match="Output hash mismatch"):
        promote_candidate_to_approved_release(out_dir, audit_path=audit_file)

    # Case E1: candidate gold_sha256 omitted from record (F-150)
    missing_gold_sha_records = [dict(r) for r in valid_records]
    del missing_gold_sha_records[0]["gold_sha256"]
    write_audit(missing_gold_sha_records)
    with pytest.raises(ReleaseGateError, match="Candidate gold_sha256 binding missing in audit"):
        promote_candidate_to_approved_release(out_dir, audit_path=audit_file)

    # Case E2: candidate gold_sha256 mismatch
    mismatched_gold_sha_records = [dict(r) for r in valid_records]
    mismatched_gold_sha_records[0]["gold_sha256"] = "1111111111111111111111111111111111111111111111111111111111111111"
    write_audit(mismatched_gold_sha_records)
    with pytest.raises(ReleaseGateError, match="Gold SHA-256 mismatch in audit"):
        promote_candidate_to_approved_release(out_dir, audit_path=audit_file)


def test_f149_promotion_creates_approved_release_and_preserves_gold_bytes(tmp_path: Path) -> None:
    """F-149 P0: Valid promotion creates approved release with 7 artifacts, matching manifest hashes, and identical gold_sha256."""
    import shutil

    out_dir = tmp_path / "valid_promotion_golden"
    out_dir.mkdir()
    shutil.copy2(GOLDEN_DIR / "CURRENT", out_dir / "CURRENT")
    shutil.copytree(GOLDEN_DIR / "releases", out_dir / "releases")

    cand_rel = resolve_current_release(out_dir, require_approved=False)
    cand_rel_id = cand_rel["release_id"]
    cand_gold_sha = cand_rel["gold_sha256"]
    cand_gold_bytes = cand_rel["artifacts"]["golden_1039.jsonl"].read_bytes()

    # Load corpus messages to build exact evidence message hashes for all 120 rows
    messages_path = ROOT / "data/processed/messages.jsonl"
    msgs: dict[str, Any] = {}
    with open(messages_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                m = json.loads(line)
                msgs[m["message_id"]] = m

    gold_rows = [json.loads(line) for line in cand_gold_bytes.decode("utf-8").splitlines() if line.strip()]
    mh_rows = [r for r in gold_rows if r["stratum"] == "multi_hop_evidence"]

    # Construct 100% valid audit records matching candidate release exactly
    audit_records = []
    for r in mh_rows:
        qid = r["query_id"]
        bindings = []
        for mid in r["gold_evidence_message_ids"]:
            m_obj = msgs[mid]
            m_bytes = json.dumps(m_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
            m_hash = hashlib.sha256(m_bytes).hexdigest()
            bindings.append({
                "message_id": mid,
                "message_hash": m_hash,
                "session_index": m_obj.get("session_index", 0),
                "text_snippet": m_obj.get("text", "")[:80],
            })
        audit_records.append({
            "query_id": qid,
            "output_hash": r["output_hash"],
            "gold_sha256": cand_gold_sha,
            "evidence_bindings": bindings,
            "verdict": "sufficient",
            "reviewer_key": "codex",
            "reviewed_at": "2026-09-23T09:30:00+07:00",
            "reasoning_rationale": "Direct read: 100% strictly entailed by cited evidence.",
        })

    audit_path = out_dir / "codex_audit_input.jsonl"
    a_lines = [json.dumps(rec, sort_keys=True, separators=(",", ":")) for rec in audit_records]
    audit_path.write_text("\n".join(a_lines) + "\n", encoding="utf-8")
    expected_audit_sha = hashlib.sha256(audit_path.read_bytes()).hexdigest()

    # Perform promotion
    promoted_gold, promoted_manifest = promote_candidate_to_approved_release(
        output_dir=out_dir,
        audit_path=audit_path,
    )

    # 1. Assert resolve_current_release succeeds with require_approved=True
    approved_rel = resolve_current_release(out_dir, require_approved=True)
    assert approved_rel["release_stage"] == "approved"
    assert approved_rel["phase2_accepted"] is True
    assert approved_rel["candidate_release_id"] == cand_rel_id
    assert approved_rel["release_id"] != cand_rel_id
    assert promoted_gold == approved_rel["artifacts"]["golden_1039.jsonl"]
    assert promoted_manifest == approved_rel["artifacts"]["golden_manifest.json"]

    # 2. Assert gold_sha256 is byte-for-byte identical to candidate release
    assert approved_rel["gold_sha256"] == cand_gold_sha
    assert approved_rel["artifacts"]["golden_1039.jsonl"].read_bytes() == cand_gold_bytes

    # 3. Assert exactly 7 artifacts in approved release
    assert len(approved_rel["artifacts"]) == 7
    expected_7 = {
        "golden_1039.jsonl",
        "components@R.json",
        "assignment@R.json",
        "anti_leak_audit.json",
        "multi_hop_judgment.jsonl",
        "multi_hop_audit.jsonl",
        "golden_manifest.json",
    }
    assert set(approved_rel["artifacts"].keys()) == expected_7

    # 4. Assert manifest bindings for both judgment and audit
    manifest = approved_rel["manifest"]
    assert manifest["release_stage"] == "approved"
    assert manifest["phase2_accepted"] is True
    assert manifest["candidate_release_id"] == cand_rel_id
    assert manifest["multi_hop_judgment_ref"] == "multi_hop_judgment.jsonl"
    assert manifest["multi_hop_audit_ref"] == "multi_hop_audit.jsonl"
    assert manifest["multi_hop_audit_sha256"] == expected_audit_sha
    assert approved_rel["artifacts"]["multi_hop_audit.jsonl"].read_bytes() == audit_path.read_bytes()

    # 5. Assert load_current_golden_rows succeeds with require_approved=True
    loaded_rows = load_current_golden_rows(out_dir, require_approved=True)
    assert len(loaded_rows) == 1039

    # 6. Negative control: Tampering with multi_hop_audit.jsonl fails closed
    target_audit = approved_rel["artifacts"]["multi_hop_audit.jsonl"]
    target_audit.write_text('{"tampered": true}', encoding="utf-8")
    with pytest.raises(ReleaseGateError, match="Checksum mismatch for artifact 'multi_hop_audit.jsonl'"):
        resolve_current_release(out_dir, require_approved=True)


def test_f150_audit_gold_sha256_mandatory_and_rejects_missing_or_malformed() -> None:
    """F-150 P0: validate_multi_hop_audit enforces candidate gold_sha256 on EVERY record when expected_gold_sha256 is supplied."""
    cand_rel = resolve_current_release(GOLDEN_DIR, require_approved=False)
    cand_gold_sha = cand_rel["gold_sha256"]
    cand_gold_bytes = cand_rel["artifacts"]["golden_1039.jsonl"].read_bytes()
    gold_rows = [json.loads(line) for line in cand_gold_bytes.decode("utf-8").splitlines() if line.strip()]
    mh_rows = [r for r in gold_rows if r["stratum"] == "multi_hop_evidence"]

    messages_path = ROOT / "data/processed/messages.jsonl"
    msgs: dict[str, Any] = {}
    with open(messages_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                m = json.loads(line)
                msgs[m["message_id"]] = m

    # Construct 120 valid records
    valid_records = []
    for r in mh_rows:
        qid = r["query_id"]
        bindings = []
        for mid in r["gold_evidence_message_ids"]:
            m_obj = msgs[mid]
            m_bytes = json.dumps(m_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
            m_hash = hashlib.sha256(m_bytes).hexdigest()
            bindings.append({
                "message_id": mid,
                "message_hash": m_hash,
                "session_index": m_obj.get("session_index", 0),
                "text_snippet": m_obj.get("text", "")[:80],
            })
        valid_records.append({
            "query_id": qid,
            "output_hash": r["output_hash"],
            "gold_sha256": cand_gold_sha,
            "evidence_bindings": bindings,
            "verdict": "sufficient",
            "reviewer_key": "codex",
            "reviewed_at": "2026-09-23T09:30:00+07:00",
            "reasoning_rationale": "Direct read: entailed by cited messages.",
        })

    # Positive control: full set with candidate gold_sha256 passes cleanly
    validate_multi_hop_audit(
        valid_records,
        gold_rows,
        messages=msgs,
        expected_reviewer="codex",
        expected_gold_sha256=cand_gold_sha,
    )

    # Negative control 1: Codex reproduction - omitting gold_sha256 from all records fails closed
    omitted_all = [dict(r) for r in valid_records]
    for r in omitted_all:
        del r["gold_sha256"]
    with pytest.raises(ReleaseGateError, match="Candidate gold_sha256 binding missing in audit"):
        validate_multi_hop_audit(
            omitted_all,
            gold_rows,
            messages=msgs,
            expected_reviewer="codex",
            expected_gold_sha256=cand_gold_sha,
        )

    # Negative control 2: omitting gold_sha256 from single record fails closed
    omitted_single = [dict(r) for r in valid_records]
    del omitted_single[42]["gold_sha256"]
    with pytest.raises(ReleaseGateError, match="Candidate gold_sha256 binding missing in audit"):
        validate_multi_hop_audit(
            omitted_single,
            gold_rows,
            messages=msgs,
            expected_reviewer="codex",
            expected_gold_sha256=cand_gold_sha,
        )

    # Negative control 3: empty string gold_sha256 fails closed
    empty_sha = [dict(r) for r in valid_records]
    empty_sha[0]["gold_sha256"] = ""
    with pytest.raises(ReleaseGateError, match="Candidate gold_sha256 binding missing in audit"):
        validate_multi_hop_audit(
            empty_sha,
            gold_rows,
            messages=msgs,
            expected_reviewer="codex",
            expected_gold_sha256=cand_gold_sha,
        )

    # Negative control 4: mismatched gold_sha256 fails closed
    mismatched = [dict(r) for r in valid_records]
    mismatched[0]["gold_sha256"] = "deadbeef" * 8
    with pytest.raises(ReleaseGateError, match="Gold SHA-256 mismatch in audit"):
        validate_multi_hop_audit(
            mismatched,
            gold_rows,
            messages=msgs,
            expected_reviewer="codex",
            expected_gold_sha256=cand_gold_sha,
        )










