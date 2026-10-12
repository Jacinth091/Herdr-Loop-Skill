---
name: herdr-engineering-loop
description: Bootstrap, resume, and coordinate the human-directed Herdr role-pack engineering workflow. Use for Herdr session or workspace setup, manager/delegator/worker/verifier/critic role assignment, role packets, cross-agent handoffs, RAM-aware worktree operation, serial test gates, or run recovery. This is the session-controller skill, not a substitute for the separate herdr-manager, herdr-delegator, herdr-worker, herdr-verifier, or herdr-critic skills.
---

# Herdr engineering loop: session controller

Give each active agent exactly one role, a named pane, the corresponding installed role skill, and an explicit Markdown role packet. This skill controls **bootstrapping and recovery** only. Never make every agent follow this broad controller instead of its own role contract.

**Worker origin invariant:** A code Worker exists only when the Delegator provisions a Git worktree and Herdr workspace/pane, arranges its `herdr agent start` through the authorized launch owner, and confirms its registered name and pane. A human-approved `non-code-isolated-copy` exception still requires a separately registered Herdr agent and pane, but no Git worktree or fabricated commit. **Codex internal subagents (`spawn_agent`, agent teams, child threads, or similar Codex-native delegation) are forbidden for Manager, Delegator, Worker, Verifier and Critic roles.** They do not count as workers, reviewers, or verifiers. If Herdr creation is unavailable, report `BLOCKED: HERDR_WORKER_UNAVAILABLE`; never substitute hidden Codex agents.

## Role readiness, Signals, and access safety

- Load [Signal and Receipt Contract](references/signals.md) at every role activation. Each agent must know its assigned sender/recipient, handoff event, durable report file and `ROLE_READY` handshake. Before implementation, a worker must have a worker-local `READY.md`, an observed Delegator receipt, and a deliberate `TASK_START`. A Verifier needs `VERIFY_START` and a serial verification lock. Herdr status and toast notifications alone are not delivery.
- Load [Access-Denied Preflight](references/access-triage.md) on any permission error or when a spawned agent cannot reach Herdr. Classify the exact protected resource and source pane before escalating; **never automatically elevate, disable a sandbox, edit system ACLs, or switch to invisible Codex subagents**. A Docker pipe/WSL denial is an environment block, not a product-test failure.
- `SESSION.md` records named recipients, `ROLE_READY` receipts, pending `REPORT_READY`/`VERIFY_READY`, blocked transport, and human-owned panes. Explicit file-only fallback requires a human decision; never claim an undelivered signal was received.

## Role selection

1. Inspect the user's explicit assignment, then `.herdr-loop/<run-id>/ROSTER.md` and the agent's `role-packets/<agent-name>.md`. The explicit latest human assignment wins when the ledger is stale; reconcile the ledger before work.
2. If this agent is bound as Manager, Delegator, Worker, Verifier, or Critic, **activate the matching standalone skill** (`herdr-manager`, `herdr-delegator`, `herdr-worker`, `herdr-verifier`, `herdr-critic`) and follow only that role. Do not cross roles just because tools are available.
3. Use this session-controller skill to bootstrap an unassigned run, recover topology, or coordinate user-requested role changes. Do not infer a role from model identity: either Claude or Codex may be Manager or Delegator.
4. Only the human may authorize a role reassignment, final promotion, production action, destructive cleanup, or a change to the concurrency budget. Parallel workers above the default of one require an explicit accepted resource decision and independent scopes. A manager may make product architecture decisions inside delegated authority.

## Minimal operating procedure

1. **Discover before mutating.** Verify `HERDR_ENV=1` before controlling live local Herdr. Inspect `herdr --help`, `herdr workspace list`, `herdr tab list`, `herdr pane list`, `herdr agent list`, `herdr worktree list --cwd <repo>`, `git worktree list --porcelain`, and each relevant Git HEAD/dirty state. Do not pretend to control Herdr if access is unavailable. For CLI examples, load [Herdr operations](references/herdr-cli.md).
2. **Reuse topology.** Prefer the user's existing session, workspace, tab, pane and manually started agents. New `herdr workspace create` produces a tab and root pane; `herdr agent start` needs an already-empty shell pane. Record returned IDs, not guessed ones.
3. **Bind agents.** Assign one Manager (architecture/decisions) and one Delegator (task allocation/integration), both Claude or Codex. Optionally bind one active Agy, Codex Luna or Claude Haiku implementation Worker, an optional independent Sonnet 5.5 Critic, and a non-overlapping Verifier. Names and `kind` are not roles. Install each role skill through the supplied pack installer, then send each agent the prompt in its role packet. Roster and launch prompt are authoritative, not global availability of skills.
4. **Record state.** Treat `SESSION.md` as a recoverable execution ledger, not a second plan: preserve plan revision, source-to-integration SHA mapping, task dependencies, distinct content acceptance and verification/CI states, serial verification owner and SHA, run-owned versus human-owned panes, and pending handoffs. Default to `<repo>/.herdr-loop/<run-id>/` containing `ROSTER.md`, `SESSION.md`, `PLAN.md`, `role-packets/`, `tasks/`, `reports/`, and `verifications/`. Treat Markdown plus exact Git IDs as evidence. Keep run files untracked; do not commit them without an explicit decision. See [Handoff contracts](references/briefs.md).
5. **Begin flow.** Manager pins scope, accepted plan, and dependencies; Delegator selects an eligible task, creates/inspects a code-worker worktree and Herdr pane (or an expressly approved `non-code-isolated-copy` exception), **requests the authorized launcher to run Herdr `agent start` and verifies registration**, then sends `TASK.md`; Worker implements and returns `REPORT.md` plus commit; Delegator reviews, integrates, and arranges serial verification; Manager accepts/revises. Do not skip the manager return path.
6. **Recover rather than recreate.** On stalled workers, quota exhaustion, closed panes, or uncertain cherry-picks, load [Recovery](references/recovery.md) and reconcile Markdown, Git and Herdr before reissuing tasks.

## Model-aware Herdr spawn routing

Read [Model Routing](references/model-routing.md) before launching any worker or Critic. Codex Luna workers prefer `gpt-6-luna` at `xhigh`; human-approved difficult scopes may use `max`. Claude Haiku 5.5 may serve either as an implementation Worker **or as an optional dedicated Verifier/tester** (a separate Herdr agent, never both roles for one task). A read-only Claude Sonnet 5.5 Critic has its own `herdr-critic` role and `CRITIQUE_READY` report. A Codex Delegator **must not launch model-overridden Codex CLI workers**: hand that single Herdr launch to a Claude Manager, or a human/non-Codex operator if both leaders are Codex. A Claude Delegator launches directly. The Delegator always retains lifecycle/task/integration ownership. Model/effort MUST be checked in the actual agent before `TASK_START`; never silently use defaults or Codex subagents.

## What humans see

Read [Human-facing task presentation](references/user-status.md) before replying with task progress. **Do not lead with `T01`, pane IDs, agent IDs, or attempt numbers.** Summarize the exact slice in plain language, e.g. "Fix expired-session recovery: in progress". The Delegator maps this back to immutable internal task IDs in `SESSION.md`, PLAN and signals. Only show raw keys/SHAs on an explicit debug request. If a slice lacks an approved descriptive title/outcome, obtain them from the Manager rather than inventing a summary. When the full role pack is installed, `tools/progress_view.py` offers an optional read-only formatter. It is not part of this controller-only skill ZIP.

## Authority, attempts, and cancellation

Read [Run integrity](references/run-integrity.md) when opening a new run, recovering a replaced attempt, freezing a verification candidate, or cancelling. A human request to **execute** may already authorize the run; record that source in `SESSION.md` rather than asking for repetitive permission. A planning-only request is not an execution grant. Keep stable tasks separate from disposable agent attempts, one writer per checkout, and a frozen exact-SHA verification candidate. Cancelling requires stopping further dispatch, preserving evidence, accounting for each run-owned process, and never closing human-owned panes. Do not treat cancelled work as accepted.

## Transactional state: optional offline pilot only

The current live workflow still uses Delegator-owned `SESSION.md`. A separately packaged, **experimental offline** SQLite ledger is available for evaluating token-efficient state operations on brand-new throwaway runs; load [SQLite ledger pilot](references/sqlite-ledger-pilot.md) only for that explicit purpose. Do not convert existing runs, run two mutable state authorities, or use the pilot as live Herdr orchestration. It has no automatic Herdr Signals integration or execution authority. The full promotion gates in that reference must pass before any future live cutover.

## Constraints

- Resource defaults: one active implementation worker; a human-approved budget may allow up to three *independent, non-overlapping* implementation workers; one **total** active expensive local test/build/lint/typecheck/package-install process regardless of worker count; remote CI is tracked separately, not counted as a local process. Pause workers during heavy verification on low-RAM machines. The Delegator can run a short approved check; the preferred separate dedicated Verifier for bounded tests is Claude Haiku 5.5, or another capable explicitly assigned agent for more complex verification. A dedicated Verifier has its own Herdr pane and uses the same one-heavy-test lock; it does not consume an implementation-worker slot.
- Workers **do not** execute tests, builds, typechecks, linters, or package installations, **even if the Worker runs Haiku**. Only the Delegator or an assigned Verifier/tester (often a separate Haiku agent) runs approved checks serially in the integration checkout and records whether they passed, failed or were skipped.
- Use the official Herdr skill for exact live CLI syntax if installed. Inspect `herdr --help`; do not assume the packaged examples match every release. CLI commands without `HERDR_ENV=1` can be prepared as a handoff but not executed by this agent as session control.
- Agy's requested worker arguments are `--sandbox --dangerously-skip-permissions`. They are **not** a default for Manager/Delegator/Verifier. Confirm support, actual filesystem access and that no secrets or unsafe permissions are exposed; the bypass flag remains dangerous even with a sandbox. If editing is blocked by the sandbox, report the incompatibility rather than silently removing isolation.
- No auto-push, merge, force-delete/reset, production deployment, new dependencies, speculative schema changes, invented Relay execution, or invented Signals deliveries.
- Compose separate skills and existing repo conventions; do not turn this into a daemon, state database, custom agent runtime or extra ticket tracker.

## Signal, trust, and acceptance boundaries

- Report to Manager on `REPORT_READY`, decisions, blockers or explicitly requested checkpoints, not routine play-by-play. Do not use sleep/poll loops to chase completion; a bounded one-shot observation is acceptable when needed.
- Default to Git worktrees for code changes. For a narrowly approved documentation-only or offline-file task where exposing the original source would be unsafe, the Delegator may stage *only* the intended file and role/brief inside a `non-code-isolated-copy` workspace. Require a Herdr pane, an explicit exception in `SESSION.md` and `TASK.md`, and diff/readback before applying the change to the real target.
- Preserve both `content accepted` and `verification pending` as separate facts. Tests attach to an exact SHA; do not relabel older evidence as tests at a newer HEAD. Manager may expressly carry forward relevant unchanged-code checks after a docs-only change, but current-head CI remains a distinct gate.

## Handoff

Give the user the current run ID, assigned names and roles, Herdr workspace/pane IDs if observed, task/commit/integration state, actual verification evidence, and outstanding approvals. State separately what the session controller **could not** inspect or execute.
