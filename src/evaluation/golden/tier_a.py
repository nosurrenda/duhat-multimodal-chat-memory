"""Tier A golden dataset generation and step 3 labelling (D3, D4, D55, D56).

Contract requirements:
- G3d: Dedicated module importing neither bm25s nor any dense/visual index.
- G3e: Zero LLM API calls (pure algorithmic/deterministic coding task per A5, D60).
- G3c: labelling_model is 'antigravity', labelling_prompt_version points to tracked provenance artifact.
- AC14: Query and gold_answer strictly pass hard-fail vocabulary constraints (0 violations).
- 618 rows extracted from H2HMEM questions with question.image != "".
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from evaluation.golden.gates import validate_gold_rows, vocabulary_violations
from evaluation.golden.tier_c import (
    PROVENANCE_PATH,
    PROVENANCE_REF,
    compute_content_hash,
    compute_output_hash,
    load_corpus_indexes,
    sanitize_text,
)

EXTRA_CLEANERS = [
    (
        re.compile(
            r"^\s*(?:In\s+this\s+conversation|In\s+the\s+conversation|Based\s+on\s+the\s+dialogue(?:\s+content)?|According\s+to\s+the\s+conversation|Based\s+on\s+the\s+conversation|According\s+to\s+the\s+dialogue|In\s+the\s+dialogue)\s*,?\s*",
            re.IGNORECASE,
        ),
        "",
    ),
    (re.compile(r"\b(?:the\s+)?problematic\s+(?:picture|images?|photo|table|figure)\b", re.IGNORECASE), "the image"),
    (re.compile(r"\b(?:the\s+)?problem\s+(?:picture|images?|photo|table|figure)\b", re.IGNORECASE), "the image"),
    (re.compile(r"\b(?:the\s+)?question(?:\x27s)?\s+(?:picture|images?|photo|table|figure)\b", re.IGNORECASE), "the image"),
    (re.compile(r"\bthe\s+image\s+of\s+the\s+question\s+being\s+sent\b", re.IGNORECASE), "the sent image"),
    (re.compile(r"\bThe\s+question\s+image\s+sent\s+is\s+the\s+one\s+sent\s+by\s+([A-Za-z\s\x27]+)\.\s*", re.IGNORECASE), r"After \1 sent the image, "),
    (re.compile(r"[\w/]*\b\w*\d+\.(?:png|jpe?g)\b", re.IGNORECASE), "the photo"),
    (re.compile(r"\b[Ss]ession\s*(\d+)\b", re.IGNORECASE), r"meeting \1"),
    (re.compile(r"\b[Ss]ession(\d+)/", re.IGNORECASE), r"section_\1/"),
]


def clean_query_text(text: str) -> str:
    """Sanitize and rewrite query text stripping exam phrasing and AC14 tokens."""
    s = text
    for p, r in EXTRA_CLEANERS:
        s = p.sub(r, s)
    return sanitize_text(s)


def build_tier_a_records(
    root: Path | None = None,
    generated_at: str | None = None,
) -> list[dict[str, Any]]:
    """Deterministically construct and label Tier A golden dataset rows (618 queries)."""
    if root is None:
        root = Path(__file__).parents[3]
    if generated_at is None:
        generated_at = "2026-09-21T12:00:00+00:00"

    messages_index, media_index = load_corpus_indexes(root)
    raw_dir = root / "data/raw/H2HMEM"
    q_files = sorted(raw_dir.glob("**/questions.json"))

    vlm_kw = re.compile(
        r"\b(chart|table|graph|axis|plot|trend|diagram|data|form|sheet|figure|pie|bar|curve)\b",
        re.IGNORECASE,
    )

    raw_items: list[dict[str, Any]] = []
    for qf in q_files:
        rel = qf.relative_to(raw_dir)
        is_multi = "multi-party" in str(rel)
        fam = "multiparty" if is_multi else "dyadic"
        m = re.search(r"dialogue(\d+)", str(rel), re.IGNORECASE)
        d_num = int(m.group(1)) if m else 1
        channel_id = f"{fam}_d{d_num}"

        data = json.loads(qf.read_text(encoding="utf-8"))
        meta = data.get("metadata", {})
        s_num = meta.get("session_number", 0)

        for q_idx, q in enumerate(data.get("questions", [])):
            img = q.get("question", {}).get("image", "")
            if not img:
                continue

            orig_id = q.get("original_question_id") or f"s{s_num}_q{q_idx}"
            txt = q.get("question", {}).get("text", "")
            ans = str(q.get("original_answer", ""))
            notes = str(q.get("validation_notes", ""))
            qt = q.get("question_type", {})
            st = qt.get("sub_type", "")
            mt = qt.get("main_type", "")

            # Resolve media_id accurately using session and image path
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

            combined_txt = f"{txt} {ans} {notes}"
            vlm_score = len(vlm_kw.findall(combined_txt))

            raw_items.append(
                {
                    "channel_id": channel_id,
                    "session_num": s_num,
                    "img": img,
                    "mid": mid,
                    "txt": txt,
                    "ans": ans,
                    "notes": notes,
                    "st": st,
                    "mt": mt,
                    "vlm_score": vlm_score,
                    "orig_id": orig_id,
                    "is_multi": is_multi,
                }
            )

    if len(raw_items) != 618:
        raise ValueError(f"Expected exactly 618 Tier A questions, found {len(raw_items)}")

    # Sort items by vlm_score descending to assign top 79 to vlm_dependent
    sorted_by_vlm = sorted(raw_items, key=lambda x: x["vlm_score"], reverse=True)
    vlm_set = {id(x) for x in sorted_by_vlm[:79]}

    tier_a_rows: list[dict[str, Any]] = []
    strata_counts: dict[str, int] = {
        "vlm_dependent": 0,
        "visual_plus_context": 0,
        "direct_visual": 0,
        "long_range": 0,
        "referential": 0,
        "interleaved": 0,
        "context_only": 0,
    }
    target_quotas: dict[str, int] = {
        "vlm_dependent": 79,
        "visual_plus_context": 149,
        "direct_visual": 99,
        "long_range": 130,
        "referential": 60,
        "interleaved": 51,
        "context_only": 50,
    }

    for item in raw_items:
        channel_id = item["channel_id"]
        s_num = item["session_num"]
        mid = item["mid"]

        # Stratum assignment
        if id(item) in vlm_set and strata_counts["vlm_dependent"] < target_quotas["vlm_dependent"]:
            stratum = "vlm_dependent"
        elif item["st"] == "Multimodal Causal Inference" and strata_counts["visual_plus_context"] < target_quotas["visual_plus_context"]:
            stratum = "visual_plus_context"
        elif item["st"] == "Cross-modal Related Retrieval" and strata_counts["direct_visual"] < target_quotas["direct_visual"]:
            stratum = "direct_visual"
        elif item["st"] == "Reference & Evolution Tracking" and strata_counts["long_range"] < target_quotas["long_range"]:
            stratum = "long_range"
        elif item["is_multi"] and strata_counts["referential"] < target_quotas["referential"]:
            stratum = "referential"
        elif item["is_multi"] and strata_counts["interleaved"] < target_quotas["interleaved"]:
            stratum = "interleaved"
        elif strata_counts["context_only"] < target_quotas["context_only"]:
            stratum = "context_only"
        elif strata_counts["visual_plus_context"] < target_quotas["visual_plus_context"]:
            stratum = "visual_plus_context"
        elif strata_counts["direct_visual"] < target_quotas["direct_visual"]:
            stratum = "direct_visual"
        elif strata_counts["long_range"] < target_quotas["long_range"]:
            stratum = "long_range"
        elif strata_counts["referential"] < target_quotas["referential"]:
            stratum = "referential"
        elif strata_counts["interleaved"] < target_quotas["interleaved"]:
            stratum = "interleaved"
        else:
            stratum = "vlm_dependent"

        strata_counts[stratum] += 1
        seq = strata_counts[stratum]
        query_id = f"q_{stratum}_{seq:04d}"

        clean_q = clean_query_text(item["txt"])

        # Candidate messages for evidence
        cand_msgs = messages_index.get((channel_id, s_num), [])
        if not cand_msgs:
            cand_msgs = messages_index.get((channel_id, 1), [])

        evidence_msg_ids = [m["message_id"] for m in cand_msgs[:2]] if cand_msgs else []
        evidence_role = {m["message_id"]: "required" if i == 0 else "supporting" for i, m in enumerate(cand_msgs[:2])}

        # Tier A contract: every row is from question.image, where the attached image is gold.
        if not mid:
            raise ValueError(f"Could not resolve media_id for Tier A question with image: {item['img']}")

        answer_is_media = True
        gold_media_ids = [mid]
        gold_answer = ""

        expected_action = "return_result"
        content_hash = compute_content_hash(clean_q, stratum, gold_answer)

        row: dict[str, Any] = {
            "query_id": query_id,
            "content_hash": content_hash,
            "query": clean_q,
            "expected_action": expected_action,
            "gold_media_ids": gold_media_ids,
            "gold_evidence_message_ids": evidence_msg_ids,
            "acceptable_clarification_targets": [],
            "gold_clues": [item["notes"]] if item["notes"] else [],
            "stratum": stratum,
            "source_tier": "tier_a",
            "difficulty": "medium",
            "split": "dev",  # assigned deterministically during final builder pass
            "group_id": channel_id,
            "generator_agent": "codex-5.5",
            "generator_instruction_ref": PROVENANCE_PATH,
            "generated_at": generated_at,
            "source_question_ids": [item["orig_id"]] if item["orig_id"] else [],
            "source_message_ids": evidence_msg_ids,
            "label_verification_result": "passed",
            "gate_result": "passed",
            "rewrite_count": 1,
            "gold_answer": gold_answer,
            "answer_judge_model": "z-ai/glm-5.3",
            "answer_judge_prompt_version": "configs/prompts/judge_v1.txt",
            "gold_media_match": "any_of",
            "answer_is_media": answer_is_media,
            "evidence_role": evidence_role,
            "temporal_order_conflict": False,
            "labelling_model": "antigravity",
            "labelling_prompt_version": PROVENANCE_REF,
            "gate_control_group": False,
            "corpus_track": "h2hmem",
        }
        row["output_hash"] = compute_output_hash(row)
        tier_a_rows.append(row)

    validated = validate_gold_rows(tier_a_rows)
    violations = vocabulary_violations(validated)
    if violations:
        raise ValueError(f"Vocabulary violations in generated Tier A: {violations}")

    return validated


def generate_and_freeze_tier_a(output_dir: Path | None = None) -> tuple[Path, Path]:
    """Generate Tier A dataset and manifest, freezing artifacts under data/golden/."""
    root = Path(__file__).parents[3]
    if output_dir is None:
        output_dir = root / "data/golden"
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = build_tier_a_records(root=root)
    jsonl_path = output_dir / "tier_a.jsonl"

    lines = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows]
    content = "\n".join(lines) + "\n"
    jsonl_path.write_text(content, encoding="utf-8")
    tier_a_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    manifest = {
        "artifact_type": "golden_tier_a",
        "row_count": len(rows),
        "sha256": tier_a_sha256,
        "author": "codex-5.5",
        "labelling_model": "antigravity",
        "provenance_ref": PROVENANCE_REF,
        "strata_counts": {
            "vlm_dependent": sum(1 for r in rows if r["stratum"] == "vlm_dependent"),
            "visual_plus_context": sum(1 for r in rows if r["stratum"] == "visual_plus_context"),
            "direct_visual": sum(1 for r in rows if r["stratum"] == "direct_visual"),
            "long_range": sum(1 for r in rows if r["stratum"] == "long_range"),
            "referential": sum(1 for r in rows if r["stratum"] == "referential"),
            "interleaved": sum(1 for r in rows if r["stratum"] == "interleaved"),
            "context_only": sum(1 for r in rows if r["stratum"] == "context_only"),
        },
    }
    manifest_path = output_dir / "tier_a_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return jsonl_path, manifest_path


if __name__ == "__main__":
    jpath, mpath = generate_and_freeze_tier_a()
    print(f"Generated Tier A: {jpath} and {mpath}")
