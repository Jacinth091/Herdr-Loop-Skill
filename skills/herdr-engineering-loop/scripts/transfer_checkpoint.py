"""Strict, single-document checkpoints for observed-quiescent schema-v3 runs."""
import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3

TABLES = ('run', 'task', 'attempt', 'candidate', 'verification', 'decision', 'critique', 'finding', 'signal', 'claim', 'event')
MARKER = '<!-- HERDR_CHECKPOINT_V1 -->'


def snapshot(db):
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute('PRAGMA user_version').fetchone()[0] != 3:
            raise RuntimeError('UNSUPPORTED_SCHEMA: explicit upgrade required')
        return {table: [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY rowid')] for table in TABLES}


def digest(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def render(profile, state):
    run = state['run'][0]
    lines = [f"# Herdr Run: {run['id']}", f"Status: {run['state']}", f"Repo: `{run['repo']}`",
             f"Approved plan: {run['plan_path']} SHA-256 {run['plan_sha']}",
             f"Execution grant: {run['grant_ref']}",
             'Verification lock: NONE; historical results retain their original candidate and evidence.',
             '', '## Task frontier',
             '| Task | User-facing title | Expected outcome | Depends on | State | Accepted candidate |',
             '| --- | --- | --- | --- | --- | --- |']
    for task in state['task']:
        lines.append('| ' + ' | '.join((task['id'], task['title'], task['outcome'],
                                       ','.join(json.loads(task['deps'])) or '-', task['state'],
                                       task['accepted_candidate'] or '-')) + ' |')
    lines += ['', '## Evidence and history',
              'The checkpoint below is part of this active ledger. Human edits require reconciliation before transfer.',
              'All former role packets require refresh and a new acknowledgment before dispatch.',
              MARKER, '```json', json.dumps(dict(schema=1, profile=profile, tables=state), indent=2, sort_keys=True), '```', '']
    return '\n'.join(lines)


def parse(text):
    if text.count(MARKER) != 1:
        raise RuntimeError('TRANSFER_REQUIRES_RECONCILIATION: checkpoint missing or duplicated')
    tail = text.split(MARKER, 1)[1].strip()
    if not tail.startswith('```json\n') or not tail.endswith('\n```'):
        raise RuntimeError('TRANSFER_REQUIRES_RECONCILIATION: checkpoint format invalid')
    data = json.loads(tail[8:-4])
    if data.get('schema') != 1 or set(data.get('tables', {})) != set(TABLES):
        raise RuntimeError('TRANSFER_REQUIRES_RECONCILIATION: unknown checkpoint schema')
    if render(data['profile'], data['tables']) != text:
        raise RuntimeError('TRANSFER_REQUIRES_RECONCILIATION: human text/checkpoint changed; normalize explicitly')
    return data['profile'], data['tables']


def restore(db, state):
    import herdr_ledger
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(herdr_ledger.SCHEMA)
        conn.execute('PRAGMA user_version=3')
        for table in TABLES:
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info({table})')]
            for row in state[table]:
                if set(row) != set(columns):
                    raise RuntimeError('TRANSFER_REQUIRES_RECONCILIATION: checkpoint columns differ')
                conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                             [row[c] for c in columns])
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise RuntimeError('TRANSFER_REQUIRES_RECONCILIATION: broken references')
        conn.commit()


def validate(root, state, observe=True):
    import herdr_ledger as ledger
    import verification_guard as guard
    prefix = 'TRANSFER_REQUIRES_RECONCILIATION: '
    if set(state) != set(TABLES) or len(state['run']) != 1:
        raise RuntimeError(prefix + 'invalid run/checkpoint shape')
    run = state['run'][0]
    if run['id'] != root.name or run['state'] not in ('PLANNING', 'EXECUTING', 'CANCELLED'):
        raise RuntimeError(prefix + 'run identity or cancellation state unresolved')
    if any(c['active'] for c in state['claim']):
        raise RuntimeError(prefix + 'active claims remain')
    if any(a['state'] not in ('INTERRUPTED', 'REPORTED') for a in state['attempt']):
        raise RuntimeError(prefix + 'active/uncertain attempts remain')
    if any(v['state'] not in ('PASS', 'FAIL', 'INDETERMINATE') for v in state['verification']):
        raise RuntimeError(prefix + 'active/uncertain/stale verification remains')
    if any(t['state'] not in ('PLANNED', 'BLOCKED', 'ACCEPTED', 'CANCELLED') for t in state['task']):
        raise RuntimeError(prefix + 'task frontier requires reconciliation')
    if guard.lock_path().exists():
        owner = json.loads(guard.lock_path().read_text(encoding='utf8'))
        if owner.get('run_id') == str(root.resolve()):
            raise RuntimeError(prefix + 'machine verification gate held by run')
    latest = {}
    for event in state['event']:
        if event['op'] == 'role-activation':
            receipt = json.loads(event['receipt'])
            latest[receipt['operation_id']] = receipt
    if any(r['status'] not in ('CONFIRMED_APPLIED', 'CONFIRMED_NOT_APPLIED', 'DENIED') for r in latest.values()):
        raise RuntimeError(prefix + 'unconfirmed prompt submissions remain')
    pairs = {'run': [('plan_path', 'plan_sha')], 'task': [('brief_path', 'brief_sha')],
             'attempt': [('report_path', 'report_sha')], 'verification': [('evidence_path', 'evidence_sha')],
             'decision': [('rationale_path', 'rationale_sha')], 'critique': [('report_path', 'report_sha')],
             'finding': [('evidence_path', 'evidence_sha')],
             'signal': [('evidence_path', 'evidence_sha'), ('receipt_path', 'receipt_sha')]}
    for table, fields in pairs.items():
        for row in state[table]:
            for path, sha in fields:
                if row[path] or row[sha]:
                    if ledger.relative_evidence(root, row[path])[1] != row[sha]:
                        raise RuntimeError(prefix + f'{table} evidence changed')
    if run['grant_ref'] and not any(d['authority'] == 'HUMAN' and d['provenance'] == 'execution-grant'
                                  and d['rationale_path'] == run['grant_ref'] for d in state['decision']):
        raise RuntimeError(prefix + 'human execution grant provenance missing')
    for candidate in state['candidate']:
        if candidate['state'] != 'FROZEN' or not ledger.candidate_identity(candidate):
            raise RuntimeError(prefix + 'candidate changed or stale')
    for task in state['task']:
        if task['state'] == 'ACCEPTED':
            candidate = task['accepted_candidate']
            if (not any(c['id'] == candidate and c['task_id'] == task['id'] for c in state['candidate']) or
                    not any(v['candidate_id'] == candidate and v['state'] == 'PASS' for v in state['verification']) or
                    not any(d['subject'] == candidate and d['authority'] == 'MANAGER' and d['disposition'] == 'APPROVED' for d in state['decision'])):
                raise RuntimeError(prefix + 'accepted candidate provenance incomplete')
    if observe:
        # No inferred quiescence: transport failure or missing observation keeps the source intact.
        agents = ledger.herdr_json('agent', 'list')
        panes = ledger.herdr_json('pane', 'list')
        if not isinstance(agents.get('agents'), list) or not isinstance(panes.get('panes'), list):
            raise RuntimeError(prefix + 'Herdr topology response incomplete')
        for attempt in state['attempt']:
            if attempt['process_pid'] or attempt['pane']:
                # shortcut: Herdr does not retain descendant ownership for external workers; admit these only after a proven process-scope API exists.
                raise RuntimeError(prefix + 'external worker descendant ownership is unavailable')
            ledger.observe_unstarted(attempt['agent_hint'])
            if not ledger.git(attempt['checkout'])[3]:
                raise RuntimeError(prefix + 'former worker checkout dirty')
