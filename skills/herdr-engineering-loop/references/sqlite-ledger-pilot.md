# SQLite ledger continuation (gated source implementation)

The installed role workflow still uses `SESSION.md`. This CLI is for new, explicitly authorized disposable runs while the Windows Herdr tracer and authority gates are completed. Do not point it at a Markdown run, migrate a historical run, or make SQLite the default. `role_bind.py` refuses to create Markdown state in a run that already has `ledger.sqlite`.

The CLI lives at `../scripts/herdr_ledger.py` relative to this reference. Use `python herdr_ledger.py --run-dir <run> <command> --help` for exact arguments. The run directory must be outside the clean Git candidate checkout and contain an approved immutable `PLAN.md` and grant reference. `init` creates schema v2. `upgrade` is explicit for an existing v1 SQLite-only pilot, writes `ledger.v1.backup.sqlite` through SQLite's backup API, and marks v1 asserted live attempts and tests `UNCERTAIN`. Unknown schema versions are refused.

## Attempt and signal contract

`attempt-reserve` fences the assigned checkout. A valid code Worker still needs a separate Herdr pane and worktree, with model launch ownership and sandbox rules from `model-routing.md`. `attempt-attach` reads the installed `herdr agent get <name>` and `herdr pane process-info --pane <id>` JSON, checks name, pane, cwd, process PID and requested launch flags, and records `OBSERVED`. It does not start an agent or authorize edits. A claimed pane argument alone cannot attach. Herdr reads happen outside SQLite write transactions.

The trusted recipient/operator must inspect the actual prompt, worker-local `READY.md`, effective model/effort display and Herdr process before recording readiness. Copy the checked evidence into the run root. The READY evidence must contain these exact lines: `attempt=<id>`, `agent=<name>`, `pane=<id>`, `observed_model=<model>`, `observed_effort=<effort>`. For each signal, create one receipt file with the exact envelope on one line and `evidence_sha=<SHA-256 of the referenced evidence>` on another line:

```text
ROLE_READY <run> <attempt> <event> <worker> <worker-pane> <delegator>
TASK_START <run> <attempt> <event> <delegator> <delegator-pane> <worker>
REPORT_READY <run> <attempt> <event> <worker> <worker-pane> <delegator>
```

Use `attempt-ready` with the READY evidence, recipient's observed `--operator-pane` and receipt. The worker then writes a separate TASK_START acknowledgment containing `attempt=<id>`, `event=<event-id>`, `agent=<name>`, `pane=<worker-pane>`, and `plan_sha=<approved-plan-hash>`; `attempt-start` uses that evidence and its directed receipt. Both transitions recheck the same Herdr process and recipient pane. `attempt-report` requires its own report, correlated receipt and actual worker HEAD. Request IDs and event IDs are unique; identical requests replay one receipt, conflicting payloads fail. A successful `herdr agent prompt` alone is submission, not a received or processed signal. Receipt files are **operator attestations**, not cryptographic proof of the operator or transport. The live tracer must prove these observations before a production-quality SQLite run is permitted.

An ambiguous spawn, lost signal, wrong pane or transport denial remains reserved/observed/uncertain; do not start another agent. `recover` is read-only and does not respawn. `attempt-release` requires Herdr to show either the reserved name was never registered or the recorded PID is absent, plus a clean checkout and current fencing generation. It never removes a worktree. A blocked task needs a Manager decision bound to that task and `task-retry` before a fresh attempt ID can be reserved; previous attempts remain in the ledger.

## Integration, review and verification

`candidate-freeze` requires the reported source commit and a clean integrated HEAD with the same stable Git patch identity. It records the exact source SHA, integrated HEAD and tree. `verify-start` claims both the candidate checkout and the run-wide `heavy-local-verification` resource. `verify-exec -- <approved command>` captures the real process exit and raw output outside the write transaction; `verify-record` cannot record a manual PASS. An interrupted `verify-exec` stays `RUNNING` with its locks held for reconciliation. `verify-abort` is only for a STARTED check whose command never began.

Use `critique-record` and stable `finding-record` IDs for incremental issues; OPEN HIGH/BLOCKING findings prevent acceptance. `decision-record` records the Manager's candidate decision with actor, trusted attestor, provenance and hashed rationale. `task-accept` requires that decision, unchanged evidence, an independent passing verifier and the still-clean exact candidate. The ledger cannot independently authenticate a human or Manager merely from a file; the trusted operator must check the real authority source. A worker report, process exit or source commit is never acceptance.

`run-cancel` immediately fences new dispatch and records the human cancellation source. `cancel-finalize` refuses while a writer or test claim, active/uncertain attempt, or active/uncertain verification remains. `cancel-block` records the blocked state without releasing anything. No command closes human-owned panes, changes ACLs, pushes, deploys or disables a sandbox.

`status --human`, `next`, `task-show`, `recover` and `events` are bounded reads. `export` prints an on-demand generated, non-authoritative status with decisions and findings; it is never read back as mutable truth. `tools/progress_view.py --run-dir <run>` shows descriptive SQLite status and refuses competing `SESSION.md`; `--session` retains the legacy Markdown reader. Keep detailed reports and logs only as hashed substantive evidence.

## Remaining promotion gates

The CLI is not wired into live role packets or binder creation. Prove a disposable Windows Herdr worker and recipient through actual panes, effective runtime model/effort, receipt processing, recovery after a failed command, independent critique and verifier, exact candidate acceptance, cancellation, and installer/role regression checks. Resolve interrupted verification and stale locks with observed process identity. Compare provider token receipts when available, otherwise label prompt/context bytes as a proxy. Only then enable new-run SQLite opt-in; keep all active Markdown sessions unchanged. Switching the default for future runs is a later human promotion decision.
