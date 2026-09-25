from __future__ import annotations

import hashlib
import json
import os
import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from trace.metrics import _percentile

SECRET = re.compile(r"(?:sk-|or-)[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]+", re.IGNORECASE)


def redact(value: Any) -> Any:
    """Redact provider errors before persistence because artifacts are committed evidence."""
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return SECRET.sub("[REDACTED]", value) if isinstance(value, str) else value


def write_capability_artifact(path: str | Path, payload: dict[str, Any]) -> str:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(redact(payload), indent=2, sort_keys=True) + "\n").encode()
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, destination)
    return hashlib.sha256(encoded).hexdigest()


def preflight_cost(probes: list[tuple[int, Decimal]], cap_usd: Decimal = Decimal("0.50")) -> Decimal:
    """Reject before networking when declared worst-case token spend exceeds the hard cap."""
    estimate = sum((Decimal(tokens) * price / Decimal(1_000_000) for tokens, price in probes), Decimal(0))
    if estimate > cap_usd:
        raise ValueError(f"capability probe estimate {estimate} exceeds cap {cap_usd}")
    return estimate


def catalogue_requires_dated_id(model_id: str, catalogue_ids: list[str]) -> bool:
    """Fail closed if catalogue matching is ambiguous; aliases cannot certify release pinning."""
    base = re.sub(r"-\d{8}$", "", model_id)
    matches = [item for item in catalogue_ids if re.sub(r"-\d{8}$", "", item) == base]
    if not matches:
        raise ValueError("model catalogue has no unambiguous configured model entry")
    return any(re.fullmatch(re.escape(base) + r"-\d{8}", item) for item in matches)


def fetch_catalogue(api_key: str) -> list[str]:
    """Live network entrypoint, deliberately called only by explicit capability commands/tests."""
    request = Request("https://openrouter.ai/api/v1/models", headers={"Authorization": f"Bearer {api_key}"})
    with urlopen(request, timeout=30) as response:
        data = json.load(response)
    return [item["id"] for item in data.get("data", []) if isinstance(item.get("id"), str)]


def resolve_endpoint_pricing(
    api_key: str, model_id: str, endpoint: str
) -> dict[str, Decimal]:
    """Fetch live pricing from OpenRouter endpoint metadata; fail closed if endpoint tag is missing."""
    request = Request(
        f"https://openrouter.ai/api/v1/models/{model_id}/endpoints",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urlopen(request, timeout=30) as response:
        data = json.load(response).get("data", {})
        endpoints = data.get("endpoints", [])
        matches = [ep for ep in endpoints if ep.get("tag") == endpoint]
        if len(matches) != 1:
            raise ValueError(
                f"no unambiguous endpoint pricing metadata for endpoint tag '{endpoint}' on model '{model_id}': found {len(matches)} matches"
            )
        p = matches[0].get("pricing", {})
        prompt_per_m = (Decimal(str(p.get("prompt", "0"))) * Decimal(1_000_000)).quantize(Decimal("0.01"))
        compl_per_m = (Decimal(str(p.get("completion", "0"))) * Decimal(1_000_000)).quantize(Decimal("0.01"))
        return {"input_per_million": prompt_per_m, "output_per_million": compl_per_m}


def _chat_completion(api_key: str, body: dict[str, Any]) -> dict[str, Any]:
    """Make one explicitly requested OpenRouter probe; ordinary pytest never calls this function."""
    request = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=90) as response:
        return json.load(response)


def run_capability_probes(
    *,
    api_key: str,
    model_id: str,
    endpoints: list[str],
    max_input_tokens: int,
    input_price_per_million: Decimal | None = None,
    output_price_per_million: Decimal | None = None,
    artifact_path: str | Path,
    cap_usd: Decimal = Decimal("0.50"),
    requires_dated_model_id: bool = False,
) -> dict[str, Any]:
    """Run gated vision/schema/context probes and persist redacted, reproducible evidence."""
    if not endpoints:
        raise ValueError("capability probes require at least one exact endpoint")
    catalogue_ids = fetch_catalogue(api_key)
    catalogue_requires_date = catalogue_requires_dated_id(model_id, catalogue_ids)
    if catalogue_requires_date != requires_dated_model_id:
        fail_artifact = {
            "capability_schema_version": "1.0.0",
            "model_id": model_id,
            "catalogue_check": {
                "model_exposes_dated_id": catalogue_requires_date,
                "requires_dated_model_id": requires_dated_model_id,
                "catalogue_model_count": len(catalogue_ids),
                "status": "fail",
            },
            "status": "invalid_for_release",
        }
        write_capability_artifact(artifact_path, fail_artifact)
        raise ValueError("configured requires_dated_model_id disagrees with the model catalogue")
    resolved_pricing: dict[str, dict[str, str]] = {}
    for ep in endpoints:
        if input_price_per_million is not None and output_price_per_million is not None:
            resolved_pricing[ep] = {
                "input_per_million": str(input_price_per_million),
                "output_per_million": str(output_price_per_million),
            }
        else:
            fetched = resolve_endpoint_pricing(api_key, model_id, ep)
            resolved_pricing[ep] = {
                "input_per_million": str(fetched["input_per_million"]),
                "output_per_million": str(fetched["output_per_million"]),
            }

    max_in = max(Decimal(v["input_per_million"]) for v in resolved_pricing.values())
    max_out = max(Decimal(v["output_per_million"]) for v in resolved_pricing.values())
    # Reserve the full endpoint-by-probe cross-product before dispatching any network request.
    input_tokens = (max_input_tokens + 128) + 256 + 160
    output_tokens = 32 * 3
    preflight_cost(
        [(input_tokens * len(endpoints), max_in), (output_tokens * len(endpoints), max_out)], cap_usd
    )
    probes: list[dict[str, Any]] = []
    for endpoint in endpoints:
        provider = {"order": [endpoint], "allow_fallbacks": False, "require_parameters": True}
        cases = {
            "vision": {"messages": [{"role": "user", "content": [{"type": "text", "text": "Return JSON."}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="}}]}]},
            "structured_output": {"messages": [{"role": "user", "content": "Return the requested object."}], "response_format": {"type": "json_schema", "json_schema": {"name": "probe", "strict": True, "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}}}},
            "context_limit": {"messages": [{"role": "user", "content": "x " * max_input_tokens}]},
        }
        for name, extra in cases.items():
            started = time.perf_counter()
            try:
                response = _chat_completion(api_key, {"model": model_id, "provider": provider, "max_tokens": 32, **extra})
                probes.append({
                    "name": name,
                    "endpoint": endpoint,
                    "status": "pass",
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "provider": response.get("provider"),
                    "is_capacity_rejection": False,
                })
            except (OSError, ValueError) as error:  # Provider failures are evidence, not a reason to leak raw payloads.
                err_msg = str(error)
                is_cap = any(sub in err_msg.lower() for sub in ["429", "capacity", "overloaded", "rate limit", "busy"])
                probes.append({
                    "name": name,
                    "endpoint": endpoint,
                    "status": "fail",
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "error_class": type(error).__name__,
                    "error": err_msg,
                    "is_capacity_rejection": is_cap,
                })

    tier_summary: dict[str, Any] = {}
    for endpoint in endpoints:
        ep_probes = [p for p in probes if p["endpoint"] == endpoint]
        sample_count = len(ep_probes)
        passing_latencies = sorted(p["latency_ms"] for p in ep_probes if p.get("status") == "pass")
        rejected_count = sum(bool(p.get("is_capacity_rejection")) for p in ep_probes)
        tier_summary[endpoint] = {
            "sample_count": sample_count,
            "latency_ms_p50": _percentile(passing_latencies, 0.5)["value"],
            "latency_ms_distribution": passing_latencies,
            "capacity_rejection_rate": {
                "value": (rejected_count / sample_count) if sample_count else None,
                "sample_size": sample_count,
                "rejected_count": rejected_count,
            },
        }

    # S1: Select endpoint based on measurement (D-P0-4: test flex first, fall back to standard)
    valid_flex = [
        ep for ep in endpoints
        if "flex" in ep and tier_summary[ep]["capacity_rejection_rate"]["value"] == 0.0
    ]
    if valid_flex:
        selected_endpoint = min(
            valid_flex,
            key=lambda ep: tier_summary[ep]["latency_ms_p50"] if tier_summary[ep]["latency_ms_p50"] is not None else 999999,
        )
    else:
        valid_standard = [
            ep for ep in endpoints
            if "flex" not in ep and tier_summary[ep]["capacity_rejection_rate"]["value"] == 0.0
        ]
        selected_endpoint = min(
            valid_standard or endpoints,
            key=lambda ep: tier_summary[ep]["latency_ms_p50"] if tier_summary[ep]["latency_ms_p50"] is not None else 999999,
        )

    selected_pricing = resolved_pricing[selected_endpoint]
    # B2: If flex is selected, provide 2.0x headroom to accommodate standard tier fallback
    headroom_multiplier = Decimal("2.0") if "flex" in selected_endpoint else Decimal("1.0")
    proposed_in = (Decimal(selected_pricing["input_per_million"]) * headroom_multiplier).quantize(Decimal("0.01"))
    proposed_out = (Decimal(selected_pricing["output_per_million"]) * headroom_multiplier).quantize(Decimal("0.01"))

    proposal = {
        "pricing_floor": {
            "input_per_million": selected_pricing["input_per_million"],
            "output_per_million": selected_pricing["output_per_million"],
        },
        "headroom_multiplier": float(headroom_multiplier),
        "input_per_million": str(proposed_in),
        "output_per_million": str(proposed_out),
        "derived_from_endpoint": selected_endpoint,
    }
    artifact = {
        "capability_schema_version": "1.0.0",
        "model_id": model_id,
        "catalogue_check": {
            "model_exposes_dated_id": catalogue_requires_date,
            "requires_dated_model_id": requires_dated_model_id,
            "catalogue_model_count": len(catalogue_ids),
            "status": "pass",
        },
        "resolved_pricing": resolved_pricing,
        "probes": probes,
        "tier_summary": tier_summary,
        "max_price_proposal": proposal,
    }
    artifact["sha256"] = write_capability_artifact(artifact_path, artifact)
    return artifact
