# Changelog

All notable changes to the Herdr Role Pack will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Fixed
- Installers write the real pack version (from `manifest.json`) into each managed skill's marker instead of a hardcoded `v2.8`.
- `tools/update.py` compares installed versions with the latest release: reports "Up to date" and does nothing unless `--force` is given.

### Planned
- Native Herdr Signals and Relay transport evaluation once upstream becomes available.
- Extended token billing metrics comparison between Markdown and SQLite state backends.

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
