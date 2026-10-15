# SQLite role procedure (explicit opt-in)

Use this procedure when the role packet selects `sqlite` and its epoch matches `backend.active.json`. `ledger.sqlite` is the only mutable run ledger. Keep substantive plans, briefs, reports and decision evidence in referenced files. Use the installed `scripts/herdr_ledger.py` path printed in the packet. Markdown remains the new-run default pending real Windows E2E proof.

## Setup and role acknowledgment

`tools/activate_roles.py --run-dir <run> --roles <roles>` observes named agent pane, process and checkout before sending setup. It records intent and submission in SQLite events. Submission is not readiness; uncertain delivery blocks replay. It never launches an agent or grants execution.

The submitted prompt includes `ROLE_ACK operation=<id> prompt_sha=<hash> run=<run> epoch=<epoch>`. The assigned role writes a readiness artifact containing these exact lines, with its observed name/pane and assigned role:

```text
run=<run>
epoch=<epoch>
agent=<name>
pane=<pane>
role=<role>
operation=<id>
prompt_sha=<hash>
```

After actually observing directed ROLE_READY, the assigned recipient records this receipt:

```text
ROLE_READY <run> <id> <name> <pane> <recipient>
evidence_sha=<SHA-256 of readiness file bytes>
```

Record it with `--run-dir <run> --as-agent <name> --epoch <epoch> role-confirm --operation-id <id> --pane <pane> --recipient <recipient> --recipient-pane <recipient-pane> --evidence <ready-file> --receipt <receipt-file>`. Lead's recipient is `human`; omit recipient-pane only for the human-direct receipt. The recorder must actually observe receipt, not manufacture it from prompt success. Confirmation re-observes process identity and requested model/effort. Restarted processes require a new acknowledgment.

For subsequent mutations, supply global `--as-agent <assigned-name> --epoch <current-epoch>` before the command. The caller must be in its acknowledged Herdr pane. Current prompt/evidence hashes and process identity are checked again. Historical offline pilots without a selector retain their explicit offline API; they are never silently promoted.

## Typed lifecycle

1. **Plan:** Manager records `plan-approve` with approved plan and rationale. Lead records human plan approval in its Manager role. This leaves the run `PLANNING`. Delegator/Lead separately records `run-authorize` with actual human grant evidence, human actor and distinct recorder. Neither command invents consent. The run must be `EXECUTING` before task allocation.
2. **Dispatch:** Delegator/Lead uses `task-add`, `next`, `task-show`, then `attempt-reserve` for the assigned checkout and Worker. After authorized Herdr launch, `attempt-attach` observes identity. Worker-local READY and directed ROLE_READY evidence precede `attempt-ready`; a separate observed TASK_START acknowledgment precedes `attempt-start`. Worker edits remain prohibited until TASK_START.
3. **Integration:** Delegator/Lead records observed REPORT_READY with `attempt-report`, integrates the source, and calls `candidate-freeze` at a clean exact HEAD/tree with matching patch provenance. Writer release requires observed shutdown; preserve claims on inconclusive observation.
4. **Review and checks:** The assigned Critic records `critique-record` and findings after authorized critique start and observed report. Delegator/Lead allocates `verify-start` to a separately assigned Verifier. Only the Verifier runs approved commands with `verify-exec`. PASS requires captured exit result, unchanged candidate and empty owned process scope. `verify-record` cannot assert PASS. Timeout, surviving descendants or unknown scope cannot become successful verification.
5. **Decision:** Manager/Lead records `decision-record` with immutable rationale. Delegator/Lead uses `task-accept`, validating source report, signals, critique, candidate, passing verification and Manager decision. Task acceptance is not final human acceptance, push, merge or deployment.
6. **Cancel/recover:** Human-authorized `run-cancel` fences dispatch. `cancel-finalize` refuses outstanding ownership. `recover` is read-only and never auto-respawns. `verify-reconcile` requires observed stopped process ownership; absent parent PID alone is insufficient.

CLI assertions and local receipt files do not authenticate humans or provider-side delivery. Record only decisions and receipts actually observed. Mocked tests do not satisfy the live Windows gate.

## Bounded state reads

Do not re-read or re-render the ledger by hand; ask the ledger for the minimum you need.

- `packet --role <role> [--agent <name>]` prints the bounded next-step directive: run/state/backend/epoch, `NEXT` (the owning command, or `none for role=...` when another role owns it), the role's allowed `MAY-RUN` commands, plus `READY`, `ACTIVE` and open `BLOCKERS`. Use it instead of reading a status document.
- `status [--human] [--ids]`, `next`, `task-show --task <id>` and `events --limit <n>` return compact projections; prefer them over prose logs.
- `export` prints the non-authoritative generated view. `export --write NAME.md` writes that same view to one `NAME.md` file inside the run directory (atomic; reserved names such as `SESSION.md` are refused, and no nested paths). The file is generated only: regenerate it instead of editing, and never feed it back as a ledger input.

## Composed receipts

`role-confirm`, `attempt-ready`, `attempt-start` and `attempt-report` accept `--compose-receipt` in place of `--receipt`. The ledger writes the exact correlated envelope it already validates to `receipts/<kind>-<event>.receipt` (atomically), so the boilerplate no longer costs an authored file. Supply exactly one of `--receipt` or `--compose-receipt`; supplying neither or both is refused.

This changes only who writes the envelope. The substantive artifact stays yours (`--evidence`), the receipt is still stored on disk and hashed into the same immutable columns, and the same envelope/hash validation runs either way. Nothing about observation, ordering or the evidence chain is relaxed.

## Shared heavy-verification gate

Both backends use `scripts/verification_guard.py` and the same machine lock under `~/.herdr-locks` (or one shared `HERDR_LOCKS_DIR`). SQLite uses it automatically through `verify-start`/`verify-exec`. For Markdown, freeze the checkout and wrap each already-approved heavy command:

```text
python <controller>/scripts/verification_guard.py --run-dir <run> --verification <id> --checkout <integration-checkout> --timeout 300 -- <approved-command>
```

Record tested SHA and captured result in SESSION.md. A changed checkout makes the result indeterminate. Windows checks enter a Job Object before execution; Linux checks enter a process group. Surviving children retain the gate. If a Windows owner crashes and its named job is inaccessible, reconciliation stays blocked. No stale gate is automatically cleared.

## Backend transfer

Run `run_backend.py --run-dir <run> switch --to <backend> --dry-run` first. A human-authored approval file must contain its exact `Backend transfer approval:` line; agents must not fabricate it. The real `switch --approval-ref <file>` archives source, validates staged destination and advances the selector epoch. Rebind role packets and obtain fresh acknowledgments before continuing.

Beyond pristine planning, supported checkpoints include validated structured plans/grants/tasks and released reservations whose named agent never started. Herdr topology must be reachable, ownership inactive, signals confirmed, and evidence unchanged. SQLite history becomes a versioned section inside the single active SESSION.md. Human text must match that section exactly before switching back. Free-form legacy edits require explicit normalization.

Launched external Worker history remains `TRANSFER_REQUIRES_RECONCILIATION`: current Herdr observations do not retain descendant ownership. Transfer never stops agents or closes panes. `recover` rolls back before source retirement or completes the verified target after retirement; unfinished transfer blocks ledger access. Clear a leftover backend lock only after observing its process stopped.
