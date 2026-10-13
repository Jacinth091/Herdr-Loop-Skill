# Unreleased - Gated SQLite continuation

- Added explicit v1-to-v2 SQLite backup/upgrade, Herdr agent and pane process observation before attachment, correlated readiness/start/report receipts, Manager-gated retries with fresh attempt IDs, patch-matched candidate provenance, a run-wide heavy-test lock, captured verification commands, Manager decisions, incremental critique findings, cancellation fencing, and read-only status export.
- Kept Markdown as the active role-pack authority and preserved installed skill copies. Live Herdr receipt processing, interrupted-test reconciliation and Windows cutover gates remain open.

# v2.8.0 - Transactional SQLite ledger prototype (offline only)

- Inspected the supplied `.herdr-loop.rar` and confirmed redundant, cumulative operational Markdown in a real run. The existing v2.7 `SESSION.md` system remains the live default.
- Bundled a Python-standard-library **opt-in offline** CLI in the installed controller skill (`scripts/herdr_ledger.py`). One fresh run uses one SQLite database, named legal transitions, exact Git candidate identity, resource fences, idempotent event receipts, compact queries and descriptive human status.
- Refuse to initialize the pilot beside an existing `SESSION.md`, and leave all historical runs untouched. No automatic Herdr interaction, model changes, agent spawning, signals, deployment or new authority.
- Added a focused pilot workflow reference, independent transaction/locking/dirty candidate/replay tests and a controller/delegator adoption guard.
- Deferred decisions, incremental findings, cancellation, true Herdr signal/attempt correlation, Windows acceptance, historical export, and token billing measurement to separately validated cutover phases.

# v2.7.0 - Human-friendly task progress with internal routing IDs

- Internal `Txx` / `Txx-Ay` keys remain stable for task frontier, signals, retries, Git mapping and reports.
- Every approved slice now has a specific **user-facing title** and an **observable outcome**, authored from the Manager plan and recorded in `SESSION.md` and `TASK.md`.
- User updates default to descriptive work + evidence-grounded status, not task/pane/attempt IDs or raw signal names. Show IDs only when explicitly requested for debugging.
- Added an optional read-only `tools/progress_view.py` that renders the existing `SESSION.md` task frontier as a human-facing summary, with no additional state store. Incomplete descriptions are surfaced as errors, not fabricated.
- Added backward-compatible `task_title` / `task_outcome` role config fields and validation; role packets, activation and roster include the mapping. Legacy configs still bind but need an approved description before work starts.
- Added offline coverage for labels, missing labels, status translation and identifier hiding.

# v2.6.0 - Lean run authority, attempts, candidate fencing and cancellation

- Borrowed only useful ideas from the user's Herdr/Relay context pack. **No Relay or persistent-memory dependency**, new daemon, database, or general ticket engine.
- Added human execution-grant tracking (an existing explicit request to execute counts; planning-only does not), separate from Manager plan approval.
- Added stable task versus disposable attempt identity (`T01` versus `T01-A1/A2`); optional backward-compatible `attempt_id` in worker configs, generated packets, roster and activation.
- Documented exclusive write ownership per worktree and frozen candidate SHA/checkout during serial verification; preserve exact tested evidence and docs-only applicability decisions.
- Added a procedural `CANCEL_RUN`/`CANCEL_ACK` route; prevent new dispatch, preserve partial commits/reports, reconcile process stops and safeguard human-owned panes. Cancellation never counts as acceptance.
- Added a short conditional run-integrity reference and offline validation tests. Existing `SESSION.md` is preserved during binding and installation.

# v2.5.0 - Dedicated Claude Haiku tester

- Default a `claude` Verifier to `claude-haiku-5-5` in generated role packets while allowing a different explicitly selected capable Verifier.
- Keep testing inside existing `herdr-verifier`, distinct from the `herdr-worker` implementation role; do not invent a new skill.
- Require independent Herdr identity/pane, active model confirmation, `ROLE_READY`, `VERIFY_START`, exclusive serial verification lock and exact-SHA evidence for a Haiku tester.
- Include a sample Haiku tester roster, controller/delegator/verifier guidance and offline tests.

# Herdr Role Pack changelog

## v2.4.0

- Added `herdr-critic`, a separate read-only Claude Sonnet 5.5 role, with a `CRITIQUE_START`/`CRITIQUE_READY` Markdown handoff.
- Added Claude Haiku 5.5 as an implementation Worker option. Worker test prohibition and separate worktree/pane requirements remain unchanged.
- Recommended GPT-6 Luna (`gpt-6-luna`) with `xhigh` reasoning for Codex implementation Workers, or explicitly approved `max` for harder tasks.
- Routed Codex CLI worker model/effort override creation to a **non-Codex** launcher. A Claude Delegator launches directly; a Claude Manager has a narrowly scoped spawn-only exception when the Delegator is Codex; two Codex leaders require a human or reassigned non-Codex operator.
- Added `SPAWN_REQUEST`/`SPAWN_READY` receipts and required confirmation of the *effective* model/effort before `TASK_START`, not just `herdr agent get` registration.
- Added role-packet/model fields and examples for Codex Luna and Claude Haiku. The installer now includes all six skills.
- Preserved the existing live `SESSION.md`, worker readiness, signaling, sandbox safety, and serial local verification rules.

## v2.3.0

- Classified access-denied failures instead of auto-elevating, weakening sandbox, or bypassing Herdr.
- Required `ROLE_READY`, directed signal recipients, `TASK_START` and `VERIFY_START` gates, and durable receipts.
