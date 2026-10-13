# Decision: SQLite for the Herdr workflow, phased rollout

**As of October 12, 2026:** adopt SQLite as the **recommended future live coordination ledger**, but do not replace the current Markdown-based execution process yet. The v2.8 download includes a self-contained offline pilot; live operation remains on the v2.7-compatible Markdown state path until the gates below pass.

**Implementation checkpoint, October 13:** Role binding can explicitly create a planning-only SQLite run with a selected-backend record. Omitted `state_backend` still selects Markdown. SQLite role activation fails closed. Backend transfer is implemented for pristine planning checkpoints only (no approved plan or grant, no tasks, claims, verification, decisions, or activation receipts), with dry-run, human approval bound to a source fingerprint, an immutable archive, a journaled two-phase cutover, and `recover`. Any other state is refused. The live tracer, complete role instructions, and a real Windows Herdr run remain cutover gates, and the default stays Markdown. The test fixture is not live-run proof.

## What the uploaded archive established

The S3 private-media run has a 131,147-byte `SESSION.md`, 245 Markdown files consuming 1,547,842 bytes, and a six-file cumulative CR-03 critique chain totaling 300,416 bytes. Each successive CR-03 file includes the entire preceding file as a prefix. These are verified **disk sizes**, not invoices or measured language-model token usage.

The core inefficiency is not Markdown as a document format. It is repeatedly asking an LLM to express routine state transitions, clone earlier reviews, and reconcile multiple descriptions of the same status. **A database only saves tokens if the agents use named deterministic actions and read compact targeted results.** Moving long prose into JSON or free-form SQL would not fix it.

## What was added safely in v2.8

- A bundled Python/SQLite CLI on the installed `herdr-engineering-loop` controller skill, with a versioned per-run database.
- Typed operations for run/task/attempt, atomic idempotent transition receipts, a claim-based checkout lock, clean Git candidate freezing, recorded verification outcomes, exact-candidate acceptance with decision evidence, bounded read-only recovery and descriptive progress output.
- Fail-closed checks for replay conflicts, source revision drift, dirty candidates, evidence path traversal, wrong claimed Herdr identity, requested-versus-observed model/reasoning mismatch, and concurrent checkout writers.
- A testable offline workflow where `SESSION.md` is **not** created. Attempting to initialize the pilot in a run already containing `SESSION.md` is refused. Installed role binder and live role skills continue using the old state path by default.

The CLI does not spawn Herdr agents, receive real Signals, enforce human identity, independently authenticate Manager decisions, execute product tests, or create an OS security boundary. A pilot with supplied observation fields is not proof that the underlying process performed those actions.

## Cutover gates remaining

| Gate | Requirement | Status |
|---|---|---|
| Source inspection | Inspect current installed agent skills, Herdr CLI/help, signal APIs and actual launch behavior on the user's Windows machine | CLI help and read-only agent metadata checked; disposable launch pending (see `docs/SQLITE_WINDOWS_GATE.md`) |
| Live tracer | Observe Herdr spawn, `ROLE_READY`, task start, correlated report, integration, critique, verify and acceptance with bounded SQLite receipts | Not done |
| Missing lifecycle | Implement decisions, incremental critique findings, cancellation, ambiguous recovery and human export without a second state authority | Not done |
| Identity and permissions | Bind observed pane+process+model+worktree to attempt; never auto-elevate on Windows denial | Offline structural checks only |
| Windows resource safety | Exercise locks, busy DB, paths, real Git worktrees and permissions on the user's machine | Mocked/fixture paths tested; real role run pending |
| Efficiency evidence | Measure input/output tokens or comparable prompt-volume proxies per accepted task, and verify no quality-gate regressions | Historical file counts only |
| Migration | New-run opt-in after above gates; old runs remain read-only and are never silently promoted from prose | Deferred |

## Rejected alternatives

Do not require Relay; create a daemon/server; replace Herdr panes; introduce an additional permanent state.json or JSON event store; maintain both SQLite and live `SESSION.md`; turn a raw `REPORT_READY` into acceptance; skip independent verification; or assume an old clean SHA proves a changed candidate.

## Recommended next step

Use the offline pilot's Git-backed unit tracer as a starting test fixture. Extend only one real Herdr implementation attempt with verified pane/session signaling, then add independent candidate verification and manager decisions in the supported live transport. Do not switch `role_bind.py` or Delegator defaults until that complete path passes on Windows and state conflicts are ruled out.
