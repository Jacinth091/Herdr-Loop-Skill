# Herdr Loop Skill

A structured, role-based workflow for coordinating autonomous coding agents (Claude Code, Codex, and Agy CLI) in [Herdr](https://herdr.dev).

Rather than relying on one unbounded mega-prompt or a complex external daemon, Herdr Loop Skill equips agents with **bounded, specialized role skills**. Agents collaborate inside their own Herdr workspaces and panes using isolated Git worktrees, sequential test gates, and explicit handoffs.

> [!WARNING]
> **Experimental hobby project.** This is a personal experiment for my own Herdr workflow, built largely with AI coding agents. It is shared as-is for anyone curious about the approach, not as a supported product.
>
> - **No stability promises:** roles, file formats and commands can change or break between versions.
> - **No support commitment:** issues and ideas are welcome, but I may not respond or fix them.
> - **Read before you run:** it installs agent instructions into your user skill folders and steers agents that edit code and run commands. Review the skills and try `--dry-run` first.
> - **Provided under the [MIT License](LICENSE), without warranty of any kind.**

---

## Table of Contents

1. [What is Herdr Loop Skill?](#1-what-is-herdr-loop-skill)
2. [Why Use It / Who Is It For?](#2-why-use-it--who-is-it-for)
3. [At a Glance: Team Roles](#3-at-a-glance-team-roles)
4. [Requirements and Safety Prerequisites](#4-requirements-and-safety-prerequisites)
5. [Install in One Minute](#5-install-in-one-minute)
6. [Your First Safe Session](#6-your-first-safe-session)
7. [Communication and State](#7-communication-and-state)
8. [Models and Worker Launching](#8-models-and-worker-launching)
9. [Concurrency and Verification](#9-concurrency-and-verification)
10. [Handling Blockers and Restarting](#10-handling-blockers-and-restarting)
11. [Updates, Compatibility, Rollback, and Uninstall](#11-updates-compatibility-rollback-and-uninstall)
12. [What is Supported Today?](#12-what-is-supported-today)
13. [Troubleshooting and FAQ](#13-troubleshooting-and-faq)
14. [Maintaining and Contributing](#14-maintaining-and-contributing)

---

## 1. What is Herdr Loop Skill?

**Herdr Loop Skill** is an installable instruction pack and toolset that turns multi-agent coding into a disciplined engineering team. Instead of asking one model to plan, code, test, critique, and self-certify its own work, the workflow divides responsibilities across specialized personas: a **Manager** who plans and accepts, a **Delegator** who briefs and integrates, **Workers** who write code in isolated worktrees, an independent **Critic** who reviews diffs, and a dedicated **Verifier** who executes tests.

The skill operates entirely through local Markdown packets, Git commits, and standard Herdr pane prompts. It is **not Herdr itself** (Herdr must be installed separately), **not Relay**, and requires no background services, cloud databases, or elevated machine permissions.

---

## 2. Why Use It / Who Is It For?

### Who it is for:
- Developers and students working on laptop-class hardware (e.g. 8 GB–16 GB RAM) who want to coordinate multiple coding agents without running out of memory.
- Teams coordinating disparate models (e.g. Claude Code for architecture, Codex Luna or Agy for implementation, Claude Haiku for fast test checks).
- Anyone needing auditable provenance: knowing exactly which agent wrote what commit, what tests ran at what SHA, and what the human authorized.

### When NOT to use it:
- **Trivial single-file changes:** If you just need a one-line bugfix, an ordinary single-agent prompt is faster.
- **No Herdr session:** The skill coordinates agents through Herdr panes and workspaces (`HERDR_ENV=1`); it does not manage processes outside Herdr.
- **Unattended auto-deployment:** The skill requires human authorization for execution grants, privileged resources, and final promotions; it will never automatically push to production or merge PRs.

---

## 3. At a Glance: Team Roles

| Role | Skill Name | Primary Responsibility | Strict Boundaries |
| :--- | :--- | :--- | :--- |
| **Manager** | `herdr-manager` | Architectural lead; approves `PLAN.md`, defines human-facing task titles, renders final acceptance verdicts. | Never writes implementation code, dispatches workers, or runs tests. |
| **Delegator** | `herdr-delegator` | Dispatcher & integrator; provisions worktrees, briefs workers, reviews commits, integrates code, manages serial test lock. | Never invents architectural requirements or bypasses test gates. |
| **Lead** | `herdr-lead` | Budget option: one agent does both the Manager and Delegator jobs (plan, dispatch, integrate, verify). Builds on `herdr-manager` + `herdr-delegator`. | Never implements or acts as Worker/Verifier/Critic. You approve the plan before any worker starts and give final acceptance. |
| **Worker** | `herdr-worker` | Task implementer; implements exactly one slice in an isolated Git worktree; writes source code and `REPORT.md`. | **Strictly forbidden** from running tests, builds, linters, typechecks, or package installs. |
| **Verifier** | `herdr-verifier` | Independent tester; runs approved sequential checks against the frozen integration SHA under a run-wide lock. | **Strictly forbidden** from editing application or test source files. |
| **Critic** | `herdr-critic` | Read-only reviewer; inspects plan briefs and candidate Git diffs against architectural intent; reports findings. | Read-only. Never tests, runs code, or edits files. |
| **Controller** | `herdr-engineering-loop` | Setup & bootstrap entrypoint; provisions topology, binds role packets, coordinates recovery. | Orchestration entrypoint; not a day-to-day engineering persona. |

> [!NOTE]
> Provider and model names (Claude, Codex, Agy) are interchangeable recommendations based on capability, not fixed identities. Roles are assigned dynamically per run via `roles.json`.

---

## 4. Requirements and Safety Prerequisites

1. **Herdr:** Installed and running. Live operations require an active session where `HERDR_ENV=1`.
2. **Coding Agent CLIs:** At least one supported CLI installed and authenticated:
   - Claude Code (`claude`)
   - Codex CLI (`codex`)
   - Agy CLI (`agy`)
3. **Git:** Repository must have clean Git worktree support.
4. **Permissions:** The skill runs under user permissions. **It never requires Administrator/root privileges and will never request sudo or disable sandboxes.**

---

## 5. Install in One Minute

The installer places the skills into your user-level agent configuration folders (`~/.claude/skills`, `~/.agents/skills`, `~/.gemini/antigravity-cli/skills`).

### One Command (no clone needed)

```bash
npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1
```

That's it: `npx` fetches the pack from GitHub and runs the installer. It backs up anything it replaces, rolls back if a copy fails part-way, and never touches skill folders it didn't install.

**Needs:** Node.js 16+. If the repository is private to you, you also need Git access to it (run `gh auth setup-git` once, or sign in through Git Credential Manager). Restart your agent CLIs afterwards so they load the skills.

**Pick what you want:**

| I want to... | Command |
|---|---|
| Install everything (all roles, all agents) | `npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1` |
| See what it would do first, changing nothing | `npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --dry-run` |
| Update an existing install to this release | `npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --force` |
| Try the latest unreleased `main` | `npx github:Jacinth091/Herdr-Loop-Skill --force` |
| Install for Claude Code only | `npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --agents claude` |
| Install for Claude Code and Codex only | `npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --agents claude,codex` |
| Claude as Manager + Critic | `npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --agents claude --roles controller,manager,critic` |
| Codex as Delegator | `npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --agents codex --roles controller,delegator` |
| One agent as Manager + Delegator (Lead), to save cost | `npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --agents claude --roles controller,lead` |
| Agy as a Worker only | `npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --agents agy --roles controller,worker` |
| Claude Haiku as Verifier | `npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --agents claude --roles controller,verifier` |
| Also install the official `herdr` skill (from `herdr --skill`) | `npx github:Jacinth091/Herdr-Loop-Skill#v2.8.1 --with-herdr` |
| Remove everything the pack installed | `npx github:Jacinth091/Herdr-Loop-Skill --uninstall` |
| Remove it from one agent only | `npx github:Jacinth091/Herdr-Loop-Skill --uninstall --agents agy` |
| See all options | `npx github:Jacinth091/Herdr-Loop-Skill --help` |

- **Agents:** `claude`, `codex`, `agy`, or `all` (the default).
- **Roles:** `controller` (the `herdr-engineering-loop` session skill), `manager`, `delegator`, `lead` (also installs `manager` and `delegator`, which it builds on), `worker`, `verifier`, `critic`, or `all` (the default). Keep `controller` in any role subset: it is the skill that sets up and coordinates the run.
- **Flags combine freely**, e.g. `--agents claude --roles controller,manager --dry-run`.
- **Pinning:** `#v2.8.1` installs exactly that published release. Leave it off to get whatever is on `main` right now. Replace it with a newer tag (see [Releases](https://github.com/Jacinth091/Herdr-Loop-Skill/releases)) to upgrade.
- **Already installed?** Without `--force`, existing pack skills are skipped. A folder with the same name that the pack did not install is never overwritten; the installer stops and tells you which one.

### From a Local Clone (Node.js / npx)
```bash
# Preview actions without writing:
npx . --dry-run

# Install for Claude Code, Codex, and Agy CLI:
npx .

# Force refresh existing installed copies:
npx . --force
```
Every option in the table above works here too, e.g. `npx . --agents claude --roles controller,manager`.

### Windows (PowerShell)
```powershell
# Review actions first:
.\install.ps1 -DryRun

# Install all six skills:
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

### macOS / Linux (POSIX Shell)
```sh
# Review actions first:
sh ./install.sh --dry-run

# Install all six skills:
sh ./install.sh
```

---

## 6. Your First Safe Session

Here is a worked walkthrough for a single-task run (e.g. adding token expiry handling):

### Step 1: Open Herdr and Assign Panes
Inspect available panes:
```bash
herdr pane list
```
Start your Manager and Delegator agents in empty panes (specifying your actual pane IDs):
```bash
herdr agent start manager --kind claude --pane <pane-id-1>
herdr agent start delegator --kind codex --pane <pane-id-2>
```

### Step 2: Configure the Run Roster
Create a run configuration file `roles.json`:
```json
{
  "run_id": "auth-fix-v1",
  "base_ref": "main",
  "max_active_workers": 1,
  "agents": {
    "manager": { "name": "manager", "kind": "claude" },
    "delegator": { "name": "delegator", "kind": "codex" },
    "workers": [
      {
        "name": "worker_01",
        "kind": "codex",
        "task_id": "T01",
        "task_title": "Fix token expiration redirect",
        "task_outcome": "Expired sessions redirect to /login with 401 status",
        "model": "gpt-6-luna",
        "reasoning_effort": "xhigh"
      }
    ]
  }
}
```

**Low on credits? Use Lead mode.** Replace the `manager` and `delegator` entries with a single `"lead": { "name": "lead", "kind": "claude" }`. That one agent plans, dispatches, integrates and verifies, but never implements: it shows you `PLAN.md` and waits for your GO before any worker starts, and its verdict is a recommendation that you accept. A complete example is in [`examples/roles.lead.json`](examples/roles.lead.json).

### Step 3: Bind Role Packets
Generate isolated role instructions for the default Markdown engine:
```powershell
py -3 .\tools\role_bind.py --config .\roles.json --repo "C:\path\to\your\repo"
```

### Step 4: Dispatch Work & Track Progress
1. **Manager Approval:** The Manager drafts `PLAN.md`. Once accepted by the human, the Delegator creates `tasks/T01.md`.
2. **Worker Provisioning:** The Delegator creates a Git worktree for `T01` and opens a dedicated Herdr pane.
3. **Readiness Handshake:** The Worker inspects its task, writes `.herdr-loop/READY.md`, and sends `ROLE_READY`.
4. **Implementation:** Delegator observes `ROLE_READY` and sends `TASK_START`. The Worker implements the change, commits to Git, writes `REPORT.md`, and signals `REPORT_READY`.
5. **Serial Verification:** The Delegator cherry-picks the commit into the integration branch, freezes candidate SHA, acquires the test lock, runs the approved test (`npm test`), and logs the result.
6. **Manager Acceptance:** The Manager reviews the candidate commit and test evidence, recording final acceptance.
7. **Human-Facing Progress:** View clean evidence-backed progress without internal task IDs:
```powershell
py -3 .\tools\progress_view.py --session "C:\path\to\your\repo\.herdr-loop\auth-fix-v1\SESSION.md"
```

---

## 7. Communication and State

Agents coordinate through explicit handoff envelopes sent as Herdr prompt hints:

- `ROLE_READY`: Sent by Worker after verifying its assigned workspace and writing `READY.md`.
- `TASK_START`: Sent by Delegator authorizing implementation start.
- `REPORT_READY`: Sent by Worker with source commit SHA and `REPORT.md`.
- `CRITIQUE_READY`: Sent by Critic after static inspection of candidate diff.
- `VERIFY_START` & `VERIFY_READY`: Bounded execution of test suite under exclusive serial lock.
- `BLOCKED`: Explicit alert when environment, permission, or model override issues arise.

> [!IMPORTANT]
> **Prompt Submission ≠ Receipt:** A submitted prompt is only an alert hint. True handoffs require observed evidence files (`READY.md`, `REPORT.md`, Git commit hashes).  
> **Report Ready ≠ Accepted:** A worker finishing code does not mean the task is accepted. Acceptance requires clean integration, independent test passage, and Manager sign-off.

---

## 8. Models and Worker Launching

### Model Preferences:
- **Codex Worker:** Prefers `gpt-6-luna` with `xhigh` reasoning effort.
- **Claude Haiku Worker or Verifier:** `claude-haiku-5-5` provides fast, low-cost execution.
- **Claude Critic:** `claude-sonnet-5-5` provides deep architectural challenge.

### Strict Spawning Rules:
- **No Codex Subagents:** Never use Codex's native `spawn_agent`, agent teams, or background child threads. All agents must exist in genuine Herdr panes.
- **Cross-Model Launch Routing:** A Codex Delegator cannot launch a model-overridden Codex CLI worker directly. When this combination is needed, a **Claude Manager** performs the launch step via a `SPAWN-REQUEST.md` receipt.
- **Agy Sandbox Flags:** When launching Agy with `--sandbox --dangerously-skip-permissions`, verify that directory isolation is effective and no machine secrets are accessible.

---

## 9. Concurrency and Verification

- **Default 1 Worker:** To keep RAM usage safe on 8 GB–16 GB laptops, runs default to one active worker. Up to 3 independent workers may run only after explicit human approval.
- **Worker Test-Execution Ban:** Workers never run test suites or builds. This prevents context window waste from repeated test failures and protects machine memory.
- **Serial Heavy Test Lock:** In offline SQLite pilot runs, at most **one** memory-heavy local test process (pytest, npm test, docker build) holds the `heavy-local-verification` lock within the run database. In standard Markdown sessions, operators must serialize test executions across tasks. Across independent runs, operators must avoid concurrent heavy workloads.

---

## 10. Handling Blockers and Restarting

- **`Access is denied` Triage:** Never auto-elevate to Administrator or disable sandboxes. Diagnose whether the block is a Herdr socket error, Docker Desktop named-pipe restriction, or worktree permissions using `tools/access_probe.py`.
- **Closed or Stalled Panes:** For standard runs, inspect Herdr panes (`herdr pane list`) and reconcile `SESSION.md`. For offline SQLite pilot runs, run diagnostic recovery:
  ```powershell
  python skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <path-to-run-dir> recover
  ```
  The system confirms process quiescence via Herdr CLI before releasing held locks. See [docs/OPERATIONS.md](docs/OPERATIONS.md) for full recovery procedures.
- **Safe Cancellation:** A human `CANCEL_RUN` stops future dispatches, waits for active processes to halt, and preserves dirty worktrees for operator triage.

---

## 11. Updates, Compatibility, Rollback, and Uninstall

### Check for Updates
To check if a newer published release is available on GitHub:
```powershell
python tools/update.py --check
```
*Note: If no GitHub Release has been published yet, the tool reports "Development version / no published releases yet."*

### Explicit Update
To safely update from published release assets with automated checksum verification:
```powershell
# Dry run preview:
python tools/update.py --dry-run

# Apply update with pre-update backup:
python tools/update.py
```
No clone? Update straight from GitHub by pinning the newer release tag:
```bash
npx github:Jacinth091/Herdr-Loop-Skill#vX.Y.Z --force
```

### Rollback on Demand
If an update causes issues, restore your previous installation cleanly:
```powershell
# List local backups:
python tools/update.py --list-backups

# Restore previous backup:
python tools/update.py --rollback latest
```

### Safe Uninstall
To remove pack-managed skills without deleting your custom files or project sessions:
```bash
npx github:Jacinth091/Herdr-Loop-Skill --uninstall
# from a local clone:
npx . --uninstall
# or on Windows:
.\install.ps1 -Uninstall
```

---

## 12. What is Supported Today?

| Capability | Current Status | Notes |
| :--- | :--- | :--- |
| **Role-Based Multi-Agent Workflow** | **Stable / Supported** | Manager, Delegator, Worker, Verifier, Critic, Controller. |
| **Lead Mode (Manager + Delegator in one agent)** | **New in 2.8.1** | `agents.lead` in `roles.json` (see `examples/roles.lead.json`); human approves the plan and gives final acceptance. |
| **Standard Markdown Ledger (`SESSION.md`)** | **Stable / Default** | Human-readable, transparent, zero-dependency. Active runtime authority. |
| **Transactional SQLite Ledger** | **Offline Pilot Only** | Offline CLI pilot for evaluation only; not live-bindable (`role_bind.py` explicitly refuses `state_backend: "sqlite"`). |
| **Zero-Dependency Installers** | **Supported** | `npx .`, PowerShell (`install.ps1`), and POSIX shell (`install.sh`). |
| **Safe Updater & Rollback** | **Supported** | `tools/update.py` with backup, mandatory checksum checks, and rollback. |
| **Native Herdr Signals Transport** | *Future Proposal* | Current release uses Herdr `agent prompt` hints; documents remain truth. |

---

## 13. Troubleshooting and FAQ

**Q: Herdr reports "transport unavailable" or "pane not found"?**  
A: Ensure Herdr is running and `HERDR_ENV=1` is active in the shell. Run `tools/access_probe.py` to diagnose socket communication.

**Q: Worker is stuck or idle without sending `REPORT_READY`?**  
A: Inspect the worker pane directly. Check if the model hit a rate limit or is awaiting interactive input.

**Q: Can I run multiple workers simultaneously?**  
A: Yes, set `"max_active_workers": 2` or `3` and `"parallel_workers_authorized": true` in `roles.json`, but ensure their task scopes do not modify overlapping files.

---

## 14. Maintaining and Contributing

For detailed documentation, operational architecture, maintainer guidelines, and release procedures:
- [Operations & Architecture Guide](docs/OPERATIONS.md) — System lifecycle, candidate Git provenance, and offline SQLite pilot.
- [Updating & Rollback Guide](docs/UPDATING.md) — Safe release updates, integrity checks, and rollback.
- [Maintainer Guide](docs/MAINTAINING.md) — Development setup, testing standards, and core invariants.
- [Release Guide](docs/RELEASING.md) — Tag-gated releases, checksum generation, and publishing checklist.
- [Changelog](CHANGELOG.md) — Detailed version history.
