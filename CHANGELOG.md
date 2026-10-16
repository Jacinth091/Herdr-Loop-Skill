# Changelog

All notable changes to the Herdr Role Pack will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- **Generated command reference:** `herdr_ledger.py --run-dir <run> reference` prints the per-role command lists and every command's exact arguments, derived from the CLI parser and role maps rather than hand-written prose; `reference --role <role>` prints just one role's list (about 1 KB for a verifier, versus 6 KB for the full reference). The full text is committed as `references/command-reference.md`, and a test fails if it drifts from the CLI. The duplicated command listings in `docs/OPERATIONS.md` and the controller `SKILL.md` are replaced by pointers to it.
- **`--json` reads with a read-cost proxy:** `status`, `next`, `task-show`, `events`, `recover` and `packet` accept `--json`; each payload carries `read_cost` (`text_bytes`, `approx_tokens`). Plain `status` also reports `view_bytes`/`view_tokens`, the measured cost of the detailed human view.
- **Ledger-composed receipts:** `role-confirm`, `attempt-ready`, `attempt-start` and `attempt-report` accept `--compose-receipt` instead of `--receipt`. The ledger writes the exact correlated envelope it already validates to `receipts/<kind>-<event>.receipt` (atomic), so agents stop authoring boilerplate while the on-disk evidence and SHA chain are unchanged. Exactly one of `--receipt`/`--compose-receipt` is required.
- **Bounded ledger reads (`packet`, `export --write`):** `herdr_ledger.py packet --role <role>` prints a compact, role-scoped next-step directive (state/epoch, the owning command or `none for role=...`, the role's allowed commands, ready/active tasks, open blockers) so agents stop re-reading a full status document. `export --write NAME.md` writes the existing non-authoritative view to one file inside the run directory; reserved names (`SESSION.md`, `ledger.sqlite`, …) and nested paths are refused, so canonical-ledger integrity is preserved.
- **Governed OpenCode support:** `tools/role_bind.py` now recognizes `opencode` as a known agent kind in every role and loads its skills as `@skill-id`; `tools/access_probe.py` classifies the harness; and `herdr_ledger.py attempt-reserve` accepts `--provider opencode`, so OpenCode can hold governed roles in both Markdown and SQLite runs. Approval capability stays `UNVERIFIED` until the installed CLI offers a native request. Observed model and effort come from the agent process arguments, so an OpenCode attempt is reserved without `--expected-effort`, and without `--expected-model` unless its launch passes an explicit `--model` (see `references/sqlite-live.md`).
- **OpenCode install target:** `install.sh`, `install.ps1`, `bin/install.js`, and `tools/update.py` now accept `opencode` as an agent, installing the role skills into the user-level `~/.config/opencode/skills` folder that OpenCode loads. `manifest.json` advertises `opencode` in `target_agents`.
- **Backend transfer for planning checkpoints:** `run_backend.py switch --to <markdown|sqlite>` moves a pristine planning run between backends. It requires `--dry-run` first, a human approval file that quotes the source fingerprint, an immutable archive, and a journaled two-phase cutover. `run_backend.py recover` finishes or rolls back an interrupted transfer. Runs with tasks, claims, verification, approvals, or edited Markdown are refused and left unchanged.
- **Portable reference regeneration:** `tools/render_reference.py` rewrites the committed `references/command-reference.md` as UTF-8 without a BOM and LF-terminated, and `--check` reports whether it is current; the generated header now points at it. Regenerating by shell redirection is unsafe (Windows PowerShell's `>` re-encodes the stream as UTF-16LE with CRLF and corrupted the file). A `.gitattributes` now pins text files to LF so a `core.autocrlf=true` checkout can no longer change what a byte-compared generated artifact contains.
- **One source of truth for the role list:** `tools/role_bind.py` now derives `SKILLS` and the `KINDS` role keys from `herdr_ledger.ROLE_ORDER` instead of restating them, so a role cannot be added to one and forgotten in the other; a test guards the derivation and checks each declared role's skill is actually installed.
- **`npm run test:ci`:** runs the platform-independent CI mirror locally — the unit suite, the Node installer dry-run, and the deterministic packaging step. Packaging writes `dist/ci-test/`, which is now git-ignored; the Windows PowerShell and POSIX shell installer dry-runs remain platform-specific and stay in the workflow.
- **Unproven paths are recorded in the repository:** `docs/UNPROVEN.md` lists what is not demonstrated end to end, gated, or blocked, and what would prove each; `docs/MAINTAINING.md` points to it and requires it to be updated when a gate is cleared or a gap is found.

### Fixed
- **Reserve refuses an expectation the observer could never confirm:** `attempt-reserve` now rejects `--expected-effort` for every provider except Codex, because `attempt-attach` reads effort only from Codex's own `-c model_reasoning_effort=` argument. Such a declaration previously reserved cleanly and then failed at attach with `MODEL_OVERRIDE_NOT_APPLIED`; it now fails where the operator can still fix it, and the rejected expectation could only ever have been unconfirmable. Declaring no expectation is still accepted, so the change cannot reject a reservation that would previously have worked.
- **A test file now says what it tests:** `tests/test_live_sqlite_lifecycle.py` is renamed `tests/test_sqlite_lifecycle.py` (it is a mocked contract suite, not live Herdr evidence), and two assertions that pinned exact prose wording — a composed receipt's whole text and a sentence inside a generated prompt — were replaced by checks on the envelope fields they actually protect.
- **Unnamed pane agents now name their own remedy:** `observe_herdr` and `observe_recipient` refuse an agent that has no registered name with `HERDR_AGENT_UNNAMED`, naming the pane and the exact `herdr agent rename <pane> <agent-name>` fix (a pane that already hosts an agent is named, never re-started). Previously this dead end surfaced as a generic `HERDR_IDENTITY_MISMATCH` with no next step.
- **A cleared agent name is no longer reported as a transport failure:** a missing name now probes its pane before refusing. `herdr agent not found` for an agent whose name Herdr cleared (it clears a name when the agent exits, is released, or is replaced) was previously reported as `HERDR_TRANSPORT_UNAVAILABLE` with the reason truncated at 100 characters, because the CLI writes its error object to stderr and only stdout was parsed. Structured CLI errors are now parsed from either stream, so a name that is gone while its pane still hosts the agent is refused as `HERDR_AGENT_UNNAMED` with the exact `herdr agent rename <pane> <name>` fix, and a name whose pane is gone is refused as `HERDR_ERROR` with a start command instead.
- **CI now covers the declared Python floor:** the required `test` job runs 3.13 (newest), and a new informational `Python Floor` job runs 3.10 (the documented minimum) on Ubuntu and Windows. The required job keeps its exact name because protected `main` matches required checks by name. Previously only 3.11 ran, so both the floor the docs promise and the newest interpreter in use were unverified.

### Planned
- Native Herdr Signals and Relay transport evaluation once upstream becomes available.
- Extended token billing metrics comparison between Markdown and SQLite state backends.

---

## [2.8.2] - 2026-10-13

### Fixed
- **Preflight Rollback Integrity (AUTH-03):** Hardened `restore_backup()` in `tools/update.py` to verify that every original backup item exists on disk and matches its manifest SHA-256 before any filesystem mutation. Missing original backup files fail closed with `REFUSE`, preventing erroneous slot deletion.
- **Selective Role Update Preservation (FLOW-05):** In `tools/update.py`, updates now inspect each agent's installed roles and invoke `bin/install.js --agents <agent> --roles <roles>`, ensuring updates do not widen an existing minimal footprint to all 7 roles.
- **Rollback Candidate Filtering (FLOW-07):** In `tools/update.py`, `--rollback latest` and `--list-backups` filter strictly for directories containing valid `backup_manifest.json` files sorted newest first, avoiding incompatible Node backup folders and failing closed if no valid backups exist.
- **Engine-Aware Critic and Lead Prompts (FLOW-08):** In `tools/role_bind.py`, Critic prompts adapt to the agent engine (no Sonnet 5.5 demand for Codex/Agy Critics), and Lead spawner rules restrict cross-model Codex worker launching strictly to Claude Leads.
- **Lead Signal Transport Clarity (DOC-05):** In `tools/role_bind.py`, Lead role packets and activation manifests set `signal_transport='human-direct'` for direct console interaction with the human operator rather than invalid `herdr agent prompt the human` commands.
- **Strict Option Parsing (QUAL-03):** In `bin/install.js`, `--target-dir` validates that the argument is present, non-empty, does not start with `-`, and is an absolute path, failing fast with exit code 2.
- **State Backend Authority Clarity (DOC-03, ARCH-01):** In `skills/herdr-engineering-loop/SKILL.md`, clarified that runtime authority is Delegator-owned `SESSION.md` and SQLite is an isolated offline CLI pilot (`role_bind.py` fails closed with `SQLITE_BACKEND_NOT_LIVE`), and documented that heavy verification locking is a per-run ledger table in SQLite pilot and advisory serial coordination in Markdown.

---

## [2.8.1] - 2026-10-13

### Added
- **Lead role (`herdr-lead`):** a budget option where one agent holds both the Manager and Delegator duties. Bind it with `agents.lead` in `roles.json` (see `examples/roles.lead.json`); `role_bind.py` rejects mixing it with `manager`/`delegator` and still forbids any other doubled-up role. The Lead never implements or acts as Worker, Verifier or Critic; the human approves the plan before any worker starts and gives final acceptance. Installing the `lead` role also installs `herdr-manager` and `herdr-delegator`, which it builds on.
- **Install with any AI CLI:** [`INSTALL-FOR-AI.md`](INSTALL-FOR-AI.md) is a step-by-step guide an AI agent follows to install the pack for itself (find its skills folder, preview, install, verify, report back), with fallbacks for no Node.js and for tools without a skills feature. The README has a copy-paste prompt for it.
- **`--target-dir <dir>`** in `bin/install.js` installs into any skills folder, for CLIs other than Claude, Codex and Agy.

### Changed
- **Any CLI can hold a role.** `tools/role_bind.py` accepts any well-formed agent `kind` with a warning (shown on the console and in `ROSTER.md`); Claude, Codex and Agy keep their existing role rules.
- **The Critic model is a recommendation.** `claude-sonnet-5-5` stays the default for a Claude Critic, and another model only produces a warning; a Codex or Agy Critic is allowed.
- Skills label model names as defaults and Codex-specific rules as Codex-only, and state which rules apply to every CLI.

### Fixed
- Installers write the real pack version (from `manifest.json`) into each managed skill's marker instead of a hardcoded `v2.8`.
- `tools/update.py` compares installed versions with the latest release: reports "Up to date" and does nothing unless `--force` is given.

---

## [2.8.0] - 2026-10-13

> First published release. Versions 2.4.0-2.7.0 below are internal milestones that were never tagged or released.

### Added
- **Skill-First Documentation:** Rebuilt `README.md` as a user-focused practical manual detailing team roles, safety requirements, and worked session walkthroughs.
- **Maintainer Documentation:** Added `docs/MAINTAINING.md` and `docs/RELEASING.md` separating internal architecture, testing invariants, and release checklists from user guides.
- **Deterministic Packaging:** Added `tools/package.py` to produce verified release archives (`herdr-role-pack-vX.Y.Z.zip`, `herdr-controller-vX.Y.Z.zip`) with path traversal guards and `SHA256SUMS`.
- **Safe Explicit Updater:** Added `tools/update.py` for checking GitHub Releases, verifying asset checksums, creating pre-update backups in `~/.herdr-backups/`, and providing one-command rollback (`--rollback latest`).
- **Continuous Integration & Tag-Gated Releases:** Added `.github/workflows/ci.yml` (multi-OS testing on Ubuntu and Windows) and `.github/workflows/release.yml` (tag-gated publishing with version validation).
- **Cross-Platform Node.js Installer:** Added `bin/install.js` and `package.json` enabling one-command installation via `npx .` with atomic staging and rollback.
- **Transactional SQLite Ledger (Schema v2):** Added deterministic ACID state engine in `skills/herdr-engineering-loop/scripts/herdr_ledger.py` for offline evaluation and inspection; routine live runs use Markdown and role binding fails closed on SQLite.
- **Candidate Provenance & Test Lock:** Enforced Git `patch-id` matching on cherry-picked worker commits and serial heavy local verification locks.

### Changed
- Preserved Delegator-owned `SESSION.md` as the default backend for standard runs.
- Enforced mutual exclusion between `SESSION.md` and `ledger.sqlite` in `tools/role_bind.py`.
- Updated `tools/progress_view.py` to support querying both Markdown sessions and SQLite databases.

### Fixed
- `verify-reconcile` releases verification locks only after the recorded test PID is observed gone; unknown liveness keeps the locks.
- `tools/update.py` requires an exact `SHA256SUMS` entry and Node.js before touching anything, authenticates private-release access (token never forwarded to the asset CDN), updates only agents that already have the pack, and refuses rollback over unmanaged or locally edited slots.
- `bin/install.js` removes newly created slots when a fresh install fails part-way and reports incomplete rollbacks.
- Release workflow refuses tags whose commit is not on `main`.

---

## [2.7.0] - 2026-10-12

### Added
- **Human-Friendly Task Progress:** Decoupled internal routing keys (`T01`, `T01-A1`, Herdr pane IDs) from human status reporting.
- Added `tools/progress_view.py` for evidence-grounded status summaries.
- Added backward-compatible `task_title` and `task_outcome` fields in `roles.json`.

---

## [2.6.0] - 2026-10-11

### Added
- **Run Authority & Candidate Fencing:** Human execution-grant tracking separate from Manager plan approval.
- Added `attempt_id` tracking distinguishing stable tasks from disposable attempts (`T01` vs `T01-A1`).
- Added procedural `CANCEL_RUN` / `CANCEL_ACK` flow preserving uncommitted work and human-owned panes.

---

## [2.5.0] - 2026-10-10

### Added
- **Dedicated Claude Haiku Tester:** Defaulted `claude` Verifier to `claude-haiku-5-5` under the exclusive serial verification lock.
- Maintained strict separation: Verifiers never edit code, Workers never run tests.

---

## [2.4.0] - 2026-10-09

### Added
- Added `herdr-critic` read-only Claude Sonnet 5.5 role.
- Supported Claude Haiku 5.5 implementation workers.
- Recommended GPT-6 Luna (`gpt-6-luna`) with `xhigh` reasoning for Codex workers.
- Routed Codex CLI worker model/effort override creation through authorized non-Codex spawner.
