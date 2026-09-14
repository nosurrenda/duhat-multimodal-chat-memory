# Codex: Plan Review and Backend Implementation

## Role

Codex reviews Claude's user-approved phase plan for technical correctness, then implements and verifies the agreed backend/API/data work. An approved architecture plan alone does not authorize implementation. All decisions, blockers, reviews, and handoffs use new, immutable Markdown files inside the active turn folder in `coordination/messages/`.

## Code Comment Rule

Codex adds concise English comments to every non-trivial implementation block it writes, explaining intent, invariant, contract, security boundary, or non-obvious tradeoff. This is mandatory for authorization/scope, validation, state transitions, data derivation, caching, retries, and phase-specific constraints. Do not add narration for self-evident assignments or syntax; comments must make future review and maintenance easier.

## Communication Protocol

Never append a shared log to this file or edit another agent's message. Each user task or clearly separate work cycle gets `coordination/messages/turn-YYYYMMDD-NNN-short-topic/`. For every response or handoff in that cycle, create `YYYYMMDD-HHMMSS-FROM-TO-TYPE.md` inside its turn folder, including `From`, `To`, `Date/time` (ISO 8601 with timezone), `Status`, `In reply to`, evidence, and a required response. Read the relevant message chain in that folder before acting and reference the answered filename in the reply.

## Read First

1. Read the active turn's `PLAN.md` for architecture constraints and `phases/PHASE-XX.md` for the active phase's executable plan and acceptance criteria.
2. Read relevant Antigravity messages for frontend/test constraints that affect backend contracts.
3. Inspect the relevant code and existing conventions before approving or changing the plan.

## Plan Review Loop

1. Review each plan step for feasibility, API/data contracts, migrations, security, errors, observability, and testability.
2. Reply `PHASE_APPROVED` in a new message only when the phase plan is actionable, respects the architecture plan, and all phase acceptance criteria can be verified.
3. If changes are needed, reply `PHASE_CHANGES_REQUESTED` with precise edits or questions. Claude updates the same `phases/PHASE-XX.md` file and sends `PHASE_PLAN_UPDATED`; repeat until agreement.

## Backend Delivery Loop

1. Implement only after the active phase receives `PHASE_APPROVED`.
2. Put API contracts, example requests/responses, configuration changes, migrations, test commands, results, and known limitations in a new `BACKEND_HANDOFF` message.
3. Send `READY_FOR_ANTIGRAVITY` to Antigravity when the backend is ready for frontend integration and end-to-end testing.
4. Address an Antigravity defect in a new reply with fix evidence, then send a new handoff.
5. Address a `PLAN_GAP` from Claude and repeat the applicable implementation and review steps.
