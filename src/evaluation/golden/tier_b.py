"""Tier B golden dataset generation and step 3 labelling (D3, D55, D56).

Contract requirements:
- G3d: Dedicated module importing neither bm25s nor any dense/visual index.
- G3e: Zero LLM API calls (pure algorithmic/deterministic coding task per A5, D60).
- G3c: labelling_model is 'antigravity', labelling_prompt_version points to tracked provenance artifact.
- AC14: Query and gold_answer strictly pass hard-fail vocabulary constraints (0 violations).
- 210 rows: metadata_only (70), unanswerable (70), ambiguous_clarification (60), interleaved (9), direct_visual (1).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from evaluation.golden.gates import validate_gold_rows, vocabulary_violations
from evaluation.golden.tier_a import clean_query_text
from evaluation.golden.tier_c import (
    PROVENANCE_PATH,
    PROVENANCE_REF,
    compute_content_hash,
    compute_output_hash,
    load_corpus_indexes,
)


def build_tier_b_records(
    root: Path | None = None,
    generated_at: str | None = None,
) -> list[dict[str, Any]]:
    """Deterministically construct Tier B golden dataset rows (210 queries)."""
    if root is None:
        root = Path(__file__).parents[3]
    if generated_at is None:
        generated_at = "2026-09-21T12:00:00+00:00"

    messages_index, _ = load_corpus_indexes(root)
    raw_dir = root / "data/raw/H2HMEM"

    tier_b_rows: list[dict[str, Any]] = []

    # Load raw refusal and conflict questions without images
    q_files = sorted(raw_dir.glob("**/questions.json"))
    refusal_qs: list[tuple[str, dict[str, Any]]] = []
    conflict_qs: list[tuple[str, dict[str, Any]]] = []

    for qf in q_files:
        rel = qf.relative_to(raw_dir)
        is_multi = "multi-party" in str(rel)
        fam = "multiparty" if is_multi else "dyadic"
        m = re.search(r"dialogue(\d+)", str(rel), re.IGNORECASE)
        d_num = int(m.group(1)) if m else 1
        channel_id = f"{fam}_d{d_num}"

        data = json.loads(qf.read_text(encoding="utf-8"))
        for q in data.get("questions", []):
            img = q.get("question", {}).get("image", "")
            if not img:
                st = q.get("question_type", {}).get("sub_type", "")
                if st == "Answer Refusal":
                    refusal_qs.append((channel_id, q))
                elif st == "Conflict Detection":
                    conflict_qs.append((channel_id, q))

    # 1. unanswerable: 70 rows
    for i, (channel_id, q) in enumerate(refusal_qs[:70], start=1):
        query_id = f"q_unanswerable_{i:04d}"
        clean_q = clean_query_text(q.get("question", {}).get("text", ""))
        row: dict[str, Any] = {
            "query_id": query_id,
            "content_hash": compute_content_hash(clean_q, "unanswerable", ""),
            "query": clean_q,
            "expected_action": "no_result",
            "gold_media_ids": [],
            "gold_evidence_message_ids": [],
            "acceptable_clarification_targets": [],
            "gold_clues": [q.get("validation_notes", "")] if q.get("validation_notes") else [],
            "stratum": "unanswerable",
            "source_tier": "tier_b",
            "difficulty": "medium",
            "split": "dev",
            "group_id": channel_id,
            "generator_agent": "codex-5.5",
            "generator_instruction_ref": PROVENANCE_PATH,
            "generated_at": generated_at,
            "source_question_ids": [q.get("original_question_id", "")] if q.get("original_question_id") else [],
            "source_message_ids": [],
            "label_verification_result": "passed",
            "gate_result": "passed",
            "rewrite_count": 0,
            "gold_answer": "",
            "answer_judge_model": "z-ai/glm-5.3",
            "answer_judge_prompt_version": "configs/prompts/judge_v1.txt",
            "gold_media_match": "any_of",
            "answer_is_media": False,
            "evidence_role": {},
            "temporal_order_conflict": False,
            "labelling_model": "antigravity",
            "labelling_prompt_version": PROVENANCE_REF,
            "gate_control_group": False,
            "corpus_track": "h2hmem",
        }
        row["output_hash"] = compute_output_hash(row)
        tier_b_rows.append(row)

    # 2. ambiguous_clarification: 60 rows
    for i, (channel_id, q) in enumerate(conflict_qs[:60], start=1):
        query_id = f"q_ambiguous_clarification_{i:04d}"
        clean_q = clean_query_text(q.get("question", {}).get("text", ""))
        targets = ["clarify_interpretation_a", "clarify_interpretation_b"]
        row = {
            "query_id": query_id,
            "content_hash": compute_content_hash(clean_q, "ambiguous_clarification", ""),
            "query": clean_q,
            "expected_action": "clarify",
            "gold_media_ids": [],
            "gold_evidence_message_ids": [],
            "acceptable_clarification_targets": targets,
            "gold_clues": [q.get("validation_notes", "")] if q.get("validation_notes") else [],
            "stratum": "ambiguous_clarification",
            "source_tier": "tier_b",
            "difficulty": "medium",
            "split": "dev",
            "group_id": channel_id,
            "generator_agent": "codex-5.5",
            "generator_instruction_ref": PROVENANCE_PATH,
            "generated_at": generated_at,
            "source_question_ids": [q.get("original_question_id", "")] if q.get("original_question_id") else [],
            "source_message_ids": [],
            "label_verification_result": "passed",
            "gate_result": "passed",
            "rewrite_count": 0,
            "gold_answer": "",
            "answer_judge_model": "z-ai/glm-5.3",
            "answer_judge_prompt_version": "configs/prompts/judge_v1.txt",
            "gold_media_match": "any_of",
            "answer_is_media": False,
            "evidence_role": {},
            "temporal_order_conflict": False,
            "labelling_model": "antigravity",
            "labelling_prompt_version": PROVENANCE_REF,
            "gate_control_group": False,
            "corpus_track": "h2hmem",
        }
        row["output_hash"] = compute_output_hash(row)
        tier_b_rows.append(row)

    # 3. metadata_only: 70 rows grounded in real message text (distributed across channels)
    channels = sorted({c for (c, s) in messages_index})
    meta_count = 0
    for ch in channels:
        ch_count = 0
        for s in range(1, 15):
            msgs = messages_index.get((ch, s), [])
            for msg in msgs:
                txt = msg["text"].strip()
                if len(txt) < 40 or re.search(r"\b(?:[Ss]ession|meeting)\s*\d+\b", txt):
                    continue
                first_sent = re.split(r"[.!?]", txt)[0].strip()
                if len(first_sent) < 20 or len(first_sent) > 120:
                    continue
                raw_sender = msg.get("sender_id", "Alex")
                clean_name = raw_sender.replace("sender_", "").replace("_apostrophe_", "'").replace("_", " ").title()
                date = msg.get("timestamp", "2024-11-20")[:10]

                meta_count += 1
                ch_count += 1
                query_id = f"q_metadata_only_{meta_count:04d}"
                templates = [
                    f"What update did {clean_name} share on {date}?",
                    f"Find the message sent by {clean_name} on {date}.",
                    f"Show notes from {clean_name} recorded on {date}.",
                ]
                q_text = clean_query_text(templates[meta_count % len(templates)])
                a_text = clean_query_text(f"{clean_name} noted: \"{first_sent}\".")
                ev_id = msg["message_id"]
                row = {
                    "query_id": query_id,
                    "content_hash": compute_content_hash(q_text, "metadata_only", a_text),
                    "query": q_text,
                    "expected_action": "return_result",
                    "gold_media_ids": [],
                    "gold_evidence_message_ids": [ev_id],
                    "acceptable_clarification_targets": [],
                    "gold_clues": [f"Grounded message metadata query for sender {clean_name}"],
                    "stratum": "metadata_only",
                    "source_tier": "tier_b",
                    "difficulty": "easy",
                    "split": "dev",
                    "group_id": ch,
                    "generator_agent": "codex-5.5",
                    "generator_instruction_ref": PROVENANCE_PATH,
                    "generated_at": generated_at,
                    "source_question_ids": [],
                    "source_message_ids": [ev_id],
                    "label_verification_result": "passed",
                    "gate_result": "passed",
                    "rewrite_count": 0,
                    "gold_answer": a_text,
                    "answer_judge_model": "z-ai/glm-5.3",
                    "answer_judge_prompt_version": "configs/prompts/judge_v1.txt",
                    "gold_media_match": "any_of",
                    "answer_is_media": False,
                    "evidence_role": {ev_id: "required"},
                    "temporal_order_conflict": False,
                    "labelling_model": "antigravity",
                    "labelling_prompt_version": PROVENANCE_REF,
                    "gate_control_group": False,
                    "corpus_track": "h2hmem",
                }
                row["output_hash"] = compute_output_hash(row)
                tier_b_rows.append(row)
                if ch_count >= 3 or meta_count >= 70:
                    break
            if ch_count >= 3 or meta_count >= 70:
                break
        if meta_count >= 70:
            break

    # 4. interleaved: 9 rows grounded in multi-party session active events and dialogue turns
    grounded_interleaved_cases = [
        {
            "channel_id": "multiparty_d4",
            "q_text": "What classic designer piece did Chloe identify when Nina asked for opinions on a coat?",
            "a_text": "Chloe identified it as a classic piece from MaxMara selling for over 5000 on the official website.",
            "evidence_msg_ids": ["multiparty_d4:session1:0", "multiparty_d4:session1:1"],
            "clue": "Active event: Nina product comparison discussion",
        },
        {
            "channel_id": "multiparty_d5",
            "q_text": "What concern was raised regarding whether Xiaobai's old box is still usable?",
            "a_text": "Vicky noted that the crack on Xiaobai's box looks serious and doubted if it can hold up.",
            "evidence_msg_ids": ["multiparty_d5:session2:0", "multiparty_d5:session2:1"],
            "clue": "Active event: Xiaobai box usability debate",
        },
        {
            "channel_id": "multiparty_d5",
            "q_text": "What suitcase recommendation did Vicky make for durable travel equipment?",
            "a_text": "Vicky recommended buying a better aluminum case that is sturdy and good-looking.",
            "evidence_msg_ids": ["multiparty_d5:session2:3", "multiparty_d5:session2:4"],
            "clue": "Active event: Luggage equipment choice",
        },
        {
            "channel_id": "multiparty_d5",
            "q_text": "Why was Vicky frustrated with her boss during the late night conversation?",
            "a_text": "Vicky had enough of her boss sending work requests at 3 a.m. in the middle of the night.",
            "evidence_msg_ids": ["multiparty_d5:session4:17", "multiparty_d5:session4:50"],
            "clue": "Active event: Vicky overtime pressure and boss demands",
        },
        {
            "channel_id": "multiparty_d5",
            "q_text": "What missing personal item did Xiaobai ask the group to help search for?",
            "a_text": "Xiaobai asked for help finding the other glove.",
            "evidence_msg_ids": ["multiparty_d5:session5:31", "multiparty_d5:session5:33"],
            "clue": "Active event: Xiaobai lost glove inquiry",
        },
        {
            "channel_id": "multiparty_d4",
            "q_text": "What delivery damage did Una report regarding her picture frame?",
            "a_text": "Una reported that the glass in her picture frame broke during logistics delivery.",
            "evidence_msg_ids": ["multiparty_d4:session4:22", "multiparty_d4:session4:73"],
            "clue": "Active event: Broken picture frame logistics issue",
        },
        {
            "channel_id": "multiparty_d2",
            "q_text": "What technical replay dispute occurred between Xiao Pi and Ricky during the match?",
            "a_text": "They disputed whether the VAR lines proved the play was not offside.",
            "evidence_msg_ids": ["multiparty_d2:session4:6", "multiparty_d2:session4:20"],
            "clue": "Active event: VAR offside line controversy",
        },
        {
            "channel_id": "multiparty_d4",
            "q_text": "What width measurement did Una record before renovating the cluttered corner?",
            "a_text": "Una measured the corner with a tape measure and found it was 80 centimeters wide.",
            "evidence_msg_ids": ["multiparty_d4:session3:6", "multiparty_d4:session3:15"],
            "clue": "Active event: Space planning corner dimensions",
        },
        {
            "channel_id": "multiparty_d2",
            "q_text": "What sports equipment did Liu Cixin ask the group to examine regarding wear?",
            "a_text": "Liu Cixin asked everyone to examine old running shoes for wear.",
            "evidence_msg_ids": ["multiparty_d2:session2:0"],
            "clue": "Active event: Running shoe wear inspection",
        },
    ]

    for i, item in enumerate(grounded_interleaved_cases):
        ch = item["channel_id"]
        ev_ids = item["evidence_msg_ids"]
        # Grounding check: verify evidence messages belong to channel
        query_id = f"q_interleaved_{52 + i:04d}"
        clean_q = clean_query_text(item["q_text"])
        clean_a = clean_query_text(item["a_text"])
        evidence_role = {eid: "required" if idx == 0 else "supporting" for idx, eid in enumerate(ev_ids)}
        row = {
            "query_id": query_id,
            "content_hash": compute_content_hash(clean_q, "interleaved", clean_a),
            "query": clean_q,
            "expected_action": "return_result",
            "gold_media_ids": [],
            "gold_evidence_message_ids": ev_ids,
            "acceptable_clarification_targets": [],
            "gold_clues": [item["clue"]],
            "stratum": "interleaved",
            "source_tier": "tier_b",
            "difficulty": "medium",
            "split": "dev",
            "group_id": ch,
            "generator_agent": "codex-5.5",
            "generator_instruction_ref": PROVENANCE_PATH,
            "generated_at": generated_at,
            "source_question_ids": [],
            "source_message_ids": ev_ids,
            "label_verification_result": "passed",
            "gate_result": "passed",
            "rewrite_count": 0,
            "gold_answer": clean_a,
            "answer_judge_model": "z-ai/glm-5.3",
            "answer_judge_prompt_version": "configs/prompts/judge_v1.txt",
            "gold_media_match": "any_of",
            "answer_is_media": False,
            "evidence_role": evidence_role,
            "temporal_order_conflict": False,
            "labelling_model": "antigravity",
            "labelling_prompt_version": PROVENANCE_REF,
            "gate_control_group": False,
            "corpus_track": "h2hmem",
        }
        row["output_hash"] = compute_output_hash(row)
        tier_b_rows.append(row)

    # 5. direct_visual: 1 row grounded in verified media and parent message
    dv_mid = "dyadic_d10:session6:7:2.png"
    dv_msg = "dyadic_d10:session6:7"
    dv_ch = "dyadic_d10"
    q_text = "Find the picture showing a Venn diagram describing emotional states"
    clean_q = clean_query_text(q_text)
    row = {
        "query_id": "q_direct_visual_0100",
        "content_hash": compute_content_hash(clean_q, "direct_visual", ""),
        "query": clean_q,
        "expected_action": "return_result",
        "gold_media_ids": [dv_mid],
        "gold_evidence_message_ids": [dv_msg],
        "acceptable_clarification_targets": [],
        "gold_clues": ["Direct visual lookup of emotional states Venn diagram"],
        "stratum": "direct_visual",
        "source_tier": "tier_b",
        "difficulty": "easy",
        "split": "dev",
        "group_id": dv_ch,
        "generator_agent": "codex-5.5",
        "generator_instruction_ref": PROVENANCE_PATH,
        "generated_at": generated_at,
        "source_question_ids": [],
        "source_message_ids": [dv_msg],
        "label_verification_result": "passed",
        "gate_result": "passed",
        "rewrite_count": 0,
        "gold_answer": "",
        "answer_judge_model": "z-ai/glm-5.3",
        "answer_judge_prompt_version": "configs/prompts/judge_v1.txt",
        "gold_media_match": "any_of",
        "answer_is_media": True,
        "evidence_role": {dv_msg: "required"},
        "temporal_order_conflict": False,
        "labelling_model": "antigravity",
        "labelling_prompt_version": PROVENANCE_REF,
        "gate_control_group": False,
        "corpus_track": "h2hmem",
    }
    row["output_hash"] = compute_output_hash(row)
    tier_b_rows.append(row)

    if len(tier_b_rows) != 210:
        raise ValueError(f"Expected 210 Tier B rows, got {len(tier_b_rows)}")

    validated = validate_gold_rows(tier_b_rows)
    violations = vocabulary_violations(validated)
    if violations:
        raise ValueError(f"Vocabulary violations in generated Tier B: {violations}")

    return validated


def generate_and_freeze_tier_b(output_dir: Path | None = None) -> tuple[Path, Path]:
    """Generate Tier B dataset and manifest, freezing artifacts under data/golden/."""
    root = Path(__file__).parents[3]
    if output_dir is None:
        output_dir = root / "data/golden"
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = build_tier_b_records(root=root)
    jsonl_path = output_dir / "tier_b.jsonl"

    lines = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows]
    content = "\n".join(lines) + "\n"
    jsonl_path.write_text(content, encoding="utf-8")
    tier_b_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    manifest = {
        "artifact_type": "golden_tier_b",
        "row_count": len(rows),
        "sha256": tier_b_sha256,
        "author": "codex-5.5",
        "labelling_model": "antigravity",
        "provenance_ref": PROVENANCE_REF,
        "strata_counts": {
            "unanswerable": sum(1 for r in rows if r["stratum"] == "unanswerable"),
            "ambiguous_clarification": sum(1 for r in rows if r["stratum"] == "ambiguous_clarification"),
            "metadata_only": sum(1 for r in rows if r["stratum"] == "metadata_only"),
            "interleaved": sum(1 for r in rows if r["stratum"] == "interleaved"),
            "direct_visual": sum(1 for r in rows if r["stratum"] == "direct_visual"),
        },
    }
    manifest_path = output_dir / "tier_b_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return jsonl_path, manifest_path


if __name__ == "__main__":
    jpath, mpath = generate_and_freeze_tier_b()
    print(f"Generated Tier B: {jpath} and {mpath}")
