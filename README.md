# Herdr Role Pack v2.8

A **multi-skill local installer**, not one all-powerful prompt. Assign separate, bounded skills to session agents and keep their work together through Markdown and Git. Designed for Herdr with Codex, Claude Code, and Agy CLI on Windows or Unix. No Relay, Signals, Node, daemon, API key, cloud service or additional runtime is needed to *install* the six skills.

## User-facing progress without internal task IDs (v2.7)

Keep `T01`, `T01-A1`, Herdr pane IDs, and exact SHAs for durable orchestration in `SESSION.md` and machine/agent handoffs. **Show the human the slice title, expected result and evidence-grounded progress instead**, not `T01` or `REPORT_READY`. The Manager gives every approved slice a meaningful title/outcome. The Delegator records that mapping in the existing `SESSION.md` frontier, so no second source of truth is created.

Example of what the human sees:

> **Improve sign-in recovery messages: Testing**  
> The change is integrated. A dedicated tester is checking that expired sessions show clear recovery instructions without changing existing login behavior.

Internally the same work may be `T01` with `T01-A2` as its active attempt. Those keys remain available when the user explicitly requests a diagnostic view.

The sample role configs now accept `task_title` and `task_outcome` alongside `task_id`. Older configs without these fields still bind, but the Delegator must obtain both from the accepted plan before `TASK_START` or presenting status. For an evidence-grounded on-demand human view, run:

```powershell
py -3 .\tools\progress_view.py --session "C:\path\to\repo\.herdr-loop\<run-id>\SESSION.md"
```

The view is read-only, parses the task frontier in `SESSION.md`, uses only recorded statuses, and rejects missing descriptions instead of inventing them. `--include-internal-ids` is an optional **debug-only** view. It does not query Herdr or Git itself.

## What is installed

| Skill | Owner | Rules | Deliverables |
| --- | --- | --- | --- |
| `herdr-engineering-loop` | Human or session bootstrap/controller | Inspect/reuse Herdr layout, bind agents, recover session | Roster, session, role activation |
| `herdr-manager` | Claude or Codex | Decide architecture, own approved plan, resolve ambiguities, accept work | `PLAN.md`, decisions, verdict |
| `herdr-delegator` | Claude or Codex | Brief/dispatch workers, manage worktrees, review+integrate, serial test gate | `TASK.md`, integration history, verified handoff |
| `herdr-worker` | Agy, Codex Luna or Claude Haiku 5.5 | One branch/worktree/task, code and commit, **no tests/builds/lint/typecheck/installs** | `REPORT.md`, source SHA |
| `herdr-verifier` | **Claude Haiku 5.5 preferred**, or any capable dedicated tester | **Only** approved sequential checks at integration SHA under exclusive lock; no feature edits | `verifications/Txx.md`, `VERIFY_READY` |
| `herdr-critic` | Claude Sonnet 5.5 in its own Herdr pane | Independent read-only plan/diff critique, no tests or edits | `critiques/<review-id>.md`, `CRITIQUE_READY` |

All skills may be *available* to an agent CLI after installation. **Availability does not assign a role**. The run's roster + role activation packet decide which one to use. Claude and Codex can swap roles on subsequent runs without reinstalling.

## v2.8: SQLite ledger evaluation, deliberately opt-in

A supplied run archive independently confirms real Markdown-amplification: the S3 private-media run has a **131,147-byte SESSION.md** and six cumulative CR-03 critique snapshots totaling **300,416 bytes**. These are **disk volumes, not token invoices**. The proposed solution is to move repetitive state mutations to deterministic code while keeping Managers responsible for meaning and Critic/Verifier evidence intact.

**This release does not silently convert current Herdr sessions to SQLite.** All v2.7 sessions and normal new runs still use Delegator-owned `SESSION.md`. The new controller-bundled `scripts/herdr_ledger.py` is a **separate offline pilot only**. It accepts a fresh run directory without `SESSION.md`, stores transactional typed task/attempt/candidate/verification state in one `ledger.sqlite`, and returns small receipts plus optional human-facing `status --human` titles. It never starts agents, processes actual Signals, runs tests, raises permissions or pushes Git changes.

The pilot proves a bounded real-Git tracer in offline tests: task dependency → worker attempt reservation → observed-binding record → report → cherry-pick → clean exact-head candidate freeze → exclusive verification claim → evidence → Manager-decision reference → acceptance → next task. It rejects conflicting replay IDs, mismatched model/effort overrides, stale candidates, dirty checkouts, contradictory checkout claims and attempted operation on a legacy `SESSION.md` run. **It is not an accepted live replacement:** actual Herdr receipt correlation, decision/critique deltas, cancellation, Markdown export, Windows testing and measured token savings are still required before cutover. Follow `skills/herdr-engineering-loop/references/sqlite-ledger-pilot.md` for the fixture and usage.

The existing `tools/role_bind.py`, `tools/activate_roles.py`, and `tools/progress_view.py` deliberately retain legacy behavior. Avoid using both backends in the same active run. This is an additive experiment, not a migration and not a dependency on Relay.

## v2.6: borrow the useful principles, not the Relay machinery

The user-supplied orchestration context pack inspired **four small improvements**, not a new runtime or role hierarchy:

- **Execution authority:** `SESSION.md` records the human-approved objective and source of authorization to execute. An original instruction to implement is sufficient; a planning-only request is not a dispatch grant. Manager approves the implementation *plan* but cannot invent human permission to deploy, expand scope or modify privileged resources.
- **Task ≠ attempt:** stable `T01` may have `T01-A1` (agent/pane/worktree), then `T01-A2` after a failure or quota limit. Use an optional worker `attempt_id` in `roles.json`; the binder defaults to `<task_id>-A1` and records it in the role packet/roster/activation. Record replacements in the Delegator-owned `SESSION.md`, preserving prior commits, reports and blockers. This is not another ticket tracker.
- **Exclusive ownership and candidate fencing:** one active writer per worktree or isolated copy. Before `VERIFY_START`, freeze the integration checkout and full candidate SHA; no other agent may write to that target. Changed code/config/test inputs make an ongoing check stale. Any Manager-approved docs-only evidence carry-forward stays pinned to the original SHA; latest-head CI remains separate.
- **Safe cancellation:** human-authorized `CANCEL_RUN` stops scheduling, asks run-owned agents to preserve partial work, confirms the processes actually stopped, and only then releases locks or safely reclaims clean run-owned resources. `CANCELLED` is not success. Human-owned panes and dirty worktrees are not automatically removed. Restart needs an execution grant and recovery reconciliation.

For detailed procedures see `skills/herdr-delegator/references/run-integrity.md`. This is deliberately **not** a dependency on Relay, persistent memory, a daemon, a database or a second event bus. Existing Herdr named-agent prompts still carry the events, and Markdown/Git retain the truth.

**Adopting during an active run:** Do not reset or rewrite the current `SESSION.md`, spawn replacement agents solely for the update, or force cancellation to conform to v2.6. Finish the ongoing task under its accepted brief. Adopt the new fields and human-facing labels at the next natural Delegator handoff or in the next new run. Installer updates only refresh managed skill files.

## Critical Codex rule: Herdr workers, not internal subagents

**Never use Codex's native `spawn_agent`, internal subagents, agent teams, or background child threads for Herdr engineering tasks.** This applies to Codex in every role, especially the Delegator. An internal Codex subagent is not a Herdr-spawned Worker, has no independently registered Herdr agent name/pane, and does not establish the task's worktree ownership.

Only the **Delegator** provisions the worker scope, worktree and pane. When the Delegator is Codex and the worker needs a Codex CLI model/effort override, a **Claude Manager** performs just the `herdr agent start` step (details below). The required flow is:

```text
1. Manager approves the task in PLAN.md; Delegator creates tasks/T01.md.
2. herdr worktree create --cwd <repo> --branch herdr/<run-id>/t01 --base <approved-sha> --no-focus
3. Inspect the returned Herdr workspace/root pane and verify Git worktree provenance.
4. Copy TASK.md and ROLE.md into that worktree's .herdr-loop directory.
5. herdr agent start worker_t01 --kind agy --pane <root-pane-id> -- --sandbox --dangerously-skip-permissions
   OR (AUTHORIZED NON-CODEX SPAWNER): herdr agent start worker_t01 --kind codex --pane <root-pane-id> -- -m gpt-6-luna -c model_reasoning_effort='"xhigh"'
   OR: herdr agent start worker_t01 --kind claude --pane <root-pane-id> -- --model claude-haiku-5-5
6. herdr agent get worker_t01   # verify Herdr identity AND runtime model/effort before prompting
7. herdr agent prompt worker_t01 "Read ROLE.md/TASK.md; verify pane, write READY.md, send ROLE_READY to the named Delegator. No product edits yet."
8. Delegator observes the worker-local READY.md + ROLE_READY, logs receipt in SESSION.md, then sends TASK_START with approved plan revision.
9. Worker implements, writes REPORT.md and source commit, sends REPORT_READY. Delegator integrates and verifies serially.
```

Commands are examples: inspect `herdr --help` and the locally installed Herdr skill for your version. Agy's requested bypass flags are security-sensitive and require the sandbox checks described below. Do not start an Agy worker with those flags automatically before confirming appropriate isolation.

**Failure rule:** if Herdr, the root pane, worktree or agent launch/registration is unavailable, stop with `BLOCKED: HERDR_WORKER_UNAVAILABLE`. Do **not** substitute `spawn_agent`, direct background `codex exec`, or an agent outside Herdr. A Manager sends assignments to the Delegator; only a **Claude Manager** may launch an overridden Codex worker for a Codex Delegator, with an explicit request and receipt. Workers, Critics, and Verifiers never spawn assistants.

## v2.5: Claude Haiku as a dedicated tester

**Default recommendation:** let Claude Haiku 5.5 run a separate `herdr-verifier` agent when testing would otherwise tie up the Delegator. The Delegator still decides verification scope, grants the **one total heavy-local-verification lock**, and owns the test handoff. A dedicated tester is **not another implementation Worker** and does not increase the worker concurrency count. It runs the approved commands against the integration SHA, records exit codes/evidence in `verifications/Txx.md`, and returns `VERIFY_READY`. If a check fails, it documents the failure; implementation fixes go back through Delegator to the owning Worker.

A Claude Haiku agent assigned `herdr-worker` still **must not run tests**. Role packets decide whether the instance writes code **or** tests it, never both. Delegator can run a trivial check directly without launching an extra agent; more complex verification can be assigned to another capable dedicated Verifier, while Sonnet 5.5 remains the independent read-only Critic. All Verifiers require a Herdr pane, `ROLE_READY`, actual-model confirmation, `VERIFY_START` and the exclusive lock.

Example roster: `examples/roles.haiku-verifier.json`, with a Codex Luna implementer, a distinct Claude Haiku Verifier, and a Sonnet Critic.

## v2.4: model-aware launch authority and independent critique

The recommended Codex Worker is **GPT-6 Luna** (`gpt-6-luna`) at **`xhigh` reasoning**, with **`max`** for specifically approved difficult tasks. This is a *preference in the role packet*, not evidence that the CLI actually honored the override. For smaller implementation tasks, a **Claude Haiku 5.5** (`claude-haiku-5-5`) Worker is supported in a separate Herdr pane and task worktree. The independent **Critic** runs **Claude Sonnet 5.5** (`claude-sonnet-5-5`) and challenges plans/diffs read-only; it is *not* a substitute for a Verifier or Manager acceptance.

**Observed Codex limitation:** In this workflow, a **Codex agent must not launch another Codex CLI worker that needs an explicit model and reasoning override**. The Herdr CLI may accept forwarded flags, but that does not guarantee the override will work from within Codex. The allowed launcher is selected by *assigned role plus agent kind*, not a particular brand-based permanent hierarchy:

| Delegator | Manager | Who runs `herdr agent start ... --kind codex ... -m ... -c ...`? |
| --- | --- | --- |
| Claude | Claude or Codex | **Claude Delegator** |
| Codex | Claude | **Claude Manager**, one-spawn-only exception |
| Codex | Codex | **Human / an explicitly reassigned non-Codex operator**; block until available |

When the Claude Manager must launch, the Codex Delegator first prepares the **task-specific worktree, empty Herdr pane, ROLE.md, TASK.md and `SPAWN-REQUEST.md`**. The Manager then starts only the specified overridden Codex agent through Herdr, checks the actual name/pane/model/effort, writes `SPAWN-RECEIPT.md`, and sends `SPAWN_READY`. The Delegator reconciles the receipt and continues its usual worker `ROLE_READY` / `TASK_START` flow. **The Manager does not take over delegation, integration, or cleanup.** If model/effort is wrong or cannot be confirmed in the live agent (such as with the Codex `/model` view), report `BLOCKED: MODEL_OVERRIDE_NOT_APPLIED` and do not start implementation. Never fall back to plain Codex, internal `spawn_agent`, or global config mutation.

**CLI launch templates** (adapt shell quoting after checking `--help`; verify actual model before execution):

```text
herdr agent start worker_t01 --kind codex --pane <pane-id> -- -m gpt-6-luna -c model_reasoning_effort='"xhigh"'
herdr agent start worker_t01 --kind codex --pane <pane-id> -- -m gpt-6-luna -c model_reasoning_effort='"max"'
herdr agent start worker_t02 --kind claude --pane <pane-id> -- --model claude-haiku-5-5
herdr agent start verifier_t01 --kind claude --pane <pane-id> -- --model claude-haiku-5-5
herdr agent start critic_t01 --kind claude --pane <pane-id> -- --model claude-sonnet-5-5
```

The Critic receives `ROLE_READY` then a separate `CRITIQUE_START <run> <review-id> <plan-revision> <sha>`. It returns `CRITIQUE_READY` with an evidence-linked Markdown report. Critique runs are optional and only at an explicit requested gate; neither critique nor test verification runs automatically on every commit. See `skills/herdr-delegator/references/model-routing.md` for the complete role matrix and safety checks.

## v2.3: access denied and explicit signaling

The v2.3 signaling and permission rules continue to cover two observed gaps: some agents interpret `Access is denied` as a request for Administrator privileges or an unconfined sandbox, while others never learn who should receive completion signals.

**Permission failures:** the role instructions now distinguish incorrect Herdr process/pane origin, socket/API denial, unwritable worktree, Agy/Codex sandbox restrictions, Docker Desktop named-pipe access, Windows WSL denial and out-of-scope file reads. No role may auto-elevate, disable sandbox, weaken machine permissions, expose Docker over TCP or silently switch to an internal Codex subagent. A failed Docker pipe check is an **environment-blocked verification**, not a failed Laravel/product test. Use a human-approved trusted verifier/pane only when permitted; retain the serial verification lock and exact SHA.

**Startup handshake:** every generated role packet names its precise **Herdr signal recipient**. Workers must check Herdr identity, task/worktree and a narrow workspace-local write probe, write `.herdr-loop/READY.md`, and send `ROLE_READY` to the Delegator. The Delegator records observed receipt before sending a separate `TASK_START`. A worker must not edit product files during the initial activation prompt. Verifiers send `ROLE_READY` and wait for `VERIFY_START` **and** a test lock. At completion, Workers send `REPORT_READY`; Verifiers send `VERIFY_READY`; Manager/Delegator exchange `DECISION_READY`, `DECISION_NEEDED` and `MANAGER_REVIEW_READY`. These are brief Herdr `agent prompt` messages, **not an implemented Herdr Signals/Relay transport**. Report files and Git SHAs remain authoritative.

**On delivery failure:** preserve the Markdown report, mark `SIGNAL_TRANSPORT_UNAVAILABLE` or another specific class in `SESSION.md`, inspect recipient and report before one retry; never assume that a successful prompt submission was processed. A file-only fallback may be approved by the human, never chosen silently. No sleep/wait polling loops.

**Optional bounded diagnostic (Python 3):** within the assigned Herdr pane, check registration/server and perform a harmless, auto-deleted probe *only* under your own `<workspace>/.herdr-loop/` directory. It never requests elevation or changes ACLs:

```powershell
py -3 .\tools\access_probe.py --workspace "C:\path\to\worker-worktree" --agent worker_t01 --probe-write
```

For a purely local filesystem comparison outside Herdr, add `--skip-herdr`; its output is **not** proof of valid Herdr agent origin. The diagnostic utility is advisory and cannot grant authorization or bypass a sandbox.

Read `skills/herdr-delegator/references/signals.md` and `skills/herdr-delegator/references/access-triage.md` for the complete event/permissions contracts (each applicable installed role contains its own copy).

## Session-ledger lessons (general, not tied to one repository)

The live `SESSION.md` belongs to the Delegator. It is a recoverable index of the **accepted plan revision**, task dependency frontier, exact source-to-integration commit map, evidence links, content acceptance vs verification/CI, current verification lock (owner + tested SHA), exceptions, pending report receipts, and ownership of human vs run-created panes. Keep command logs and verbose detail in linked reports. A `REPORT_READY` hint never replaces the source artifact. Avoid periodic Manager messages and polling loops.

**Two separate concurrency budgets:** `max_active_workers` defaults to one; values two or three are allowed only when `parallel_workers_authorized: true` reflects an explicit human decision and task scopes do not overlap. The heavy *local* verification budget always remains one. Remote CI may run concurrently without consuming that local slot. Reuse approved test environments rather than recreating heavy resources per check.

**Explicit exception:** Code implementation still needs a separate Git worktree. A narrow non-code/document-only task may, only with human/Manager approval, use a restricted isolated copy in its own Herdr workspace/pane where the source tree may expose unrelated secrets or archives. Do not put such data into worker context. The Worker returns before/after diffs instead of an invented commit; the Delegator applies only the reviewed change and records real destination evidence.

**SHA-linked acceptance:** Manager may mark content accepted while new-head CI remains pending. If a subsequent docs-only commit advances HEAD, retain the old exact-SHA local verification report, document changed-path impact and the Manager's decision whether to carry it forward, and observe any required current-HEAD CI. Never claim the old tests ran at the new SHA.

## Install on Windows 11 (PowerShell, no Python/Node needed)

Extract this archive, open PowerShell in the `herdr-role-pack` directory, and **review scripts before running**. From that folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

This installs all six skills for Claude Code, Codex and Agy CLI under these standard user-level directories:

```text
%USERPROFILE%\.claude\skills\             Claude Code
%USERPROFILE%\.agents\skills\             Codex
%USERPROFILE%\.gemini\antigravity-cli\skills\   Agy CLI
```

Optional:

```powershell
# Preview only
.\install.ps1 -DryRun
# Update pack-managed copies (never overwrites unrelated skills)
.\install.ps1 -Force
# Only manager/delegator skills in Claude/Codex
.\install.ps1 -Agents Claude,Codex -Roles Manager,Delegator
# Also install the official Herdr CLI skill from YOUR local herdr binary
.\install.ps1 -WithHerdr
# Remove only installer-owned skills
.\install.ps1 -Uninstall
```

Use a new agent session after installing or refreshing skills if your CLI does not discover them dynamically. For an **already-running** Herdr session, prefer continuing with its current accepted plan and existing `SESSION.md`; do not bulk-reactivate or override live authority without a deliberate handoff. This script does **not** install Claude/Codex/Agy/Herdr binaries; they must exist separately. If Windows script policy blocks execution, use `powershell -NoProfile -ExecutionPolicy Bypass -File` rather than permanently changing machine-wide settings.

**Upgrading from v2.5 or earlier:** use `-Force` to refresh installer-owned copies, then regenerate only the session's generated packets using `role_bind.py --force`. The binder preserves the live `SESSION.md`. A running worker or verifier should **finish under its original accepted plan/brief**; new startup barriers apply to newly assigned or intentionally reactivated tasks. Do not overwrite a live worker's ROLE.md/TASK.md or retroactively mark it unacknowledged. Update Manager/Delegator one at a time with a controlled handoff; use fresh agent processes if instructions are cached.

## Install on macOS / Linux

```sh
sh ./install.sh
sh ./install.sh --dry-run
sh ./install.sh --force --with-herdr
sh ./install.sh --agents claude,codex --roles manager,delegator
sh ./install.sh --uninstall
```

The optional `--with-herdr` uses **local** `herdr --skill`, which prints an official release-matched CLI skill. If Herdr isn't installed, the installer warns and continues. This is independent of the role instructions.

## Assign named agents per run

1. Copy and edit `examples/roles.example.json`; replace run ID, base ref, agent names and kinds. To see the new configurations, use `examples/roles.codex-override.json` (Claude Manager + Codex Delegator + GPT-6 Luna `xhigh` worker) or `examples/roles.claude-haiku.json` (Claude Haiku 5.5 worker, separate Sonnet 5.5 Critic), or `examples/roles.haiku-verifier.json` (dedicated Haiku tester, Codex Luna worker, separate Sonnet Critic).  Put the actual assigned worker workspace path into the config **after** the Delegator creates it, not beforehand (the `worktree` JSON field also accepts an approved non-code isolated-copy path). Use 1 active worker by default. For a replacement attempt, set a new `attempt_id` such as `T01-A2` on that worker; do not overwrite evidence from `T01-A1`. To declare 2-3 active independent workers, set `max_active_workers` accordingly and `parallel_workers_authorized: true` only after explicit human approval. This **does not** change the serial local verification limit.
2. Generate role packets. This optional automation uses Python 3 (standard library only). The **installer itself does not require Python**.

```powershell
py -3 .\tools\role_bind.py --config .\examples\roles.example.json --repo C:\path\to\repo
```

```sh
python3 tools/role_bind.py --config examples/roles.example.json --repo /path/to/repo
```

The binder creates:

```text
<repo>/.herdr-loop/<run-id>/
  ROSTER.md                   # Named agent -> role/kind/task mapping
  SESSION.md                  # New run ledger; preserved on updates
  ACTIVATE.md                 # Ready-to-paste prompts for each assigned agent
  ACTIVATION.json             # Machine-readable prompts, not live proof
  role-packets/<agent>.md     # Exact responsibilities/limits for each agent
  PLAN.md                     # Created by Manager, NOT the binder
  tasks/T01.md                # Created by Delegator
  reports/T01.md              # Copied from Worker by Delegator
  verifications/T01.md       # Created by Delegator/Verifier
  critiques/review-01.md     # Created by optional read-only Sonnet 5.5 Critic
  SPAWN-REQUEST.md           # Only for Codex Delegator requesting overridden Codex worker from Claude Manager
  SPAWN-RECEIPT.md           # Observed launch receipt, only after Herdr + model/effort checks
```

**Important:** `.herdr-loop/` contains temporary run instructions. Keep it out of commits using a repository-local Git exclude or an agreed ignore rule. The binder never overwrites an existing `SESSION.md`, even when run with `--force`. Use `--dry-run` for a preview and `--force` only to refresh *generated* packets after intentional role changes.

3. Activate the named live agents. The binder does not launch them, and it does not assume Herdr panes already exist. After starting/naming agents in Herdr, paste prompts from `ACTIVATE.md`, or use the optional dispatcher **inside Herdr** (`HERDR_ENV=1`):

```powershell
py -3 .\tools\activate_roles.py --run-dir C:\path\to\repo\.herdr-loop\feature-example
```

```sh
python3 tools/activate_roles.py --run-dir /path/to/repo/.herdr-loop/feature-example
```

Default activation sends only **Manager and Delegator** role packets; it doesn't launch processes. For Workers, the Delegator must first copy a role packet to `<assigned-workspace>/.herdr-loop/ROLE.md` and task brief to `<assigned-workspace>/.herdr-loop/TASK.md`, update the roster config, regenerate packets with `--force` and then explicitly use `activate_roles.py --roles worker`. For Verifiers, prepare an approved `VERIFY.md` and single verification lock first. The Critic may receive a setup-only role activation with `--roles critic`; prepare `CRITIQUE-BRIEF.md` and later authorize it with `CRITIQUE_START` before it inspects a diff. An activation response only proves a prompt was **submitted**, not that work finished.

## Example session flow

```text
human creates/opens Herdr session and workspace
  -> controller records roles and the manager's/delegator's pane IDs
  -> user execution grant recorded (an explicit instruction to implement already counts); manager studies repo/spec -> accepts PLAN.md revision
  -> delegator creates T01 brief and T01-A1 attempt + linked code-worker worktree/workspace/pane (or approved document-only isolated copy)
  -> non-Codex launch owner starts overridden Codex worker (Claude Manager exception if Codex Delegator); confirms actual model/effort
  -> Delegator reconciles SPAWN_READY receipt and Herdr agent get
  -> worker reads local ROLE.md/TASK.md, probes local scratch, writes READY.md and signals ROLE_READY
  -> delegator records readiness and sends TASK_START
  -> Agy (sandboxed), Codex Luna (xhigh/max), or Claude Haiku 5.5 implements ONLY T01, no tests
  -> Worker commits + writes REPORT.md; sends REPORT_READY hint
  -> delegator checks source commit+report, integrates into integration branch
  -> optional Sonnet 5.5 Critic reads exact-SHA diff, sends CRITIQUE_READY; Delegator routes findings to Manager
  -> delegator freezes integration SHA and checkout writes, then runs a small test OR starts a separate Haiku 5.5 Verifier in its own Herdr pane
  -> Verifier receives VERIFY_START + exclusive lock, runs approved tests serially, sends VERIFY_READY
  -> delegator writes evidence-backed results; manager accepts or revises
  -> delegator preserves or reclaims task worktree after safe checks
  -> if cancelled: stop dispatch, preserve partial evidence, reconcile processes, release lock only after stop; do not claim acceptance
```

Do not confuse `Herdr agent idle/done` with work completion. A worker may be content-accepted with CI still pending; neither report presence nor older-SHA verification proves latest-HEAD acceptance. Verify Markdown and Git. No automatic destructive worktree removal, pushes, PR merges, production deployment, or role reassignment.

## Low-RAM rule

For an 8 GB-class laptop: one active implementation worker by default (2-3 only after explicit human resource approval and independent scopes), one *total* memory-heavy local verification process, avoid concurrent watchers/builds. A Worker never runs its own tests. The separate Claude Haiku 5.5 Verifier is optional and recommended when repeated or nontrivial checks are worth offloading; starting it consumes agent memory, **not** another concurrent-test allowance. For trivial checks the Delegator verifies directly. The Haiku tester must hold the sole verification lock before starting a resource-heavy process.

## Security / limitations

- `--sandbox --dangerously-skip-permissions` is the requested Agy **implementation Worker** launch choice. Permission bypass can execute unsafe tool actions; sandbox strength and write permissions vary by environment. Inspect `agy --help` and verify effective isolation and secret exposure **before** launching. Never drop `--sandbox` silently to make writes possible.
- Skills are instructions, not an enforcement sandbox. They cannot stop a misbehaving tool or guarantee that agents will follow their role. Require observable Git/Markdown evidence at every gate.
- The role pack installer installs only skills, not Herdr, agent CLIs, or an always-on orchestration engine. Herdr live operations require a running server and an agent inside a Herdr pane with `HERDR_ENV=1`.
- No native Signals or Relay transport is used by this version. The explicit signaling envelope uses Herdr named-agent prompts as wake-up hints; documents, receipts and commits remain authoritative.
- Test instructions and CI automation belong to the Delegator/Verifier, not to Worker role. Managed automatic installers must not silently mutate global machine settings.

## Sources / platform paths

- Herdr CLI skill / release-matched `herdr --skill`: https://herdr.dev/docs/agent-skill/
- Herdr CLI worktree and agent commands: https://herdr.dev/docs/cli-reference/
- Agy CLI skills: https://www.agy.dev/docs/skills/
- Claude Code skills: https://code.claude.com/docs/en/skills
- Codex skills: https://developers.openai.com/codex/skills
- Herdr model-aware launch: https://herdr.dev/docs/agent-automation/
- GPT-6 Luna model/effort: https://developers.openai.com/api/docs/models/gpt-6-luna
- Claude Code `--model`: https://support.claude.com/en/articles/11940350-claude-code-model-configuration

## What's next

Keep this deliberately small: first test with a real `T01` and one Worker, then adjust the skill instructions based on observed failures. Later, when Signals/Relay is working, replace only transport/wake-ups, not Git evidence or role boundaries.

## ChatGPT skill upload vs local multi-skill pack

The multi-skill ZIP is for **local installation** into Herdr-facing coding agents. ChatGPT's single-skill uploader expects **one SKILL.md entrypoint per archive**. For ChatGPT, import the separately packaged `herdr-engineering-loop` controller `skill.zip` if desired; for local Herdr sessions, use this entire role pack and run its installer. Each standalone role in `skills/` is individually valid and can also be packaged/imported separately.
