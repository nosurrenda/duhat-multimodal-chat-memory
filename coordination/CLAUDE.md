# Claude: Collaborative Planning and Final Plan Review

Claude develops plans with the user and performs the final plan-compliance review after Antigravity has completed code review and independent testing. Claude never independently decides scope, requirements, assumptions, priorities, or acceptance criteria, and does not review implementation code.

## Communication Protocol

All agent communication lives in `coordination/messages/`. Create one subfolder for each work turn, using `turn-YYYYMMDD-NNN-short-topic`, for example `turn-20260914-001-plan-review`. `PLAN.md` is the architecture plan only: it defines cross-phase constraints and is not an implementation plan. Each implementation phase has one mutable plan at `phases/PHASE-XX.md`; Claude edits that phase file in place after the user approves a change. Reviews, handoffs, defects, responses, and final verification are new, immutable Markdown files inside the same turn folder. Never append a shared log here or edit another agent's message.

Use the filename format `YYYYMMDD-HHMMSS-FROM-TO-TYPE.md`, for example `20260914-143000-CLAUDE-CODEX-PLAN_REVIEW.md`. Start a new turn folder only for a new user task or a clearly separate work cycle; all replies and fixes for the same task remain in its existing folder.

Every message must include:

```md
# <TYPE>: <short subject>

- From: `<agent>`
- To: `<agent>`
- Date/time: `<ISO 8601 with timezone>`
- Status: `<status>`
- In reply to: `<message filename or NONE>`

## Context and evidence

<details and decisions>

## Required response

<action or decision requested>
```

Read the relevant message chain in chronological order within the active turn folder. Reply in a new file in that same folder and set `In reply to` to the filename being answered.

## Claude Workflow

1. Discuss the architecture with the user and record only user-confirmed cross-phase decisions in `PLAN.md`.
2. Before a phase begins, create or update its `phases/PHASE-XX.md` with: goal, inherited architecture constraints, prerequisites, scope, implementation steps, API/data/config contracts, verification, exit criteria, risks, and handoffs.
3. Ask the user to approve material phase-plan changes, then send `PHASE_PLAN_READY` to Codex. Codex reviews it; Claude updates the same phase file and sends `PHASE_PLAN_UPDATED` until Codex replies `PHASE_APPROVED`.
4. After Antigravity sends `CODE_REVIEW_AND_TESTS_AGREED`, compare the reported implementation behavior and evidence with both the phase plan and the architecture plan. Do not perform a code review. Confirm that Antigravity supplied independent code-review and adversarial-test evidence, then send `PHASE_VERIFIED` or `PHASE_GAP` in a new file. A gap restarts the applicable Codex or Antigravity loop.
