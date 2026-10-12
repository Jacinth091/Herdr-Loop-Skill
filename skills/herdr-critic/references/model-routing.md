# Model selection and cross-provider spawn ownership

A model **preference** is not evidence that the model was selected. Check your local `herdr`, `codex`, and `claude` versions; record the actual observed model/effort at `ROLE_READY`. Never silently fall back to a different model, reasoning effort, or non-Herdr process.

## Defaults

| Role | Herdr kind | Preferred model | Effort | Work |
| --- | --- | --- | --- | --- |
| Implementation worker (Codex) | `codex` | `gpt-6-luna` | `xhigh`; `max` for human-approved particularly complex scope | Small scoped code changes, no tests |
| Implementation worker (Claude) | `claude` | `claude-haiku-5-5` | CLI default | Lighter scoped code changes, no tests |
| Dedicated tester / Verifier (Claude) | `claude` | `claude-haiku-5-5` | CLI default | Execute approved serial verification at exact integration SHA; no code edits |
| Critic (Claude) | `claude` | `claude-sonnet-5-5` | CLI default | Read-only plan/diff critique, no tests |
| Implementation worker (Agy) | `agy` | Existing supported local model | As locally configured | Scoped code changes in sandbox, no tests |

A Claude Haiku agent may be an **implementation Worker** or a **dedicated tester (Verifier)**, never both in the same assignment. The optional tester is a separately registered Herdr agent in its own pane, receives `ROLE_READY`, then `VERIFY_START` with the exclusive lock, and writes `VERIFY_READY` evidence. The Delegator can handle a trivial approved test without spawning a Verifier. This is not a separate `herdr-tester` skill: reuse `herdr-verifier`. The Sonnet Critic remains read-only and cannot run tests.

The user-observed restriction is authoritative for this workflow: **a Codex process must NOT attempt to spawn another Codex CLI process requiring custom model or reasoning overrides.** Never use Codex `spawn_agent`, internal subagents, nested `codex exec`, or a default-model Codex CLI as a substitute.

## Spawn authority matrix

| Delegator | Manager | Model-overridden Codex worker launch authority | Who still owns work |
| --- | --- | --- | --- |
| Claude | Claude/Codex | **Delegator (Claude)** | Delegator |
| Codex | Claude | **Manager (Claude)**, for the single approved Herdr agent-start exception | Delegator |
| Codex | Codex | **Human or reassigned non-Codex operator**; blocked until available | Delegator |

**Two-phase delegation when Claude Manager must launch:** The Codex Delegator prepares the approved worker worktree and free dedicated Herdr pane, writes ROLE/TASK packets and a durable `SPAWN-REQUEST.md` with run/task/name/pane/worktree/exact model/effort, and signals `SPAWN_REQUEST` to the Claude Manager. The Manager verifies the request and those exact IDs, invokes **only** `herdr agent start` with the requested overrides, verifies `herdr agent get` and observed model/effort, then writes `SPAWN-RECEIPT.md` and sends `SPAWN_READY`. The Manager does **not** allocate tasks, create additional workers, select a new model, integrate, test, or approve a worker's output as a consequence of the spawn. The Delegator verifies the receipt and continues the standard setup-only `ROLE_READY` / `TASK_START` handshake. Failed launch, absent matching identity, mismatched model/effort or missing recipient yields `BLOCKED: CODEX_OVERRIDE_SPAWNER_UNAVAILABLE` or `BLOCKED: MODEL_OVERRIDE_NOT_APPLIED`. Preserve created but unused resources for explicit cleanup; do not create duplicates blindly.

When the Claude Delegator can launch directly, it uses the same verification and durable spawn receipt but does not ask the Manager to launch. A Critic, Worker, or Verifier must never be used as an unapproved spawn delegate.

## Example Herdr launches (non-executable templates until IDs are observed)

Herdr forwards every argument **after `--`** to the selected agent executable:

```text
herdr agent start worker_t01 --kind codex --pane <actual-pane-id> -- -m gpt-6-luna -c model_reasoning_effort='"xhigh"'
herdr agent start worker_t01 --kind codex --pane <actual-pane-id> -- -m gpt-6-luna -c model_reasoning_effort='"max"'
herdr agent start worker_t02 --kind claude --pane <actual-pane-id> -- --model claude-haiku-5-5
herdr agent start verifier_t01 --kind claude --pane <actual-pane-id> -- --model claude-haiku-5-5
herdr agent start critic_t01 --kind claude --pane <actual-pane-id> -- --model claude-sonnet-5-5
```

The quoted `-c` parameter is a TOML value; shell quoting varies between POSIX and PowerShell. A Herdr `agent get` result validates process identity only; it does **not** validate the model or effort. Inspect the agent's actual Codex `/model` or Claude `/status` display (or trustworthy session metadata) and have the Worker record the observed selection in `READY.md` before `TASK_START`. If the CLI cannot expose verifiable model/effort, treat the override as unconfirmed and stop rather than guessing. Adapt quoting using CLI `--help` and confirm the **effective runtime selection**, not simply the spawn command's success. On Windows, prefer passing a literal argument array to the appropriate program instead of building an unsafe shell command. In case the Codex effort override is not honored, **stop before `TASK_START`**; the worker must report `MODEL_OVERRIDE_NOT_APPLIED`. No silent downgrade and no mutation of global Codex configuration.

Before launching: validate one free pane, correct working directory, isolated worktree, permissions, explicit approved worker budget, and that other Herdr agents have not already claimed the name/pane. On exit, Delegator owns cleanup and `SESSION.md` lineage. Never restart/kill a human-owned pane to change a model.

References: https://herdr.dev/docs/agent-automation/ ; https://herdr.dev/docs/cli-reference/ ; https://developers.openai.com/api/docs/models/gpt-6-luna ; https://support.claude.com/en/articles/11940350-claude-code-model-configuration
