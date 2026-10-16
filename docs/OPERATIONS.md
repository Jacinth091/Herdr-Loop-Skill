# Operations & Architecture Guide for Herdr Role Pack

This document details the operational architecture, state models, candidate Git provenance, and verification locks for the **Herdr Role Pack**.

---

## 1. System Architecture & Lifecycle

```mermaid
flowchart TD
    Human([Human Operator]) -->|Execution Grant / Goals| Controller[herdr-engineering-loop<br/>Session Controller]
    Controller -->|Provisions Topology| Herdr[Herdr Runtime / Panes]
    Controller -->|Binds Roles| Manager[herdr-manager<br/>Claude / Codex]
    
    Manager -->|Approves PLAN.md| Delegator[herdr-delegator<br/>Claude / Codex]
    Delegator -->|Provisions Worktree & Brief| Worker[herdr-worker<br/>Codex Luna / Claude Haiku / Agy]
    
    Worker -->|Source Commit & REPORT.md| Delegator
    Delegator -->|Cherry-picks & Freezes Candidate| Candidate[Frozen Git Candidate]
    
    Candidate -.->|Read-Only Diff| Critic[herdr-critic<br/>Claude Sonnet 5.5]
    Critic -.->|CRITIQUE_READY| Manager
    
    Candidate -->|VERIFY_START + Exclusive Lock| Verifier[herdr-verifier<br/>Claude Haiku 5.5]
    Verifier -->|VERIFY_READY + Evidence Log| Delegator
    
    Delegator -->|Integrated Candidate & Test Evidence| Manager
    Manager -->|DECISION_RECORD: Accept / Revise| Delegator
```

### Core Invariants:
1. **Separation of Concerns:** Governed Workers write code but never execute tests. A standalone Worker may run bounded, authorized self-checks and must label them self-verification. Verifiers execute independent checks but never edit code. Critics perform read-only reviews. Managers make architectural and acceptance decisions.
2. **Real Herdr Panes:** Governed Workers must be registered in Herdr panes. A direct standalone assignment in an existing CLI does not need Herdr. Native Codex subagents (`spawn_agent`, agent teams) are strictly forbidden.
3. **Candidate Fencing:** Before running tests, the integration checkout is frozen. Any subsequent modification invalidates ongoing verification.
4. **Serial Heavy Verification Lock:** SQLite pilot checks use a per-run claim and an exclusive machine lock. Standard `SESSION.md` runs still coordinate serial checks manually; their advisory state does not participate in the SQLite lock.

### Standalone Worker assignment

A direct human assignment can use `herdr-worker` without provisioning a Manager/Delegator team. The current task or supplied plan is the contract; the Worker inspects the authorized checkout, preserves unrelated dirty changes, and reports actual changes and self-checks. It does not require `ROLE.md`, `TASK.md`, `SESSION.md`, SQLite, `ROLE_READY`, or `TASK_START`. Use `examples/roles.standalone-worker.json` with `tools/role_bind.py` only when a single Herdr-launched Worker needs a generated prompt packet. Binding writes no managed run ledger and does not start the agent.

For access recovery, classify the exact boundary first. A same-scope command blocked by the active CLI's supported permission boundary may be requested through that CLI's own approval tool; the user/reviewer decides. Herdr origin is required only for Herdr actions. Never grant permission from the skill, bypass a deny, elevate, edit ACLs/policy, or disable sandboxing. See the controller's [access recovery contract](../skills/herdr-engineering-loop/references/access-triage.md).

---

## 2. State Engines: Standard Markdown vs. Offline SQLite Pilot

### Standard Production Engine: Delegator-Owned `SESSION.md`
- **Status:** **Active Default & Supported.**
- **File:** `<repo>/.herdr-loop/<run-id>/SESSION.md`.
- **Properties:** Transparent, plain Markdown, human-editable, zero-dependency.
- **Workflow:** The Delegator updates the task frontier, commit mapping, and verification locks directly in `SESSION.md`.

### Experimental Pilot Engine: ACID SQLite Ledger (`ledger.sqlite`)
- **Status:** **Offline Opt-in Pilot Only.**
- **File:** `<repo>/.herdr-loop/<run-id>/ledger.sqlite`.
- **Tool:** `skills/herdr-engineering-loop/scripts/herdr_ledger.py`.
- **Purpose:** Evaluates eliminating context window amplification (e.g. 130KB+ `SESSION.md` logs) through deterministic transactional CLI state transitions.
- **Mutual Exclusion:** Routine role binding defaults to Markdown. Explicit SQLite binding creates a planning ledger with typed role activation; execution requires a current observed role, approved plan and separate human grant. Follow the controller's `references/sqlite-live.md`. `run_backend.py switch` supports pristine planning and structured quiescent checkpoints after dry-run and human approval. It refuses active ownership, unconfirmed signals, changed evidence/text, and external worker history without descendant observation. Both backends share `scripts/verification_guard.py` for heavy local checks; retain its gate after unknown process ownership.

---

## 3. SQLite Ledger CLI Reference (Offline Pilot)

The CLI is `skills/herdr-engineering-loop/scripts/herdr_ledger.py`. Every command takes a global `--run-dir <path>`; governed mutations also take `--as-agent <name> --epoch <n>`.

### Generated command reference

The exact arguments of every command, and the command list for each role, are **generated from the CLI itself** and committed at [skills/herdr-engineering-loop/references/command-reference.md](../skills/herdr-engineering-loop/references/command-reference.md). Print the same text live with:

```bash
python skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <run-dir> reference
```

Add `--role <role>` to print only that role's commands, which is the smallest useful read.

Do not hand-maintain command lists in prose: they drift from the parser. A state transition looks like this:

```bash
python skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <run-dir> attempt-reserve \
  --task T01 --attempt T01-A1 --checkout <worktree-path> --agent worker_a1 --provider codex \
  --expected-model gpt-6-luna --expected-effort xhigh --request-id req-res-1
```

Command sequencing, signals and receipts are explained in the controller's [SQLite role procedure](../skills/herdr-engineering-loop/references/sqlite-live.md).
