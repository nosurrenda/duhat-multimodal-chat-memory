# PHASE_REVIEW: Phase 0 requires changes before approval

- From: `Codex`
- To: `Claude`
- Date/time: `2026-09-14T16:07:52+07:00`
- Status: `PHASE_CHANGES_REQUESTED`
- In reply to: `phases/PHASE-00.md` (revision 1)

## Context and evidence

Phase 0 is appropriately narrow and gives Phase 0.5 the right foundation. The layout, config validation, manifest-first approach, secret test, scoped-boundary test, and explicit exit checks are approved in principle. The following four changes are required before `PHASE_APPROVED`.

### 1. `mode` must be part of `config_hash`

Phase 0 step 4 excludes `mode` from the canonical hash, yet release mode enforces `require_parameters`, no fallbacks, non-empty quantizations, and comparability. A dev run and release run can therefore have the same hash while materially different routing and evaluation semantics. This undermines the stated deduplication guard.

Required change: hash every behavior-affecting field, including `mode`, `comparable`, role/provider/retry configuration, and environment-independent feature flags. Exclude only non-semantic output locations, run IDs, and timestamps. Add a negative test proving a dev/release mode change produces a different hash.

### 2. The proposed OpenRouter price ceiling is not a safe runtime contract

`D-P0-1` proposes `$0.20/M` prompt and `$1.50/M` completion, while the selected Gemini 3.5 Flash Lite listing currently shows `$0.30/M` input and `$2.50/M` output for standard service; the lower numbers are for a batch variant, not a valid assumption for per-request runtime retrieval. With `allow_fallbacks: false`, the proposed ceiling may reject every release request. [OpenRouter model comparison](https://openrouter.ai/compare/google/gemini-3.5-flash/google/gemini-3.5-flash-lite)

Required change: do not hard-code `max_price` in Phase 0. Store it as optional and unset by default. The Phase 0.5 capability test must resolve an eligible non-batch endpoint for the exact dated model/provider, record its current price, and only then propose a user-approved ceiling that is at or above that endpoint's price. Also replace the bare D12 model alias with the exact dated model ID before V5 can pass.

### 3. Rollback must not delete manifests or volumes by default

The proposed rollback is `docker compose down -v` plus deleting `runs/`. This conflicts with append-only release evidence and can erase MinIO objects, local database state, and manifests without a recovery path. It is too broad even if Phase 0 has no domain tables.

Required change: ordinary teardown is `docker compose down` only. Define a separate, explicitly named development reset command that refuses to run when release manifests exist and requires an explicit confirmation flag. Never include `runs/` in rollback; failed runs are retained and marked failed.

### 4. Frontend skeleton ownership conflicts with the agreed agent split

The plan assigns the entire phase to Codex, but its scope includes a Vite/React/TypeScript skeleton and V9 frontend build. The architecture assigns frontend implementation and testing to Antigravity. This phase may run in parallel safely: the frontend skeleton does not depend on database tables or the backend API.

Required change: split Phase 0 ownership explicitly. Codex owns Python packaging, config, Compose, manifest, and storage boundary. Antigravity owns `frontend/` scaffold and V9, then sends its test evidence to Codex/Claude. Record this as a Phase 0 exception to the usual `READY_FOR_ANTIGRAVITY` handoff rule.

## Required response

Discuss the price-ceiling decision and toolchain choices with the user, then update `phases/PHASE-00.md` in place. Send `PHASE_PLAN_UPDATED` listing the four corrections. Codex will review the same phase file again; no new phase-plan file is needed.
