from __future__ import annotations

import argparse
import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from vsf.trace.metrics import compute_metrics
from vsf.trace.records import TraceRecord
from vsf.trace.writer import TraceReader


def render_cli(records: list[TraceRecord], corpus: dict[str, Any] | None = None) -> str:
    """Render human-readable causal evidence chain per query for CLI."""
    corpus = corpus or {}
    grouped: dict[str, list[TraceRecord]] = defaultdict(list)
    for record in records:
        grouped[record.query_id].append(record)

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append(f"TRACE VIEWER: {len(records)} records across {len(grouped)} queries")
    lines.append("=" * 80)

    for query_id, q_records in grouped.items():
        started = next((r for r in q_records if r.record_type == "query_started"), None)
        completed = next((r for r in q_records if r.record_type == "query_completed"), None)
        outcome = completed.payload.get("outcome", "unknown") if completed else "in_progress"
        latency = completed.payload.get("total_latency_ms", "N/A") if completed else "N/A"
        start_time = started.payload.get("started_at", "N/A") if started else "N/A"

        lines.append(f"\nQuery [{query_id}] | Started: {start_time} | Outcome: {outcome} | Latency: {latency}ms")
        lines.append("-" * 80)

        for rec in q_records:
            t = rec.record_type
            p = rec.payload
            parent = f" (parent: {rec.parent_event_id[-6:]})" if rec.parent_event_id else ""

            if t == "query_started":
                lines.append(f"  [{rec.event_id[-6:]}] Query Started{parent}")
            elif t == "round":
                action = p.get("action", "unknown")
                if action == "jump":
                    outcome_jump = p.get("outcome")
                    if outcome_jump == "executed":
                        clues = p.get("used_clue_ids", [])
                        resolved_clues = [f"{c}:{corpus.get(c, c)}" if c in corpus else c for c in clues]
                        lines.append(
                            f"  [{rec.event_id[-6:]}] Round Action: JUMP (EXECUTED){parent}\n"
                            f"      Missing constraints: {p.get('missing_constraints_before_jump', [])}\n"
                            f"      Clues: {resolved_clues}\n"
                            f"      Destination anchors: {p.get('destination_anchor_ids', [])}\n"
                            f"      New anchors: {p.get('new_anchor_ids', [])}\n"
                            f"      Constraints gained: {p.get('constraints_gained', [])}"
                        )
                    else:
                        lines.append(
                            f"  [{rec.event_id[-6:]}] Round Action: JUMP (BLOCKED){parent}\n"
                            f"      Block reason: {p.get('block_reason')}\n"
                            f"      Details: {json.dumps({k: v for k, v in p.items() if k not in ('action', 'outcome', 'block_reason')})}"
                        )
                elif action == "expand_context":
                    lines.append(
                        f"  [{rec.event_id[-6:]}] Round Action: EXPAND_CONTEXT{parent}\n"
                        f"      Strategy: {p.get('strategy', 'default')}\n"
                        f"      Anchors: {p.get('anchor_ids', [])} -> New: {p.get('new_anchor_ids', [])}"
                    )
                elif action == "extract_clues":
                    clues = p.get("clue_ids", [])
                    resolved_clues = [f"{c}:{corpus.get(c, c)}" if c in corpus else c for c in clues]
                    lines.append(
                        f"  [{rec.event_id[-6:]}] Round Action: EXTRACT_CLUES{parent}\n"
                        f"      Extracted clues: {resolved_clues}"
                    )
                elif action == "resolve_bridge":
                    lines.append(
                        f"  [{rec.event_id[-6:]}] Round Action: RESOLVE_BRIDGE{parent}\n"
                        f"      Bridge details: {json.dumps({k: v for k, v in p.items() if k != 'action'})}"
                    )
                else:
                    lines.append(f"  [{rec.event_id[-6:]}] Round Action: {action.upper()}{parent}")
            elif t == "llm_call":
                lines.append(
                    f"  [{rec.event_id[-6:]}] LLM Call: {p.get('role', 'unknown')} | "
                    f"Model: {p.get('model_id', 'N/A')} | Cost: ${p.get('cost_usd', '0')} | "
                    f"Latency: {p.get('latency_ms', 'N/A')}ms{parent}"
                )
            elif t == "result":
                supporting = p.get("supporting_round_event_ids", [])
                lines.append(
                    f"  [{rec.event_id[-6:]}] Result Produced{parent}\n"
                    f"      Supporting rounds: {[s[-6:] for s in supporting]}\n"
                    f"      Output media: {p.get('media_id', p.get('media_ids', []))}"
                )
            elif t == "query_completed":
                lines.append(
                    f"  [{rec.event_id[-6:]}] Query Completed{parent} | "
                    f"Final result ref: {p.get('result_event_id', 'None')}"
                )

    # Add metrics summary if available
    try:
        metrics = compute_metrics(records)
        lines.append("\n" + "=" * 80)
        lines.append("SUMMARY METRICS")
        lines.append("-" * 80)
        for k, v in metrics.items():
            lines.append(f"  {k:30s}: {v}")
        lines.append("=" * 80)
    except (ValueError, KeyError, ZeroDivisionError, TypeError) as err:
        lines.append(f"\n[Warning: metrics calculation failed: {err}]")

    return "\n".join(lines)


def render_html(records: list[TraceRecord], corpus: dict[str, Any] | None = None) -> str:
    """Render static standalone HTML visualization of the trace evidence chain."""
    corpus = corpus or {}
    grouped: dict[str, list[TraceRecord]] = defaultdict(list)
    for record in records:
        grouped[record.query_id].append(record)

    try:
        metrics = compute_metrics(records)
    except (ValueError, KeyError, ZeroDivisionError, TypeError):
        metrics = {}

    html_parts: list[str] = [
        "<!DOCTYPE html>",
        "<html lang='en'>",
        "<head>",
        "<meta charset='utf-8'>",
        "<title>VSF Trace & Evidence Chain Viewer</title>",
        "<style>",
        "  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 20px; background: #f8f9fa; color: #333; }",
        "  h1, h2, h3 { color: #1a1a2e; }",
        "  .metrics-card { background: #fff; padding: 15px 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 25px; }",
        "  .metrics-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 15px; }",
        "  .metric-item { background: #f1f3f5; padding: 10px; border-radius: 6px; }",
        "  .metric-label { font-size: 0.85em; color: #666; text-transform: uppercase; }",
        "  .metric-value { font-size: 1.25em; font-weight: bold; color: #2b2d42; margin-top: 4px; }",
        "  .query-card { background: #fff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); margin-bottom: 20px; overflow: hidden; }",
        "  .query-header { padding: 12px 18px; background: #e9ecef; display: flex; justify-content: space-between; align-items: center; }",
        "  .query-header h3 { margin: 0; font-size: 1.1em; }",
        "  .badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 0.8em; font-weight: bold; }",
        "  .badge-answered { background: #d4edda; color: #155724; }",
        "  .badge-no_result { background: #f8d7da; color: #721c24; }",
        "  .badge-clarification { background: #fff3cd; color: #856404; }",
        "  .badge-executed { background: #cce5ff; color: #004085; }",
        "  .badge-blocked { background: #e2e3e5; color: #383d41; }",
        "  .timeline { padding: 15px 20px; list-style: none; margin: 0; }",
        "  .timeline-step { border-left: 3px solid #dee2e6; padding: 0 0 15px 18px; position: relative; }",
        "  .timeline-step:last-child { border-left: 3px solid transparent; }",
        "  .timeline-step::before { content: ''; width: 10px; height: 10px; background: #007bff; border-radius: 50%; position: absolute; left: -6.5px; top: 3px; }",
        "  .step-type { font-weight: bold; margin-bottom: 4px; }",
        "  .step-details { font-size: 0.9em; background: #fdfdfe; border: 1px solid #edf2f7; padding: 8px 12px; border-radius: 6px; }",
        "  pre { margin: 0; font-family: monospace; font-size: 0.85em; }",
        "</style>",
        "</head>",
        "<body>",
        "<h1>VSF Multimodal Retrieval - Trace Viewer</h1>",
    ]

    # Metrics overview card
    if metrics:
        html_parts.append("<div class='metrics-card'><h2>Summary Metrics</h2><div class='metrics-grid'>")
        for k, v in metrics.items():
            val_str = str(v.get("value") if isinstance(v, dict) and "value" in v else v)
            html_parts.append(
                f"<div class='metric-item'><div class='metric-label'>{html.escape(k.replace('_', ' '))}</div>"
                f"<div class='metric-value'>{html.escape(val_str)}</div></div>"
            )
        html_parts.append("</div></div>")

    # Queries
    for query_id, q_records in grouped.items():
        completed = next((r for r in q_records if r.record_type == "query_completed"), None)
        outcome = completed.payload.get("outcome", "in_progress") if completed else "in_progress"
        latency = completed.payload.get("total_latency_ms", "N/A") if completed else "N/A"
        badge_cls = f"badge-{outcome}" if outcome in ("answered", "no_result", "clarification") else "badge-blocked"

        html_parts.append("<div class='query-card'><div class='query-header'>")
        html_parts.append(f"<h3>Query: {html.escape(query_id)}</h3>")
        html_parts.append(f"<div><span class='badge {badge_cls}'>{outcome}</span> {latency}ms</div></div>")
        html_parts.append("<ul class='timeline'>")

        for rec in q_records:
            t = rec.record_type
            p = rec.payload
            html_parts.append(f"<li class='timeline-step'><div class='step-type'>{html.escape(t.upper())} <small style='color:#888;'>[{html.escape(rec.event_id)}]</small></div>")
            html_parts.append(f"<div class='step-details'><pre>{html.escape(json.dumps(p, indent=2))}</pre></div></li>")

        html_parts.append("</ul></div>")

    html_parts.extend(["</body>", "</html>"])
    return "\n".join(html_parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="View and inspect VSF trace JSONL files.")
    parser.add_argument("trace_path", help="Path to trace.jsonl")
    parser.add_argument("--html", help="Output path for HTML report")
    parser.add_argument("--corpus", help="Optional path to corpus JSON for clue resolution")
    args = parser.parse_args()

    corpus_data = None
    if args.corpus:
        with open(args.corpus, encoding="utf-8") as f:
            corpus_data = json.load(f)

    reader = TraceReader(args.trace_path)
    records = reader.read()

    cli_output = render_cli(records, corpus_data)
    print(cli_output)

    if args.html:
        html_output = render_html(records, corpus_data)
        Path(args.html).write_text(html_output, encoding="utf-8")
        print(f"\nHTML report saved to: {args.html}")


if __name__ == "__main__":
    main()
