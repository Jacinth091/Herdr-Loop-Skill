# Recovery and Escalation

Do not restart work blindly after a terminal crash, power outage, exhausted model quota, uncertain permission dialog, or Herdr reconnect. Recover the already-owned run, not a new copy.

## Recovery order

1. Load `<primary-repo>/.herdr-loop/<run-id>/SESSION.md` and the referenced `PLAN.md`. If either is missing, inspect Git worktrees and Markdown task/report files before reconstructing the ledger. Never claim it existed when it did not.
2. Observe `herdr status`, `herdr workspace list`, `herdr agent list`, and `herdr worktree list`. Compare actual pane/workspace IDs and agents to the ledger; IDs belong to one server and can change across re-creation.
3. Observe `git worktree list --porcelain`, each worktree's `git status --short`, current branch, base and HEAD. Do not reset or delete an unknown or dirty checkout.
4. Reconcile **task responsibility versus active attempt** first: an attempt has its own name/pane/worktree, while the task stays the same. Confirm that no two active attempts claim one writable worktree, and preserve the reports/commits of every replaced attempt. For each `assigned` or `working` task, inspect worker-local `.herdr-loop/REPORT.md`, commit history, and `herdr agent get/read` if the agent is live. If the report is valid, collect it even if the pane disappeared; do not duplicate the task.
5. For each `reported` task, compare archived report commits with integration branch history. Integration records must state source and destination SHAs. If a cherry-pick may have applied before a crash, compare diffs and ancestry before retrying.
6. For each `verifying` task, confirm the frozen candidate full SHA, checkout and write owner; invalidate checks if code/config/test inputs changed during verification. Inspect the lock owner, target SHA and any still-running test process before releasing a stale lock. Mark interrupted verification `not-run` or `incomplete`, never `pass`; preserve partial logs and exact tested SHA. Distinguish pending latest-head CI from earlier successful local checks.
7. For `CANCELLING`/`CANCELLATION_BLOCKED`, do not resume scheduling. Reconcile each run-owned process, unfinished report, dirty worktree and lock; preserve evidence. A human must authorize resumed execution. Inspect `SESSION.md` cleanup ownership before closing panes or deleting directories: human-created workspaces and verifier panes are outside run-owned cleanup even after a test succeeds. Resume from the first verified incomplete state. Escalate missing source commits, conflicting changes, or destroyed worktrees to the manager/human with observable evidence.

## On lost signaling or access denied

Reconcile the Worker-local `READY.md`, `REPORT.md`, `ROLE.md` recipient, and observed Herdr agent name/pane with the ledger before reactivating. A new agent process must repeat `ROLE_READY`. Preserve task-progress commits even if the signal never reached the Delegator. Record a missing or failed event as `SIGNAL_TRANSPORT_UNAVAILABLE`, not completion. If a worker was not acknowledged/start-authorized, do not consider its edits eligible for integration without explicit Manager/human reconciliation. See [Signal and Receipt Contract](signals.md) and [Access-Denied Preflight](access-triage.md).

For `Access is denied`, isolate the exact failing resource (Herdr socket, worktree, Agy/Codex sandbox, Docker pipe, WSL, unrelated OS file). For the same authorized in-scope command blocked only by the current harness, request permission through that agent's own tool when it offers native approval, then verify the postcondition. Explicit denials, OS restrictions and unavailable approval remain blockers. Never request admin, disable a sandbox or widen ACLs; keep unrelated roles running only if independent and safe.

## Escalation rules

- **Ambiguous specification or architecture change:** Stop worker, ask manager for a decision, update `PLAN.md` revision, then issue a revised brief.
- **Agy permission or sandbox restriction:** Do not widen privileges or escape sandbox automatically. Request human review or switch to a permitted execution mode.
- **Worker timeout / unknown status:** Do not launch repeated polling loops or send routine status messages. Do not assume failure or success. Inspect Markdown and Git. Prompt once only when no active work or already-delivered response is present.
- **Dirty worktree on cleanup:** Preserve it, record `cleanup-blocked`, report exact dirty files to delegator/human.
- **Cherry-pick conflicts:** Record exact conflicts, stop integration, choose correction from task intent and approved repo architecture; do not discard either side mechanically.
- **Failed tests:** Record command, exit status, failure evidence; send minimum repair brief to the worker who owns the implementation. Avoid rerunning every test during each repair cycle.
- **Out of memory / stalled machine:** Stop starting agents or tests; reduce to one worker, then zero during verification, and recover from durable reports. Human-approved parallel workers (up to three) do not imply parallel local verification. Do not start multiple simultaneous verification processes to make up for slowness.
- **Quota exhausted:** Stop assigning new workers, preserve brief/report/commit evidence, state what's done and blocked. No fake background progress.

- **Unsafe input boundary:** If the worker root contains sensitive nested archives or unrelated files, halt that Worker. With human/Manager approval, create a minimal non-code-only isolated copy, preserve before/after evidence, and review exact intended application before touching the real target.
- **New HEAD after docs-only change:** Do not automatically rerun expensive local checks or silently relabel earlier test evidence as new-HEAD results. Record Manager applicability decision and still await required current-head CI.

## Future transport integration

The one-line `REPORT_READY` envelope and Markdown contracts are transport independent. If Herdr Signals or Relay becomes available and the user chooses to enable it, adapt only notification delivery and acknowledgements. Do not replace the plan, Git evidence, or report contracts with opaque transport state.
