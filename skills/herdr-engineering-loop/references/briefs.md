# Markdown Contracts

Use small, explicit, agent-readable files. Copy the precise brief into each worker's assigned isolated workspace because a sandbox may not see files outside that root. Treat `SESSION.md` as the Delegator-owned **recovery ledger**, not a second plan or raw output log; preserve evidence in reports and Git, not chat transcript fragments.

## Human-facing task mapping

Keep stable `Txx`/attempt identifiers internally while mapping each approved slice to a **descriptive title and observable outcome**. The Manager authors those descriptions in PLAN.md, the Delegator records them in SESSION.md and `TASK.md`, and user-facing messages use the description rather than bare IDs. Never guess titles or treat a worker report as acceptance.

## `SESSION.md` (delegator owns)

```markdown
# Herdr Run: <id>
Status: planned | executing | verifying | blocked | ready-for-manager | accepted | cancelling | cancelled | cancellation-blocked
Repo: <absolute path>
Base: <branch> @ <sha>
Integration: <branch> @ <sha> in <worktree path>
Plan revision: <id>
Execution grant: <human instruction/reference or NOT AUTHORIZED>
Approved intent: <goal, constraints, non-goals, acceptance>
Resource budget: max active implementation workers <1-3 as human authorized>; max concurrent heavy local verification jobs 1; remote CI separately tracked
Manager: <name> / <kind> / <pane id>
Delegator: <name> / <kind> / <pane id>
Verification lock: none | <task / owner / pane / exact tested SHA / command>

## Task frontier
| Task | User-facing title | Expected outcome | Depends on | Status | Worker / pane | Workspace or worktree | Branch | Source commit(s) | Integrated commit(s) | Report / Manager verdict | Local verification + tested SHA | Latest-head CI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| T01 | Improve session-expiry messages | Explain recovery when a session expires | none | ready | - | - | - | - | - | - | pending | pending |

## Attempts and write ownership
| Task/attempt | Herdr agent/pane | Worktree | State | Prior attempt/evidence |
| --- | --- | --- | --- | --- |
| T01-A1 | <name/pane> | <path> | <working/stopped> | <report/commit or none> |
Worktree write owner: <attempt and path; no shared writable checkout>
Candidate: <integration checkout + full SHA + expected changes>
Candidate writer state: <OPEN | FROZEN>
Cancellation: <none | CANCELLING/CANCELLED/CANCELLATION_BLOCKED, approver, remaining processes>

## Decisions and exceptions
- <decision id>: <short content; owner; date or plan revision>

## Recoverable ownership
- Workspaces/panes created by this run and cleanup result: <IDs>
- Human-owned workspace/panes not to close: <IDs>
- Pending REPORT_READY receipts, decisions, CI checks and blocked states: <details>
```

Only report `accepted` when the required acceptance and latest-head verification/CI gates are complete. A Manager may accept content while checks are still pending, but preserve that difference in the ledger. Do not claim older exact-SHA checks ran at a newer HEAD; document any Manager-authorized carry-forward of unaffected checks.

State vocabulary: `ready`, `assigned`, `working`, `reported`, `integrated`, `verifying`, `verified`, `repair-needed`, `blocked`, `content-accepted-verification-pending`, `accepted`, `cleanup-blocked`. When work is aborted, record the reason explicitly. Never advance from `working` to `integrated` just because Herdr says `done`.

## Signal readiness and receipt ledger

Each role packet names a **specific signal recipient**; `ROLE_READY` is mandatory on initial activation. For Workers the Delegator must inspect a worker-local `.herdr-loop/READY.md` and observe `ROLE_READY` **before** sending `TASK_START`. No task implementation begins with the first activation prompt. For Verifier, require `VERIFY_START` and the one-at-a-time test lock. Use [Signal and Receipt Contract](signals.md). Do not create a new Signals runtime; the transport is a Herdr named-agent prompt with Markdown receipts in `SESSION.md`.

### `.herdr-loop/READY.md` (worker owns, before any edits)

```markdown
# Worker readiness: <run> / <task>
Agent / role: <name> / worker
Herdr pane / workspace: <observed IDs>
Assigned cwd / worktree: <path, code branch and SHA>
Signal destination: <actual Delegator agent name>
Transport: herdr agent prompt
Workspace-local write probe: pass | blocked (minimal redacted evidence)
Ready signal: submitted | failed (not a claim of receipt)
Awaiting: TASK_START from Delegator
```

### SESSION signal receipts (Delegator owns)

```markdown
## Signaling and readiness
- T01 / worker_t01: role_ready=observed | missing | blocked; pane=<id>; worker_ready_md=<path>; authorized_start=<TASK_START event or pending>
- T01 / report_ready=observed | pending | blocked; report=<path>; source_sha=<sha>; receipt key=<run|task|event|sha>
- T01 / verifier_ready=observed | pending | blocked; verification_sha=<sha>; lock=<owner>
```

Do not equate a successfully submitted prompt with an observed receipt. Avoid repeated blind retries; reconcile event keys and source artifacts before any resend.

## `PLAN.md` (manager owns)

```markdown
# Plan <run-id> / Revision <N>
## Goal and user-visible outcome
<What must work, and what is explicitly outside scope>
## Current evidence
<Spec/issue, code paths, branch/head, repo standards>
## Architecture and decisions
<Smallest owning seam, invariants, risk, concrete decision IDs>
## Slices in dependency order
### T01 <specific user-facing slice title>
Expected user-visible outcome: <observable result; use in SESSION.md and task brief>
Blocked by: None
Scope: <expected owning areas, non-goals>
Accept when: <observable behavior and boundary cases>
Verification to schedule: <specific checks, not for workers>
### T02 ...
Blocked by: T01
## Final acceptance gate
<Which behavior, evidence, test statuses, and manager sign-off are needed>
## Open decisions
<What the manager or human still needs to decide>
```

## `TASK.md` (delegator sends to worker)

```markdown
# WORKER BRIEF: <run-id> / T01
User-facing title: <approved descriptive slice title>
Expected outcome: <observable user-visible result>
Role: implementation worker only (Agy, Codex Luna or Claude Haiku 5.5)
Attempt: <T01-A1, unique across replacements>
Priority: <P0/P1/P2>; blocked by: <task id or none>
Workspace type: git-worktree | non-code-isolated-copy (exception requires Manager/human approval in SESSION.md)
Assigned workspace: <absolute worker path or narrowly staged document copy>
Branch/base: <branch> from <sha> | none (approved non-code copy)
Manager plan: <path or sufficient copied excerpt>
Goal: <one demonstrable vertical slice>
Owned files/seams: <paths or subsystems>
Do not touch: <paths, contracts, other workers' areas>
Acceptance: <concrete behavior; edge cases; no unnecessary extras>
Constraints: <compatibility, smallest safe change, architectural decisions>
Signal recipient: <exact registered Delegator agent name from ROLE.md>
Readiness first: write .herdr-loop/READY.md and send ROLE_READY; DO NOT edit code until explicit TASK_START.
Report file: .herdr-loop/REPORT.md inside THIS worktree

Instructions:
- Stay in this checkout and assigned slice. Do not open new worktrees or agents.
- In a governed Worker assignment, never run tests, lint, typecheck, builds, installs, CI, or deployment steps. Standalone self-checks follow the direct assignment and `herdr-worker` contract.
- First acknowledge role/signal route with READY.md + ROLE_READY. Begin implementation only on TASK_START with plan revision.
- Implement, inspect the diff, and make scoped local commit(s) for code; for an approved non-code isolated copy, report reviewed diff/readback and no Git commit. No push.
- Write REPORT.md with changed files, exact commit SHA(s), assumptions, risks,
  suggested verification commands, and "Tests run: NOT RUN (by role policy)".
- Signal the delegator once if possible; if unavailable, leave the report intact.
- Escalate architecture/spec ambiguity instead of guessing or expanding scope.
```

The delegator may specify a read-only diagnosis or research task instead of implementation. In that case require no commit, and distinguish `No code changes` from a failed attempt. Do not instruct a researcher to fabricate commit IDs.

## `REPORT.md` (worker owns)

```markdown
# WORKER REPORT: <run-id> / T01
Outcome: done | blocked | needs-decision | partial | cancelled
Attempt: <T01-A1>
Agent: <name/kind/pane>; workspace: <path>; branch: <branch or none for approved non-code copy>
Base SHA: <sha>
Commits: <sha(s)> | none (approved non-code copy or documented reason)
Changed files: <short list>
Behavior implemented: <summary>
Scope deviations: none | <explicit details and authorization ID>
Decisions needed: none | <specific question>
Risks and known gaps: <explicit details>
Tests run: NOT RUN (by role policy)
Suggested verification: <smallest useful command(s) and expected result>
Cleanup ready: yes | no (why)
```

## `verifications/T01.md` (delegator or verifier owns)

```markdown
# INTEGRATION AND VERIFICATION: <run-id> / T01
Source branch/commit(s): <branch, sha(s)>
Integration branch/head: <branch, sha>
Diff scope reviewed: <what was checked>
Status: pass | fail | blocked | incomplete | not-run
Verification lock: <acquired/released; owner; tested SHA>
Latest HEAD: <sha>; CI status: <pass/fail/pending/not-required with evidence>
Earlier test evidence carried forward: <original SHA, changed-path proof, Manager decision> | none
Checks, in execution order:
- `<exact command>`: PASS | FAIL | NOT RUN; exit=<code or n/a>; evidence=<output/log path>
Conflict/repair record: <details or none>
Manager decision requested: <accept | reject | revise plan; supporting reason>
```

## Compact signal envelope (current Herdr prompt, not a native Signals API)

```text
ROLE_READY <run-id> <agent-name> worker <pane-id> <task-id>
TASK_START <run-id> <task-id> <accepted-plan-revision>
REPORT_READY <run-id> <task-id> <absolute-report-path> <source-sha-or-none>
```

Use the envelope in the worker-to-delegator Herdr prompt when available. Report and Git artifact are still the source of truth; the message is only a wake-up hint. Later, Relay/Signals may transport the same envelope without changing the Markdown contracts.

## `ROSTER.md` + role packets (pack v2)

`ROSTER.md` maps each live agent **name** to exactly one role and kind, its Herdr workspace/pane and optional worktree/task. It is generated by `tools/role_bind.py` from `examples/roles.example.json`; treat any generated IDs as initial hints until checked against Herdr. `SESSION.md` records the **observed** state and updates it after every task transition.

Every agent must receive an explicit activation prompt referencing its native skill:

- Claude: `/herdr-manager`, `/herdr-delegator`, `/herdr-worker`, `/herdr-verifier`.
- Codex: `$herdr-manager`, `$herdr-delegator`, `$herdr-worker`, `$herdr-verifier`.
- Agy: `/herdr-worker` (or other explicitly assigned skill).

Workers must receive **copied local files** under their own worktree `.herdr-loop/ROLE.md` and `.herdr-loop/TASK.md` because a strict sandbox may be unable to read the canonical run directory.

## `VERIFY.md` (delegator sends to optional verifier)

```markdown
# VERIFICATION BRIEF: <run-id> / T01
Role: verifier (not implementation worker)
Integration worktree: <absolute path>
Integration branch and exact SHA: <values>
Verification lock: <task id / delegator grant>
Environment preflight: <disposable/non-production target confirmed; reuse existing approved images/services if available; one-time setup requires authorization>
Commands in strict serial order:
1. <exact targeted command>
2. <broader command, if required>
Expected evidence: <exit code / test suite / output>
Constraints: no product edits, no concurrent test processes, no auto-installs
Report: <run-dir>/verifications/T01.md
```

`VERIFICATION.md` may include `INCOMPLETE` when interrupted, and `BLOCKED` when the environment prevents meaningful checks; neither counts as PASS. Always preserve exact command and Git SHA.

## Evidence, trust and notification rules

- The run-specific `SESSION.md` is **mutable** and remains Delegator-owned. Never let a skill installer, `role_bind.py --force`, worker, or Verifier overwrite it. Keep task frontier and owned resources concise; use linked evidence files for verbose logs.
- Allow up to three independent implementation Workers only when the human expressly authorizes the larger RAM budget. This never raises the one-heavy-local-verification-at-a-time limit. Remote CI is observed independently and is not a local job.
- Record content acceptance and verification/CI as independent states. Evidence is keyed by immutable Git SHA. Following a docs-only change, earlier local checks may remain relevant only with explicit Manager decision and changed-path proof; they are not new-HEAD checks. Any required current-HEAD CI remains pending until observed.
- For a non-code-only document task, the Manager/human may approve `non-code-isolated-copy`: the Delegator creates a restricted input copy with only target and brief/role documents, launches a named Herdr agent in its own pane, and reviews the returned diff against the real target. Source SHA is `none` by design; the actual applied destination commit/readback becomes the integration evidence. Never use this path for executable source code.
- Before exposing files to Agy, account for nested archives, old patches, credentials and other secret-bearing material. Do not trust the full directory merely because a top-level listing appears safe. If narrow isolation cannot be established, block rather than disabling the sandbox.
- Notifications are wake-up hints: `REPORT_READY`, explicit decisions, major failures and human-requested checkpoints. Avoid routine Manager status spam, polling/sleep loops and duplicate re-prompts. If Signals/Relay later exists, preserve the same durable contracts.

## Permission and environment blocker evidence

On any `Access is denied`, `EPERM`, WinError 5, Docker named-pipe or Herdr socket failure, load [Access-Denied Preflight](access-triage.md) if present. Store the sanitized operation, effective role and actual pane, resource class, code, and environment-vs-product classification in the appropriate report or `SESSION.md`. For an authorized in-scope command blocked only by the current harness, use its native approval tool if offered and verify the result; explicit denial or OS restriction remains a blocker. Never elevate, disable Agy sandbox, alter ACLs, or switch providers to bypass a deny. Workers never run tests to probe Docker.
