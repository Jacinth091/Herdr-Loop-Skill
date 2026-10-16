# Ledger command reference (generated)

Generated from the ledger CLI itself (its parser and role maps). Do not hand-edit this file:
regenerate it with `python tools/render_reference.py`. Never regenerate it by shell
redirection; Windows PowerShell re-encodes the stream as UTF-16LE and corrupts the file.

Read one role's list with `reference --role <role>`.

## Role command lists

- `manager`: `decision-record`, `events`, `export`, `finding-record`, `next`, `packet`, `plan-approve`, `role-confirm`, `status`, `task-show`
- `delegator`: `attempt-attach`, `attempt-ready`, `attempt-release`, `attempt-report`, `attempt-reserve`, `attempt-start`, `cancel-block`, `cancel-finalize`, `candidate-freeze`, `events`, `export`, `next`, `packet`, `role-confirm`, `run-authorize`, `run-cancel`, `status`, `task-accept`, `task-add`, `task-retry`, `task-show`, `verify-abort`, `verify-reconcile`, `verify-start`
- `lead`: `attempt-attach`, `attempt-ready`, `attempt-release`, `attempt-report`, `attempt-reserve`, `attempt-start`, `cancel-block`, `cancel-finalize`, `candidate-freeze`, `decision-record`, `events`, `export`, `finding-record`, `next`, `packet`, `plan-approve`, `role-confirm`, `run-authorize`, `run-cancel`, `status`, `task-accept`, `task-add`, `task-retry`, `task-show`, `verify-abort`, `verify-reconcile`, `verify-start`
- `worker`: `events`, `export`, `next`, `packet`, `role-confirm`, `status`, `task-show`
- `verifier`: `events`, `export`, `next`, `packet`, `role-confirm`, `status`, `task-show`, `verify-exec`, `verify-reconcile`, `verify-record`
- `critic`: `critique-record`, `events`, `export`, `finding-record`, `next`, `packet`, `role-confirm`, `status`, `task-show`

Every role may also run the read-only commands `events`, `export`, `next`, `packet`, `recover`, `status`, `task-show`.
Each assigned role records its own `role-confirm` activation acknowledgment.

## Commands

- `attempt-attach --attempt <attempt> --pane <pane> --agent <agent> --request-id <request_id>`
- `attempt-ready --attempt <attempt> --operator <operator> --operator-pane <operator_pane> [--sender-pane <sender_pane>] --event-id <event_id> --evidence <evidence> [--receipt <receipt>] --request-id <request_id> [--compose-receipt]`
- `attempt-release --attempt <attempt> --generation <generation> --observation <observation> --request-id <request_id>`
- `attempt-report --attempt <attempt> --evidence <evidence> --source-sha <source_sha> --event-id <event_id> --recipient <recipient> [--receipt <receipt>] --request-id <request_id> [--compose-receipt]`
- `attempt-reserve --task <task> --attempt <attempt> --checkout <checkout> --agent <agent> --provider <provider> [--expected-model <expected_model>] [--expected-effort <expected_effort>] --request-id <request_id>`
- `attempt-start --attempt <attempt> --operator <operator> --operator-pane <operator_pane> --sender-pane <sender_pane> --event-id <event_id> --evidence <evidence> [--receipt <receipt>] --request-id <request_id> [--compose-receipt]`
- `cancel-block --evidence <evidence> --request-id <request_id>`
- `cancel-finalize --decision-id <decision_id> --request-id <request_id>`
- `candidate-freeze --task <task> --candidate <candidate> --checkout <checkout> --request-id <request_id>`
- `critique-record --review <review> --candidate <candidate> --critic <critic> --report <report> --request-id <request_id>`
- `decision-record --decision-id <decision_id> --subject <subject> --authority <MANAGER|HUMAN> --actor <actor> --attestor <attestor> --disposition <APPROVED|REJECTED> --provenance <provenance> --rationale <rationale> --request-id <request_id>`
- `events [--limit <1..20>] [--json]`
- `export [--write <NAME.md>]`
- `finding-record --finding <finding> --review <review> --actor <actor> --severity <BLOCKING|HIGH|MEDIUM|LOW> --disposition <OPEN|RESOLVED|DISMISSED> --evidence <evidence> [--decision-id <decision_id>] --request-id <request_id>`
- `init --run-id <run_id> --repo <repo> [--max-workers <max_workers>] [--plan <plan>] [--grant-ref <grant_ref>] [--parallel-approved] [--bootstrap]`
- `next [--json]`
- `packet --role <manager|delegator|lead|worker|verifier|critic> [--agent <agent>] [--json]`
- `plan-approve --plan <plan> --decision-id <decision_id> --authority <MANAGER|HUMAN> --actor <actor> --attestor <attestor> --rationale <rationale> --request-id <request_id>`
- `recover [--json]`
- `reference [--role <manager|delegator|lead|worker|verifier|critic>]`
- `role-confirm --operation-id <operation_id> --pane <pane> --recipient <recipient> [--recipient-pane <recipient_pane>] --evidence <evidence> [--receipt <receipt>] [--compose-receipt]`
- `run-authorize --grant-ref <grant_ref> --decision-id <decision_id> --actor <actor> --attestor <attestor> --request-id <request_id>`
- `run-cancel --decision-id <decision_id> --actor <actor> --attestor <attestor> --authority-source <authority_source> --reason <reason> --request-id <request_id>`
- `status [--human] [--ids] [--json]`
- `task-accept --task <task> --candidate <candidate> --decision-id <decision_id> --request-id <request_id>`
- `task-add --task <task> --title <title> --outcome <outcome> --brief <brief> [--deps <deps>] --request-id <request_id> [--critique-required]`
- `task-retry --task <task> --decision-id <decision_id> --request-id <request_id>`
- `task-show --task <task> [--json]`
- `upgrade`
- `verify-abort --verification <verification> --reason <reason> --request-id <request_id>`
- `verify-exec --verification <verification> [--timeout <timeout>] --request-id <request_id> <argv>...`
- `verify-reconcile --verification <verification> --reason <reason> --request-id <request_id>`
- `verify-record --verification <verification> --result <PASS|FAIL|INDETERMINATE> --exit-code <exit_code> --evidence <evidence> --request-id <request_id>`
- `verify-start --verification <verification> --candidate <candidate> --verifier <verifier> [--gate <gate>] --request-id <request_id>`

## Global options

- `--run-dir <path>` (required for every command except `reference`, which ignores it)
- `--as-agent <name>` and `--epoch <n>` (required for governed mutations in a selected run)
