"""Experiment: does silence-gap segmentation help retrieval?

Standalone. Reads data/processed/ and data/raw/ read-only; imports nothing from
src and writes nothing outside experiments/segment_gap/out/. No LLM anywhere.
Retrieval is single-shot: no jump, no multi-round, no agent.

Run:  python experiments/segment_gap/run.py
"""

from __future__ import annotations

import collections
import glob
import itertools
import json
import os
import re
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROC = os.path.join(ROOT, "data", "processed")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")

GAPS_MIN = [5, 15, 30, 60]
KS = [5, 10, 20]
# A segment above this is "too long to be one retrieval unit" for reporting purposes.
OVERSIZED_MESSAGES = 40


# --------------------------------------------------------------------------- load


def load_corpus():
    with open(os.path.join(PROC, "messages.jsonl"), encoding="utf-8") as file:
        msgs = [json.loads(line) for line in file]
    with open(os.path.join(PROC, "media.jsonl"), encoding="utf-8") as file:
        media = [json.loads(line) for line in file]
    with open(os.path.join(PROC, "senders.jsonl"), encoding="utf-8") as file:
        senders = {sender["sender_id"]: sender for sender in map(json.loads, file)}
    media_by_msg = collections.defaultdict(list)
    for m in media:
        media_by_msg[m["parent_message_id"]].append(m)
    msgs.sort(key=lambda m: (m["channel_id"], m["chronological_rank"]))
    SENDER_OF.update({m["message_id"]: m["sender_id"] for m in msgs})
    return msgs, media, media_by_msg, senders


def session_of(message_id: str) -> str:
    # message_id is "<channel>:<session>:<turn>"; the session is the gold unit in questions.json.
    return message_id.split(":")[1]


def load_questions():
    """questions.json gives gold at SESSION granularity only - no message or media ids."""
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "raw", "H2HMEM", "*", "*", "scenes", "*", "questions.json"))):
        parts = path.split(os.sep)
        family = "multiparty" if parts[-5] == "multi-party" else "dyadic"
        dnum = re.sub(r"\D", "", parts[-4])
        channel = f"{family}_d{dnum}"
        with open(path, encoding="utf-8") as file:
            blob = json.load(file)
        # Every question whose file is marked converted_from_cross_session carries
        # gold "session0" regardless of content - the conversion flattened a
        # multi-session answer onto a placeholder. Kept, but flagged, because
        # scoring against it measures the defect rather than retrieval.
        converted = blob["metadata"].get("source") == "converted_from_cross_session"
        for q in blob.get("questions", []):
            sess = q.get("answer_session") or []
            if len(sess) != 1:
                continue
            out.append(
                {
                    "text": q["question"]["text"],
                    "has_query_image": bool(q["question"].get("image")),
                    "sub_type": q["question_type"]["sub_type"],
                    "channel_id": channel,
                    "gold_session": sess[0],
                    "gold_is_placeholder": converted,
                }
            )
    return out


# ---------------------------------------------------------------------- segmenting


def build_segments(msgs, media_by_msg, senders, gap_min):
    """Cut a channel's stream wherever the silence exceeds gap_min. Silence only -
    no cap on message count, which is the point of the experiment."""
    segs = []
    cur = []
    prev = None
    for m in msgs:
        if prev is not None:
            same_channel = m["channel_id"] == prev["channel_id"]
            delta = (
                datetime.fromisoformat(m["timestamp"]) - datetime.fromisoformat(prev["timestamp"])
            ).total_seconds() / 60.0
            if not same_channel or delta > gap_min:
                segs.append(_finish(cur, media_by_msg, senders))
                cur = []
        cur.append(m)
        prev = m
    if cur:
        segs.append(_finish(cur, media_by_msg, senders))
    return segs


SENDER_OF: dict[str, str] = {}


def _finish(msgs, media_by_msg, senders):
    lines, media_ids = [], []
    for m in msgs:
        name = senders.get(m["sender_id"], {}).get("display_name", m["sender_id"])
        attached = media_by_msg.get(m["message_id"], [])
        media_ids.extend(x["media_id"] for x in attached)
        body = (m["text"] or "").strip()
        if attached:
            body = (body + " " + " ".join("[ảnh]" for _ in attached)).strip()
        lines.append(f"{name}: {body}")
    span = (
        datetime.fromisoformat(msgs[-1]["timestamp"]) - datetime.fromisoformat(msgs[0]["timestamp"])
    ).total_seconds() / 60.0
    return {
        "channel_id": msgs[0]["channel_id"],
        "sessions": sorted({session_of(m["message_id"]) for m in msgs}),
        "message_ids": [m["message_id"] for m in msgs],
        "n_messages": len(msgs),
        "n_speakers": len({m["sender_id"] for m in msgs}),
        "duration_min": span,
        "media_ids": media_ids,
        # A mention relation carries no target_sender_id - only candidate_message_ids,
        # every message sent by the matched sender. The target is that sender, so
        # derive it from any candidate. Reading the absent field instead silently
        # yields None for every mention and reports 0% interleaving.
        "mention_targets": [
            SENDER_OF.get(r["candidate_message_ids"][0])
            for m in msgs
            for r in m.get("inferred_relations", [])
            if r.get("relation_type") == "mention" and r.get("candidate_message_ids")
        ],
        "text": "\n".join(lines),
    }


# ---------------------------------------------------------------------- retrieval


def tokenize(s: str):
    return re.findall(r"[a-z0-9]+", s.lower())


class BM25:
    """Plain BM25 over a fixed corpus. Identical scorer for both units, so the
    comparison isolates the unit and nothing else."""

    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.toks = [tokenize(d) for d in docs]
        self.len = [len(t) for t in self.toks]
        self.avg = sum(self.len) / max(len(self.len), 1)
        self.df = collections.Counter()
        self.tf = []
        for t in self.toks:
            c = collections.Counter(t)
            self.tf.append(c)
            self.df.update(c.keys())
        self.N = len(docs)
        self.post = collections.defaultdict(list)
        for i, c in enumerate(self.tf):
            for term, f in c.items():
                self.post[term].append((i, f))

    def top(self, query, k):
        import math

        scores = collections.defaultdict(float)
        for term in set(tokenize(query)):
            if term not in self.post:
                continue
            idf = math.log(1 + (self.N - self.df[term] + 0.5) / (self.df[term] + 0.5))
            for i, f in self.post[term]:
                dl = self.len[i]
                scores[i] += idf * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg))
        return sorted(scores, key=scores.get, reverse=True)[:k]


def eval_units(questions, unit_docs, unit_meta, ks):
    """Score both units the same way: project each retrieved unit to the set of
    sessions it covers, and ask whether the gold session is in that set.

    Two readings are reported because they disagree and the disagreement matters:
      raw@K       - top K units, however many sessions that happens to cover
      distinct@K  - scan down until K DISTINCT sessions are collected
    Segment retrieval returns K distinct sessions at raw@K almost by construction,
    so raw@K flatters it. distinct@K is the honest comparison.
    """
    by_channel = collections.defaultdict(list)
    for i, meta in enumerate(unit_meta):
        by_channel[meta["channel_id"]].append(i)

    index = {}
    for ch, idxs in by_channel.items():
        index[ch] = (BM25([unit_docs[i] for i in idxs]), idxs)

    raw = {k: 0 for k in ks}
    dist = {k: 0 for k in ks}
    total = 0
    fails = []
    for q in questions:
        if q["channel_id"] not in index:
            continue
        total += 1
        bm, idxs = index[q["channel_id"]]
        ranked = [idxs[j] for j in bm.top(q["text"], max(ks) * 6)]
        for k in ks:
            covered = set()
            for i in ranked[:k]:
                covered |= set(unit_meta[i]["sessions"])
            if q["gold_session"] in covered:
                raw[k] += 1
            covered, seen = set(), []
            for i in ranked:
                s = unit_meta[i]["sessions"]
                for x in s:
                    if x not in covered:
                        covered.add(x)
                        seen.append(x)
                if len(covered) >= k:
                    break
            if q["gold_session"] in covered:
                dist[k] += 1
            elif k == max(ks):
                fails.append(q)
    return raw, dist, total, fails


# ------------------------------------------------------------------------- report


def main():
    os.makedirs(OUT, exist_ok=True)
    msgs, media, media_by_msg, senders = load_corpus()
    questions = load_questions()
    report = []

    def say(s=""):
        print(s)
        report.append(s)

    say("# Segment-by-silence experiment")
    say()
    say(f"corpus: {len(msgs)} messages, {len(media)} media, {len({m['channel_id'] for m in msgs})} channels")
    say(f"questions with a single gold session: {len(questions)}")
    say()

    # ---- 0. the precondition the sweep depends on
    say("## 0. Gap distribution (does the sweep have anything to sweep?)")
    say()
    buckets = collections.Counter()
    within_session_gaps = 0
    by_ch = collections.defaultdict(list)
    for m in msgs:
        by_ch[m["channel_id"]].append(m)
    for ms in by_ch.values():
        for a, b in itertools.pairwise(ms):
            d = (datetime.fromisoformat(b["timestamp"]) - datetime.fromisoformat(a["timestamp"])).total_seconds() / 60
            buckets["<=5" if d <= 5 else ("5-60" if d <= 60 else ">60")] += 1
            if d > 5 and session_of(a["message_id"]) == session_of(b["message_id"]):
                within_session_gaps += 1
    say("| bucket | count |")
    say("| --- | --- |")
    for k in ["<=5", "5-60", ">60"]:
        note = "  <- entire sweep range" if k == "5-60" else ""
        say(f"| {k} min{note} | {buckets[k]} |")
    say()
    say(f"gaps >5 min that fall *inside* a session: **{within_session_gaps}**")
    say()

    # ---- 1-4. per-G structure
    say("## Structure per G")
    say()
    say(f"| G | segments | == sessions? | median msgs | p90 | max | median speakers | oversized (>{OVERSIZED_MESSAGES} msgs) | multi-recipient segs |")
    say("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    per_g = {}
    for g in GAPS_MIN:
        segs = build_segments(msgs, media_by_msg, senders, g)
        per_g[g] = segs
        sizes = sorted(s["n_messages"] for s in segs)
        spk = sorted(s["n_speakers"] for s in segs)
        p = lambda arr, q: arr[min(len(arr) - 1, int(len(arr) * q))]
        n_sessions = len({(m["channel_id"], session_of(m["message_id"])) for m in msgs})
        exact = all(len(s["sessions"]) == 1 for s in segs) and len(segs) == n_sessions
        over = sum(1 for s in segs if s["n_messages"] > OVERSIZED_MESSAGES)
        with_mentions = [s for s in segs if s["mention_targets"]]
        multi = sum(1 for s in with_mentions if len(set(filter(None, s["mention_targets"]))) > 1)
        say(
            f"| {g} | {len(segs)} | {'YES' if exact else 'no'} | {p(sizes,0.5)} | {p(sizes,0.9)} | {sizes[-1]} "
            f"| {p(spk,0.5)} | {over} | {multi}/{len(with_mentions)} ({100*multi/max(len(with_mentions),1):.0f}%) |"
        )
    say()

    say("### The average hides the only interesting population: split by family")
    say()
    segs = per_g[GAPS_MIN[0]]
    say("| family | segments | median msgs | max | median speakers | max | median hours | median distinct mention recipients |")
    say("| --- | --- | --- | --- | --- | --- | --- | --- |")
    import statistics as st
    for fam in ["dyadic", "multiparty"]:
        S = [s for s in segs if s["channel_id"].startswith(fam)]
        n = sorted(x["n_messages"] for x in S)
        sp = sorted(x["n_speakers"] for x in S)
        hr = sorted(x["duration_min"] / 60 for x in S)
        rec = [len(set(filter(None, x["mention_targets"]))) for x in S if x["mention_targets"]]
        say(
            f"| {fam} | {len(S)} | {st.median(n):.0f} | {n[-1]} | {st.median(sp):.0f} | {sp[-1]} "
            f"| {st.median(hr):.1f} | {st.median(rec):.0f} |" if rec else
            f"| {fam} | {len(S)} | {st.median(n):.0f} | {n[-1]} | {st.median(sp):.0f} | {sp[-1]} | {st.median(hr):.1f} | - |"
        )
    say()

    # ---- co-location, as far as session-level gold permits
    say("## Co-location, at the granularity the gold actually supports")
    say()
    media_sessions = {(m["channel_id"], session_of(m["parent_message_id"])) for m in media}
    crr = [q for q in questions if q["sub_type"] == "Cross-modal Related Retrieval"]
    with_media = sum(1 for q in crr if (q["channel_id"], q["gold_session"]) in media_sessions)
    say(f"Cross-modal Related Retrieval questions: {len(crr)}")
    say(f"  gold session contains at least one image: {with_media} ({100*with_media/max(len(crr),1):.1f}%)")
    say(f"  gold session contains no image at all:    {len(crr)-with_media}")
    say()

    # ---- retrieval
    say("## Retrieval: message unit vs segment unit, BM25 only")
    say()
    msg_docs, msg_meta = [], []
    for m in msgs:
        name = senders.get(m["sender_id"], {}).get("display_name", m["sender_id"])
        att = media_by_msg.get(m["message_id"], [])
        body = (m["text"] or "").strip()
        if att:
            body = (body + " " + " ".join("[ảnh]" for _ in att)).strip()
        msg_docs.append(f"{name}: {body}")
        msg_meta.append({"channel_id": m["channel_id"], "sessions": [session_of(m["message_id"])]})

    clean = [q for q in questions if not q["gold_is_placeholder"]]
    say(
        f"excluded {len(questions)-len(clean)} questions whose gold session is the "
        f"`converted_from_cross_session` placeholder; {len(clean)} remain"
    )
    say()

    rows = []
    base_raw, base_dist, total, base_fails = eval_units(clean, msg_docs, msg_meta, KS)
    rows.append(("baseline (message)", base_raw, base_dist))
    seg_fails = {}
    for g in GAPS_MIN:
        segs = per_g[g]
        r, d, _, f = eval_units(clean, [s["text"] for s in segs], segs, KS)
        rows.append((f"segment G={g}", r, d))
        seg_fails[g] = f

    say(f"scored on {total} questions; hit = gold session covered by the retrieved units")
    say()
    header = "| unit | " + " | ".join(f"raw@{k}" for k in KS) + " | " + " | ".join(f"distinct@{k}" for k in KS) + " |"
    say(header)
    say("| --- |" + " --- |" * (2 * len(KS)))
    for name, r, d in rows:
        say(
            f"| {name} | "
            + " | ".join(f"{100*r[k]/total:.1f}%" for k in KS)
            + " | "
            + " | ".join(f"{100*d[k]/total:.1f}%" for k in KS)
            + " |"
        )
    say()

    # ---- failures
    say("## Where segment and baseline disagree, and where both fail")
    say()
    bf = {(q["channel_id"], q["gold_session"], q["text"]) for q in base_fails}
    sf = {(q["channel_id"], q["gold_session"], q["text"]) for q in seg_fails[GAPS_MIN[2]]}
    say(f"- both miss at distinct@20: **{len(bf & sf)}**")
    say(f"- segment fixes (baseline misses, segment hits): **{len(bf - sf)}**")
    say(f"- segment breaks (baseline hits, segment misses): **{len(sf - bf)}**")
    say()
    say("### Failures shared by both units (the real retrieval ceiling)")
    say()
    shared = [q for q in seg_fails[GAPS_MIN[2]] if (q["channel_id"], q["gold_session"], q["text"]) in bf]
    by_type = collections.Counter(q["sub_type"] for q in shared)
    for k, v in by_type.most_common():
        say(f"- {k}: {v}")
    say()
    seen = set()
    for q in shared:
        if q["sub_type"] in seen:
            continue
        seen.add(q["sub_type"])
        say(f"- **[{q['sub_type']}]** `{q['channel_id']}/{q['gold_session']}` — {q['text'][:190]}")
    say()
    say("### Cases segment broke that the message unit got right")
    say()
    for q in [q for q in seg_fails[GAPS_MIN[2]] if (q["channel_id"], q["gold_session"], q["text"]) not in bf][:5]:
        say(f"- **[{q['sub_type']}]** `{q['channel_id']}/{q['gold_session']}` — {q['text'][:190]}")
    say()

    path = os.path.join(OUT, "report.md")
    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(report))
    print(f"\nwritten: {path}")


if __name__ == "__main__":
    main()
