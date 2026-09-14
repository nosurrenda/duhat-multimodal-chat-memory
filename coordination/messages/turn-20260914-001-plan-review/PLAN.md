# ARCHITECTURE PLAN: Contextual Multimodal Retrieval for Chat

- From: `Claude`
- To: `Codex`
- Date/time: `2026-09-14T17:50:00+07:00`
- Status: `ARCHITECTURE_UPDATED`
- Plan revision: `7`
- In reply to: `20260914-161517-CODEX-CLAUDE-PHASE_REVIEW.md`

## Purpose

This is the approved architecture and cross-phase contract. Every value below is current; there are no amendment layers to resolve. It is used to compare phase plans against the overall design; it does not authorize implementation by itself. Every phase requires its own reviewed plan in `phases/` before work begins.

## Independent Test Rule

Antigravity owns independent code review and an adversarial suite under `tests/antigravity/`. For every implemented phase, it reviews Codex's changed code against this architecture plan and the phase plan, then derives tests from those contracts, API behavior, and raw fixtures before relying on Codex's tests. Its suite must include black-box behavior, negative/security cases, boundary or malformed input, and regressions for fixed defects. Passing Codex-authored tests alone is never sufficient for phase verification. Codex may diagnose failures but may not weaken, skip, or edit Antigravity-owned assertions or fixtures; a product-contract change requires a documented new decision. Claude performs only the final plan-compliance review after Antigravity reports no unresolved code-review finding and passing independent tests.

---

## Task

Build V1 of Contextual Multimodal Retrieval for Chat — an image retrieval layer over chat history that resolves queries combining visual content, metadata, and conversational context, per `docs/Contextual Multimodal Retrieval for Chat.md` (product) and `docs/Contextual Multimodal Retrieval Architecture.md` (architecture). Includes converting `data/raw/H2HMEM` into a canonical corpus and a golden retrieval benchmark.

User decisions: `USER_DECISIONS_RECORDED`.

## Decision Log (user-approved)

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | Corpus is one merged workspace, 25 channels (one per dialogue) | Per-dialogue corpora are 215–360 messages — too small to distinguish good retrieval from bad. Merging creates real distractors (health 55 / pet 54 / food 43 sessions overlap) and makes §6 permission scope testable. Eval still runs per-channel via scope filter. |
| D2 | Golden dataset queries are English-only | Corpus is English. Product doc examples are Vietnamese, but cross-lingual would contaminate every metric. |
| D3 | Golden dataset is **1,090 queries**, agent-generated, every case passing the anti-leak gate, with 15% human review per stratum and 30% for Tier C strata | Sized for statistical power on the strata that decide AC7/AC8/AC10, not for convenience. The earlier 600–800 target left no held-out allocation. Gold labels come from the data, never from the generating agent. |
| D4 | Chart/table images are distractors by default; gold answers **only** within the `vlm_dependent` stratum | SigLIP is weak on charts, so gold-everywhere would depress visual metrics for reasons unrelated to architecture. But `vlm_dependent` needs them as gold or Phase 8 cannot be measured. |
| D5 | Phase 8 stays in V1 as a **single lazy VLM stage in query-independent extract mode**. No separate OCR engine. | OCR is a subset of VLM capability; two lazy paths means two triggers and two caches with a blurry boundary. Extract mode (`{description, visible_text, chart_trend, numbers}`) makes the cache key exactly `media_id + model_version` as §18 specifies — a query-conditioned call breaks that key. §24's `OCR/VLM calls` becomes `VLM calls`. |
| D6 | No VLM/OCR pass for dataset construction | Verified unnecessary: 383 Cross-modal Related Retrieval questions already encode image-only visual facts, and 252 questions are already attached to chart images with trend descriptions written out. Near-duplicate mining uses SigLIP + pHash, built anyway. Also removes the self-fulfilling-test risk of labelling with the model that serves runtime. |
| D7 | **Phase 0.5 — Trace & Observability Contract**, before Phase 1 | §20 makes the trace the eval harness's input, yet the trace was previously gated at Phase 9 while the harness is Phase 4. The trace also cannot be retrofitted: `Jump Success Rate`, `No-gain Jump Rate`, and `Loop Prevention Count` are computable only from it. Defining it first fixes the controller's action set before the controller exists, which is what makes §14's "finite action set" enforceable. |
| D8 | **Build-time and runtime LLM work are separate contracts.** Phase 2 generation is a coding-agent task with no API call; only the four runtime roles use OpenRouter. | Generating labels happens once and yields a frozen checksummed artifact; serving queries happens per request, forever, and must be measured and reproducible. Conflating them imposed API-shaped provenance on agent-authored rows. |
| D9 | **Antigravity authors Tier C; Codex consumes it read-only through the loader/evaluator.** | Tier C is the sole evidence for AC8, and Codex implements the Search Jump controller AC8 judges. Separating *authorship* from *implementation* removes the bias; separating *access* would have blocked Phase 2 acceptance and Phase 4 baselines, which is why the earlier read-ban was wrong. |
| D10 | Stack: Python/FastAPI backend, TypeScript/React frontend, Docker Compose with Postgres 16 + pgvector + MinIO | Matches §21 and keeps production shape, so scope enforcement and the outbox are exercised for real. |
| D11 | Embeddings both local: `bge-m3` dense text, SigLIP 2 visual | No embedding-API dependency; indexes stay freely rebuildable as §5 requires of derived state. 7,078 messages and 1,265 images is minutes of local inference. |
| D12 | All four runtime LLM roles use `google/gemini-3.5-flash-lite` via OpenRouter | One model family, fewest debugging variables. Vision, structured outputs, tools, and 1M context in one model covers every role including VLM extract. Stronger models are swept, not assumed. |
| D13 | No LangGraph. No Langfuse or LangSmith. | §14 defines a bounded controller, not a general agent, so LangGraph's branching machinery buys nothing and inserts an adapter between its state model and the §20 schema. On observability: the §20 trace records retrieval evidence, and most of the pipeline (BM25, SigLIP, pgvector, metadata filters, structural traversal) makes no LLM call at all, so an LLM-span platform cannot replace it. AC4 also requires the trace to be a committed checksummed artifact, which a hosted service cannot be. OpenRouter returns `usage` and the resolved provider per response, so §24's counters go into the same trace — one artifact, and the provider audit needs them there regardless. |

## Scope

**In scope (V1).** Ingest (raw store, async text indexing, visual embedding, metadata/inferred relations); query analysis; hybrid text retrieval; visual retrieval; structural traversal; configurable context expansion; clue extraction; evidence-driven Search Jump; Context–Media Bridge Resolver; bounded retrieval controller; lazy VLM rerank; provenance/trace; eval harness.

**Out of scope (V1), per §22.** Full-corpus captioning, knowledge graph, precomputed topic/episode hierarchy, general research agent, video/voice/document modalities.

**Hard constraints.**

1. Structural relations are used directly; the LLM is never asked to re-derive them (§8).
2. Permission/scope is resolved before retrieval, and a Search Jump may never leave the resolved scope (§6).
3. Context Expansion and Search Jump are distinct operations with distinct counters (§10, §11).
4. A jump requires all three: a missing constraint, an unused new clue, and plausible relevance of that clue to the missing constraint (§11.1).
5. Bounded controller: 2–3 rounds total, finite action set, not a general ReAct agent (§14).
6. Every retrieval knob in §23 is config, not code.
7. Raw data is source of truth; every index is rebuildable derived state (§5, §21).

## Assumptions

- **A1.** Postgres + pgvector + object storage suffices at this corpus size; no separate search cluster.
- **A2.** Synthetic per-message timestamps are acceptable — H2HMEM carries only session-level dates. Generation is deterministic and seeded.
- **A3.** Inferred-reply coverage will be meaningful for multi-party (1,096 `@mentions` / 1,762 turns) and near-zero for dyadic (2 files contain `@`). The `referential` stratum is scoped to multi-party plus adjacency references, and is reported as an inferred-evidence stratum. Coverage appears in the Phase 1 validation report.
- **A4.** Cross-session gold provenance in `session0/questions.json` was overwritten to `["session0"]`, but `validation_notes` names source sessions in prose ("Session 3 + Session 5"). Phase 2 recovers it by parsing those notes; every recovered case is verified before use.
- **A5.** **No LLM API is used for dataset construction.** Tier A/B are authored by Codex and Tier C by Antigravity, both as coding tasks (D8, D9).

## Data findings (verified against `data/raw/H2HMEM`)

| Fact | Value |
| --- | --- |
| Dialogues / sessions / turns | 25 (20 dyadic + 5 multi-party) / 308 / 7,078 |
| Image references in turns / files on disk | 1,265 / 1,300 (0 broken references) |
| Unreferenced image files | 33 — free hard negatives |
| QA pairs | 2,236 across 333 `questions.json` |
| Questions carrying `question.image` (deterministic gold media) | 618 |
| Of those, attached to chart/table images | 252 |
| Cross-modal Related Retrieval questions (image-only facts) | 383 |
| Answer Refusal / Conflict Detection questions | 271 / 246 |
| `@mention` density (multi-party) | 1,096 mentions / 1,762 turns |
| Temporal span per dyadic dialogue | 11–16 sessions across calendar 2024 |

**Data defects to fix in Phase 1.**

1. `timeline_date` is dirty: `2025-01-01 上午`, `2024-9-25`, `2024.9.5`, `2024-11-23 (参观后)`.
2. Session number does not follow chronology (dialogue1: session9 = 09-05 precedes session8 = 09-10), yet questions cite sessions by index. Both axes are kept.
3. Speaker aliases are inconsistent: `A_LinSen` / `Lin Sen`, `维基` / `@Vicky`, `Xiaobai` / `@小白`. Alias resolution precedes mention parsing.
4. Image filenames collide globally (`1.png` appears 129 times) — global media IDs are mandatory.
5. `session0` has no `session.json`; it is the cross-session QA container, not a session.
6. 151 / 7,078 turns still contain CJK text.
7. `reply_to`, `thread_id`, and channel structure are entirely absent and must be derived.

---

## Canonical data model

- **Messages**: `message_id` (`ch:sess:turn_idx`), `channel_id` (`dyadic_d1`), `thread_id` (`<session_id>`), `sender_id` (canonical, alias table alongside), `text`, `timestamp`, `source_turn_index`, `session_index`, `chronological_rank`.
- **`reply_to_message_id` is always NULL.** H2HMEM has no explicit reply relation. Inferred links live only in `inferred_relations` with `relation_type`, `derivation_method`, `confidence`, `candidate_message_ids[]`, `evidence_span`, `model_version`. They are **non-authoritative for all of V1**; no promotion path is implemented. The n ≥ 100 / precision ≥ 0.90 study is research evidence only. Every provenance citation of an inferred relation carries method, confidence, candidates, and evidence span.
- **Timestamps**: strictly increasing in `source_turn_index` **within a session**. Across sessions, `chronological_rank` follows normalized date. Tie-breaker `(normalized_date, session_index, source_turn_index, message_id)`. The `dyadic/dialogue1` session 9 / session 8 date inversion has a regression test. No global source-order monotonicity is claimed.
- **Media**: all **1,265 logical rows and IDs retained**; `media_id` is never merged. `content_sha256` plus `storage_object_ref` provide physical deduplication. pHash and SigLIP clusters are recorded as `near_dup_cluster_id` for split grouping and distractor mining only — never as an identity relation.

## Authorization

- Every query carries `caller_id`. Membership is `(caller_id, channel_id, role, valid_from, valid_to)`. Missing, unknown, or expired identity is **default-deny** — an empty accessible-channel set, not an unfiltered search.
- `ScopedRepository` is the **only** path to messages, media, embeddings, cached VLM extracts, and provenance. Exact-ID lookup, cache reads, expansion, jump destinations, and provenance serialization all route through it. Unscoped access is a build failure, enforced by an architectural test.
- Negative fixtures: inaccessible exact media ID; expansion crossing a channel boundary; a jump whose best destination is out of scope; a cached VLM extract on an inaccessible medium. Each asserts no leak into the result **and** no leak into the trace. The benchmark includes restricted-caller coverage.

## Retrieval implementation

- **Lexical is pinned `bm25s`** (exact version in the lockfile and in every run manifest), with analyzer/tokenizer, `k1`, `b`, document unit, field weighting, tie order, and score normalization all recorded. Postgres FTS may serve as a candidate retriever labelled `fts_candidate`; it is never labelled BM25 and never used for the anti-leak gate or a reported baseline.
- **Dense text** is `bge-m3`; **visual** is the pinned SigLIP 2 variant. `bge-m3`'s sparse head may be added as an optional extra candidate retriever, but never replaces pinned `bm25s` for the gate or baselines.
- **Calibration**: visual and text score normalization parameters and fusion weights are fitted on the **dev split only** and frozen into the config before any test run.
- **Outbox** is table-backed with transactionally written records, `FOR UPDATE SKIP LOCKED`, lease/visibility timeout, idempotency keys, bounded retries, and a dead-letter state. No broker in V1.

## Runtime LLM contract

A **single role-to-model mapping object** (not separate top-level keys) holds, per role, the model id, prompt version, JSON Schema, structured-output requirement, provider policy, retry policy, and budget.

| Role | Model | Notes |
| --- | --- | --- |
| `query_analyzer` | `google/gemini-3.5-flash-lite` | emits `{target, visual, sender, time, context, media_type}` |
| `clue_extraction` | `google/gemini-3.5-flash-lite` | ambiguous references only; deterministic extraction runs first |
| `bridge_resolver` | `google/gemini-3.5-flash-lite` | baseline; sweep targets `google/gemini-3.8-flash-20260902`, `z-ai/glm-5.3` |
| `vlm_extract` | `google/gemini-3.5-flash-lite` | vision + structured output, cached by `media_id + model_version` |

- **Release runs must resolve to one unambiguous serving endpoint.** Two independent things can drift, so both are pinned:
  - **The exact provider endpoint tag is always required** in `provider.order` (e.g. `google-vertex/global`). Pinning the model alone is not enough — `google/gemini-3.5-flash-lite` is served by eight endpoints spanning \$0.15–\$0.54 per 1M input, with different latency and capacity behaviour, and OpenRouter may route among them freely.
  - **A dated model id is required wherever the model exposes one** (e.g. `google/gemini-3.8-flash-20260902`). Where it does not — `google/gemini-3.5-flash-lite` returns only the bare slug — the pinned endpoint tag stands alone, and the manifest records that no dated form exists. An alias that moves to a new version changes behaviour while the endpoint tag stays constant, which is why the endpoint pin does not cover this case.
- **`provider.quantizations` is required only where the model's endpoints advertise real variants.** All eight endpoints for the selected model report `quantization: unknown` — first-party Google endpoints with no variants — so an unconditional non-empty requirement would make every release run impossible. Where variants exist they must be pinned; where they do not, the endpoint tag is what carries determinism.
- **Structured output is enforced at provider selection, not only validated after the fact.** Every structured role defines its JSON Schema and uses `response_format` with strict validation. Release runs set `provider.require_parameters: true` alongside `provider.order` and `allow_fallbacks: false` — OpenRouter's default is `require_parameters: false`, under which a provider silently ignores unsupported parameters, so post-hoc validation alone can yield schema-violating JSON or unpredictable failures once fallbacks are off. Dev configuration may relax this only when the trace marks the run **non-comparable**.
- **`provider.max_price` is optional and unset until Phase 0.5 resolves it.** No ceiling is set from a documentation page: the selected model's standard endpoints are \$0.30 / \$2.50 per 1M, while the flex endpoints are \$0.15 / \$1.25, and a cap read off the cheap end would reject every standard request once `allow_fallbacks: false` is set. The Phase 0.5 capability test resolves an eligible non-batch endpoint, records its live prompt and completion price in the release manifest, and only then is a ceiling proposed for user approval at or above that price. Once a cap exists, a price rejection must surface as an explicit error naming the cap and the resolved endpoint price — with fallbacks disabled, a price rejection and a routing failure are otherwise indistinguishable.
- **Serving-tier preference for Phase 0.5: test flex first, fall back to standard.** Flex (`google-vertex/global/flex`, `google-ai-studio/flex`) is a synchronous per-request tier on best-effort capacity at half the standard price, so it suits eval runs that are not latency-sensitive. It is not assumed viable — Phase 0.5 measures its latency and capacity-rejection rate and reports both before the tier is fixed.
- The **resolved provider, endpoint, and quantization are recorded in the trace and run manifest** on every run, dev included.
- **Bridge Resolver starts on the cheap model and sweeps upward.** If the baseline already clears AC7's +12pp the upgrade is unnecessary; starting strong never reveals whether the cost was needed. §23 requires the sweep to be configurable regardless.
- **Cost estimate, explicitly provisional**: ~\$0.0019 per query at the cheapest serving tier, ~\$1.30 per 654-case test run. This rests on assumed fire rates (clue ~40%, bridge ~50%) and assumed input sizes, none measured. §24 already mandates tracking `LLM calls/query` and `cost/query`, so Phase 4 replaces these guesses with measurements.

## Golden dataset

**Target 1,090 queries.** Dev 436 / test 654.

| Stratum | Total | dev | test | Source |
| --- | --- | --- | --- | --- |
| `visual_plus_context` | 200 | 80 | 120 | Tier A, MCR on photos (149) + Tier C top-up |
| `long_range` | 130 | 52 | 78 | `session0` cross-session (26 files) |
| `vlm_dependent` | 130 | 52 | 78 | the 252 chart-attached questions (D4) |
| `multi_round_jump` | 120 | 48 | 72 | Tier C — sole source, highest supply risk |
| `direct_visual` | 100 | 40 | 60 | Tier B + CRR on photos (99) |
| `context_only` | 90 | 36 | 54 | Tier C + `session0` |
| `metadata_only` | 70 | 28 | 42 | Tier B templates |
| `unanswerable` | 70 | 28 | 42 | Answer Refusal (271) |
| `referential` | 60 | 24 | 36 | multi-party mention chains (inferred-evidence) |
| `interleaved` | 60 | 24 | 36 | multi-party `meta.active_events` |
| `ambiguous_clarification` | 60 | 24 | 36 | Conflict Detection (246) |
| **Total** | **1,090** | **436** | **654** | |

Phase 2 reports `multi_round_jump` supply progress; if it cannot reach 120, AC8 is renegotiated with the user rather than silently weakened.

**Tiers.** Tier A: the 618 `question.image` rows, where the attached image is gold and the agent only rewrites QA phrasing into retrieval phrasing. Tier B: templates over existing fields. Tier C: authored by Antigravity per D9, producing cases whose evidence sits in a different session from the image.

**Row schema.** `query_id` (`q_<stratum>_<zero-padded seq>`, immutable; retired rows are tombstoned, never renumbered), `content_hash`, `query`, `gold_media_id`, `gold_evidence_message_ids`, `gold_clues[]`, `stratum`, `source_tier`, `difficulty`, `split`, `group_id`, `generator_agent` (e.g. `codex-5.5`, `antigravity`), `generator_instruction_ref` (the commissioning message filename), `generated_at`, `output_hash`, `source_question_ids`, `source_message_ids`, `label_verification_result`, `gate_result`, `rewrite_count`.

Agent generation is not reproducible the way a pinned API call is. This is acceptable because the dataset is a **frozen, checksummed artifact that is never regenerated** — it must be fixed and auditable, not deterministically reproducible. Runtime is the opposite case and keeps full reproducibility requirements.

**Tier C handoff (answers Codex open question 6).** Before Phase 2 begins, the following are published: the Tier C row schema (identical to the schema above), a validation command, the commissioning message reference, a handoff checklist, and the anti-leak and review requirements. Antigravity delivers Tier C as a **frozen, schema-validated artifact before Phase 2 acceptance and before Phase 4 evaluation**. Codex reads the released artifact only through the dataset loader and evaluator, and does not generate, modify, relabel, or inspect test gold while tuning. Enforcement is by file ownership in the workflow plus checksum verification in the evaluator.

**Anti-leak gate.** Statistic: top-1 accuracy of pinned `bm25s` whose document unit is the **parent message text alone** — no expansion, no surrounding context — over the full merged 25-channel corpus with unrestricted scope. A case is rejected when the gold medium's parent message ranks first. Released-set parent-only top-1 must be **≤ 15%**. A rejected case is rewritten once (paraphrase removing tokens shared with the parent message while preserving gold and constraints), re-gated, and dropped if it fails again. Per-stratum rejection, rewrite, and survival counts are reported.

**Splits.** Dev 40 / test 60. No train split; nothing is trained, only configurations selected. Dev selects every parameter; test is final-only.

A **component** is the indivisible assignment unit: union-find connected sets over same `source_dialogue` **or** a near-duplicate edge (SigLIP cosine ≥ `dup_threshold` or pHash Hamming ≤ `phash_threshold`). Assignment is **deterministic stratified greedy**: order components by `(total_size desc, stratum_count_vector desc lexicographic, component_id asc)`; for each, identify its rarest stratum by remaining global quota; assign to the split with the larger deficit in that stratum; break ties by total remaining deficit, then `test` before `dev`, then `component_id`.

Before acceptance, report actual per-stratum dev/test counts, group counts, largest group per stratum, and quota deviation. Deviation beyond **±8 absolute or ±15% relative, whichever is larger**, is `quota_infeasible` and blocks acceptance until the user decides. Quotas are never auto-adjusted.

**Freezing.** Each release freezes `components@R`, `assignment@R`, and `gold_sha256`. A new case in an existing group inherits its split; a new group is assigned without moving existing groups. A new duplicate edge joining two released components in **different** splits is never merged silently — it raises `audit_finding: cross_split_leak` and quarantines the newer case from evaluation pending a user decision. Same-split merges are recorded without reassignment.

**Human review.** 15% per stratum, 30% for Tier C strata (`multi_round_jump`, `context_only`). Sampling is deterministic from `sha256(query_id + review_salt)` and the sampled ID list is committed before review. Each review records `reviewer_key` (anonymized, stable) and `reviewed_at`, against a fixed rule: the gold medium satisfies every constraint in the query; the gold evidence messages suffice to establish the context constraint; no other corpus medium satisfies the query equally well. A stratum failing below a 90% pass rate is regenerated, not patched case by case. A 20% overlap subset is double-reviewed and Cohen's κ reported.

## Evaluation framing

AC7 and AC8 are **holdout estimates, not confirmatory tests**. The test split is repo-visible, so no mechanism genuinely seals it in a local repository and the plan does not claim otherwise. Each reports a 95% confidence interval; McNemar p-values are computed and reported as **descriptive**, carrying that caveat.

- `data/golden/public/queries.jsonl` — `query_id`, text, stratum, split, `group_id`. No gold fields.
- `data/golden/sealed/gold.jsonl` — gold labels, read only by the evaluator path; `gold_sha256` in the manifest. It is committed repo data: this is friction and audit, not access control, and is described as such.
- `eval dev` cannot read the test split. `eval release` requires an `evaluation_release_id`.
- Append-only `data/golden/eval_log.jsonl` records `evaluation_release_id`, `config_hash`, `gold_sha256`, `evaluator_version`, timestamp, and results digest. Re-running an existing `(release_id, config_hash)` against test fails unless `--reproduce` is passed; a reproduction is recorded as such and may not be cited as a new result.

## Acceptance Criteria

**AC1 — Canonical corpus.** `data/processed/` contains `messages.jsonl`, `media.jsonl`, `channels.jsonl`, `senders.jsonl`, loaded into Postgres. Exactly 7,078 messages and 1,265 media rows; 0 broken media→message links; 0 unparsed dates; 0 duplicate global IDs; `reply_to_message_id` NULL throughout. The session 9 / session 8 timestamp regression test passes. A committed validation report states inferred-relation coverage per channel type.

**AC2 — Golden dataset.** 1,090 queries in `data/golden/` carrying the full row schema above. Every stratum meets its dev and test quota within tolerance, or is flagged `quota_infeasible` and escalated. Every case passes the anti-leak gate. Review coverage and pass rates are recorded per stratum. Tier C is delivered as a frozen schema-validated artifact before acceptance.

**AC3 — Anti-leak gate.** Released-set parent-only `bm25s` top-1 is ≤ 15%, with per-stratum rejection, rewrite, and survival counts reported. **AC3b**: if B2 reaches ≥ 0.70 Exact Media Accuracy on `visual_plus_context` test, the benchmark is too easy — AC2 is not met and Phase 2 reopens.

**AC4 — Eval harness.** One command runs a named config end to end and emits per-stratum Exact Media Accuracy, Media Recall@K, Message Evidence Recall, Context Precision, Context–Media Bridge Recall, Constraint Satisfaction Accuracy, plus Jump Success Rate, Useful Clue Precision, Average Jumps/Query, No-gain Jump Rate, Loop Prevention Count, and system metrics (rounds, search calls, LLM calls, VLM calls, latency p50/p95, cost/query). Each run persists the full §23 config snapshot plus `evaluation_release_id`, `gold_sha256`, `evaluator_version`, component and assignment manifests, `bm25s` parameters, `split_salt`, and the resolved model id, provider, endpoint, and quantization.

**AC5 — Baselines.** B0 visual-only, B1 text-only hybrid, B2 independent score fusion, B3 B2 + temporal expansion, all measured and recorded as the comparison floor.

**AC6 — Architecture correctness.** Context Expansion and Search Jump are counted separately. No jump fires without a missing constraint plus an unused clue. Loop prevention is demonstrated on a Dalat→Pine Hill→Dalat fixture. No result or trace leaves the caller's resolved scope; the four negative fixtures pass. Rounds never exceed the configured cap. The controller emits no action outside the Phase 0.5 enum.

**AC7 — Bridge value.** On `visual_plus_context` test (n=120), full system minus B2 on Exact Media Accuracy is **≥ +12pp**, reported as a holdout estimate with a 95% CI and a descriptive McNemar p-value. A case where visual score is lower but contextual evidence is decisive resolves correctly.

**AC8 — Jump value.** On `long_range` ∪ `multi_round_jump` test (n=150), full system minus the no-jump configuration is **≥ +10pp**, same reporting treatment, with Jump Success Rate and No-gain Jump Rate reported.

**AC9a — Trace contract (Phase 0.5).** Schema, action enum, writer/reader, conformance test, and viewer exist and are versioned. Result-level fields: `media_id`, `parent_message_id`, `visual_score`, `context_message_ids`, `structural_relations`, `supported_constraints`. Round-level: `round`, `source_anchor_id`, `extracted_clues`, `jump_query`, `destination_anchor_ids`, `missing_constraints_before_jump`, `supported_constraints_after_jump`. Per-LLM-call records carry role, dated model id, resolved provider, prompt version, tokens, cost, latency.

**AC9b — Provenance UI (Phase 9).** The interface surfaces the "matched because" evidence chain from the trace, labelling inferred relations as inferred.

**AC10 — VLM stage.** Runs only on top-K in query-independent extract mode, cached by `media_id + model_version`. Cache hit rate is reported. The VLM-on minus VLM-off effect on `vlm_dependent` test (n=78) is reported **with a 95% CI and is not a pass/fail gate** — n=78 cannot resolve the desired effect, and where the interval crosses zero the report states the result is inconclusive rather than negative.

**AC11 — Clarification.** On `ambiguous_clarification` the system asks rather than guessing; on `unanswerable` it returns no result rather than a low-confidence guess. Both measured, not asserted.

## Implementation Plan

**Phase 0 — Skeleton and contracts.** Repo layout; config schema covering every §23 knob plus the role-to-model mapping object, `provider.order`, `allow_fallbacks`, `require_parameters`, `quantizations`, `max_price`; Docker Compose (Postgres 16 + pgvector + MinIO); run-manifest writer.

**Phase 0.5 — Trace & Observability Contract.** Trace schema (both levels); controller action enum `retrieve_lexical | retrieve_dense | retrieve_visual | filter_metadata | expand_context | extract_clues | resolve_bridge | jump | vlm_extract | stop`; per-call LLM record; writer/reader library; schema-conformance test; minimal trace viewer (CLI and static HTML). Also a **capability-contract test** against the configured model and provider covering vision input, JSON Schema structured output, and the configured context limit, storing resolved model id, provider endpoint, quantization, and capability result in the run manifest. Standing rule: from Phase 1 onward every phase emits trace events, and a phase is not complete until its events validate. Gate: AC9a.

**Phase 1 — Data normalization → canonical corpus.** Normalize `timeline_date`; synthesize deterministic seeded per-turn timestamps; assign IDs; resolve speaker aliases; derive `inferred_relations` from `@alias` (never into `reply_to_message_id`); move media into object storage with `content_sha256` dedup preserving all 1,265 logical rows; register the 33 orphans as distractors; synthesize channel membership; emit the validation report. Implements the first trace emitters. Gate: AC1.

**Phase 1.5 — Embeddings.** SigLIP 2 visual and `bge-m3` dense text, versioned by model. Pulled ahead of Phase 3 because Phase 2 needs near-duplicate clusters for split grouping and distractor mining.

**Phase 2 — Golden dataset.** Tier A/B by Codex, Tier C by Antigravity per D9 and the handoff contract. Anti-leak gate, split assignment, freezing, human review. Gate: AC2, AC3.

**Phase 3 — Ingestion and indexes.** Outbox → idempotent workers stamped with `model_version`: pinned `bm25s` lexical, `bge-m3` dense (pgvector), SigLIP 2 visual (reuse Phase 1.5). Metadata served from SQL. Edit/delete propagates.

**Phase 4 — Eval harness and baselines.** Config-driven runner over the Phase 0.5 trace; metric computation. B0–B3 measured. Gate: AC4, AC5. If B2 saturates the benchmark, return to Phase 2 per AC3b.

**Phase 5 — Query analyzer, parallel retrieval, context expansion.** Analyzer emits the constraint object under enforced JSON Schema. Three branches in parallel producing initial anchors, not answers. Five expansion strategies (reply/thread, temporal, same-sender, participant-aware, semantic), independently switchable under a token budget, so §23 configs A–D all run.

**Phase 6 — Context–Media Bridge Resolver.** Structural/graph resolution first (media → parent → expanded context); LLM judgment only where structure is insufficient. Bridge Recall measured here. Gate: AC7.

**Phase 7 — Clue extraction and Search Jump controller.** Deterministic clue extraction first; LLM only for ambiguous reference resolution. Controller state: `visited_queries`, `visited_anchor_ids`, `visited_media_ids`, `used_clues`, `remaining_constraints`, `round`, `jump_count`. Query-fingerprint loop prevention. Gate: AC6, AC8.

**Phase 8 — Lazy VLM rerank and clarification.** Single VLM stage in extract mode on top-K, cached by `media_id + model_version`. Clarification on small confidence gaps; explicit no-result when evidence is absent. Gate: AC10, AC11.

**Phase 9 — API and provenance UI.** Backend contract for Codex; UI for Antigravity. Gate: AC9b.

**Ordering note.** Phases 2 and 4 precede 5–7 deliberately. Without the benchmark and the baselines there is no way to tell whether Search Jump earns its cost or merely burns tokens.

---

## Changed in revision 4 (response to `20260914-155250-CODEX-CLAUDE-PLAN_REVIEW.md`)

All three corrections applied. Finding 1 was an over-correction on my side; finding 2 identified a gap that post-hoc validation genuinely does not close.

**Finding 1 — Tier C ownership.** The previous D9 forbade Codex from reading Tier C until Phase 7, which breaks the ordering: Tier C is the sole source for `multi_round_jump`, supplies `context_only`, and is required for Phase 2 acceptance and the Phase 4 baselines. Blocking access removed the bias and the benchmark with it. D9 now separates **authorship** from **access** — Antigravity authors and delivers a frozen schema-validated artifact before Phase 2 acceptance and Phase 4 evaluation; Codex reads it through the loader and evaluator only, never generating, modifying, relabelling, or inspecting test gold while tuning, enforced by workflow file ownership plus evaluator checksum verification. The **Tier C handoff** paragraph publishes the row schema, validation command, commissioning reference, checklist, and gate/review requirements before Phase 2 — closing open question 6.

**Finding 2 — structured-output enforcement.** Accepted. I verified the routing contract against the cited OpenRouter docs before writing it in: `require_parameters` defaults to `false`, under which a serving provider **silently ignores** unsupported parameters, so the default is precisely the footgun. Release runs now set `provider.require_parameters: true` with `provider.order` and `allow_fallbacks: false`; dev may relax it only when the trace marks the run **non-comparable**. Phase 0.5 gains the capability-contract test (vision, JSON Schema, context limit) recording resolved model, provider, endpoint, and quantization in the manifest. **Two additions beyond the request**, from the same doc page, because each closes a gap this plan had flagged without a mechanism: `provider.quantizations` is pinned for release runs (same model id at a different quantization is different behaviour, which `order` alone does not cover), and `provider.max_price` caps per-request price directly rather than hoping `order` lands on a cheap tier.

**Finding 3 — canonical metadata.** Rather than patch the header, revisions 2–4 were folded into the body and the override sections deleted. There are no amendment layers left to resolve.

| Section | Change |
| --- | --- |
| Header | `Plan revision: 4`, consistent with the title; status `PLAN_UPDATED` |
| Decision Log | D3 rewritten to the current 1,090 policy; D9 rewritten to authorship-vs-access; D7, D8, D10–D13 folded in |
| Assumptions | A3 reworded for inferred relations; A5 replaced — no LLM API in dataset construction |
| Canonical data model | **New.** NULL `reply_to`, non-authoritative `inferred_relations`, timestamp semantics, 1,265 preserved media rows |
| Authorization | **New.** `caller_id`, default-deny, `ScopedRepository`, four negative fixtures |
| Retrieval implementation | **New.** Pinned `bm25s`, `fts_candidate` labelling, `bge-m3` / SigLIP 2, dev-only calibration, outbox |
| Runtime LLM contract | **New.** Role-to-model mapping object, dated ids, `require_parameters`, `quantizations`, `max_price`, bridge sweep, provisional cost |
| Golden dataset | **New.** 1,090 with dev/test per stratum, tiers, row schema, Tier C handoff, anti-leak gate, split algorithm, freezing, review protocol |
| Evaluation framing | **New.** Holdout framing, public/sealed split, `eval dev` vs `eval release`, append-only log |
| Acceptance Criteria | All rewritten with canonical values; AC9 split into AC9a (Phase 0.5) and AC9b (Phase 9) |
| Implementation Plan | Phase 0.5 added with capability-contract test; Phase 1.5 widened to both embedding models; Phases 0, 1, 2, 3 updated |
| Revision 2/3/4 override sections | **Deleted** — folded into the body |

## Change history

- **rev 1** — initial plan from the two design docs and a scripted survey of `data/raw/H2HMEM`.
- **rev 2** — Codex found four blockers: undefined splits/thresholds, a derived reply edge masquerading as source structure, scope as an AC without an authorization model, and "Postgres FTS/BM25" conflating two different rankers. Dataset raised 600–800 → 1,090 for statistical power; numeric gates added; AC10 downgraded to a reported effect because n=78 cannot resolve +8pp.
- **rev 3** — Codex found three more: a hash threshold cannot honour a quota table; test isolation was policy rather than mechanism; and inferred-relation promotion contradicted the storage contract. Added deterministic stratified greedy assignment with freezing, reframed AC7/AC8 as holdout estimates, and made inferred relations non-authoritative for all of V1.
- **rev 4** — user added Phase 0.5 and the build-time/runtime LLM split; stack chosen. Codex found three corrections: the Tier C read-ban broke Phase 2/4 ordering (D9 now separates authorship from access), structured output needed `require_parameters` enforcement at provider selection rather than post-hoc validation, and the document carried contradictory canonical values. This revision folds all amendment layers into the body; there are no overrides left to resolve.
- **rev 5** — Phase 0 planning surfaced two runtime rules that were unsatisfiable for the selected model: a dated model id it does not expose, and a quantization pin its endpoints do not advertise. Both would have blocked every release run. Replaced by an always-pinned endpoint tag plus a dated model id where available, with quantization required only where variants exist. The price ceiling approved during Phase 0 planning was withdrawn — it was read off the flex tier and would have rejected every standard endpoint — and is deferred to the Phase 0.5 capability test, which also measures whether flex is viable before the tier is fixed.

## Resolved questions (Codex, `20260914-160024-CODEX-CLAUDE-PLAN_REVIEW.md`)

1. **Phase 0.5 is fully implemented before Phase 1** — schema, writer/reader, conformance test, viewer, action enum, and capability-contract test all complete there. Phase 1 implements the first domain emitters against that finished contract.
2. **Use the role-to-model mapping object**, as the single unit carrying model, prompt version, JSON Schema, provider policy, retry policy, and budget per role.
3. **The capability-contract test runs before every `eval release`**, not only on a detected change. It is a small preflight, and it catches capability regressions and provider-side changes that comparing static manifests cannot see. Its result is recorded in that release's manifest.

## Still open

- **`provider.max_price` ceilings — deferred to Phase 0.5.** No ceiling may be hard-coded from a documentation page. The capability test resolves an eligible non-batch endpoint, records its live price, and only then is a ceiling proposed for user approval at or above that price.
- **Config file placement** — resolved: `configs/llm.yaml`, separate, with its own `llm_contract_version`, folded into the single `config_hash`. See `phases/PHASE-00.md` D-P0-2.

## Architecture amendment, revision 5 (user-approved)

Two runtime-reproducibility rules from revision 4 were unsatisfiable for the selected model and would have made every release run impossible. Found while verifying `/api/v1/models/google/gemini-3.5-flash-lite/endpoints` during Phase 0 planning; raised by `phases/PHASE-00.md` revision 3 and confirmed by Codex in `20260914-161517-CODEX-CLAUDE-PHASE_REVIEW.md` as architecture decisions rather than phase exceptions.

| Rule in rev 4 | Why it fails | Rule in rev 5 |
| --- | --- | --- |
| Model ids pinned in dated form, never an alias | `google/gemini-3.5-flash-lite` exposes no dated variant — the API returns the bare slug. `google/gemini-3.8-flash-20260902` does have one, which is why the rule looked universal. | Endpoint tag always pinned; dated model id additionally required wherever the model exposes one. |
| `provider.quantizations` pinned non-empty | All eight endpoints report `quantization: unknown` — first-party Google endpoints with no variants. | Required only where endpoints advertise real variants; otherwise the endpoint tag carries determinism. |

The user chose the **stricter** reading of the replacement rule. An earlier draft allowed a dated model id *or* an endpoint tag, either alone. That leaves both holes open: pinning only the model still lets OpenRouter route among eight endpoints with different prices, latency, and capacity behaviour, while pinning only the endpoint does not stop an alias moving to a new model version. The two pins cover two different drifts, so release mode requires both wherever both are available.

## Phase plans

This architecture plan does not authorize implementation on its own. Each phase requires its own reviewed plan in `phases/` before work begins.

| Phase | Plan | Status |
| --- | --- | --- |
| 0 — Skeleton and contracts | `phases/PHASE-00.md` | drafted, awaiting user approval |
| 0.5 — Trace & observability contract | `phases/PHASE-00-5.md` | not started |
| 1 → 9 | `phases/PHASE-NN.md` | not started |
