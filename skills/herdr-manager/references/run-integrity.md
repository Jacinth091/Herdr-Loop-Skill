# Run integrity: authority, attempts, leases, cancellation

Read when starting/restarting a governed run, assigning a replacement worker, acquiring verification, or cancelling work. This is a procedure, **not** a Relay runtime or a second tracker. The Delegator keeps the current truth in `SESSION.md`; detailed proof stays in existing task, report, Git and verification artifacts.

## Human intent and execution grant

- The human owns objective, scope, privileges, concurrency budget and final promotion. The Manager turns this intent into an accepted `PLAN.md` revision; the Delegator may dispatch only its ready tasks.
- Record in `SESSION.md` the approved goal, required behavior, constraints, non-goals, acceptance and the source of authorization to **execute**. An explicit original request to start the work is sufficient: record it; do not demand a ceremonial second approval.
- If the user only asked to plan/review, mark `execution: not authorized` and do not spawn implementation workers. A skill file, manager proposal, or role packet cannot manufacture human consent for push, deploy, broad scope change, destructive cleanup or privilege changes.

## Stable task versus disposable attempt

- A task (`T01`) is one approved responsibility and dependency edge. An attempt (`T01-A1`) is the particular provider, named Herdr agent, pane, worktree and invocation used to execute it. A failed or quota-exhausted attempt does not change the task's goal, create an accepted result, or erase its evidence.
- On replacement, give the next attempt a fresh registered Herdr agent name/pane and record `T01-A2` with reason, prior source commit/report, start authorization and current status. Do not silently reuse stale agent/pane identities or count an abandoned attempt as successful.
- Use a compact **Active attempts** table in `SESSION.md` only when useful. Do not create one Markdown file per attempt. Avoid blind retries; repeated identical errors or a scope/architecture change require Manager/human resolution.

## Exclusive writers and immutable candidate

- Assign at most one active writing attempt to a particular Git worktree or isolated document copy. Record the write owner/path in the ledger. Different workers may run concurrently only in independent worktrees; no two workers write to the integration checkout.
- Before `VERIFY_START`, pin the integrated candidate's **full commit SHA**, checkout path and expected change scope. Mark verification lock owner and candidate writer state `FROZEN`. Pause writes that could alter this checkout or its test environment. A Critic can inspect read-only; a Verifier can run authorized checks without altering product code.
- If code/config/test inputs or the target SHA change during verification, mark the old run **STALE/INCOMPLETE** and require fresh applicable evidence. A documented **docs-only** successor SHA is the limited exception: keep original tested SHA and ask Manager to determine evidence applicability from the actual diff; never pretend tests ran at the new SHA. Required latest-head CI remains a separate gate.
- Release write and verification ownership only after observing stopped processes and preserving reports, commits, logs and artifacts. `REPORT_READY` alone is not a release or acceptance.

## Cancellation and safe stop

- A human may cancel any run; a Manager may request cancellation within its delegated authority. Record who authorized cancellation, reason, and affected tasks in `SESSION.md` before stopping new dispatch. Mark run `CANCELLING`, not `COMPLETE`.
- Delegator stops new work, sends one `CANCEL_RUN` to run-owned agents when available, and uses Herdr interruption only on those it owns. Workers/Verifier stop at a safe boundary, record partial outputs, and do not launch further processes. Inspect actual Herdr/Git state; a sent prompt alone does not prove a process stopped.
- Preserve all commits, reports, and dirty/unknown worktrees. Release verification/write locks only after confirming no related command/process is running. Clean disposable **run-owned, clean** worktrees/panes only under the existing cleanup authorization. Never close human-owned panes or delete branches by default.
- Record `CANCELLED` only when the affected run-owned activity has been accounted for; otherwise record `CANCELLATION_BLOCKED` and the remaining owner/process. No cancelled or incomplete result counts as accepted. Resumption requires a new explicit authorization and fresh reconciliation.
