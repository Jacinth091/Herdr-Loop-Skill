# SQLite ledger pilot: opt-in, NOT live-session authority

Load this reference **only** when asked to evaluate or pilot local transactional state instead of AI-authored Markdown. It is not part of normal Herdr task execution and does not authorize converting active sessions.

## Decision and boundaries

- Existing v2.7-style runs keep their current Delegator-owned `SESSION.md` authority. Do **not** run the SQLite pilot beside, against, or over an existing `SESSION.md` run.
- For a **new, explicitly authorized offline pilot**, create one SQLite database per fresh run directory. It must not contain `SESSION.md`. `PLAN.md` remains the approved scope baseline; Git owns commits; Herdr owns observed pane/process reality; Manager owns semantic acceptance.
- The pilot CLI is located at `../scripts/herdr_ledger.py` relative to this file, and uses Python standard library `sqlite3` only. It does **not** launch workers, deliver/ingest Signals, inspect Herdr agents, run tests, or grant permissions. `attempt-attach` records a human/operator-supplied observation; the client must first inspect the actual Herdr process independently.
- Do not let a model write free-form SQL. Use only named, validated commands with a unique `--request-id` for state transitions. Same request and payload returns the same receipt; same key with different payload fails.
- Do not create a second mutable `state.json`, events Markdown or SESSION file for this pilot. Normal status queries are bounded. `status --human` displays descriptive task titles rather than task IDs.
- The pilot is **incomplete** for real-world cutover. It lacks live Herdr transport/receipt verification, critique-finding deltas, decisions and cancellation state handling, historical import/export, and full Windows runtime verification. `task-accept` requires a Manager decision evidence file but cannot cryptographically verify who authored it. Do not entrust an actual governed session to this CLI yet.

## Minimal offline tracer

Create a **throwaway** Git fixture checkout and sibling worker worktree; commit the worker change, then cherry-pick into a clean integration checkout using Git. Create a new external run folder with `PLAN.md`, `TASK.md`, `REPORT.md`, `VERIFY.log`, and `MANAGER.txt`. Keep the run directory **outside** the Git checkout to avoid polluting candidate cleanliness.

Invoke from the pack root (Windows: replace `python3` with `py -3`):

```text
python3 skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <fresh-run> init --run-id demo --repo <integration-checkout> --plan PLAN.md --grant-ref <actual-human-execution-grant>
python3 skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <fresh-run> task-add --task T01 --title "Fix duplicate records" --outcome "One record per request" --brief TASK.md --request-id add-01
python3 skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <fresh-run> next
python3 skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <fresh-run> attempt-reserve --task T01 --attempt T01-A1 --checkout <worker-checkout> --agent worker_a1 --provider codex --expected-model gpt-6-luna --expected-effort xhigh --request-id reserve-01
# Simulate/observe a genuine worker launch externally. The CLI never launches it.
python3 skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <fresh-run> attempt-attach --attempt T01-A1 --pane <observed-pane-id> --agent worker_a1 --model <observed-effective-model> --effort <observed-effective-effort> --request-id attach-01
python3 skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <fresh-run> attempt-report --attempt T01-A1 --source-sha <real-worker-head-sha> --evidence REPORT.md --request-id report-01
# Independently integrate committed changes to the clean integration checkout.
python3 skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <fresh-run> candidate-freeze --task T01 --candidate C01 --checkout <integration-checkout> --request-id freeze-01
python3 skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <fresh-run> verify-start --verification V01 --candidate C01 --verifier haiku_tester --request-id verify-01
# Execute the approved verification OUTSIDE the CLI, one local heavy process at a time.
python3 skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <fresh-run> verify-record --verification V01 --result PASS --exit-code 0 --evidence VERIFY.log --request-id verified-01
python3 skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <fresh-run> task-accept --task T01 --candidate C01 --decision MANAGER.txt --request-id accepted-01
python3 skills/herdr-engineering-loop/scripts/herdr_ledger.py --run-dir <fresh-run> status --human
```

`VERIFY.log` is only evidence **when the real command actually ran and the exit code was observed**. The CLI accepts a caller-provided exit code; never interpret its own receipt as proof a test was executed. Likewise a provided Manager decision file is not independently authenticated by SQLite.

## Safety properties demonstrated offline

1. Task readiness is derived from dependency acceptance, not a second `READY` state.
2. Reservation precedes external spawning; `recover` flags unfinished reservations and **does not respawn**.
3. Named transitions are atomic; retries return the same receipt or conflict. An observed model/effort mismatch blocks attempt activation.
4. Checkout claims enforce one active scheduled writer or verifier per checkout; fenced generations block stale release.
5. Freeze requires clean Git HEAD/tree identity; changed HEAD/dirty checkout makes PASS stale or acceptance unavailable.
6. Evidence refs are file-hashed and confined to the run root; routine status never dumps full reports.
7. Existing `SESSION.md` runs are rejected for pilot initialization, preventing two live state authorities.

## Promotion gates: do not silently skip

Before a real new-run cutover, inspect actual installed skill sources and the user's Herdr CLI; add authenticated/observed agent-prompt receipt handling, decisions/finding deltas and cancellation, safe recovery, import/export decisions, Windows tests, and one end-to-end Herdr tracer. Prove quality gates at least as strong as the old workflow; compare **measured** tokens if provider telemetry exists, otherwise label file/context byte measures as proxies. Old runs remain untouched and must not be migrated by interpreting worker prose as accepted results. Never auto-promote the pilot.
