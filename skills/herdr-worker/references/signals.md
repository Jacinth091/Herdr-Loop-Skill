# Signal and Receipt Contract (current Herdr, without Relay)

**Read this on role activation and again before completing a task.** There is no assumed `herdr signal` command or installed Relay/Signals bridge. Current transport is a **named Herdr agent prompt**; the durable artifact (`READY.md`, `REPORT.md`, verification or decision file) remains authoritative. Herdr `idle`/`done`, a notification toast, and a successful `agent prompt` submission are **not** proof that the receiver processed a handoff.

## Directed routes (role names resolved from the run's ROLE.md / role packet)

| Sender | Recipient | One-line message | Durable artifact |
| --- | --- | --- | --- |
| Any newly activated role | Delegator (Delegator -> Manager) | `ROLE_READY <run> <agent> <role> <pane-or-unknown> <task-or-none>` | Worker-local `.herdr-loop/READY.md`, or recipient ledger for Manager/Delegator |
| Delegator | Manager (only Claude Manager when Delegator is Codex) | `SPAWN_REQUEST <run> <task> <request-path>` | `SPAWN-REQUEST.md` exact model/effort and pane/worktree |
| Claude Manager | Delegator | `SPAWN_READY <run> <task> <receipt-path>` | `SPAWN-RECEIPT.md` observed agent/model/effort |
| Delegator | Critic | `CRITIQUE_START <run> <review-id> <plan-revision> <fixed-point-sha>` | Read-only `CRITIQUE-BRIEF.md` |
| Delegator | Worker | `TASK_START <run> <task> <plan-revision>` | Accepted `TASK.md` and recorded readiness receipt |
| Worker | Delegator | `REPORT_READY <run> <task> <report-path> <sha-or-none>` | Worker-local `REPORT.md` + source commit/diff |
| Critic | Delegator | `CRITIQUE_READY <run> <review-id> <report-path> <fixed-point-sha>` | `critiques/<review-id>.md` |
| Verifier | Delegator | `VERIFY_READY <run> <task> <report-path> <tested-sha> <PASS|FAIL|BLOCKED|INCOMPLETE>` | `verifications/<task>.md` |
| Worker/Verifier | Delegator | `BLOCKED <run> <task> <blocker-file> <blocker-code>` | `REPORT.md` or verification report |
| Delegator | Manager | `DECISION_NEEDED <run> <task> <decision-request-path>` or `MANAGER_REVIEW_READY <run> <task> <integration-report-path>` | Decision request or integration report |
| Manager | Delegator | `DECISION_READY <run> <task> <decision-path>` | Signed-off `PLAN.md` revision / decision / verdict |
| Delegator | Run-owned agents | `CANCEL_RUN <run> <reason-or-decision-path>` | `SESSION.md` cancellation decision and preserved current state |
| Worker/Verifier/Critic | Delegator | `CANCEL_ACK <run> <agent> <attempt-or-task> <partial-report-or-none>` | Partial report and observed process stop (not prompt delivery alone) |

**Do not infer recipients from the CLI kind.** Agent names, not `claude`/`codex` kinds, are the primary targets. If a named agent isn't registered, consult the *observed* recorded pane ID; never guess a target or deliver a message to a different session.

## Mandatory worker startup barrier

1. Delegator provisions a real Herdr workspace/pane and code worktree (or approved non-code isolated copy); copies the exact `ROLE.md` and `TASK.md` into the worker-local `.herdr-loop/`.
2. Delegator starts the named agent, validates `herdr agent get <worker>`, and sends a **setup-only** activation prompt. Do not include "implement now" in this first prompt.
3. Worker reads its local role, task, run ID and **Delegator's exact agent name**; confirms pane/workspace/worktree provenance. Perform only a harmless write probe inside the assigned `.herdr-loop/`, create `READY.md` with `run, task, role, pane, cwd, signal recipient, transport=herdr-agent-prompt, observed_model, observed_effort_or_not_applicable`, and send **one** `ROLE_READY` prompt to the Delegator.
4. Delegator verifies the worker-local `READY.md` and the directed `ROLE_READY` receipt (or explicitly reports a transport blocker), then logs `signal-ready` plus worker provenance in `SESSION.md`. **Only then** sends `TASK_START` with accepted plan revision. Worker must not edit product files before receiving this go-ahead.
5. A Verifier uses the same readiness contract, but `TASK_START` is replaced by an explicit `VERIFY_START` plus the one-process verification lock and approved `VERIFY.md`. Never run tests merely because the Verifier has acknowledged its role.

`ROLE_READY` should happen **once per process/session assignment**, not on every tool call. On a role change or a fresh agent process, repeat it. A role that does not understand the recipient, transport, or event formats is **not ready**.

## Send, receive, and recover

- Sender: finish writing the artifact **before** signaling; use `herdr agent get <recipient>` then `herdr agent prompt <recipient> "<envelope>"`. Quote Windows paths containing spaces as part of the one text argument. Do not run a signal command from outside an authorized Herdr pane.
- Receiver (Delegator for Workers/Verifier; Manager for Delegator): inspect the artifact and Git evidence, record `observed|missing|blocked` receipt keyed by run + task + event + source SHA in `SESSION.md`. A delivery command exit 0 means **submitted**, not accepted/processed.
- On `timeout`, `agent_prompt_stalled`, `agent_blocked` or `Access is denied`, inspect one current Herdr state and whether the artifact already exists **before** attempting any resend. Do not spam retry loops, repeated prompts, sleeps or background watchers. Duplicate envelopes must not cause duplicate cherry-picks or tests.
- If transport remains unavailable after the active tool's supported permission flow was denied or unavailable, preserve the report and `SESSION.md` blocker (`SIGNAL_TRANSPORT_UNAVAILABLE` / `SIGNAL_RECIPIENT_UNAVAILABLE`). Ask Delegator/human for a decision; **never** claim a signal was received. File-only continuation requires explicit human authorization, and no unacknowledged governed Worker should start product edits.
- Herdr `notification show` is a UI toast, **not** cross-agent report delivery. Future native Signals/Relay can replace only the transport, keeping these artifacts, routes, receipt semantics and authorization barriers intact.

## Model and critique receipts

Model-overridden Codex worker creation has a separate `SPAWN_REQUEST` / `SPAWN_READY` round trip when the Delegator itself is Codex. This does not replace `ROLE_READY` or `TASK_START`. Verify the requested **and observed** model/effort before authorizing work. A read-only Sonnet 5.5 Critic receives `CRITIQUE_START` only after readiness and returns `CRITIQUE_READY` with the evidence-linked report; Manager retains acceptance authority. See the `model-routing.md` reference in the Controller/Manager/Delegator/Worker/Critic roles.

## Cancellation is not completion

A `CANCEL_RUN` envelope requests a safe stop; it is not an automatic process interrupt or proof of receipt. Record a `CANCEL_ACK` only after the agent has stopped its own work and preserved artifacts. Delegator still independently confirms Herdr/process status and only then releases locks/leases. Cancellation never triggers `REPORT_READY`, `VERIFY_READY`, or Manager acceptance automatically. A missed cancellation prompt must be escalated and reconciled without polling storms.
