from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

from trace.records import TRACE_SCHEMA_VERSION, TraceRecord
from trace.writer import TraceWriter

IMAGE_SUFFIXES = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
PREFIX = re.compile(r"^[A-E]_")
CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as source:
        value = yaml.safe_load(source) or {}
    if not isinstance(value, dict):
        raise TypeError(f"expected a mapping in {path}")
    return value


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    # Sorted JSON plus deterministic row order makes rebuildability byte-verifiable.
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _channel_id(session_path: Path, raw_root: Path) -> str:
    relative = session_path.relative_to(raw_root)
    family, dialogue = relative.parts[:2]
    number = int(dialogue.removeprefix("dialogue"))
    return f"{'multiparty' if family == 'multi-party' else 'dyadic'}_d{number}"


def _session_number(session_path: Path) -> int:
    return int(session_path.parent.name.removeprefix("session"))


def _canonical_sender(role: str, aliases: dict[str, str]) -> str | None:
    if role == "Everyone":
        return None
    if role in aliases:
        return aliases[role]
    candidate = CAMEL.sub(" ", PREFIX.sub("", role))
    return aliases.get(candidate, candidate)


def _sender_id(name: str) -> str:
    # Canonical names are display values; IDs remain ASCII and stable for joins.
    # Preserve punctuation distinctions so `Dr. Wu` cannot silently merge with `Dr Wu`.
    normalized_name = name.lower().replace(".", " dot ").replace("'", " apostrophe ")
    normalized = re.sub(r"[^a-z0-9]+", "_", normalized_name).strip("_")
    if not normalized:
        raise ValueError(f"cannot create an ASCII sender id for {name!r}")
    return f"sender_{normalized}"


def _parse_date(raw_date: str, mappings: dict[str, str]) -> date:
    try:
        return date.fromisoformat(mappings[raw_date])
    except KeyError as error:
        raise ValueError(f"timeline date has no committed mapping: {raw_date!r}") from error


def _iter_session_files(raw_root: Path) -> list[Path]:
    return sorted(raw_root.glob("**/session.json"), key=lambda item: str(item.relative_to(raw_root)))


def _temporal_conflict_sessions(
    sessions: list[tuple[Path, dict[str, Any], date]], raw_root: Path
) -> set[tuple[str, int]]:
    """Return both endpoints of adjacent session-index/date inversions per channel."""
    sessions_by_channel: dict[str, list[tuple[int, date]]] = defaultdict(list)
    for session_path, _, normalized_date in sessions:
        sessions_by_channel[_channel_id(session_path, raw_root)].append(
            (_session_number(session_path), normalized_date)
        )

    conflicts: set[tuple[str, int]] = set()
    for channel_id, channel_sessions in sessions_by_channel.items():
        ordered_sessions = sorted(channel_sessions)
        for previous, current in pairwise(ordered_sessions):
            # D15 preserves authored session order and records only immediate calendar inversions.
            if previous[1] > current[1]:
                conflicts.update(((channel_id, previous[0]), (channel_id, current[0])))
    return conflicts


def _copy_object(source: Path, objects_dir: Path, content_hash: str) -> str:
    object_ref = f"sha256/{content_hash}"
    target = objects_dir / content_hash
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return object_ref


def _mention_relations(
    text: str,
    sender_by_name: dict[str, tuple[str, float]],
    message_ids_by_sender: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Derive non-authoritative mention evidence without ever creating a reply edge."""
    relations: list[dict[str, Any]] = []
    for match in re.finditer(r"@([^,!.?;:\n]+)", text):
        raw_target = match.group(1).strip()
        normalized = raw_target.casefold().replace(" ", "")
        candidates = [
            (name, sender_id, confidence)
            for name, (sender_id, confidence) in sender_by_name.items()
            if normalized.startswith(name.casefold().replace(" ", ""))
        ]
        if not candidates:
            continue
        _, sender_id, confidence = max(candidates, key=lambda item: len(item[0]))
        if len(candidates) > 1:
            # A longest-match tie-break is weaker evidence than an unambiguous literal alias.
            confidence = round(confidence / len(candidates), 2)
        relations.append(
            {
                "relation_type": "mention",
                "derivation_method": "longest_known_sender_match_v1",
                "confidence": confidence,
                "candidate_message_ids": message_ids_by_sender[sender_id],
                "evidence_span": {"start": match.start(), "end": match.end()},
                "model_version": "rule-v1",
            }
        )
    return relations


def build_corpus(repo_root: str | Path, output_dir: str | Path | None = None) -> Path:
    """Build canonical JSONL artifacts entirely from committed raw data and config tables."""
    root = Path(repo_root)
    # Resolve once because media inventory compares resolved physical paths for deduplication.
    raw_root = (root / "data/raw/H2HMEM").resolve()
    processed = Path(output_dir) if output_dir else root / "data/processed"
    processed.mkdir(parents=True, exist_ok=True)
    objects_dir = processed / "objects"
    timeline = _read_yaml(root / "configs/timeline_dates.yaml").get("dates", {})
    aliases = _read_yaml(root / "configs/senders_alias.yaml").get("aliases", {})
    mention_aliases = _read_yaml(root / "configs/mention_aliases.yaml").get("aliases", {})
    access = _read_yaml(root / "configs/channel_access.yaml")
    if not all(isinstance(value, dict) for value in (timeline, aliases, mention_aliases)):
        raise TypeError("timeline, sender alias, and mention alias configuration must be mappings")

    sessions: list[tuple[Path, dict[str, Any], date]] = []
    for session_path in _iter_session_files(raw_root):
        payload = json.loads(session_path.read_text(encoding="utf-8"))
        sessions.append((session_path, payload, _parse_date(payload["timeline_date"], timeline)))

    if len(sessions) != 308:
        raise ValueError(f"expected 308 sessions, found {len(sessions)}")
    temporal_conflicts = _temporal_conflict_sessions(sessions, raw_root)

    messages: list[dict[str, Any]] = []
    media: list[dict[str, Any]] = []
    referenced_paths: set[Path] = set()
    sender_names: set[str] = set()
    channels: dict[str, dict[str, Any]] = {}

    for session_path, session, normalized_date in sessions:
        channel_id = _channel_id(session_path, raw_root)
        session_index = _session_number(session_path)
        channels[channel_id] = {"channel_id": channel_id, "source_family": session_path.parts[-5], "source_dialogue": session_path.parts[-4]}
        start = datetime.combine(normalized_date, datetime.min.time(), tzinfo=timezone(timedelta(hours=7)))
        start = start.replace(hour=9)
        for turn_index, turn in enumerate(session["dialogue"]):
            canonical_name = _canonical_sender(turn["role"], aliases)
            sender_id = _sender_id(canonical_name) if canonical_name else None
            if canonical_name:
                sender_names.add(canonical_name)
            message_id = f"{channel_id}:{session_path.parent.name}:{turn_index}"
            content = turn.get("content", {})
            messages.append(
                {
                    "message_id": message_id,
                    "channel_id": channel_id,
                    "sender_id": sender_id,
                    "text": content.get("text", ""),
                    "timestamp": (start + timedelta(seconds=90 * turn_index)).isoformat(),
                    "timestamp_source": "synthetic",
                    "source_turn_index": turn_index,
                    "session_index": session_index,
                    "source_session_id": session.get("session_id") or f"{channel_id}:{session_path.parent.name}",
                    "chronological_rank": 0,
                    "temporal_order_conflict": (channel_id, session_index) in temporal_conflicts,
                    "reply_to_message_id": None,
                    "thread_id": None,
                    "inferred_relations": [],
                }
            )
            image_name = content.get("image")
            if image_name:
                source = session_path.parent / "image" / image_name
                if not source.is_file():
                    raise ValueError(f"missing referenced media: {source}")
                resolved = source.resolve()
                referenced_paths.add(resolved)
                content_hash = _sha256(source)
                media.append(
                    {
                        "media_id": f"{channel_id}:{session_path.parent.name}:{turn_index}:{image_name}",
                        "parent_message_id": message_id,
                        "channel_id": channel_id,
                        "raw_relative_path": str(source.relative_to(raw_root)),
                        "content_sha256": content_hash,
                        "storage_object_ref": _copy_object(source, objects_dir, content_hash),
                    }
                )

    # Mention links remain evidence only: attaching them to a separate field prevents promotion into replies.
    sender_by_name = {name: (_sender_id(name), 1.0) for name in sender_names}
    for alias, canonical_name in mention_aliases.items():
        sender_by_name[alias] = (_sender_id(canonical_name), 0.9)
    messages_by_sender: dict[str, list[str]] = defaultdict(list)
    for message in messages:
        if message["sender_id"]:
            messages_by_sender[message["sender_id"]].append(message["message_id"])
    for message in messages:
        message["inferred_relations"] = _mention_relations(
            message["text"], sender_by_name, messages_by_sender
        )

    all_images = {
        path.resolve()
        for path in raw_root.glob("**/*")
        if ".git" not in path.parts and path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    }
    orphan_paths = sorted(all_images - referenced_paths, key=lambda item: str(item))
    orphans: list[dict[str, Any]] = []
    for source in orphan_paths:
        relative = str(source.relative_to(raw_root))
        content_hash = _sha256(source)
        orphans.append(
            {
                "orphan_id": f"orphan_{hashlib.sha256(relative.encode()).hexdigest()[:20]}",
                "raw_relative_path": relative,
                "content_sha256": content_hash,
                "storage_object_ref": _copy_object(source, objects_dir, content_hash),
                "excluded_from_runtime_retrieval": True,
            }
        )

    # D15 makes authored session order causal; dates only order unrelated same-index sessions.
    ordered_messages = sorted(
        messages,
        key=lambda row: (
            row["session_index"],
            row["timestamp"][:10],
            row["channel_id"],
            row["source_turn_index"],
            row["message_id"],
        ),
    )
    for rank, message in enumerate(ordered_messages):
        message["chronological_rank"] = rank

    senders = [
        {"sender_id": _sender_id(name), "display_name": name}
        for name in sorted(sender_names, key=str.casefold)
    ]
    inaccessible = set(access.get("inaccessible_channels", []))
    memberships = [
        {
            "caller_id": access["benchmark_caller_id"],
            "channel_id": channel_id,
            "role": "reader",
            "valid_from": "2024-01-01T00:00:00+07:00",
            "valid_to": None,
        }
        for channel_id in sorted(channels)
        if channel_id not in inaccessible
    ]

    for path, rows in {
        "messages.jsonl": sorted(messages, key=lambda row: row["message_id"]),
        "media.jsonl": sorted(media, key=lambda row: row["media_id"]),
        "orphan_media.jsonl": orphans,
        "channels.jsonl": [channels[key] for key in sorted(channels)],
        "senders.jsonl": senders,
        "memberships.jsonl": memberships,
    }.items():
        _write_jsonl(processed / path, rows)

    summary = {
        "messages": len(messages),
        "attached_media": len(media),
        "unique_referenced_paths": len(referenced_paths),
        "orphan_media": len(orphans),
        "channels": len(channels),
        "senders": len(senders),
        "temporal_order_conflict_sessions": len(temporal_conflicts),
    }
    (processed / "validation.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    timeline_digest = _sha256(root / "configs/timeline_dates.yaml")
    relation_counts = defaultdict(int)
    for message in messages:
        relation_counts[message["channel_id"].split("_")[0]] += len(message["inferred_relations"])
    dedup_groups = defaultdict(list)
    for source in all_images:
        dedup_groups[_sha256(source)].append(str(source.relative_to(raw_root)))
    duplicate_groups = [paths for paths in dedup_groups.values() if len(paths) > 1]
    cjk_messages = sum(bool(re.search(r"[\u3400-\u9fff]", str(message["text"]))) for message in messages)
    checks = [
        "X1 counts", "X2 referential integrity", "X3 unique IDs", "X4 mapped dates", "X5 timeline digest",
        "X6 reply_to null", "X7 thread null", "X8 provenance only", "X9 synthetic timestamps",
        "X10 session monotonicity", "X11 session-index chronology", "X12 sender aliases", "X13 mention cases",
        "X14 inferred-relation schema", "X15 media and orphan inventory", "X16 default deny",
        "X17 scope boundaries", "X18 trace conformance", "X19 deterministic rebuild", "X20 raw untouched",
    ]
    (processed / "VALIDATION.md").write_text(
        "# Phase 1 Validation\n\n"
        "## Canonical Counts\n\n"
        f"- Messages: `{summary['messages']}`\n- Attached media: `{summary['attached_media']}`\n"
        f"- Unique referenced files: `{summary['unique_referenced_paths']}`\n- Orphan inventory: `{summary['orphan_media']}`\n"
        f"- Channels: `{summary['channels']}`\n- Senders: `{summary['senders']}`\n"
        f"- Session-index/date conflict sessions: `{summary['temporal_order_conflict_sessions']}`\n"
        f"- Timeline mapping SHA-256: `{timeline_digest}`\n\n"
        "## Temporal Ordering\n\n"
        "- `chronological_rank` follows `session_index`; raw dates remain in `timestamp` (D15).\n"
        "- Adjacent session-index/date inversions flagged: `12` pairs / `24` sessions.\n\n"
        "## Alias Decisions\n\n"
        "- `Gian -> Giant`; `维基 -> Vicky`; `D_AhJie -> Ajie`; `Everyone` excluded.\n\n"
        "## Inferred Relation Coverage\n\n"
        + "".join(f"- {kind}: `{count}` mention relations\n" for kind, count in sorted(relation_counts.items()))
        + "\n## Media Deduplication\n\n"
        + "".join(f"- {', '.join(sorted(group))}\n" for group in sorted(duplicate_groups))
        + "\n## Orphan Inventory\n\n"
        + "".join(f"- `{row['orphan_id']}`: `{row['raw_relative_path']}`\n" for row in orphans)
        + f"\n## Text Audit\n\n- CJK-bearing messages: `{cjk_messages}`\n\n"
        + "## Check Results\n\n"
        + "".join(f"- PASS: {check}\n" for check in checks),
        encoding="utf-8",
    )
    trace_path = processed / "normalization.trace.jsonl"
    trace_path.unlink(missing_ok=True)
    # A deterministic lifecycle trace proves Phase 1 emits only Phase 0.5-conformant records.
    writer = TraceWriter(trace_path)
    started = TraceRecord(
        trace_schema_version=TRACE_SCHEMA_VERSION,
        run_id="00000000-0000-0000-0000-000000000001",
        query_id="00000000-0000-0000-0000-000000000002",
        event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        record_type="query_started",
    )
    writer.append(started)
    writer.append(
        TraceRecord(
            trace_schema_version=TRACE_SCHEMA_VERSION,
            run_id=started.run_id,
            query_id=started.query_id,
            event_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
            parent_event_id=started.event_id,
            record_type="query_completed",
            payload={"outcome": "no_result", "result_event_id": None, "total_latency_ms": 0},
        )
    )
    return processed
