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
2. **Governed Worker Test-Execution Ban:**
   A Worker in a governed run must never execute test suites, linters, builds, or package managers. A direct standalone Worker may run bounded, authorized self-checks and must label them self-verification; independent testing remains with the Delegator or a dedicated `herdr-verifier`.
3. **Serial Heavy Verification Lock:**  
   Only **one** expensive local verification command may execute across the machine at any time. Verification commands must acquire the run lock (`active_verification_lock`) before execution.
4. **Git Candidate Fencing & Provenance:**  
   Before tests begin, the Delegator freezes the integration branch. In SQLite runs, the cherry-picked commit must produce the exact same `git patch-id` as the worker's original branch commit.
5. **Decoupled Human Progress:**  
   Machine IDs (`T01`, `T01-A2`, Herdr pane IDs) must never be presented as the primary status to human operators. Always use descriptive slice titles and observable outcomes.
6. **Dual Backend Mutual Exclusion:**  
   Live runs use Delegator-owned `SESSION.md`. Explicit SQLite binding creates a planning-only candidate; live activation remains gated. Transfer covers pristine planning checkpoints only. The two backends must never operate on the same run.
7. **Safe Cancellation & Recovery:**  
   Cancellation must stop future scheduling, observe actual process termination via Herdr CLI, and preserve dirty worktrees and human-owned panes.

### Unproven Paths

Passing tests prove the mocked contracts, never a live end-to-end path. [UNPROVEN.md](UNPROVEN.md) lists
what is not demonstrated, gated, or blocked, and what would prove each. Do not present a listed path
to a human operator as working, and update the file whenever a gate is cleared or a new gap is found.

---

## 3. Local Development & Testing

### Prerequisites
- Python 3.10+ (Standard library only; no pip dependencies required for core tools). CI runs 3.10 (the floor) and 3.13 (newest) on Linux and Windows.
- Node.js 18+ (Optional, for `npm test` and `npx .`).
- Windows PowerShell 5.1+ or POSIX shell (sh/bash).

### Running Tests
Execute the full automated test suite (the same set CI runs):

```bash
# Using npm:
npm test

# Using Python directly:
python -m unittest tests/test_ledger_pilot.py tests/test_pack.py tests/test_access_probe.py tests/test_backend_transfer.py tests/test_sqlite_lifecycle.py tests/test_verification_guard.py tests/test_progress_view.py tests/test_package_and_update.py
```

`npm run test:ci` is the platform-independent CI mirror: the suite, the Node installer dry-run, and
the deterministic packaging step. Packaging writes `dist/ci-test/`, which is git-ignored. The
Windows PowerShell and POSIX shell installer dry-runs are platform-specific and stay in the
workflow.

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

### Regenerating the Command Reference

`skills/herdr-engineering-loop/references/command-reference.md` is generated from the ledger CLI parser and role maps, and a test fails if it drifts from them.

```bash
python tools/render_reference.py          # rewrite it (UTF-8, LF, no BOM)
python tools/render_reference.py --check  # exit 1 when it is stale
```

Do not regenerate it by shell redirection: on Windows PowerShell `>` re-encodes the stream as UTF-16LE with CRLF and corrupts the file.

---

## 4. Versioning Policy

- **Single Canonical Version:** The package version is declared in [manifest.json](../manifest.json) and mirrored in [package.json](../package.json).
- **Commit Message Format:** Every commit subject is `type[module]: message`, where `type` is the kind of change (`feat`, `fix`, `docs`, `test`, `ci`, `chore`, `refactor`) and `module` is the area touched (e.g. `update`, `installer`, `ledger`, `release`, `delegator`). Examples: `fix[update]: compare installed version with latest release`, `docs[readme]: correct pane setup`. **Do not create pseudo-version bumps for routine commits.**
- **Protected `main`:** Changes reach `main` only through a pull request with both CI checks (`Test & Validate (ubuntu-latest)` and `(windows-latest)`) passing; force-pushes and deletion are blocked. Required checks are matched by name, so do not rename the `test` job or give it a `python-version` matrix dimension — extra interpreters belong in the informational `Python Floor (…)` job. Only repository admins can create, move or delete `v*` release tags.
- **Releases are Explicit:** Published releases are created only via annotated Git tags (`vX.Y.Z`) on reviewed commits. See [RELEASING.md](RELEASING.md) for full instructions.
