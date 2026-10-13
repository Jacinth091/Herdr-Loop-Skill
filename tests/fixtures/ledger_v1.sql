CREATE TABLE run(id TEXT PRIMARY KEY, repo TEXT NOT NULL, plan_path TEXT NOT NULL,
 plan_sha TEXT NOT NULL, grant_ref TEXT NOT NULL, max_workers INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'EXECUTING', generation INTEGER NOT NULL DEFAULT 1);
CREATE TABLE task(id TEXT PRIMARY KEY, title TEXT NOT NULL, outcome TEXT NOT NULL,
 deps TEXT NOT NULL, brief_path TEXT NOT NULL, brief_sha TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'PLANNED', accepted_candidate TEXT);
CREATE TABLE attempt(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task(id),
 kind TEXT NOT NULL, agent_hint TEXT NOT NULL, checkout TEXT NOT NULL,
 expected_model TEXT NOT NULL, expected_effort TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'RESERVED', pane TEXT, observed_agent TEXT,
 observed_model TEXT, observed_effort TEXT, report_path TEXT, report_sha TEXT, source_sha TEXT,
 claim_generation INTEGER NOT NULL);
CREATE TABLE candidate(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task(id),
 checkout TEXT NOT NULL, head TEXT NOT NULL, tree TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'FROZEN');
CREATE TABLE verification(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidate(id),
 verifier TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'STARTED',
 evidence_path TEXT, evidence_sha TEXT, exit_code INTEGER, claim_generation INTEGER NOT NULL);
CREATE TABLE claim(resource TEXT PRIMARY KEY, owner TEXT NOT NULL,
 kind TEXT NOT NULL, generation INTEGER NOT NULL, active INTEGER NOT NULL);
CREATE TABLE event(seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE,
 digest TEXT NOT NULL, op TEXT NOT NULL, receipt TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX idx_attempt_task ON attempt(task_id);
CREATE INDEX idx_candidate_task ON candidate(task_id);
PRAGMA user_version=1;
