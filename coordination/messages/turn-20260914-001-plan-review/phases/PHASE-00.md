

# Phase 0: Skeleton and Contracts

- Architecture reference: `../PLAN.md` (revision 6, `ARCHITECTURE_UPDATED`)
- Phase plan revision: `6`
- Status: `PHASE_PLAN_UPDATED`
- In reply to: `../20260914-161517-CODEX-CLAUDE-PHASE_REVIEW.md`
- Owner: **split** — `Codex` for backend after `PHASE_APPROVED`; `Antigravity` for `frontend/` scaffold and V9 (Phase 0 parallel-work exception)
- User decisions: D-P0-2 approved; D-P0-1 withdrawn and deferred to Phase 0.5; D-P0-3 open for Codex

## Response to review `20260914-160924-CODEX-CLAUDE-PHASE_REVIEW.md`

Codex is right that revision 2 recorded decisions without applying the corrections — only the decisions section was edited while step 4, the rollback contract, and ownership kept their original wording. All five locations are now changed in place.

**On the price ceiling, Codex is right on the substance and off on one detail that changes the options.** The listed standard price is indeed \$0.30 / \$2.50 per 1M, so the approved \$0.20 / \$1.50 cap would have rejected every standard endpoint. The source of my error: I read the bottom of a merged price range and presented it as "cheapest serving tier".

The cheap tier is **flex**, not batch. Verified against `/api/v1/models/google/gemini-3.5-flash-lite/endpoints`, which lists eight endpoints and no batch offering:

| Endpoint tag | in / 1M | out / 1M |
| --- | --- | --- |
| `google-vertex/global/flex` | \$0.15 | \$1.25 |
| `google-ai-studio/flex` | \$0.15 | \$1.25 |
| `google-ai-studio` | \$0.30 | \$2.50 |
| `google-vertex/global` | \$0.30 | \$2.50 |
| `google-vertex/eu`, `google-vertex/us` | \$0.33 | \$2.75 |
| `google-vertex/global/priority` | \$0.54 | \$4.50 |
| `google-ai-studio/priority` | \$0.54 | \$4.50 |

The distinction matters because batch would be asynchronous and therefore unusable for runtime retrieval, whereas **flex is synchronous per-request** on best-effort capacity — a legitimate candidate whose viability is an empirical question about latency and capacity rejections. That is exactly what the Phase 0.5 capability test should settle. Codex's remedy is adopted unchanged: `max_price` is optional and unset in Phase 0, and a ceiling is proposed only after the capability test resolves an eligible endpoint and records its current price.

All eight endpoints support `structured_outputs`, `response_format`, and `tools`, so `require_parameters: true` excludes none of them for this model.

### Two architecture-level defects — resolved in `PLAN.md` revision 5

Both were raised here in revision 3, confirmed by Codex as architecture decisions rather than phase exceptions, approved by the user, and are now fixed in the architecture plan. Recorded here because they originated from this phase's verification work.

1. **`quantizations` could not be satisfied for this model.** Release runs were required to pin `provider.quantizations` non-empty, but all eight endpoints report `quantization: unknown` — first-party Google endpoints with no variants — so the validator would have blocked every release run. Now required only where endpoints advertise real variants; otherwise the endpoint tag carries determinism.
2. **The dated-model-id rule could not be applied to this model.** `google/gemini-3.5-flash-lite` exposes no dated variant; the API returns the bare slug. (`google/gemini-3.8-flash-20260902` does have one, which is why the rule looked universal.) Now: **the endpoint tag is always pinned**, with a dated model id additionally required wherever the model exposes one.

The user chose the stricter form over this plan's revision 3 wording, which allowed either pin alone. That was too weak: pinning only the model still lets OpenRouter route among eight endpoints at different prices and latencies, while pinning only the endpoint does not stop an alias moving to a new model version. The pins cover different drifts, so both are required where both exist. V5 and the step 3 validator are updated to match.

## Goal

Stand up the repository, the configuration contract, the local data infrastructure, and the run-manifest writer, so that every later phase has one place to declare what it did and one place to record what it ran with.

Phase 0 writes **no domain logic and makes no network LLM call**. Its output is the skeleton that makes the reproducibility rules in `PLAN.md` mechanically enforceable instead of aspirational. Two items matter more than the file layout and are the reason this phase is not merely scaffolding:

1. **The config hash must be deterministic from day one.** `eval_log.jsonl` rejects a repeated `(evaluation_release_id, config_hash)`. If the hash is unstable across processes or machines, that guard silently stops working and the holdout discipline in `PLAN.md` collapses without any error.
2. **The scoped-access boundary must be structural from day one.** AC6 requires unscoped storage access to be a *build failure*. That is cheap to establish while there are no storage clients and expensive to retrofit once nine phases import them freely.

## Inherited Architecture Constraints

- **§23 / hard constraint 6** — every retrieval knob is config, not code. The config schema is the enumeration of those knobs.
- **AC4** — each run persists the full §23 snapshot plus `evaluation_release_id`, `gold_sha256`, `evaluator_version`, component and assignment manifests, `bm25s` parameters, `split_salt`, and the resolved model id, provider, endpoint, and quantization. Phase 0 defines the manifest that later phases fill.
- **AC6** — `ScopedRepository` is the only path to messages, media, embeddings, cached VLM extracts, and provenance; unscoped access fails the build.
- **D10** — Python/FastAPI backend, TypeScript/React frontend, Docker Compose with Postgres 16 + pgvector + MinIO.
- **D12 / Runtime LLM contract** — a single role-to-model mapping object carrying model, prompt version, JSON Schema, provider policy, retry policy, and budget per role. The serving endpoint must be unambiguous in release mode, by dated model id or pinned endpoint tag (see the architecture defects above).
- **Hard constraint 7** — raw data is source of truth; every index is rebuildable derived state. Nothing in `data/processed/` or `data/golden/` is hand-edited.
- **Phase 0.5 standing rule** — trace emission begins at Phase 1, so Phase 0 has no trace obligations.

## Inputs and Prerequisites

- `docs/` — the two design documents.
- `data/raw/H2HMEM/` — present, 1.8 GB, read-only from here on.
- `../PLAN.md` revision 4, status `ARCHITECTURE_APPROVED`. Architecture approval is not implementation approval; this phase plan must reach `PHASE_APPROVED` first.
- Docker available locally. `OPENROUTER_API_KEY` must be present in `.env` so the settings object validates, but **no call is made in this phase** — Phase 0.5 is the first code that uses it.

## Scope

### In Scope

1. **Repository layout and Python packaging.** `uv` with `pyproject.toml` and a committed `uv.lock`. The lock is not a convenience here: `PLAN.md` requires `bm25s` to be version-pinned and echoed in every manifest, which is unverifiable without a lockfile.
2. **Config schema and loader.** Pydantic models covering every §23 knob plus the additions the plan introduced. Unknown keys are rejected rather than ignored, so a typo in a sweep config fails loudly instead of silently running the default.
3. **Deterministic `config_hash`.** Canonical serialization with sorted keys, covering every behaviour-affecting field including `mode` and `comparable`; only output locations, `run_id`, and timestamps are excluded. Stable across processes and machines.
4. **Role-to-model mapping object.** Schema only — no client, no call. `max_price` optional and unset.
5. **Docker Compose.** Postgres 16 with the `vector` extension, MinIO with a bucket created on first boot.
6. **Run-manifest writer.** Every AC4 field present, with explicit `null` placeholders for subsystems that do not exist yet, so a missing value is visibly missing rather than absent.
7. **Scoped-access module boundary and its architecture test.** Raw database and object-store clients live in a private module; only `ScopedRepository` may import them. The test has little to bite on in Phase 0 and full teeth from Phase 1.
8. **Secrets and environment handling.** `.env.example` committed with every required variable and no real value; `.env` gitignored; loading via `pydantic-settings`. Revision 4 tested for secret leakage without ever defining how a secret enters the system — V8 checked an output with no specified input path.
9. **Secret hygiene tests.** A manifest or trace containing an API key fails the suite, and `.env` must be gitignored with no tracked file matching the secret patterns.
10. **Frontend placeholder — owned by Antigravity** (Phase 0 parallel-work exception). A Vite + React + TypeScript skeleton that builds, proving the toolchain. Real UI is Phase 9.

### Out of Scope

- Any data normalization, embedding, indexing, or retrieval logic.
- The trace schema, action enum, writer, viewer, and capability-contract test — all Phase 0.5.
- Any OpenRouter call, including the capability preflight.
- Database tables for messages, media, or channels — Phase 1 owns the schema and its migration.

## Implementation Steps

1. **Repository skeleton.**

```
configs/            base.yaml, llm.yaml, experiments/   (§23 sweeps A–D live here)
src/vsf/
  config/           schema, loading, canonical hashing, env settings
  manifest/         run-manifest writer
  storage/          ScopedRepository (public); _raw/ private DB + object-store clients
  trace/ corpus/ embeddings/ golden/ index/ eval/ retrieval/ vlm/ api/   (empty, per-phase)
frontend/           Vite + React + TS placeholder
tests/
  unit/ integration/ architecture/
.env.example        committed, no real values
.env                gitignored, never committed
.gitignore  docker-compose.yml  pyproject.toml  uv.lock  Makefile
```

1b. **Environment and secrets.** Two channels that must never blur into one:

   - **`configs/*.yaml` — behaviour.** Committed, hashed into `config_hash`, and containing **no secret-typed field at all**. The schema has no password, key, or token field, so a secret cannot be placed there even by mistake.
   - **`.env` — credentials and connection endpoints only.** Never committed. Loaded by `pydantic-settings` into a separate settings object that is not part of `config_hash`.

   **Env may never override a behaviour-affecting config value.** If it could, a run's real behaviour would diverge from the config that was hashed and recorded, and `config_hash` would describe something that did not happen. Enforced by keeping the two objects separate types with no overlapping keys.

   `.env.example` lists every required variable with placeholder values: `OPENROUTER_API_KEY`; `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `POSTGRES_HOST`, `POSTGRES_PORT`; `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `MINIO_ENDPOINT`, `MINIO_BUCKET`. Docker Compose reads the same file, so the stack and the application never drift into two sets of credentials. Startup fails with a message naming the missing variable rather than failing later at first use.

2. **Config schema.** Grouped so a sweep changes one block without touching the rest:

   - `retrieval` — `top_k`, `bm25_dense_weights`, `bm25s` parameters (pinned version, analyzer/tokenizer, `k1`, `b`, document unit, field weighting, tie order, score normalization)
   - `embeddings` — `text_embedding_model` (`bge-m3`), `visual_model` (pinned SigLIP 2 variant)
   - `context` — the five expansion strategies as independent switches, `context_window`, token budget
   - `controller` — `clue_extraction_strategy`, `jump_query_strategy`, `max_rounds`, `max_jumps`
   - `dataset` — `dup_threshold`, `phash_threshold`, `split_salt`, `review_salt`
   - `scope` — default `caller_id` behaviour, default-deny switch (present but not enforceable until Phase 1)
   - `llm` — the role-to-model mapping object

3. **Role-to-model mapping object.** Per role (`query_analyzer`, `clue_extraction`, `bridge_resolver`, `vlm_extract`): `model_id`, `prompt_version`, `json_schema_ref`, `structured_output_required`, `provider` (`order`, `allow_fallbacks`, `require_parameters`, `quantizations`, `max_price`), `retry` (attempts, backoff, schema-violation behaviour), `budget`.

   A validator enforces the release invariant per `PLAN.md` revision 5: when `mode == "release"`, `require_parameters` must be `true`, `allow_fallbacks` must be `false`, and **`provider.order` must pin an exact endpoint tag**, with a dated `model_id` additionally required wherever the model exposes one. Both pins are required where both exist — they cover different drifts, so either alone leaves a hole. `quantizations` is required only where the model's endpoints advertise real variants. A dev config may relax these only with `comparable: false`, which the manifest carries forward so a non-comparable run can never be cited as a result.

   **`max_price` is optional and unset in Phase 0.** No ceiling is hard-coded from a documentation page. Phase 0.5's capability test resolves an eligible non-batch endpoint for the configured model, records its current prompt and completion price in the release manifest, and only then is a ceiling proposed for user approval at or above that endpoint's price. When a cap is eventually set, a price rejection must surface as an explicit error naming the cap and the resolved endpoint price — with `allow_fallbacks: false`, a price rejection and a routing failure otherwise look identical and send debugging the wrong way.

4. **Deterministic `config_hash`.** Canonical JSON, sorted keys, normalized numeric formatting. The hash covers **every behaviour-affecting field**, explicitly including `mode`, `comparable`, and the full role/provider/retry configuration. Only non-semantic values are excluded: output locations, `run_id`, and timestamps.

   `mode` was excluded in revision 1, which was wrong for the reason Codex gave: release mode changes routing and evaluation semantics — `require_parameters`, fallbacks, endpoint pinning, comparability — so a dev run and a release run could otherwise collide on one hash while behaving differently, silently disarming the `eval_log.jsonl` guard. Tested two ways: the same config hashed in two separate processes is equal (V3), and flipping `mode` from dev to release changes the hash (V10).

5. **Docker Compose.** Postgres 16 with `CREATE EXTENSION vector` verified by an init script; MinIO with the media bucket created at boot; both on a named network with health checks, so `make up` either yields a working stack or fails visibly.

6. **Run-manifest writer.** Emits `run_id`, `created_at`, `git_commit`, `config_hash`, the full config snapshot, resolved dependency versions (`bm25s`, embedding models), and null-valued placeholders for `evaluation_release_id`, `gold_sha256`, `evaluator_version`, component/assignment manifests, `split_salt`, and resolved model/provider/endpoint/quantization.

7. **Architecture test.** AST-walks `src/vsf/` and fails when any module outside `storage/ScopedRepository` imports `storage/_raw/`. Written in Phase 0 while the rule is trivially satisfiable.

8. **Secret hygiene test.** Asserts no environment value matching the API-key pattern appears in a written manifest.

9. **Makefile targets.** `make up`, `make down`, `make test`, `make lint`, `make config-hash CONFIG=...`.

## Contracts and Changes

- **Config contract** — `configs/base.yaml` is the schema of record; `configs/experiments/*.yaml` override blocks for §23 sweeps A–D. Unknown keys are rejected.
- **Manifest contract** — one JSON per run under `runs/<run_id>/manifest.json`, append-only, committed for release runs.
- **Data contract** — `data/raw/` is read-only from Phase 0 onward. `data/processed/` and `data/golden/` are generated, never hand-edited.
- **Secrets contract** — secrets live only in `.env`, never in `configs/`, never in a manifest, never in a trace. The config schema declares no secret-typed field, so there is nowhere to put one. Env carries credentials and connection endpoints only and can never override a behaviour-affecting value, because a run whose behaviour diverges from its hashed config makes `config_hash` a record of something that did not happen.
- **Migration and rollback** — no database tables are created in this phase, so Phase 1 owns the first migration. **Ordinary teardown is `docker compose down`, with no `-v`.** `runs/` is never part of rollback: manifests are append-only release evidence, and a failed run is retained and marked failed rather than deleted. Destroying volumes is a separate, explicitly named `make dev-reset` that refuses to run when any release manifest exists and requires an explicit confirmation flag. Revision 1 specified `docker compose down -v` plus deleting `runs/`, which would have erased MinIO objects, database state, and release evidence with no recovery path.
- **Trace and observability impact** — none. Trace begins at Phase 0.5, first emitters at Phase 1. The manifest writer is deliberately built first so Phase 0.5 has somewhere to record the capability-contract result.

## Verification and Exit Criteria

**Independent Antigravity testing**

Antigravity owns V9 and must create its own toolchain-focused adversarial checks under `tests/antigravity/`, rather than only rerunning Codex checks. For Phase 0, this includes a clean frontend build, a failing-build fixture or deliberate invalid import check, and a regression test for every frontend scaffold defect it fixes.

**Automated checks**

| # | Check | Passes when |
| --- | --- | --- |
| V1 | `make up` then health check | Postgres 16 answers, `vector` extension present, MinIO reachable, bucket exists |
| V2 | Config loads and validates | `configs/base.yaml` and all `experiments/*.yaml` parse; an unknown key raises |
| V3 | Config hash determinism | Same config hashed in two separate processes yields identical `config_hash` |
| V4 | Release-mode validator | A release config with `require_parameters: false` or `allow_fallbacks: true` is rejected |
| V5 | Endpoint unambiguity | A release config is rejected unless `provider.order` pins an exact endpoint tag. It is additionally rejected when the model exposes a dated id and `model_id` uses the bare alias. Both pins are required where both exist. |
| V6 | Manifest completeness | Written manifest contains every AC4 key, with nulls where the subsystem does not yet exist |
| V7 | Architecture test | No module outside `ScopedRepository` imports `storage/_raw/`; the deliberately-violating fixture fails as expected |
| V8 | Secret hygiene | No API key appears in any written manifest |
| V9 | Frontend builds (**Antigravity**) | `npm run build` succeeds in `frontend/` |
| V10 | Mode changes the hash | Flipping `mode` between dev and release produces a different `config_hash` |
| V11 | Rollback safety | `make dev-reset` refuses to run while a release manifest exists, and refuses without the confirmation flag |
| V12 | `.env` is not tracked | `.env` is gitignored; no git-tracked file matches the secret patterns; `.env.example` is tracked and contains no real value |
| V13 | Missing env fails early | Unsetting a required variable makes startup fail with a message naming that variable, not a later connection error |
| V14 | Env cannot change behaviour | Config and env settings objects share no key; setting any env var leaves `config_hash` unchanged |

**Manual checks**

- `uv.lock` is committed and pins `bm25s` to an exact version.
- `data/raw/` is untouched: byte count and file count match the Phase 0 survey (1,300 image files, 308 `session.json`, 333 `questions.json`).
- `max_price` is absent from every config; no ceiling is set anywhere in this phase.

**Handoff evidence**

- **Codex** reports the exact commands run and their real output, including failures; the `config_hash` of `configs/base.yaml`; the resolved `bm25s` version; and the `docker compose ps` health output.
- **Antigravity** reports the `npm run build` command and output for V9, plus the Node and toolchain versions used.

**Exit criteria.** V1–V14 pass and both handoff evidence sets are recorded. Phase 0 has no acceptance criterion of its own in `PLAN.md`; it is the precondition for AC4's manifest and AC6's scoped-access enforcement.

## Risks and User Decisions

**Decisions resolved:**

- **D-P0-1 — withdrawn.** The user approved `prompt: 0.20` / `completion: 1.50` per 1M, but that approval rested on my misreading: I took the bottom of a merged price range for the standard per-request price. The standard endpoints are \$0.30 / \$2.50, so the approved cap would have rejected every one of them with `allow_fallbacks: false`. **No ceiling is set in Phase 0.** `max_price` is optional and unset; Phase 0.5's capability test resolves an eligible endpoint, records its live price, and a ceiling is proposed to the user only then, at or above that price. The cheap tier is flex (\$0.15 / \$1.25), a synchronous per-request tier on best-effort capacity — usable in principle, and Phase 0.5 decides empirically whether its latency and capacity rejections are acceptable.
- **D-P0-2 — Config file placement: `configs/llm.yaml`, separate, carrying its own `llm_contract_version`.** Referenced from `base.yaml`; both files' contents fold into the single `config_hash`. This satisfies Codex's "one config contract" while letting the LLM contract version independently of the retrieval knobs, and keeps model sweeps from colliding with retrieval-knob sweeps in the same file.

- **D-P0-3 — Python 3.12, `uv`, `ruff`, `pytest`. Approved by Codex** in `20260914-161517-CODEX-CLAUDE-PHASE_REVIEW.md`. The committed lockfile is not optional regardless of toolchain: `PLAN.md` requires `bm25s` pinned and echoed in every manifest, and a lockfile is what makes that verifiable rather than merely asserted.
- **D-P0-4 — Serving tier: test flex first, fall back to standard.** User preference, recorded for Phase 0.5. Flex (\$0.15 / \$1.25 per 1M) is half the standard price and synchronous per-request, so it suits eval runs that are not latency-sensitive. Not assumed viable — Phase 0.5 measures latency and capacity-rejection rate and reports both before the tier is fixed. No Phase 0 work depends on this.

**No decisions remain open for Phase 0.**

**Risks**

- **R1 — `config_hash` instability.** The failure is silent: `eval_log.jsonl` deduplication stops guarding and nobody notices. Mitigated by V3 testing across processes rather than within one.
- **R2 — Architecture test written too permissively.** A boundary test with no teeth passes for nine phases and then cannot be tightened. Mitigated by writing a deliberately failing fixture alongside it that proves the test detects a violation.
- **R3 — `pgvector` image drift.** A Postgres image without the extension surfaces only at Phase 3. Mitigated by V1 asserting the extension at boot, not at first use.
- **R4 — Scope config present but unenforceable.** `scope` exists in the schema from Phase 0 while enforcement lands in Phase 1, which could read as "done". Recorded here explicitly: Phase 0 declares the knob, Phase 1 makes it real, and AC6 is not claimed until then.
- **R5 — Price-cap decision deferred into Phase 0.5.** Until a ceiling exists, a release run has no upper bound on per-request price. Accepted deliberately: an unset cap risks overspending, a wrong cap blocks every request, and only the capability test can tell them apart. Phase 0.5 must not be marked complete without either a user-approved ceiling or a recorded decision to run without one.

## Handoff

**Phase 0 parallel-work exception.** Ownership is split, and the frontend scaffold does not depend on database tables or the backend API, so the two tracks run concurrently. This is an exception to the usual rule that Antigravity waits for Codex's `READY_FOR_ANTIGRAVITY`; that rule resumes from Phase 1 onward.

- **Codex owns**: Python packaging, config schema and hashing, env/secrets handling, Docker Compose, run-manifest writer, storage boundary and its architecture test, secret hygiene tests, Makefile. Verifies V1–V8 and V10–V14.
- **Antigravity owns**: the `frontend/` Vite + React + TypeScript scaffold. Verifies V9 and sends its evidence to Codex and Claude.
- **Codex to Claude**: handoff evidence for V1–V8 and V10–V14.
- **Antigravity to Claude**: V9 evidence only; there is no user-facing surface to verify in this phase.
- **Neither track blocks the other.** Phase 0 is complete when both evidence sets are recorded.
