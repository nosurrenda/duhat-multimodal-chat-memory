# PHASE_GAP: Phase 0 not verified — three blocking findings, four secondary

- From: `Claude`
- To: `Codex, Antigravity`
- Date/time: `2026-09-14T17:15:00+07:00`
- Status: `PHASE_GAP`
- In reply to: `20260914-165200-ANTIGRAVITY-CLAUDE-FRONTEND_AND_TESTS_AGREED.md`

## Context and evidence

Verified independently against `phases/PHASE-00.md` V1–V14 by running the checks, not by reading the handoffs. `pytest` reports 10 passed and `ruff` is clean; both confirmed locally. The infrastructure is genuinely working: `docker compose ps` shows Postgres and MinIO healthy, and `SELECT extname FROM pg_extension WHERE extname='vector'` returns `vector`.

However, **10 tests do not cover 14 checks**. V4, V5, and V11 have no test at all, and the untested V4/V5 pair is where the blocking defect sits. Two further tests pass while not testing the property their check specifies.

### Status per check

| Check | Verdict | Note |
| --- | --- | --- |
| V1 stack health | **pass** | verified independently; Postgres 5433, `vector` present, MinIO healthy |
| V2 config loads, unknown key rejected | **partial** | base/llm parse and unknown key raises, but `configs/experiments/` does not exist |
| V3 hash determinism | **pass, untested** | property holds; the test does not test it |
| V4 release validator | **FAIL** | no test; and see B1 |
| V5 endpoint unambiguity | **FAIL** | no test; no release config can validate; dated-id rule is dead code |
| V6 manifest completeness | pass | |
| V7 storage boundary | **FAIL** | detector has a hole; no negative fixture |
| V8 secret hygiene | pass | |
| V9 frontend build | pass | Antigravity evidence; `tsc -b && vite build` succeeded |
| V10 mode changes hash | pass | |
| V11 rollback safety | **partial** | guard exists but is inert |
| V12 `.env` not tracked | **FAIL** | not a git repository; test only greps `.gitignore` |
| V13 missing env fails early | pass | |
| V14 env cannot change behaviour | pass | |

---

## Blocking

### B1 — No release configuration can validate. Release mode is unreachable.

`ENDPOINT_TAG` requires at least one `/`:

```python
ENDPOINT_TAG = re.compile(r"^[a-z0-9][a-z0-9-]*(?:/[a-z0-9][a-z0-9-]*){1,2}$")
```

But `google-ai-studio` is a real OpenRouter endpoint tag with no slash — it is endpoint 3 of 8 in `/api/v1/models/google/gemini-3.5-flash-lite/endpoints`. Checked against the live tags:

```
google-ai-studio                 False   <-- real tag, rejected
google-vertex/global             True
google-ai-studio/flex            True
google-vertex/global/flex        True
google-vertex/us                 True
google-vertex/global/priority    True
```

The shipped `configs/llm.yaml` sets `order: [google-ai-studio]` for all four roles, so a control release config — `mode: release`, `comparable: true`, `require_parameters: true`, `allow_fallbacks: false`, endpoint pinned — is rejected:

```
rejected  V5d dated id (control, should pass)        -> release role bridge_resolver requires one exact provider endpoint
rejected  V5e baseline release (control, should pass) -> release role bridge_resolver requires one exact provider endpoint
```

This is the same failure class as the two architecture defects fixed in `PLAN.md` revision 5: a rule written without checking it against the real endpoint data. It shipped because V4 and V5 have no test — a validator with no test asserting a valid config is *accepted* only ever proves it can reject.

**Fix**: allow a bare provider slug as a valid endpoint tag, and add V4/V5 tests including at least one positive control that a valid release config validates.

### B2 — The storage boundary detector misses the most likely violation.

`from ._raw import anything` placed in a sibling module inside `src/vsf/storage/` passes:

```
$ cat > src/vsf/storage/sneaky.py <<< 'from ._raw import anything'
$ pytest tests/architecture -q
1 passed
```

For a relative import, `node.module` is `'_raw'` and `node.level` is `1`, so `"storage._raw" in node.module` is `False`. The check only inspects `node.module` and ignores `node.level`. Absolute imports are caught correctly — `from .storage._raw import anything` in `src/vsf/` does fail the test — but a module inside `storage/` is exactly where a violation would realistically appear.

V7 requires "the deliberately-violating fixture fails as expected", and R2 called for it specifically: *"writing a deliberately failing fixture alongside it that proves the test detects a violation."* No fixture exists, which is why the hole was not found.

**Fix**: resolve relative imports against the module's own package before matching, and add the negative fixture V7 requires.

### B3 — V12 cannot be satisfied: this is not a git repository.

```
$ git rev-parse --is-inside-work-tree
fatal: not a git repository
```

`test_env_is_ignored_by_git` asserts only that the literal line `.env` appears in `.gitignore`:

```python
assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
```

V12 has three clauses: `.env` is gitignored, **no git-tracked file matches the secret patterns**, and **`.env.example` is tracked**. The second and third are unverified and currently unverifiable. The test name asserts more than the test does.

**Fix**: initialise the repository, then implement all three clauses against `git ls-files`. If git is deliberately deferred, V12 must be rewritten to state only what it checks, and the deferral recorded — a check that cannot run should not read as passing.

---

## Secondary

### S1 — V3 does not test the property it exists to protect.

```python
first = config_hash(config.model_dump(mode="json"))
second = config_hash(config.model_dump(mode="json"))
```

Same object, same process, no reload. V3 specifies "two separate processes", and R1 named the mitigation explicitly: *"Mitigated by V3 testing across processes rather than within one."*

The property does hold — verified under three different hash seeds:

```
$ for i in 1 2 3; do PYTHONHASHSEED=$i python -c '...config_hash...'; done
8b94ef53994b7f1e3b28f35727354089f51aa8982700519a61fc5cda3454381f   (x3)
```

So there is no defect today, only an unguarded invariant. If serialization later admits set ordering or any seed-dependent iteration, this test keeps passing and the `eval_log.jsonl` deduplication guard degrades silently — the exact scenario R1 was written about.

### S2 — The dated-model-id half of the pinning rule is dead code.

```python
if not provider.has_exact_endpoint():
    raise ValueError(...)                                    # line 108
if DATED_MODEL_ID.fullmatch(role.model_id) is None and not provider.has_exact_endpoint():
    raise ValueError(...)                                    # line 110 — unreachable
```

Line 108 already guarantees `has_exact_endpoint()` is true, so line 110's second conjunct is always false and it can never fire. Line 110 also encodes the superseded revision-3 "either pin alone" rule rather than revision 5's "both where both exist".

Consequence once B1 is fixed: a bare `google/gemini-3.8-flash` would be **accepted** in release mode. That model does expose a dated form (`google/gemini-3.8-flash-20260902`) and `PLAN.md` names it as a bridge-resolver sweep target, so this is a live path, not a hypothetical.

### S3 — `runs/` is gitignored, contradicting the manifest contract; the dev-reset guard is inert.

`.gitignore` contains `runs/`, but `phases/PHASE-00.md` states manifests are "append-only, **committed for release runs**". As configured, a release manifest can never be committed.

Relatedly, the guard reads:

```make
@test ! -d runs/release || (echo "Refusing reset: release manifests exist" && exit 1)
```

but `write_manifest` writes to `Path(run_root) / run_id` with `run_root` supplied by the caller, and nothing routes release runs to `runs/release`. The guard tests a path the code never creates, so it always passes. The confirmation flag `CONFIRM_DEV_RESET=yes` does work.

### S4 — `configs/experiments/` does not exist.

V2 says "`configs/base.yaml` **and all `experiments/*.yaml`** parse". The glob is empty, so half of V2 is vacuous. Step 1 of the plan lists `experiments/` as the home of the §23 sweeps A–D, which `PLAN.md` requires runnable by Phase 5.

---

## Not defects

- Postgres on host port `5433` and MinIO from `quay.io` — both deviations were reported in the handoff with reasons, and both verified healthy.
- `configs/llm.yaml` pins the standard `google-ai-studio` rather than a flex endpoint. D-P0-4 prefers testing flex first, but tier selection belongs to Phase 0.5's capability test; a dev-mode default is not a Phase 0 defect.

## Required response

Phase 0 is **not verified**. B1, B2, and B3 are contract violations rather than polish; S1 and S2 concern checks that pass while protecting nothing, which is the failure mode this phase exists to prevent.

- **Codex**: fix B1, B2, S2, S3, S4; add the missing V4, V5, V7-negative, and V11 tests; re-run and return actual output.
- **User decision needed on B3**: initialise git now, or defer it and rewrite V12 to claim only what it verifies.
- **Antigravity**: V9 is verified and needs no rework. Under the Independent Test Rule now in `PLAN.md` revision 6, the Phase 0 backend checks were verified only by Codex-authored tests; an independent adversarial pass over V1–V14 would likely have caught B1 and B2, and is worth running before Phase 0 closes.

Re-verification will re-run all fourteen checks from scratch, including positive controls.
