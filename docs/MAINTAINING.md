# Maintainer Guide for Herdr Role Pack

This document outlines the internal architecture, development workflow, testing standards, and design invariants for contributors and maintainers of the **Herdr Role Pack**.

---

## 1. Repository Layout & Architecture

The repository is structured to ensure that agents receive strictly bounded instructions, state is deterministically managed, and installation is zero-dependency across platforms:

```text
herdr-role-pack/
├── manifest.json              # Canonical package definition and policy constraints
├── package.json               # Zero-dependency npm / npx metadata
├── bin/
│   └── install.js             # Cross-platform Node.js installer
├── install.ps1                # Native Windows PowerShell installer
├── install.sh                 # POSIX shell installer (Linux / macOS)
├── skills/                    # Managed agent skills
│   ├── herdr-engineering-loop/# Session controller skill & SQLite ledger engine
│   ├── herdr-manager/         # Architectural lead skill
│   ├── herdr-delegator/       # Dispatcher and integrator skill
│   ├── herdr-worker/          # Task implementer skill (strictly no tests)
│   ├── herdr-verifier/        # Dedicated sequential tester skill (no edits)
│   └── herdr-critic/          # Independent read-only reviewer skill
├── tools/                     # Operational utilities (Python 3 standard library)
│   ├── role_bind.py           # Role packet generator and backend binder
│   ├── activate_roles.py      # Live Herdr prompt dispatcher
│   ├── progress_view.py       # Human-facing status presenter
│   ├── access_probe.py        # Diagnostic & permission tester
│   └── package.py             # Deterministic release packager & archive validator
├── tests/                     # Unit test suites and fixtures
│   ├── test_ledger_pilot.py   # SQLite ledger state machine tests
│   ├── test_pack.py           # Role pack invariants and binder tests
│   ├── test_progress_view.py  # Human progress formatter tests
│   └── fixtures/              # Migration SQL and test assets
└── docs/                      # Maintainer and release documentation
    ├── MAINTAINING.md         # This developer guide
    └── RELEASING.md           # Tagged release and publishing pipeline
```

---

## 2. Core Invariants (Do Not Regress)

When modifying any skill or script, you must preserve these architectural invariants:

1. **No Codex Internal Subagents:**  
   Never allow Codex's native `spawn_agent`, agent teams, or background threads to replace Herdr-visible workers. All workers must run inside registered Herdr panes with verified process identity.
2. **Worker Test-Execution Ban:**  
   `herdr-worker` must never execute test suites, linters, builds, or package managers. Testing is exclusively reserved for the Delegator or a dedicated `herdr-verifier`.
3. **Serial Heavy Verification Lock:**  
   Only **one** expensive local verification command may execute across the machine at any time. Verification commands must acquire the run lock (`active_verification_lock`) before execution.
4. **Git Candidate Fencing & Provenance:**  
   Before tests begin, the Delegator freezes the integration branch. In SQLite runs, the cherry-picked commit must produce the exact same `git patch-id` as the worker's original branch commit.
5. **Decoupled Human Progress:**  
   Machine IDs (`T01`, `T01-A2`, Herdr pane IDs) must never be presented as the primary status to human operators. Always use descriptive slice titles and observable outcomes.
6. **Dual Backend Mutual Exclusion:**  
   Live runs use Delegator-owned `SESSION.md`. The `ledger.sqlite` engine is an offline CLI pilot only: `tools/role_bind.py` refuses `state_backend: "sqlite"`. The two backends must never operate on the same run.
7. **Safe Cancellation & Recovery:**  
   Cancellation must stop future scheduling, observe actual process termination via Herdr CLI, and preserve dirty worktrees and human-owned panes.

---

## 3. Local Development & Testing

### Prerequisites
- Python 3.10+ (Standard library only; no pip dependencies required for core tools).
- Node.js 18+ (Optional, for `npm test` and `npx .`).
- Windows PowerShell 5.1+ or POSIX shell (sh/bash).

### Running Tests
Execute the full automated test suite:

```bash
# Using npm:
npm test

# Using Python directly:
python -m unittest tests/test_ledger_pilot.py tests/test_pack.py tests/test_progress_view.py
```

### Dry-Run Installer Checks
Before committing installer changes, verify dry-run behavior on your platform:

```powershell
# Windows PowerShell:
.\install.ps1 -DryRun

# Cross-platform Node.js:
node bin/install.js --dry-run

# POSIX Shell:
sh ./install.sh --dry-run
```

---

## 4. Versioning Policy

- **Single Canonical Version:** The package version is declared in [manifest.json](../manifest.json) and mirrored in [package.json](../package.json).
- **Commit Message Format:** Every commit subject is `type[module]: message`, where `type` is the kind of change (`feat`, `fix`, `docs`, `test`, `ci`, `chore`, `refactor`) and `module` is the area touched (e.g. `update`, `installer`, `ledger`, `release`, `delegator`). Examples: `fix[update]: compare installed version with latest release`, `docs[readme]: correct pane setup`. **Do not create pseudo-version bumps for routine commits.**
- **Protected `main`:** Changes reach `main` only through a pull request with both CI checks (`Test & Validate (ubuntu-latest)` and `(windows-latest)`) passing; force-pushes and deletion are blocked. Only repository admins can create, move or delete `v*` release tags.
- **Releases are Explicit:** Published releases are created only via annotated Git tags (`vX.Y.Z`) on reviewed commits. See [RELEASING.md](RELEASING.md) for full instructions.
