"""Consolidated Golden Dataset builder and release freezer (Phases 2 & 2.5).

Produces the complete 1,039-query release:
- Tier A (618 rows): question.image rewrites
- Tier B (210 rows): deterministic templates
- Tier C (211 rows): cross-session & multi-hop evidence
Total = 1,039 queries (416 dev / 623 test).

Enforces:
- G1–G5: dataset and gold validity, AC14 hard-fail vocabulary gate.
- G6: AC3 BM25s anti-leak gate (top-1 <= 15%).
- G8: Split assigner grouping by channel cluster (zero cross-split group leakage).
"""

from __future__ import annotations

import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import bm25s

from evaluation.golden.gates import (
    ReleaseGateError,
    validate_gold_rows,
    validate_multi_hop_audit,
    validate_multi_hop_judgment,
    vocabulary_violations,
)
from evaluation.golden.tier_a import build_tier_a_records
from evaluation.golden.tier_b import build_tier_b_records
from evaluation.golden.tier_c import (
    PROVENANCE_REF,
    build_tier_c_records,
    compute_content_hash,
    compute_output_hash,
)

DEV_QUOTAS: dict[str, int] = {
    "visual_plus_context": 80,
    "long_range": 52,
    "vlm_dependent": 32,
    "multi_hop_evidence": 48,
    "direct_visual": 40,
    "context_only": 36,
    "metadata_only": 28,
    "unanswerable": 28,
    "referential": 24,
    "interleaved": 24,
    "ambiguous_clarification": 24,
}

TEST_QUOTAS: dict[str, int] = {
    "visual_plus_context": 120,
    "long_range": 78,
    "vlm_dependent": 47,
    "multi_hop_evidence": 72,
    "direct_visual": 60,
    "context_only": 54,
    "metadata_only": 42,
    "unanswerable": 42,
    "referential": 36,
    "interleaved": 36,
    "ambiguous_clarification": 36,
}

TOTAL_DEV = 416
TOTAL_TEST = 623
TOTAL_QUERIES = 1039


class UnionFind:
    """Union-Find disjoint-set data structure for G8 component construction."""

    def __init__(self, elements: Iterable[str]) -> None:
        self.parent = {e: e for e in elements}

    def find(self, x: str) -> str:
        if self.parent[x] != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            if rx < ry:
                self.parent[ry] = rx
            else:
                self.parent[rx] = ry


def build_g8_components(
    rows: list[dict[str, Any]],
    duplicate_edges: list[tuple[str, str]] | None = None,
    root: Path | None = None,
) -> dict[str, list[str]]:
    """G8a: Build union-find connected components over source_dialogue (group_id) and near-duplicate edges."""
    if root is None:
        root = Path(__file__).parents[3]

    groups = sorted({r["group_id"] for r in rows})
    uf = UnionFind(groups)

    if duplicate_edges is not None:
        edges_to_apply = list(duplicate_edges)
    else:
        edge_artifact_path = root / "data/visual_embeddings/near_dup_clusters.jsonl"
        if not edge_artifact_path.exists():
            raise FileNotFoundError(f"G8 requires Phase 1.5 frozen edge/cluster artifact at {edge_artifact_path}")
        clusters: dict[str, list[str]] = {}
        with open(edge_artifact_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    clusters.setdefault(rec["near_dup_cluster_id"], []).append(rec["media_id"])
        edges_to_apply = []
        for _cid, members in sorted(clusters.items()):
            if 1 < len(members) <= 10:
                for i in range(len(members)):
                    for j in range(i + 1, len(members)):
                        edges_to_apply.append((members[i].split(":")[0], members[j].split(":")[0]))

    for u, v in edges_to_apply:
        g1 = u.split(":")[0] if ":" in u else u
        g2 = v.split(":")[0] if ":" in v else v
        if g1 in uf.parent and g2 in uf.parent:
            uf.union(g1, g2)

    component_groups: dict[str, list[str]] = defaultdict(list)
    for g in groups:
        root_g = uf.find(g)
        component_groups[root_g].append(g)

    components: dict[str, list[str]] = {}
    for idx, (_root, members) in enumerate(sorted(component_groups.items()), start=1):
        comp_id = f"comp_{idx:03d}_{members[0]}"
        components[comp_id] = sorted(members)

    return components


def assign_splits_g8(
    components: dict[str, list[str]],
    rows: list[dict[str, Any]],
    split_salt: str = "phase-2-split-salt-v1",
) -> tuple[dict[str, str], dict[str, Any], bool]:
    """G8b-G8d: Salted deterministic greedy branch-and-bound assignment satisfying per-stratum quotas within tolerance."""
    group_to_comp = {}
    for cid, members in components.items():
        for m in members:
            group_to_comp[m] = cid

    comp_counts = {cid: Counter() for cid in components}
    for r in rows:
        cid = group_to_comp[r["group_id"]]
        comp_counts[cid][r["stratum"]] += 1

    # Salted deterministic order of components
    salted_cids = sorted(
        components.keys(),
        key=lambda c: (hashlib.sha256(f"{split_salt}:{c}".encode()).hexdigest(), c),
    )

    target_dev_cids: list[str] = []
    dev_strata: Counter[str] = Counter()

    def search(idx: int, current_sum: int, current_counts: Counter) -> bool:
        nonlocal target_dev_cids, dev_strata
        if current_sum == TOTAL_DEV:
            for s, q in DEV_QUOTAS.items():
                tol = max(8, int(0.15 * q))
                if abs(current_counts[s] - q) > tol:
                    return False
            dev_strata = current_counts.copy()
            return True

        if idx >= len(salted_cids) or current_sum > TOTAL_DEV:
            return False

        remaining_sum = sum(sum(comp_counts[c].values()) for c in salted_cids[idx:])
        if current_sum + remaining_sum < TOTAL_DEV:
            return False

        cid = salted_cids[idx]
        c_size = sum(comp_counts[cid].values())

        # Branch 1: include cid in dev
        target_dev_cids.append(cid)
        new_counts = current_counts.copy()
        for s, cnt in comp_counts[cid].items():
            new_counts[s] += cnt
        if search(idx + 1, current_sum + c_size, new_counts):
            return True
        target_dev_cids.pop()

        # Branch 2: exclude cid from dev
        return bool(search(idx + 1, current_sum, current_counts))

    found = search(0, 0, Counter())
    quota_infeasible = not found

    dev_cids_set = set(target_dev_cids)
    assignment: dict[str, str] = {}
    for cid in components:
        assignment[cid] = "dev" if cid in dev_cids_set else "test"

    dev_count = sum(sum(comp_counts[cid].values()) for cid in components if assignment[cid] == "dev")
    test_count = sum(sum(comp_counts[cid].values()) for cid in components if assignment[cid] == "test")

    if not quota_infeasible and (dev_count != TOTAL_DEV or test_count != TOTAL_TEST):
        raise ValueError(f"Total split mismatch: dev={dev_count} (expected {TOTAL_DEV}), test={test_count} (expected {TOTAL_TEST})")

    test_strata: Counter[str] = Counter()
    for cid, counts in comp_counts.items():
        if assignment[cid] == "test":
            for s, cnt in counts.items():
                test_strata[s] += cnt

    strata_report: dict[str, Any] = {}
    for s, dev_q in sorted(DEV_QUOTAS.items()):
        test_q = TEST_QUOTAS[s]
        d_act = dev_strata[s]
        t_act = test_strata[s]
        diff = d_act - dev_q
        tol = max(8, int(0.15 * dev_q))
        passed = abs(diff) <= tol
        strata_report[s] = {
            "dev_actual": d_act,
            "dev_quota": dev_q,
            "test_actual": t_act,
            "test_quota": test_q,
            "dev_diff": diff,
            "tolerance": tol,
            "status": "passed" if passed else "quota_infeasible",
        }

    dev_groups = sorted({m for cid in components if assignment[cid] == "dev" for m in components[cid]})
    test_groups = sorted({m for cid in components if assignment[cid] == "test" for m in components[cid]})

    report = {
        "split_salt": split_salt,
        "dev_count": dev_count,
        "test_count": test_count,
        "dev_groups": dev_groups,
        "test_groups": test_groups,
        "strata_report": strata_report,
        "quota_infeasible": quota_infeasible,
    }

    if quota_infeasible:
        raise ValueError(f"G7a quota infeasible: {strata_report}")

    return assignment, report, quota_infeasible


def check_cross_split_leak(assignment: dict[str, str], edge: tuple[str, str]) -> str:
    """G8e: Check if an edge joins two components/groups in different splits."""
    u, v = edge
    g1 = u.split(":")[0] if ":" in u else u
    g2 = v.split(":")[0] if ":" in v else v
    s1 = assignment.get(g1)
    s2 = assignment.get(g2)
    if s1 and s2 and s1 != s2:
        return "audit_finding: cross_split_leak"
    return "ok"


def validate_cross_split_leak(assignment: dict[str, str], duplicate_edges: list[tuple[str, str]]) -> None:
    """G8e positive control validation: raises audit_finding: cross_split_leak if any duplicate edge crosses splits."""
    for edge in duplicate_edges:
        finding = check_cross_split_leak(assignment, edge)
        if finding != "ok":
            raise ValueError(f"{finding}: duplicate edge {edge} crosses splits")


def build_full_golden_dataset(root: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Assemble, partition, and validate the full 1,039 golden dataset with G8 artifacts."""
    if root is None:
        root = Path(__file__).parents[3]

    tier_a = build_tier_a_records(root=root)
    tier_b = build_tier_b_records(root=root)
    tier_c, _tier_c_reviews = build_tier_c_records(root=root)

    combined = tier_a + tier_b + tier_c
    if len(combined) != TOTAL_QUERIES:
        raise ValueError(f"Expected exactly 1,039 rows, found {len(combined)}")

    # Ensure globally unique query_id per stratum across tiers
    stratum_seq: dict[str, int] = {}
    for r in combined:
        s = r["stratum"]
        stratum_seq[s] = stratum_seq.get(s, 0) + 1
        r["query_id"] = f"q_{s}_{stratum_seq[s]:04d}"

    # F-109: Read Phase 1.5 frozen edge/cluster artifact and fail closed if missing
    edge_artifact_path = root / "data/visual_embeddings/near_dup_clusters.jsonl"
    if not edge_artifact_path.exists():
        raise FileNotFoundError(f"G8 requires Phase 1.5 frozen edge/cluster artifact at {edge_artifact_path}")
    source_edge_artifact_sha256 = hashlib.sha256(edge_artifact_path.read_bytes()).hexdigest()

    clusters: dict[str, list[str]] = {}
    with open(edge_artifact_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                clusters.setdefault(rec["near_dup_cluster_id"], []).append(rec["media_id"])

    # Transform media edges to parent dialogue groups
    # Filter to clusters of size <= 10 to exclude the degenerate 627-item pHash collision artifact
    applied_edges: list[tuple[str, str]] = []
    for _cid, members in sorted(clusters.items()):
        if 1 < len(members) <= 10:
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    g1 = members[i].split(":")[0]
                    g2 = members[j].split(":")[0]
                    applied_edges.append((g1, g2))

    # Build G8 components with ingested duplicate edges and assign splits
    components = build_g8_components(combined, duplicate_edges=applied_edges, root=root)
    comp_assignment, assign_report, infeasible = assign_splits_g8(components, combined)
    if infeasible:
        raise ValueError("G8 split assignment is infeasible")

    group_to_comp = {m: cid for cid, members in components.items() for m in members}
    for r in combined:
        cid = group_to_comp[r["group_id"]]
        r["split"] = comp_assignment[cid]
        r["output_hash"] = compute_output_hash(r)

    # Validate release contract
    validated = validate_gold_rows(combined)
    violations = vocabulary_violations(validated)
    if violations:
        raise ValueError(f"Vocabulary violations in consolidated golden dataset: {violations}")

    dev_count = sum(1 for r in validated if r["split"] == "dev")
    test_count = sum(1 for r in validated if r["split"] == "test")
    if dev_count != TOTAL_DEV or test_count != TOTAL_TEST:
        raise ValueError(f"Expected 416 dev / 623 test, got {dev_count} / {test_count}")

    dev_groups = {r["group_id"] for r in validated if r["split"] == "dev"}
    test_groups = {r["group_id"] for r in validated if r["split"] == "test"}
    if not dev_groups.isdisjoint(test_groups):
        raise ValueError(f"G8c violation: groups overlap across dev and test: {dev_groups & test_groups}")

    components_artifact = {
        "release_id": "golden_1039_v1",
        "source_edge_artifact": "data/visual_embeddings/near_dup_clusters.jsonl",
        "source_edge_artifact_sha256": source_edge_artifact_sha256,
        "applied_duplicate_edges": [list(e) for e in applied_edges],
        "applied_duplicate_edge_count": len(applied_edges),
        "component_count": len(components),
        "components": {
            cid: {
                "component_id": cid,
                "groups": members,
                "query_count": sum(1 for r in validated if r["group_id"] in members),
            }
            for cid, members in components.items()
        },
    }

    assignment_artifact = {
        "release_id": "golden_1039_v1",
        "split_salt": assign_report["split_salt"],
        "splits": {"dev": dev_count, "test": test_count},
        "dev_groups": assign_report["dev_groups"],
        "test_groups": assign_report["test_groups"],
        "strata_report": assign_report["strata_report"],
        "quota_infeasible": False,
    }

    return validated, components_artifact, assignment_artifact


def check_ac3b_saturation_dev_only(
    rows: list[dict[str, Any]],
    corpus_messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """AC3b: Evaluate saturation on dev split only; test split is strictly isolated."""
    test_rows = [r for r in rows if r.get("split") == "test"]
    if test_rows:
        raise ValueError("AC3b saturation check strictly requires dev rows only; test rows detected!")

    dev_vpc = [r for r in rows if r.get("stratum") == "visual_plus_context"]
    if not dev_vpc:
        return {"status": "skipped", "dev_vpc_count": 0}

    corpus = [m["text"] for m in corpus_messages]
    msg_ids = [m["message_id"] for m in corpus_messages]
    retriever = bm25s.BM25()
    retriever.index(bm25s.tokenize(corpus))

    queries = [r["query"] for r in dev_vpc]
    results, _ = retriever.retrieve(bm25s.tokenize(queries), k=1)

    hits = 0
    for i, r in enumerate(dev_vpc):
        ev_ids = set(r.get("gold_evidence_message_ids", []))
        if msg_ids[results[i, 0]] in ev_ids:
            hits += 1

    rate = hits / len(dev_vpc) if dev_vpc else 0.0
    if rate >= 0.70:
        raise ValueError(f"AC3b saturation violation: dev visual_plus_context hit rate {rate:.2%} >= 70%")

    return {
        "status": "passed",
        "dev_visual_plus_context_count": len(dev_vpc),
        "bm25_top1_hits": hits,
        "bm25_top1_rate": rate,
        "threshold": 0.70,
    }


PARAPHRASE_SYNONYMS: list[tuple[str, str]] = [
    (r"\barranged to meet for a meal\b", "planned to dine together"),
    (r"\bmeet for a meal\b", "dine together"),
    (r"\bgood value for money\b", "great price-performance"),
    (r"\bgreat value for money\b", "reasonable prices"),
    (r"\brecommended a restaurant\b", "suggested an eatery"),
    (r"\brecommended\b", "suggested"),
    (r"\bconvenient and practical\b", "useful and handy"),
    (r"\bpractical\b", "useful"),
    (r"\bautomatic feeder\b", "automated feeding unit"),
    (r"\bautomated device\b", "robotic appliance"),
    (r"\bnetwork camera\b", "webcam monitor"),
    (r"\bair purifier\b", "air cleaning unit"),
    (r"\btreatment plan\b", "medical regimen"),
    (r"\bdiagnosis\b", "clinical assessment"),
    (r"\bsymptoms\b", "signs"),
    (r"\boccurred\b", "appeared"),
    (r"\bpurchase\b", "buy"),
    (r"\bpurchased\b", "bought"),
    (r"\bmentioned\b", "noted"),
    (r"\bdiscussed\b", "talked about"),
    (r"\bshared\b", "sent"),
    (r"\bcuisine\b", "food style"),
    (r"\brestaurant\b", "dining place"),
    (r"\bdifferences\b", "distinctions"),
    (r"\bdivide their tasks\b", "split the responsibilities"),
    (r"\bselected\b", "picked"),
    (r"\bchose\b", "picked"),
    (r"\bequipment combination\b", "gear combo"),
    (r"\bstanding desk\b", "height-adjustable desk"),
    (r"\btimed reminders\b", "scheduled alerts"),
    (r"\bsignificantly prominent\b", "noticeably strong"),
    (r"\beffect intensity\b", "impact magnitude"),
    (r"\badvantages and disadvantages\b", "pros and cons"),
    (r"\bmodular attachment system\b", "modular mounting setup"),
    (r"\bexperimental results\b", "test findings"),
    (r"\bpreliminary results\b", "early findings"),
    (r"\bsoundproof windows\b", "acoustic glazing"),
    (r"\banxiety behaviors\b", "stress signs"),
]


def rewrite_leaking_query(query: str) -> str:
    """Paraphrase query to break verbatim lexical matches with evidence."""
    res = query
    for pattern, replacement in PARAPHRASE_SYNONYMS:
        res = re.sub(pattern, replacement, res, flags=re.IGNORECASE)
    return res


def run_anti_leak_gate(rows: list[dict[str, Any]], root: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """G6 / AC3 BM25s anti-leak check with full rewrite and un-gated control audit."""
    if root is None:
        root = Path(__file__).parents[3]

    messages_path = root / "data/processed/messages.jsonl"
    if not messages_path.exists():
        raise FileNotFoundError(f"Messages artifact missing: {messages_path}")

    messages: list[dict[str, Any]] = []
    with open(messages_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                messages.append(json.loads(line))

    pre_gate_query_ids = [r["query_id"] for r in rows]

    return_result_rows = [r for r in rows if r.get("expected_action") == "return_result"]
    total_return_result = len(return_result_rows)

    control_group_ids: set[str] = set()
    for idx, r in enumerate(return_result_rows):
        if idx % 20 == 0:
            control_group_ids.add(r["query_id"])
            r["gate_control_group"] = True
        else:
            r["gate_control_group"] = False

    corpus = [m["text"] for m in messages]
    msg_ids = [m["message_id"] for m in messages]

    retriever = bm25s.BM25()
    corpus_tokens = bm25s.tokenize(corpus)
    retriever.index(corpus_tokens)

    queries = [r["query"] for r in rows]
    q_tokens = bm25s.tokenize(queries)
    results, _ = retriever.retrieve(q_tokens, k=1)

    per_query_outcomes: dict[str, dict[str, Any]] = {}
    stratum_stats: dict[str, dict[str, Any]] = {}
    all_strata = sorted({r["stratum"] for r in rows})
    for s in all_strata:
        stratum_stats[s] = {
            "total_queries": sum(1 for r in rows if r["stratum"] == s),
            "evaluated_queries": 0,
            "hits": 0,
            "rewritten_count": 0,
            "unrewritable_count": 0,
            "clean_count": 0,
            "survived_count": sum(1 for r in rows if r["stratum"] == s),
            "control_group_count": sum(1 for r in rows if r["stratum"] == s and r.get("gate_control_group")),
        }

    total_hits = 0
    evaluated_count = 0
    control_hits = 0
    control_evaluated = 0
    total_rewritten = 0
    total_unrewritable = 0

    for i, r in enumerate(rows):
        s = r["stratum"]
        qid = r["query_id"]
        is_return_result = r.get("expected_action") == "return_result"
        if not is_return_result:
            continue

        ev_ids = set(r.get("gold_evidence_message_ids", []))
        evaluated_count += 1
        stratum_stats[s]["evaluated_queries"] += 1

        top1_doc_idx = results[i, 0]
        top1_msg_id = msg_ids[top1_doc_idx]
        is_hit = top1_msg_id in ev_ids
        orig_query = r["query"]

        if r.get("gate_control_group"):
            control_evaluated += 1
            if is_hit:
                control_hits += 1
                total_hits += 1
                stratum_stats[s]["hits"] += 1
            r["gate_result"] = "passed"
            per_query_outcomes[qid] = {
                "outcome": "control_group",
                "initial_hit": is_hit,
                "final_hit": is_hit,
                "pre_gate_query": orig_query,
                "post_gate_query": orig_query,
                "rewrite_count": r.get("rewrite_count", 0),
            }
            continue

        if is_hit:
            total_hits += 1
            stratum_stats[s]["hits"] += 1
            rewritten = rewrite_leaking_query(orig_query)
            if rewritten != orig_query:
                res_new, _ = retriever.retrieve(bm25s.tokenize([rewritten]), k=1)
                new_top1 = msg_ids[res_new[0, 0]]
                if new_top1 not in ev_ids:
                    r["query"] = rewritten
                    r["rewrite_count"] = r.get("rewrite_count", 0) + 1
                    r["content_hash"] = compute_content_hash(rewritten, r["stratum"], r.get("gold_answer", ""))
                    r["output_hash"] = compute_output_hash(r)
                    r["gate_result"] = "passed"
                    outcome = "rewritten"
                    total_rewritten += 1
                    stratum_stats[s]["rewritten_count"] += 1
                else:
                    r["gate_result"] = "passed"
                    outcome = "unrewritable"
                    total_unrewritable += 1
                    stratum_stats[s]["unrewritable_count"] += 1
            else:
                r["gate_result"] = "passed"
                outcome = "unrewritable"
                total_unrewritable += 1
                stratum_stats[s]["unrewritable_count"] += 1
        else:
            r["gate_result"] = "passed"
            outcome = "clean"
            stratum_stats[s]["clean_count"] += 1

        per_query_outcomes[qid] = {
            "outcome": outcome,
            "initial_hit": is_hit,
            "final_hit": is_hit if outcome != "rewritten" else False,
            "pre_gate_query": orig_query,
            "post_gate_query": r["query"],
            "rewrite_count": r.get("rewrite_count", 0),
        }

    post_gate_query_ids = [r["query_id"] for r in rows]
    if sorted(pre_gate_query_ids) != sorted(post_gate_query_ids):
        raise ValueError("G6b violation: input and output query_id multisets do not match")

    # Re-evaluate all released queries with BM25s
    released_queries = [r["query"] for r in rows if r.get("expected_action") == "return_result"]
    rel_results, _ = retriever.retrieve(bm25s.tokenize(released_queries), k=1)
    released_hits = 0
    rel_idx = 0
    for r in rows:
        if r.get("expected_action") != "return_result":
            continue
        ev_ids = set(r.get("gold_evidence_message_ids", []))
        if msg_ids[rel_results[rel_idx, 0]] in ev_ids:
            released_hits += 1
        rel_idx += 1

    released_hit_rate = released_hits / evaluated_count if evaluated_count > 0 else 0.0
    if released_hit_rate > 0.15:
        raise ValueError(f"AC3 anti-leak gate failed: top-1 rate is {released_hit_rate:.2%} > 15%")

    dev_rows = [r for r in rows if r.get("split") == "dev"]
    saturation_report = check_ac3b_saturation_dev_only(dev_rows, messages)

    control_rate = control_hits / control_evaluated if control_evaluated > 0 else 0.0
    gated_hits = total_hits - control_hits
    gated_evaluated = evaluated_count - control_evaluated
    gated_rate = gated_hits / gated_evaluated if gated_evaluated > 0 else 0.0

    for s, st in stratum_stats.items():
        ev = st["evaluated_queries"]
        h = st["hits"]
        st["bm25s_top1_rate"] = h / ev if ev > 0 else 0.0

    summary_report = {
        "status": "passed",
        "evaluated_return_result_queries": evaluated_count,
        "total_return_result_expected": total_return_result,
        "initial_bm25s_top1_hits": total_hits,
        "initial_bm25s_top1_rate": total_hits / evaluated_count if evaluated_count > 0 else 0.0,
        "released_bm25s_top1_hits": released_hits,
        "released_bm25s_top1_rate": released_hit_rate,
        "bm25s_top1_hits": released_hits,
        "bm25s_top1_rate": released_hit_rate,
        "rewritten_count": total_rewritten,
        "unrewritable_count": total_unrewritable,
        "control_group": {
            "count": control_evaluated,
            "hits": control_hits,
            "rate": control_rate,
        },
        "gated_group": {
            "count": gated_evaluated,
            "hits": gated_hits,
            "rate": gated_rate,
        },
        "ac3b_saturation": saturation_report,
    }

    full_audit = {
        "summary": summary_report,
        "strata": stratum_stats,
        "pre_gate_query_ids": pre_gate_query_ids,
        "post_gate_query_ids": post_gate_query_ids,
        "pre_gate_query_count": len(pre_gate_query_ids),
        "post_gate_query_count": len(post_gate_query_ids),
        "per_query_outcomes": per_query_outcomes,
        "multiset_preserved": sorted(pre_gate_query_ids) == sorted(post_gate_query_ids),
    }

    return summary_report, full_audit


def atomic_publish_versioned_release(
    staging_dir: Path,
    output_dir: Path,
    artifact_names: list[str],
    gold_sha256: str,
    manifest_sha256: str,
    row_count: int = 1039,
    release_stage: str = "candidate",
    phase2_accepted: bool = False,
    candidate_release_id: str | None = None,
) -> tuple[str, Path]:
    """Publish an immutable versioned release bundle with an atomic CURRENT pointer (F-142, F-149).

    Architecture & Invariants:
    1. Immutable Versioned Directory:
       Artifacts are copied into a versioned directory under:
           `output_dir / "releases" / <release_id>/`
       where release_id is uniquely derived from timestamp and manifest digest.
       Once written, release directories are strictly immutable. Prior releases
       remain untouched and readable at all times.
    2. Two-Stage Release Contract (F-149):
       - Candidate release: release_stage="candidate", phase2_accepted=False. Contains
         6 artifacts (dataset + manifest + Antigravity multi_hop_judgment).
       - Approved release: release_stage="approved", phase2_accepted=True. Contains
         7 artifacts (dataset + manifest + Antigravity judgment + Codex multi_hop_audit).
    3. Atomic Release Boundary:
       A small pointer file `output_dir / "CURRENT"` records the active release_id,
       its relative release_dir, release_stage, phase2_accepted, candidate_release_id,
       and exact SHA-256 checksums of all release artifacts.
       The completed pointer is written to a temporary sibling file (`.tmp_CURRENT_<token>`)
       and published with a single same-filesystem `os.replace`.
       The pointer change, NOT the individual payload files, is the atomic release boundary.
    4. Reader Consistency:
       Uncoordinated readers resolve `CURRENT` before opening artifacts via `resolve_current_release`.
       Readers observe either the complete old release or the complete new release, never a mixed state.
    5. Fail-Closed Durability:
       Failures prior to the pointer swap leave `CURRENT` untouched pointing to the prior release.
       Orphaned staging directories are rolled back / cleaned up.
    6. Single Truthful Atomic Read Path (F-143):
       No flat compatibility files are generated or modified. All callers and consumers
       must resolve artifacts through `CURRENT` via `resolve_current_release`.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    lock_file = output_dir.parent / f".{output_dir.name}.release.lock"

    now_utc = datetime.datetime.now(datetime.UTC)
    timestamp_str = now_utc.strftime("%Y%m%d_%H%M%S")
    token = uuid.uuid4().hex[:8]
    release_id = f"r_{timestamp_str}_{manifest_sha256[:8]}"

    releases_dir = output_dir / "releases"
    releases_dir.mkdir(parents=True, exist_ok=True)
    target_release_dir = releases_dir / release_id
    if target_release_dir.exists():
        release_id = f"r_{timestamp_str}_{manifest_sha256[:8]}_{token[:6]}"
        target_release_dir = releases_dir / release_id

    # Temporary staging directory on the destination filesystem matching .tmp_publish_ prefix
    tmp_release_dir = releases_dir / f".tmp_publish_stage_{release_id}_{token}"
    tmp_current: Path | None = None
    target_dir_created = False

    with open(lock_file, "w") as lock_fp:
        fcntl.flock(lock_fp, fcntl.LOCK_EX)
        try:
            # 1. Compute checksums of all artifacts in staging
            hashes: dict[str, str] = {}
            for name in artifact_names:
                src_file = staging_dir / name
                hashes[name] = hashlib.sha256(src_file.read_bytes()).hexdigest()

            # 2. Stage new release directory under releases_dir
            tmp_release_dir.mkdir(parents=True, exist_ok=True)
            for name in artifact_names:
                src_file = staging_dir / name
                shutil.copy2(src_file, tmp_release_dir / name)

            # Move staged release dir to final immutable target release dir on same filesystem
            os.replace(tmp_release_dir, target_release_dir)
            target_dir_created = True

            # 3. Prepare CURRENT pointer payload
            current_payload = {
                "format_version": "1.0",
                "release_id": release_id,
                "release_dir": f"releases/{release_id}",
                "published_at": now_utc.isoformat(),
                "release_stage": release_stage,
                "phase2_accepted": phase2_accepted,
                "candidate_release_id": candidate_release_id,
                "row_count": row_count,
                "gold_sha256": gold_sha256,
                "manifest_sha256": manifest_sha256,
                "artifacts": {
                    name: {
                        "path": name,
                        "sha256": hashes[name],
                    }
                    for name in sorted(artifact_names)
                },
            }

            # 4. Write CURRENT pointer to temporary sibling file in output_dir
            tmp_current = output_dir / f".tmp_CURRENT_{token}"
            with open(tmp_current, "wb") as cf:
                cf.write(json.dumps(current_payload, indent=2, sort_keys=True).encode("utf-8") + b"\n")
                cf.flush()
                os.fsync(cf.fileno())

            # 5. ATOMIC RELEASE BOUNDARY: Single same-filesystem os.replace
            # The prior release directory remains untouched and readable until this single swap.
            os.replace(tmp_current, output_dir / "CURRENT")
            tmp_current = None

        except BaseException:
            # Clean up temporary staging dir or unreferenced target dir on failure
            if tmp_current and tmp_current.exists():
                try:
                    tmp_current.unlink()
                except OSError:
                    pass
            if tmp_release_dir.exists():
                shutil.rmtree(tmp_release_dir, ignore_errors=True)
            if target_dir_created and target_release_dir.exists():
                # Check if CURRENT points to this target_release_dir. If not, clean it up
                current_file = output_dir / "CURRENT"
                is_active = False
                if current_file.exists():
                    try:
                        c_data = json.loads(current_file.read_text(encoding="utf-8"))
                        is_active = c_data.get("release_id") == release_id
                    except (json.JSONDecodeError, OSError, KeyError):
                        is_active = False
                if not is_active:
                    shutil.rmtree(target_release_dir, ignore_errors=True)
            # If releases_dir is empty, clean it up
            try:
                if releases_dir.exists() and not any(releases_dir.iterdir()):
                    releases_dir.rmdir()
            except OSError:
                pass
            # Clean any stray temporary files
            for p in output_dir.glob(f".tmp_*_{token}*"):
                try:
                    p.unlink()
                except OSError:
                    pass
            raise
        finally:
            try:
                fcntl.flock(lock_fp, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                lock_file.unlink(missing_ok=True)
            except OSError:
                pass

    return release_id, target_release_dir


def atomic_publish_artifacts(
    staging_dir: Path,
    output_dir: Path,
    artifact_names: list[str],
    gold_sha256: str | None = None,
    manifest_sha256: str | None = None,
    row_count: int = 1039,
    release_stage: str = "candidate",
    phase2_accepted: bool = False,
    candidate_release_id: str | None = None,
) -> tuple[str, Path]:
    """Atomically publish staged artifacts via versioned release directory and CURRENT pointer."""
    if gold_sha256 is None:
        gold_file = staging_dir / "golden_1039.jsonl"
        gold_sha256 = hashlib.sha256(gold_file.read_bytes()).hexdigest()
    if manifest_sha256 is None:
        manifest_file = staging_dir / "golden_manifest.json"
        manifest_sha256 = hashlib.sha256(manifest_file.read_bytes()).hexdigest()

    return atomic_publish_versioned_release(
        staging_dir=staging_dir,
        output_dir=output_dir,
        artifact_names=artifact_names,
        gold_sha256=gold_sha256,
        manifest_sha256=manifest_sha256,
        row_count=row_count,
        release_stage=release_stage,
        phase2_accepted=phase2_accepted,
        candidate_release_id=candidate_release_id,
    )


def resolve_current_release(
    golden_dir: Path | None = None,
    require_approved: bool = False,
) -> dict[str, Any]:
    """Resolve and strictly validate the active golden dataset release from CURRENT pointer (F-142, F-149).

    Reads golden_dir / 'CURRENT', validates its structure, verifies that the
    referenced release directory exists, and confirms that all required release
    artifacts exist and strictly match their recorded SHA-256 checksums.

    Two-stage release contract (F-149):
    - Candidate release: release_stage="candidate", phase2_accepted=False. Contains
      6 artifacts (dataset + manifest + Antigravity multi_hop_judgment).
    - Approved release: release_stage="approved", phase2_accepted=True. Contains
      7 artifacts (dataset + manifest + Antigravity judgment + Codex multi_hop_audit).
    - If require_approved is True, fails closed if active release is a candidate.

    Args:
        golden_dir: Directory containing the CURRENT pointer and releases/
            (defaults to data/golden relative to project root).
        require_approved: If True, asserts that the release is an approved
            release (release_stage == 'approved' and phase2_accepted is True).

    Returns:
        dict containing:
            - release_id (str): Active release identifier.
            - release_dir (Path): Resolved immutable release directory.
            - pointer_path (Path): Path to the CURRENT pointer file.
            - release_stage (str): 'candidate' or 'approved'.
            - phase2_accepted (bool): True if approved, False if candidate.
            - candidate_release_id (str | None): ID of candidate promoted (if approved).
            - gold_sha256 (str): SHA-256 hash of golden_1039.jsonl.
            - manifest_sha256 (str): SHA-256 hash of golden_manifest.json.
            - row_count (int): Row count (1039).
            - published_at (str): ISO-8601 publication timestamp.
            - artifacts (dict[str, Path]): Map of artifact name to its verified Path.
            - manifest (dict): Loaded golden_manifest.json dictionary.
            - pointer_data (dict): Loaded CURRENT pointer dictionary.

    Raises:
        FileNotFoundError: If golden_dir / 'CURRENT' does not exist.
        ReleaseGateError: If CURRENT is malformed, missing required fields,
            any referenced artifact file is missing, any checksum mismatches,
            or require_approved=True but release is a candidate.
    """
    if golden_dir is None:
        golden_dir = Path(__file__).parents[3] / "data/golden"

    current_file = golden_dir / "CURRENT"
    if not current_file.exists():
        raise FileNotFoundError(f"CURRENT release pointer not found at {current_file}")

    try:
        current_data = json.loads(current_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise ReleaseGateError(f"Corrupt or unreadable CURRENT pointer at {current_file}: {e}") from e

    required_keys = {"format_version", "release_id", "release_dir", "gold_sha256", "manifest_sha256", "artifacts"}
    missing = required_keys - set(current_data.keys())
    if missing:
        raise ReleaseGateError(f"CURRENT pointer missing required fields: {missing}")

    release_stage = current_data.get("release_stage", "candidate")
    phase2_accepted = current_data.get("phase2_accepted", False)
    candidate_release_id = current_data.get("candidate_release_id")

    if require_approved and (release_stage != "approved" or not phase2_accepted):
        raise ReleaseGateError(
            f"Current release '{current_data.get('release_id')}' is at stage '{release_stage}' "
            f"(phase2_accepted={phase2_accepted}), but an approved release is required"
        )

    rel_dir_val = current_data["release_dir"]
    rel_dir = Path(rel_dir_val)
    if not rel_dir.is_absolute():
        rel_dir = golden_dir / rel_dir

    if not rel_dir.is_dir():
        raise ReleaseGateError(f"Referenced release directory does not exist: {rel_dir}")

    artifacts = current_data.get("artifacts", {})
    if release_stage == "approved":
        expected_artifact_names = [
            "golden_1039.jsonl",
            "components@R.json",
            "assignment@R.json",
            "anti_leak_audit.json",
            "multi_hop_judgment.jsonl",
            "multi_hop_audit.jsonl",
            "golden_manifest.json",
        ]
    else:
        expected_artifact_names = [
            "golden_1039.jsonl",
            "components@R.json",
            "assignment@R.json",
            "anti_leak_audit.json",
            "multi_hop_judgment.jsonl",
            "golden_manifest.json",
        ]

    artifact_paths: dict[str, Path] = {}
    for name in expected_artifact_names:
        if name not in artifacts:
            raise ReleaseGateError(f"CURRENT pointer missing entry for artifact '{name}'")
        art_info = artifacts[name]
        expected_sha = art_info.get("sha256")
        if not expected_sha:
            raise ReleaseGateError(f"CURRENT pointer artifact '{name}' missing sha256 checksum")

        rel_path = art_info.get("path", name)
        art_file = rel_dir / rel_path
        if not art_file.exists():
            raise ReleaseGateError(f"Release artifact '{name}' missing on disk at {art_file}")

        actual_sha = hashlib.sha256(art_file.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            raise ReleaseGateError(
                f"Checksum mismatch for artifact '{name}': pointer expected {expected_sha}, found {actual_sha}"
            )
        artifact_paths[name] = art_file

    # Validate gold_sha256 and manifest_sha256 invariants
    if current_data["gold_sha256"] != artifacts["golden_1039.jsonl"]["sha256"]:
        raise ReleaseGateError("CURRENT pointer gold_sha256 does not match golden_1039.jsonl sha256")

    if current_data["manifest_sha256"] != artifacts["golden_manifest.json"]["sha256"]:
        raise ReleaseGateError("CURRENT pointer manifest_sha256 does not match golden_manifest.json sha256")

    manifest_bytes = artifact_paths["golden_manifest.json"].read_bytes()
    try:
        manifest_data = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as e:
        raise ReleaseGateError(f"Corrupt golden_manifest.json in release: {e}") from e

    if manifest_data.get("sha256") != current_data["gold_sha256"]:
        raise ReleaseGateError("Manifest sha256 does not match gold_sha256")

    manifest_stage = manifest_data.get("release_stage", "candidate")
    manifest_accepted = manifest_data.get("phase2_accepted", False)
    if manifest_stage != release_stage:
        raise ReleaseGateError(
            f"Release stage mismatch between pointer ('{release_stage}') and manifest ('{manifest_stage}')"
        )
    if manifest_accepted != phase2_accepted:
        raise ReleaseGateError(
            f"phase2_accepted mismatch between pointer ({phase2_accepted}) and manifest ({manifest_accepted})"
        )

    if manifest_data.get("multi_hop_judgment_ref") != "multi_hop_judgment.jsonl":
        raise ReleaseGateError("Manifest multi_hop_judgment_ref missing or mismatch")
    if manifest_data.get("multi_hop_judgment_sha256") != artifacts["multi_hop_judgment.jsonl"]["sha256"]:
        raise ReleaseGateError("Manifest multi_hop_judgment_sha256 mismatch with artifact hash")

    if release_stage == "approved":
        if manifest_data.get("multi_hop_audit_ref") != "multi_hop_audit.jsonl":
            raise ReleaseGateError("Approved manifest missing or invalid multi_hop_audit_ref")
        if manifest_data.get("multi_hop_audit_sha256") != artifacts["multi_hop_audit.jsonl"]["sha256"]:
            raise ReleaseGateError("Approved manifest multi_hop_audit_sha256 mismatch with artifact hash")
        if candidate_release_id and manifest_data.get("candidate_release_id") != candidate_release_id:
            raise ReleaseGateError("Approved manifest candidate_release_id mismatch with pointer")

    return {
        "release_id": current_data["release_id"],
        "release_dir": rel_dir,
        "pointer_path": current_file,
        "release_stage": release_stage,
        "phase2_accepted": phase2_accepted,
        "candidate_release_id": candidate_release_id,
        "gold_sha256": current_data["gold_sha256"],
        "manifest_sha256": current_data["manifest_sha256"],
        "row_count": current_data.get("row_count", 1039),
        "published_at": current_data.get("published_at"),
        "artifacts": artifact_paths,
        "manifest": manifest_data,
        "pointer_data": current_data,
    }


def load_current_golden_rows(
    golden_dir: Path | None = None,
    require_approved: bool = False,
) -> list[dict[str, Any]]:
    """Resolve active release via resolve_current_release and return golden records."""
    release = resolve_current_release(golden_dir, require_approved=require_approved)
    jsonl_path = release["artifacts"]["golden_1039.jsonl"]
    return [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def promote_candidate_to_approved_release(
    output_dir: Path | None = None,
    candidate_release_id: str | None = None,
    audit_path: Path | None = None,
) -> tuple[Path, Path]:
    """Promote an immutable candidate release to an approved release (F-149).

    1. Reads and validates candidate release specified by candidate_release_id
       (or active CURRENT candidate if None).
    2. Validates Codex's multi_hop_audit.jsonl artifact against candidate rows
       and message corpus.
    3. Copies candidate dataset artifacts unchanged (byte-for-byte identical gold_sha256)
       and includes the audit artifact.
    4. Writes approved golden_manifest.json with release_stage='approved',
       phase2_accepted=True, candidate_release_id, and both judgment and audit checksums.
    5. Atomically publishes the approved release and swaps CURRENT pointer.

    Args:
        output_dir: Golden directory containing releases/ and CURRENT pointer
            (defaults to data/golden).
        candidate_release_id: ID of candidate release to promote. If None,
            reads the active release from CURRENT.
        audit_path: Path to Codex's multi_hop_audit.jsonl file. If None,
            defaults to output_dir / "multi_hop_audit.jsonl".

    Returns:
        tuple[Path, Path]: Paths to (golden_1039.jsonl, golden_manifest.json)
        in the approved release directory.
    """
    root = Path(__file__).parents[3]
    if output_dir is None:
        output_dir = root / "data/golden"
    output_dir.mkdir(parents=True, exist_ok=True)

    releases_dir = output_dir / "releases"
    if candidate_release_id is None:
        current_info = resolve_current_release(output_dir, require_approved=False)
        candidate_release_id = current_info["release_id"]
        cand_rel_dir = current_info["release_dir"]
    else:
        cand_rel_dir = releases_dir / candidate_release_id
        if not cand_rel_dir.is_dir():
            raise ReleaseGateError(f"Candidate release directory does not exist: {cand_rel_dir}")

    cand_manifest_file = cand_rel_dir / "golden_manifest.json"
    if not cand_manifest_file.exists():
        raise ReleaseGateError(f"Candidate release missing golden_manifest.json at {cand_manifest_file}")

    cand_manifest = json.loads(cand_manifest_file.read_text(encoding="utf-8"))
    cand_gold_sha256 = cand_manifest.get("sha256")
    if not cand_gold_sha256:
        raise ReleaseGateError("Candidate manifest missing sha256 checksum for gold rows")

    cand_gold_file = cand_rel_dir / "golden_1039.jsonl"
    if not cand_gold_file.exists():
        raise ReleaseGateError(f"Candidate golden_1039.jsonl missing at {cand_gold_file}")

    cand_gold_bytes = cand_gold_file.read_bytes()
    actual_gold_sha256 = hashlib.sha256(cand_gold_bytes).hexdigest()
    if actual_gold_sha256 != cand_gold_sha256:
        raise ReleaseGateError(
            f"Candidate golden_1039.jsonl checksum mismatch: manifest {cand_gold_sha256} != actual {actual_gold_sha256}"
        )

    # Required candidate dataset artifacts
    cand_dataset_artifacts = [
        "golden_1039.jsonl",
        "components@R.json",
        "assignment@R.json",
        "anti_leak_audit.json",
        "multi_hop_judgment.jsonl",
    ]
    for art_name in cand_dataset_artifacts:
        art_path = cand_rel_dir / art_name
        if not art_path.exists():
            raise ReleaseGateError(f"Candidate release missing required artifact: {art_name}")

    # Validate audit artifact
    if audit_path is None:
        audit_path = output_dir / "multi_hop_audit.jsonl"
    if not audit_path.exists():
        raise ReleaseGateError(f"Codex multi-hop audit artifact not found at {audit_path}")

    audit_bytes = audit_path.read_bytes()
    audit_lines = [line for line in audit_bytes.decode("utf-8").splitlines() if line.strip()]
    if not audit_lines:
        raise ReleaseGateError(f"Audit artifact is empty: {audit_path}")
    audit_records = [json.loads(line) for line in audit_lines]

    cand_gold_rows = [json.loads(line) for line in cand_gold_bytes.decode("utf-8").splitlines() if line.strip()]

    messages_path = root / "data/processed/messages.jsonl"
    msgs: dict[str, Any] | None = None
    if messages_path.exists():
        msgs = {}
        with open(messages_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    m = json.loads(line)
                    msgs[m["message_id"]] = m

    validate_multi_hop_audit(
        audit_records,
        cand_gold_rows,
        messages=msgs,
        expected_reviewer="codex",
        expected_gold_sha256=cand_gold_sha256,
    )

    with tempfile.TemporaryDirectory() as staging_dir_str:
        staging_dir = Path(staging_dir_str)

        # 1. Copy dataset artifacts unchanged byte-for-byte
        for art_name in cand_dataset_artifacts:
            shutil.copy2(cand_rel_dir / art_name, staging_dir / art_name)

        # Verify golden_1039.jsonl byte identity
        staged_gold_bytes = (staging_dir / "golden_1039.jsonl").read_bytes()
        assert hashlib.sha256(staged_gold_bytes).hexdigest() == cand_gold_sha256

        # 2. Stage Codex audit artifact
        staged_audit = staging_dir / "multi_hop_audit.jsonl"
        staged_audit.write_bytes(audit_bytes)
        audit_sha256 = hashlib.sha256(audit_bytes).hexdigest()

        # 3. Construct approved manifest
        approved_manifest = dict(cand_manifest)
        approved_manifest["release_stage"] = "approved"
        approved_manifest["phase2_accepted"] = True
        approved_manifest["candidate_release_id"] = candidate_release_id
        approved_manifest["multi_hop_audit_ref"] = "multi_hop_audit.jsonl"
        approved_manifest["multi_hop_audit_sha256"] = audit_sha256
        approved_manifest["promoted_at"] = datetime.datetime.now(datetime.UTC).isoformat()

        manifest_staging = staging_dir / "golden_manifest.json"
        manifest_staging.write_text(json.dumps(approved_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest_sha256 = hashlib.sha256(manifest_staging.read_bytes()).hexdigest()

        approved_artifacts = cand_dataset_artifacts + [
            "multi_hop_audit.jsonl",
            "golden_manifest.json",
        ]

        _rel_id, release_dir = atomic_publish_versioned_release(
            staging_dir=staging_dir,
            output_dir=output_dir,
            artifact_names=approved_artifacts,
            gold_sha256=cand_gold_sha256,
            manifest_sha256=manifest_sha256,
            row_count=len(cand_gold_rows),
            release_stage="approved",
            phase2_accepted=True,
            candidate_release_id=candidate_release_id,
        )

    return release_dir / "golden_1039.jsonl", release_dir / "golden_manifest.json"


def generate_and_freeze_golden_release(
    output_dir: Path | None = None,
    review_path: Path | None = None,
) -> tuple[Path, Path]:
    """Build, evaluate, and freeze the 1,039 Golden Dataset release and all G8/AC3 artifacts.

    Fail-closed & Atomic:
    - Pre-validates required review artifact and corpus messages before any output write.
    - Builds and gates all artifacts in a staging directory.
    - Atomically publishes to output_dir only after all release gates pass.
    """
    root = Path(__file__).parents[3]
    if output_dir is None:
        output_dir = root / "data/golden"
    if review_path is None:
        review_path = output_dir / "tier_c_review.json"

    # Pre-validation 1: Required review artifact must exist before ANY output file is written
    if not review_path.exists():
        raise ReleaseGateError(f"Required review artifact missing: {review_path}")

    # Pre-validation 2: Messages corpus must exist
    messages_path = root / "data/processed/messages.jsonl"
    if not messages_path.exists():
        raise ReleaseGateError(f"Required messages corpus missing: {messages_path}")

    # Build dataset and pre-check review records before writing any file
    rows, components_artifact, assignment_artifact = build_full_golden_dataset(root=root)
    anti_leak_summary, anti_leak_audit = run_anti_leak_gate(rows, root=root)

    mh_rows = [r for r in rows if r["stratum"] == "multi_hop_evidence"]
    mh_qids = {r["query_id"] for r in mh_rows}

    rdata = json.loads(review_path.read_text(encoding="utf-8"))
    rev_by_qid = {r["query_id"]: r for r in rdata.get("records", []) if r.get("stratum") == "multi_hop_evidence"}

    if set(rev_by_qid.keys()) != mh_qids:
        missing = mh_qids - set(rev_by_qid.keys())
        extra = set(rev_by_qid.keys()) - mh_qids
        raise ReleaseGateError(
            f"Review records query_id mismatch with release multi_hop rows. Missing: {missing}, Extra: {extra}"
        )

    msgs: dict[str, Any] = {}
    with open(messages_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                m = json.loads(line)
                msgs[m["message_id"]] = m

    judgment_records = []
    for r in mh_rows:
        qid = r["query_id"]
        rev = rev_by_qid[qid]
        evidence_bindings = []
        for mid in r["gold_evidence_message_ids"]:
            m_obj = msgs.get(mid)
            if not m_obj:
                raise ReleaseGateError(f"Evidence message {mid} not found in corpus")
            m_bytes = json.dumps(m_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
            m_hash = hashlib.sha256(m_bytes).hexdigest()
            evidence_bindings.append({
                "message_id": mid,
                "message_hash": m_hash,
                "session_index": m_obj.get("session_index", 0),
                "text_snippet": m_obj.get("text", "")[:80].replace("\n", " "),
            })
        verdict = rev.get("authored_verdict") or rev.get("answer_sufficiency")
        if not verdict:
            raise ReleaseGateError(f"Review record for {qid} missing verdict")
        reviewer_key = rev.get("reviewed_by") or rev.get("reviewer")
        if not reviewer_key:
            raise ReleaseGateError(f"Review record for {qid} missing reviewer")
        reviewed_at = rev.get("reviewed_at")
        if not reviewed_at:
            raise ReleaseGateError(f"Review record for {qid} missing reviewed_at")
        rationale = rev.get("reasoning_rationale")
        if not rationale:
            raise ReleaseGateError(f"Review record for {qid} missing reasoning_rationale")

        judgment_records.append({
            "query_id": qid,
            "output_hash": r["output_hash"],
            "evidence_bindings": evidence_bindings,
            "verdict": verdict,
            "reviewer_key": reviewer_key,
            "reviewed_at": reviewed_at,
            "entailment_triage_score": rev.get("entailment_triage_score", 1.0),
            "reasoning_rationale": rationale,
        })

    # Validate multi-hop judgment records against release rows and messages
    validate_multi_hop_judgment(judgment_records, rows, msgs)

    # Stage all release artifacts in a temporary directory
    with tempfile.TemporaryDirectory() as staging_dir_str:
        staging_dir = Path(staging_dir_str)

        jsonl_staging = staging_dir / "golden_1039.jsonl"
        lines = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in rows]
        content = "\n".join(lines) + "\n"
        jsonl_staging.write_text(content, encoding="utf-8")
        gold_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

        comp_staging = staging_dir / "components@R.json"
        comp_staging.write_text(json.dumps(components_artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        assignment_artifact["gold_sha256"] = gold_sha256
        assign_staging = staging_dir / "assignment@R.json"
        assign_staging.write_text(json.dumps(assignment_artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        audit_staging = staging_dir / "anti_leak_audit.json"
        audit_staging.write_text(json.dumps(anti_leak_audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        judgment_staging = staging_dir / "multi_hop_judgment.jsonl"
        j_lines = [json.dumps(rec, sort_keys=True, separators=(",", ":")) for rec in judgment_records]
        j_content = "\n".join(j_lines) + "\n"
        judgment_staging.write_text(j_content, encoding="utf-8")
        judgment_sha256 = hashlib.sha256(j_content.encode("utf-8")).hexdigest()

        strata_counts: dict[str, dict[str, int]] = {}
        for r in rows:
            s = r["stratum"]
            split = r["split"]
            if s not in strata_counts:
                strata_counts[s] = {"total": 0, "dev": 0, "test": 0}
            strata_counts[s]["total"] += 1
            strata_counts[s][split] += 1

        manifest = {
            "artifact_type": "golden_dataset_release",
            "release_stage": "candidate",
            "phase2_accepted": False,
            "row_count": len(rows),
            "sha256": gold_sha256,
            "splits": {
                "dev": sum(1 for r in rows if r["split"] == "dev"),
                "test": sum(1 for r in rows if r["split"] == "test"),
            },
            "anti_leak_gate": anti_leak_summary,
            "provenance_ref": PROVENANCE_REF,
            "strata": strata_counts,
            "components_ref": "components@R.json",
            "assignment_ref": "assignment@R.json",
            "anti_leak_audit_ref": "anti_leak_audit.json",
            "multi_hop_judgment_ref": "multi_hop_judgment.jsonl",
            "multi_hop_judgment_sha256": judgment_sha256,
            "entailment_authoring": "antigravity_direct_read",
        }

        manifest_staging = staging_dir / "golden_manifest.json"
        manifest_staging.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        # Atomic publication: publish immutable versioned candidate release and swap CURRENT pointer
        artifact_names = [
            "golden_1039.jsonl",
            "components@R.json",
            "assignment@R.json",
            "anti_leak_audit.json",
            "multi_hop_judgment.jsonl",
            "golden_manifest.json",
        ]
        manifest_sha256 = hashlib.sha256((staging_dir / "golden_manifest.json").read_bytes()).hexdigest()
        _release_id, release_dir = atomic_publish_versioned_release(
            staging_dir=staging_dir,
            output_dir=output_dir,
            artifact_names=artifact_names,
            gold_sha256=gold_sha256,
            manifest_sha256=manifest_sha256,
            row_count=len(rows),
            release_stage="candidate",
            phase2_accepted=False,
            candidate_release_id=None,
        )

    return release_dir / "golden_1039.jsonl", release_dir / "golden_manifest.json"


if __name__ == "__main__":
    jpath, mpath = generate_and_freeze_golden_release()
    print(f"Golden dataset release frozen: {jpath} and {mpath}")
