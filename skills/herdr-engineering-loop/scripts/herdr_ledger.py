#!/usr/bin/env python3
"""Opt-in Herdr run ledger; live use remains gated on a verified tracer.

Python standard library only. Herdr reads and verification commands run outside
write transactions. The CLI never launches agents or elevates privileges.
"""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import os
import shutil
from datetime import datetime, timezone

VERSION = 2
TASK = re.compile(r"^T[0-9]{2,4}$")
ATTEMPT = re.compile(r"^(T[0-9]{2,4})-A[1-9][0-9]{0,2}$")
KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,69}$")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SCHEMA = """
CREATE TABLE run(id TEXT PRIMARY KEY, repo TEXT NOT NULL, plan_path TEXT NOT NULL,
 plan_sha TEXT NOT NULL, grant_ref TEXT NOT NULL, max_workers INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'EXECUTING', generation INTEGER NOT NULL DEFAULT 1);
CREATE TABLE task(id TEXT PRIMARY KEY, title TEXT NOT NULL, outcome TEXT NOT NULL,
 deps TEXT NOT NULL, brief_path TEXT NOT NULL, brief_sha TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'PLANNED', accepted_candidate TEXT,
 critique_required INTEGER NOT NULL DEFAULT 0);
CREATE TABLE attempt(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task(id),
 kind TEXT NOT NULL, agent_hint TEXT NOT NULL, checkout TEXT NOT NULL,
 expected_model TEXT NOT NULL, expected_effort TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'RESERVED', pane TEXT, observed_agent TEXT,
 observed_model TEXT, observed_effort TEXT, report_path TEXT, report_sha TEXT, source_sha TEXT,
 claim_generation INTEGER NOT NULL, terminal_id TEXT, process_pid INTEGER,
 ready_event TEXT, start_event TEXT);
CREATE TABLE candidate(id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task(id),
 checkout TEXT NOT NULL, head TEXT NOT NULL, tree TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'FROZEN', source_sha TEXT, integration_proof TEXT);
CREATE TABLE verification(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidate(id),
 verifier TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'STARTED',
 evidence_path TEXT, evidence_sha TEXT, exit_code INTEGER, claim_generation INTEGER NOT NULL,
 heavy_generation INTEGER NOT NULL, gate TEXT NOT NULL, command TEXT);
CREATE TABLE decision(id TEXT PRIMARY KEY, subject TEXT NOT NULL, authority TEXT NOT NULL,
 actor TEXT NOT NULL, attestor TEXT NOT NULL, disposition TEXT NOT NULL, provenance TEXT NOT NULL,
 rationale_path TEXT, rationale_sha TEXT);
CREATE TABLE critique(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidate(id),
 critic TEXT NOT NULL, report_path TEXT NOT NULL, report_sha TEXT NOT NULL);
CREATE TABLE finding(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidate(id),
 review_id TEXT NOT NULL REFERENCES critique(id),
 actor TEXT NOT NULL, severity TEXT NOT NULL, disposition TEXT NOT NULL, evidence_path TEXT NOT NULL,
 evidence_sha TEXT NOT NULL);
CREATE TABLE signal(event_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempt(id),
 kind TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL,
 sender_pane TEXT NOT NULL, evidence_path TEXT NOT NULL, evidence_sha TEXT NOT NULL,
 receipt_path TEXT NOT NULL, receipt_sha TEXT NOT NULL);
CREATE TABLE claim(resource TEXT PRIMARY KEY, owner TEXT NOT NULL,
 kind TEXT NOT NULL, generation INTEGER NOT NULL, active INTEGER NOT NULL);
CREATE TABLE event(seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE,
 digest TEXT NOT NULL, op TEXT NOT NULL, receipt TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX idx_attempt_task ON attempt(task_id);
CREATE INDEX idx_candidate_task ON candidate(task_id);
"""

class LedgerError(Exception):
    pass


def fail(message):
    raise LedgerError(message)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def relative_evidence(root, raw):
    """Only pre-existing, regular, non-escaping files are valid evidence."""
    value = Path(raw)
    if value.is_absolute() or ".." in value.parts or not raw:
        fail("evidence must be a relative path within the run directory")
    root = root.resolve()
    full = (root / value).resolve()
    try:
        full.relative_to(root)
    except ValueError:
        fail("evidence missing or outside trusted run directory")
    if not full.is_file():
        fail("evidence missing or outside trusted run directory")
    try:
        if full.stat().st_size > 20_000_000:
            fail("evidence too large for pilot capture")
        digest=fingerprint(full)
    except OSError:
        fail("BLOCKED: EVIDENCE_UNREADABLE")
    return full.relative_to(root).as_posix(), digest


def git(checkout):
    root = Path(checkout).resolve(strict=True)
    def g(*argv):
        cp = subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True,
                            timeout=12, check=False)
        if cp.returncode:
            fail("Git identity unavailable: " + " ".join(argv))
        return cp.stdout.strip()
    trusted = Path(g("rev-parse", "--show-toplevel")).resolve(strict=True)
    if trusted != root:
        fail("checkout must equal actual Git toplevel")
    head = g("rev-parse", "--verify", "HEAD")
    tree = g("rev-parse", "HEAD^{tree}")
    clean = not g("status", "--porcelain=v1", "--untracked-files=all")
    return str(trusted), head, tree, clean


def connect(root, read_only=False):
    db = root / "ledger.sqlite"
    if not db.exists():
        fail("ledger.sqlite is missing; initialize a new pilot run first")
    uri = db.resolve().as_uri() + "?mode=ro" if read_only else str(db)
    conn=None
    try:
        conn = sqlite3.connect(uri, uri=read_only, timeout=3.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=3000")
        version=conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError:
        if conn:conn.close()
        fail("BLOCKED: LEDGER_UNREADABLE")
    if version != VERSION:
        conn.close()
        fail("ledger schema is not v2; use explicit upgrade for v1, reject unknown versions")
    return conn


def upgrade(root):
    db = root / "ledger.sqlite"
    if (root / "SESSION.md").exists() or not db.is_file():
        fail("upgrade requires an existing SQLite-only run")
    with closing(sqlite3.connect(str(db), timeout=3.0, isolation_level=None)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=3000")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == VERSION:
            return "OK schema=2 already-current"
        if version != 1:
            fail("unknown ledger schema; upgrade refused")
        backup = root / "ledger.v1.backup.sqlite"
        if backup.exists():
            fail("v1 backup already exists; inspect before retrying")
        with closing(sqlite3.connect(str(backup))) as destination:
            conn.backup(destination)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for sql in (
                "ALTER TABLE attempt ADD COLUMN terminal_id TEXT",
                "ALTER TABLE attempt ADD COLUMN process_pid INTEGER",
                "ALTER TABLE attempt ADD COLUMN ready_event TEXT",
                "ALTER TABLE attempt ADD COLUMN start_event TEXT",
                "ALTER TABLE candidate ADD COLUMN source_sha TEXT",
                "ALTER TABLE candidate ADD COLUMN integration_proof TEXT",
                "ALTER TABLE task ADD COLUMN critique_required INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE verification ADD COLUMN heavy_generation INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE verification ADD COLUMN gate TEXT NOT NULL DEFAULT 'legacy-unverified'",
                "ALTER TABLE verification ADD COLUMN command TEXT",
                "CREATE TABLE decision(id TEXT PRIMARY KEY, subject TEXT NOT NULL, authority TEXT NOT NULL, actor TEXT NOT NULL, attestor TEXT NOT NULL, disposition TEXT NOT NULL, provenance TEXT NOT NULL, rationale_path TEXT, rationale_sha TEXT)",
                "CREATE TABLE critique(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidate(id), critic TEXT NOT NULL, report_path TEXT NOT NULL, report_sha TEXT NOT NULL)",
                "CREATE TABLE finding(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidate(id), review_id TEXT NOT NULL REFERENCES critique(id), actor TEXT NOT NULL, severity TEXT NOT NULL, disposition TEXT NOT NULL, evidence_path TEXT NOT NULL, evidence_sha TEXT NOT NULL)",
                "CREATE TABLE signal(event_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempt(id), kind TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL, sender_pane TEXT NOT NULL, evidence_path TEXT NOT NULL, evidence_sha TEXT NOT NULL, receipt_path TEXT NOT NULL, receipt_sha TEXT NOT NULL)",
            ):
                conn.execute(sql)
            # v1 asserted STARTED without a Herdr observation; force reconciliation.
            conn.execute("UPDATE attempt SET state='UNCERTAIN' WHERE state IN ('RESERVED','STARTED')")
            conn.execute("UPDATE verification SET state='UNCERTAIN' WHERE state='STARTED'")
            conn.execute("PRAGMA user_version=2")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return f"OK schema=2 backup={backup.name} v1-live-attempts=UNCERTAIN"


def herdr_json(*argv):
    if os.environ.get("HERDR_ENV") != "1":
        fail("BLOCKED: HERDR_CONTEXT_UNAVAILABLE")
    executable=shutil.which("herdr")
    if not executable:fail("BLOCKED: HERDR_CLI_UNAVAILABLE")
    try:
        result = subprocess.run([executable, *argv], capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail("BLOCKED: HERDR_TRANSPORT_UNAVAILABLE: " + type(exc).__name__)
    if result.returncode:
        try:
            err = json.loads(result.stdout).get("error", {})
            if "message" in err:
                fail("BLOCKED: HERDR_ERROR: " + err["message"])
        except (ValueError, KeyError, TypeError):
            pass
        fail("BLOCKED: HERDR_TRANSPORT_UNAVAILABLE: " + (result.stderr.strip() or result.stdout.strip())[:100])
    try:
        return json.loads(result.stdout)["result"]
    except (ValueError, KeyError, TypeError):
        fail("BLOCKED: HERDR_RESPONSE_INVALID")


def observe_herdr(name, pane, checkout, provider):
    """Read installed Herdr state before the SQLite write transaction."""
    agent = herdr_json("agent", "get", name)["agent"]
    process = herdr_json("pane", "process-info", "--pane", pane)["process_info"]
    if (agent.get("name") != name or agent.get("pane_id") != pane or
            Path(agent.get("cwd", "")).resolve() != Path(checkout).resolve() or
            process.get("pane_id") != pane):
        fail("BLOCKED: HERDR_IDENTITY_MISMATCH")
    processes = process.get("foreground_processes", [])
    matching = [p for p in processes if p.get("name", "").lower().startswith(provider)]
    if len(matching) != 1 or not isinstance(matching[0].get("pid"), int):
        fail("BLOCKED: HERDR_PROCESS_UNCONFIRMED")
    p = matching[0]
    if Path(p.get("cwd", "")).resolve() != Path(checkout).resolve():
        fail("BLOCKED: HERDR_PROCESS_CWD_MISMATCH")
    argv = p.get("argv", [])
    model = ""
    effort = ""
    for i, arg in enumerate(argv[:-1]):
        if arg in ("-m", "--model"):
            model = argv[i + 1]
        if arg == "-c" and argv[i + 1].startswith("model_reasoning_effort="):
            effort = argv[i + 1].split("=", 1)[1].strip("\"'")
    return dict(agent=name, pane=pane, terminal=agent.get("terminal_id", ""), pid=p["pid"],
                model=model, effort=effort)


def observe_stopped(pane, pid):
    if not pane or not pid:fail("BLOCKED: missing process identity; cannot release claim")
    panes = herdr_json("pane", "list").get("panes", [])
    if any(p.get("pane_id") == pane for p in panes):
        process = herdr_json("pane", "process-info", "--pane", pane)["process_info"]
        if process.get("pane_id") != pane:
            fail("BLOCKED: pane identity changed; cannot release claim")
        if any(p.get("pid") == pid for p in process.get("foreground_processes", [])):
            fail("UNCERTAIN: observed agent process still present")
        return f"Herdr pane={pane} pid={pid} absent"
    return f"Herdr pane={pane} closed and pid={pid} absent"


def observe_unstarted(name):
    agents=herdr_json("agent","list").get("agents",[])
    if any(a.get("name")==name for a in agents):
        fail("UNCERTAIN: reserved agent name still registered")
    return f"Herdr agent={name} absent"


def reobserve_attempt(a):
    observed=observe_herdr(a["agent_hint"],a["pane"],a["checkout"],a["kind"])
    if (observed["pid"]!=a["process_pid"] or observed["terminal"]!=a["terminal_id"] or
            observed["model"]!=a["observed_model"] or observed["effort"]!=a["observed_effort"]):
        fail("BLOCKED: HERDR_PROCESS_CHANGED; reconcile attempt before signaling")


def observe_recipient(name, pane):
    agent=herdr_json("agent","get",name)["agent"]
    if agent.get("name")!=name or agent.get("pane_id")!=pane:
        fail("BLOCKED: SIGNAL_RECIPIENT_MISMATCH")


def one(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()


def get(conn, table, key):
    if table not in ("run", "task", "attempt", "candidate", "verification", "decision", "critique", "finding"):
        fail("unsupported entity type")
    row = one(conn, f"SELECT * FROM {table} WHERE id=?", (key,))
    if row is None: fail(f"{table} {key} not found")
    return row


def guarded(conn, root, allowed_states=("EXECUTING",), check_plan=True):
    run = one(conn, "SELECT * FROM run LIMIT 1")
    if not run: fail("run metadata missing")
    plan = (root / run["plan_path"]).resolve()
    if check_plan and (not plan.is_file() or fingerprint(plan) != run["plan_sha"]):
        fail("approved PLAN.md changed; halt until new authorized generation")
    if run["state"] not in allowed_states:
        fail("run is not executing; no new transitions allowed")
    return run


def action(conn, root, op, request_id, data, fn, allowed_states=("EXECUTING",), check_plan=True):
    if not KEY.fullmatch(request_id): fail("invalid request ID")
    payload = json.dumps({"op": op, "args": data}, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    try:
        conn.execute("BEGIN IMMEDIATE")
        previous = one(conn, "SELECT digest,receipt FROM event WHERE request_id=?", (request_id,))
        if previous:
            if previous["digest"] != digest: fail("CONFLICT: request ID reused with different arguments")
            conn.commit()
            return previous["receipt"]
        guarded(conn, root, allowed_states, check_plan)
        receipt = fn()
        conn.execute("INSERT INTO event(request_id,digest,op,receipt,created) VALUES(?,?,?,?,?)",
                     (request_id, digest, op, receipt, stamp()))
        conn.commit()
        return receipt
    except Exception:
        conn.rollback()
        raise


def claim(conn, resource, owner, kind):
    r = one(conn, "SELECT * FROM claim WHERE resource=?", (resource,))
    if r and r["active"]: fail("CONFLICT: resource already claimed")
    generation = r["generation"] + 1 if r else 1
    if r:
        conn.execute("UPDATE claim SET owner=?,kind=?,generation=?,active=1 WHERE resource=?",
                     (owner, kind, generation, resource))
    else:
        conn.execute("INSERT INTO claim VALUES(?,?,?,?,1)", (resource, owner, kind, generation))
    return generation


def release(conn, resource, owner, generation):
    r = one(conn, "SELECT * FROM claim WHERE resource=?", (resource,))
    if not r or not r["active"] or r["owner"] != owner or r["generation"] != generation:
        fail("CONFLICT: owner/fencing generation mismatch")
    conn.execute("UPDATE claim SET active=0 WHERE resource=?", (resource,))


def ready(conn, tid):
    t = get(conn, "task", tid)
    if t["state"] != "PLANNED": return False
    deps = json.loads(t["deps"])
    return all(get(conn, "task", d)["state"] == "ACCEPTED" for d in deps)


def replay(conn, op, request_id, data):
    previous=one(conn,"SELECT digest,receipt FROM event WHERE request_id=?",(request_id,))
    if not previous:return None
    payload=json.dumps({"op":op,"args":data},sort_keys=True,separators=(",",":"))
    if previous["digest"]!=hashlib.sha256(payload.encode()).hexdigest():
        fail("CONFLICT: request ID reused with different arguments")
    return previous["receipt"]


def candidate_identity(c):
    path, head, tree, clean = git(c["checkout"])
    return path == c["checkout"] and head == c["head"] and tree == c["tree"] and clean


def patch_identity(checkout, commit):
    if not re.fullmatch(r"[a-f0-9]{40,64}", commit):fail("invalid commit identity")
    shown = subprocess.run(["git", "-C", str(checkout), "show", "--format=", "--binary", commit],
                           capture_output=True, timeout=12, check=False)
    if shown.returncode or not shown.stdout:fail("source/integration patch unavailable")
    result = subprocess.run(["git", "patch-id", "--stable"], input=shown.stdout,
                            capture_output=True, text=False, timeout=12, check=False)
    if result.returncode or not result.stdout:fail("source/integration patch identity unavailable")
    return result.stdout.decode("ascii").split()[0]


def signal_receipt(root, run_id, kind, attempt, event_id, sender, pane, recipient, evidence, receipt):
    if not KEY.fullmatch(event_id) or sender == recipient:
        fail("invalid signal event or recipient")
    artifact_path, artifact_sha = relative_evidence(root, evidence)
    path, digest = relative_evidence(root, receipt)
    expected = f"{kind} {run_id} {attempt} {event_id} {sender} {pane} {recipient}"
    lines = (root / path).read_text(encoding="utf-8").splitlines()
    if expected not in lines or f"evidence_sha={artifact_sha}" not in lines:
        fail("signal receipt does not contain exact correlated envelope")
    return artifact_path, artifact_sha, path, digest


def operation(root, args):
    name = args.command
    if name == "upgrade":
        return upgrade(root)
    if name == "init":
        if not RUN_ID.fullmatch(args.run_id): fail("invalid run ID")
        if args.max_workers not in (1,2,3): fail("max-workers must be 1, 2 or 3")
        if args.max_workers > 1 and not args.parallel_approved:
            fail("parallel worker budget requires explicit approval")
        if (root / "SESSION.md").exists():
            fail("refusing to create a competing ledger for an existing SESSION.md run")
        if (root / "ledger.sqlite").exists(): fail("ledger already initialized")
        if not root.is_dir(): fail("run directory must be created explicitly")
        plan_path, plan_hash = relative_evidence(root, args.plan)
        repo_path = Path(args.repo).resolve(strict=True)
        if not (repo_path / ".git").exists():
            fail("repo must be an existing Git checkout")
        if not args.grant_ref.strip(): fail("explicit execution grant reference required")
        conn = sqlite3.connect(str(root / "ledger.sqlite"), timeout=3.0)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version={VERSION}")
            conn.execute("INSERT INTO run(id,repo,plan_path,plan_sha,grant_ref,max_workers) VALUES(?,?,?,?,?,?)",
                         (args.run_id, str(repo_path), plan_path, plan_hash, args.grant_ref, args.max_workers))
            conn.execute("INSERT INTO event(request_id,digest,op,receipt,created) VALUES(?,?,?,?,?)",
                         ("init:"+args.run_id, "init", "init", f"OK run={args.run_id} schema={VERSION}", stamp()))
            conn.commit()
            return f"OK run={args.run_id} schema={VERSION} mode=sqlite-pilot"
        finally:
            conn.close()
    with closing(connect(root, name in ("status", "next", "task-show", "recover", "events", "export"))) as conn:
        if name in ("status", "next", "task-show", "recover", "events", "export"):
            run = one(conn, "SELECT * FROM run LIMIT 1")
            if not run:fail("run metadata missing")
            if name == "task-show":
                t=get(conn, "task", args.task)
                return f"task={t['id']} state={t['state']} title={t['title']}\noutcome={t['outcome']} deps={','.join(json.loads(t['deps'])) or '-'} brief={t['brief_path']}"
            if name == "events":
                rows=conn.execute("SELECT seq,op,receipt FROM event ORDER BY seq DESC LIMIT ?", (args.limit,)).fetchall()
                return "\n".join(f"{r['seq']} {r['op']} {r['receipt']}" for r in rows)
            tasks = conn.execute("SELECT * FROM task ORDER BY id").fetchall()
            if name == "export":
                lines=["# Generated Herdr status (non-authoritative)","",
                       f"Run: {run['id']} | State: {run['state']} | Plan SHA-256: {run['plan_sha']}","",
                       "## Tasks", ""]
                for t in tasks:
                    lines.append(f"- {t['title']}: {t['state']} — {t['outcome']} (key {t['id']})")
                    candidates=conn.execute("SELECT * FROM candidate WHERE task_id=? ORDER BY rowid",(t["id"],)).fetchall()
                    for c in candidates:
                        lines.append(f"  - Candidate {c['id']}: {c['head']} ({c['state']}), source {c['source_sha'] or 'unreconciled'}")
                        for v in conn.execute("SELECT * FROM verification WHERE candidate_id=? ORDER BY rowid",(c["id"],)):
                            lines.append(f"  - Verification {v['id']}: {v['state']} gate={v['gate']} evidence={v['evidence_path'] or '-'}")
                lines += ["", "## Decisions and findings", ""]
                for d in conn.execute("SELECT * FROM decision ORDER BY rowid"):
                    lines.append(f"- Decision {d['id']}: {d['disposition']} subject={d['subject']} authority={d['authority']} source={d['provenance']}")
                for f in conn.execute("SELECT * FROM finding ORDER BY rowid"):
                    lines.append(f"- Finding {f['id']}: {f['severity']} {f['disposition']} evidence={f['evidence_path']}")
                return "\n".join(lines)+"\n"
            if name == "next":
                can=[t for t in tasks if run["state"]=="EXECUTING" and ready(conn,t["id"])]
                return "\n".join(f"READY {t['id']} {t['title']}" for t in can[:5]) or "NO_READY_TASK"
            if name == "status":
                counts={s:sum(1 for t in tasks if t["state"]==s) for s in ("PLANNED","ACTIVE","REPORTED","ACCEPTED","BLOCKED")}
                nexts=[t["id"] for t in tasks if run["state"]=="EXECUTING" and ready(conn,t["id"])]
                active=[t["id"] for t in tasks if t["state"] in ("ACTIVE","REPORTED")]
                if args.human:
                    lines=["# Work progress",f"Run: {run['state'].replace('_',' ').title()}"]
                    labels={"PLANNED":"Planned", "ACTIVE":"In progress", "REPORTED":"Awaiting independent review", "BLOCKED":"Blocked", "ACCEPTED":"Accepted", "CANCELLED":"Cancelled"}
                    for t in tasks[:12]:
                        suffix=f" ({t['id']})" if args.ids else ""
                        lines.append(f"- **{t['title']}{suffix}**: {labels.get(t['state'],'Needs reconciliation')} | {t['outcome']}")
                    if len(tasks)>12:lines.append(f"({len(tasks)-12} more; use task-show)")
                    return "\n".join(lines)
                return f"run={run['id']} state={run['state']} mode=sqlite-pilot next={','.join(nexts[:3]) or '-'} active={','.join(active[:3]) or '-'} accepted={counts['ACCEPTED']} blocked={counts['BLOCKED']} tasks={len(tasks)}"
            unknown=conn.execute("SELECT id,state,pane FROM attempt WHERE state IN ('RESERVED','OBSERVED','READY','STARTED','UNCERTAIN','BLOCKED') ORDER BY id LIMIT 5").fetchall()
            checks=conn.execute("SELECT id FROM verification WHERE state IN ('STARTED','RUNNING','UNCERTAIN') ORDER BY id LIMIT 5").fetchall()
            return (f"recovery={run['id']} state={run['state']} never-auto-respawn\n"+
                    "\n".join(f"ATTENTION attempt={a['id']} state={a['state']} pane={a['pane'] or '?'} observe-Herdr-first" for a in unknown)+
                    ("\n" if unknown else "")+
                    "\n".join(f"ATTENTION verify={c['id']} lock-held inspect-process" for c in checks))
        request = args.request_id
        if name == "run-cancel":
            path,digest=relative_evidence(root,args.reason)
            data=dict(decision=args.decision_id,actor=args.actor,attestor=args.attestor,source=args.authority_source,reason=path,hash=digest)
            def act():
                run=one(conn,"SELECT * FROM run LIMIT 1")
                if not all((args.actor.strip(),args.attestor.strip(),args.authority_source.strip())):
                    fail("cancellation requires human actor, trusted attestor and authority source")
                conn.execute("INSERT INTO decision VALUES(?,?,?,?,?,?,?,?,?)",
                             (args.decision_id,run["id"],"HUMAN",args.actor,args.attestor,"APPROVED",args.authority_source,path,digest))
                conn.execute("UPDATE run SET state='CANCELLING'")
                return f"OK run={run['id']} state=CANCELLING; inspect and stop run-owned processes"
            return action(conn,root,name,request,data,act,check_plan=False)
        if name == "cancel-block":
            path,digest=relative_evidence(root,args.evidence)
            data=dict(evidence=path,hash=digest)
            def act():
                conn.execute("UPDATE run SET state='CANCELLATION_BLOCKED'")
                return "OK run state=CANCELLATION_BLOCKED; claims preserved"
            return action(conn,root,name,request,data,act,allowed_states=("CANCELLING",),check_plan=False)
        if name == "cancel-finalize":
            data=dict(decision=args.decision_id)
            def act():
                run=one(conn,"SELECT * FROM run LIMIT 1")
                d=get(conn,"decision",args.decision_id)
                if d["subject"]!=run["id"] or d["authority"]!="HUMAN" or d["disposition"]!="APPROVED":
                    fail("human cancellation decision missing")
                active=one(conn,"SELECT COUNT(*) AS n FROM claim WHERE active=1")["n"]
                attempts=one(conn,"SELECT COUNT(*) AS n FROM attempt WHERE state IN ('RESERVED','OBSERVED','READY','STARTED','UNCERTAIN','BLOCKED')")["n"]
                checks=one(conn,"SELECT COUNT(*) AS n FROM verification WHERE state IN ('STARTED','RUNNING','UNCERTAIN')")["n"]
                if active or attempts or checks:fail("CANCELLATION_BLOCKED: live or uncertain ownership remains")
                conn.execute("UPDATE run SET state='CANCELLED'")
                conn.execute("UPDATE task SET state='CANCELLED' WHERE state!='ACCEPTED'")
                return f"OK run={run['id']} state=CANCELLED"
            return action(conn,root,name,request,data,act,
                          allowed_states=("CANCELLING","CANCELLATION_BLOCKED"),check_plan=False)
        if name == "task-add":
            if not TASK.fullmatch(args.task):fail("invalid task ID")
            if (not args.title.strip() or len(args.title)>90 or not args.outcome.strip() or len(args.outcome)>240
                    or any(ord(c)<32 or c in "<>|" for c in args.title+args.outcome)):
                fail("approved safe one-line title/outcome required")
            path, digest=relative_evidence(root,args.brief)
            deps=[d.strip() for d in args.deps.split(",") if d.strip()]
            if len(set(deps))!=len(deps) or args.task in deps:fail("duplicate/self dependency")
            data=dict(task=args.task,title=args.title,outcome=args.outcome,deps=deps,brief=path,hash=digest,critique=args.critique_required)
            def act():
                for dep in deps:
                    if not TASK.fullmatch(dep):fail("invalid dependency")
                    get(conn,"task",dep)
                conn.execute("INSERT INTO task(id,title,outcome,deps,brief_path,brief_sha,critique_required) VALUES(?,?,?,?,?,?,?)",
                             (args.task,args.title,args.outcome,json.dumps(deps),path,digest,int(args.critique_required)))
                return f"OK task={args.task} state=PLANNED"
            return action(conn,root,name,request,data,act)
        if name == "decision-record":
            path,digest=relative_evidence(root,args.rationale)
            data=dict(decision=args.decision_id,subject=args.subject,authority=args.authority,actor=args.actor,
                      attestor=args.attestor,disposition=args.disposition,provenance=args.provenance,rationale=path,hash=digest)
            def act():
                if not all((args.actor.strip(),args.attestor.strip(),args.provenance.strip())):
                    fail("decision requires actor, trusted operator attestor and authority provenance")
                if args.authority=="MANAGER":
                    task_id=(get(conn,"task",args.subject)["id"] if TASK.fullmatch(args.subject) else
                             get(conn,"candidate",args.subject)["task_id"])
                    worker=one(conn,"SELECT agent_hint FROM attempt WHERE task_id=? ORDER BY rowid DESC LIMIT 1",(task_id,))
                    if worker and args.actor==worker["agent_hint"]:fail("worker cannot approve own task or candidate")
                else:
                    run=one(conn,"SELECT id FROM run LIMIT 1")
                    if args.subject!=run["id"]:fail("human run decision must target the run")
                conn.execute("INSERT INTO decision VALUES(?,?,?,?,?,?,?,?,?)",
                             (args.decision_id,args.subject,args.authority,args.actor,args.attestor,args.disposition,args.provenance,path,digest))
                return f"OK decision={args.decision_id} disposition={args.disposition} subject={args.subject}"
            return action(conn,root,name,request,data,act)
        if name == "task-retry":
            data=dict(task=args.task,decision=args.decision_id)
            prior=replay(conn,name,request,data)
            if prior:return prior
            decision=get(conn,"decision",args.decision_id)
            if relative_evidence(root,decision["rationale_path"])[1]!=decision["rationale_sha"]:
                fail("retry decision rationale changed")
            def act():
                t=get(conn,"task",args.task)
                d=get(conn,"decision",args.decision_id)
                if t["state"]!="BLOCKED" or d["subject"]!=t["id"] or d["authority"]!="MANAGER" or d["disposition"]!="APPROVED":
                    fail("blocked task needs approved Manager retry decision")
                unresolved=one(conn,"SELECT id FROM attempt WHERE task_id=? AND state IN ('RESERVED','OBSERVED','READY','STARTED','UNCERTAIN','BLOCKED') LIMIT 1",(t["id"],))
                held=one(conn,"SELECT a.id FROM attempt a JOIN claim c ON c.resource=a.checkout AND c.active=1 WHERE a.task_id=? LIMIT 1",(t["id"],))
                if unresolved or held:fail("previous attempt still owns or may own resources")
                conn.execute("UPDATE task SET state='PLANNED' WHERE id=?",(t["id"],))
                return f"OK task={t['id']} state=PLANNED next-attempt-must-be-new"
            return action(conn,root,name,request,data,act)
        if name == "critique-record":
            path,digest=relative_evidence(root,args.report)
            data=dict(review=args.review,candidate=args.candidate,critic=args.critic,report=path,hash=digest)
            def act():
                c=get(conn,"candidate",args.candidate)
                worker=one(conn,"SELECT agent_hint FROM attempt WHERE task_id=? AND source_sha=? ORDER BY rowid DESC LIMIT 1",(c["task_id"],c["source_sha"]))
                if worker and args.critic==worker["agent_hint"]:fail("worker cannot critique own candidate")
                conn.execute("INSERT INTO critique VALUES(?,?,?,?,?)",(args.review,args.candidate,args.critic,path,digest))
                return f"OK critique={args.review} candidate={args.candidate}"
            return action(conn,root,name,request,data,act)
        if name == "finding-record":
            path,digest=relative_evidence(root,args.evidence)
            data=dict(finding=args.finding,review=args.review,severity=args.severity,disposition=args.disposition,
                      evidence=path,hash=digest,decision=args.decision_id,actor=args.actor)
            def act():
                review=get(conn,"critique",args.review)
                if args.disposition=="DISMISSED":
                    d=get(conn,"decision",args.decision_id)
                    if d["subject"]!=review["candidate_id"] or d["authority"]!="MANAGER" or d["disposition"]!="APPROVED":
                        fail("dismissal requires approved Manager decision for candidate")
                    if args.actor!=d["actor"]:fail("dismissal actor must match Manager decision")
                elif args.actor!=review["critic"]:
                    fail("finding disposition must be recorded by assigned critic")
                previous=one(conn,"SELECT * FROM finding WHERE id=?",(args.finding,))
                if previous and previous["candidate_id"]!=review["candidate_id"]:
                    fail("finding cannot move between candidates")
                conn.execute("INSERT INTO finding VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET review_id=excluded.review_id,actor=excluded.actor,severity=excluded.severity,disposition=excluded.disposition,evidence_path=excluded.evidence_path,evidence_sha=excluded.evidence_sha",
                             (args.finding,review["candidate_id"],args.review,args.actor,args.severity,args.disposition,path,digest))
                return f"OK finding={args.finding} disposition={args.disposition}"
            return action(conn,root,name,request,data,act)
        if name == "attempt-reserve":
            if not ATTEMPT.fullmatch(args.attempt) or not args.attempt.startswith(args.task+"-A"):
                fail("attempt ID must correspond to task ID")
            checkout=str(Path(args.checkout).resolve())
            data=dict(task=args.task,attempt=args.attempt,checkout=checkout,agent=args.agent,provider=args.provider,
                      model=args.expected_model,effort=args.expected_effort)
            def act():
                if args.provider not in ("codex", "claude", "agy"):
                    fail("provider must be codex, claude or agy")
                if args.provider=="codex" and (not args.expected_model or args.expected_effort not in ("xhigh", "max")):
                    fail("Codex worker must declare expected model and xhigh/max effort")
                run=guarded(conn,root)
                if not ready(conn,args.task):fail("task not ready")
                running=one(conn,"SELECT COUNT(*) AS n FROM attempt WHERE kind='worker' AND state IN ('RESERVED','OBSERVED','READY','STARTED','UNCERTAIN','BLOCKED')")["n"]
                if running >= run["max_workers"]:fail("worker budget full")
                if one(conn,"SELECT id FROM attempt WHERE task_id=? AND state IN ('RESERVED','OBSERVED','READY','STARTED','UNCERTAIN','BLOCKED')",(args.task,)):
                    fail("task already has a live worker attempt")
                gen=claim(conn,checkout,args.attempt,"writer")
                conn.execute("INSERT INTO attempt(id,task_id,kind,agent_hint,checkout,expected_model,expected_effort,claim_generation) VALUES(?,?,?,?,?,?,?,?)",
                             (args.attempt,args.task,args.provider,args.agent,checkout,args.expected_model,args.expected_effort,gen))
                conn.execute("UPDATE task SET state='ACTIVE' WHERE id=?",(args.task,))
                return f"OK attempt={args.attempt} state=RESERVED fence={gen} launch-through-Herdr-then-observe"
            return action(conn,root,name,request,data,act)
        if name == "attempt-attach":
            data=dict(attempt=args.attempt,pane=args.pane,agent=args.agent)
            prior=replay(conn,name,request,data)
            if prior:return prior
            with closing(connect(root, True)) as read:
                reserved = get(read, "attempt", args.attempt)
                observed = observe_herdr(reserved["agent_hint"], args.pane, reserved["checkout"], reserved["kind"])
            def act():
                a=get(conn,"attempt",args.attempt)
                if a["state"]!="RESERVED":fail("attempt not RESERVED")
                if args.agent != a["agent_hint"] or observed["agent"] != a["agent_hint"]:
                    fail("unexpected Herdr agent identity")
                if (a["expected_model"] and a["expected_model"] != observed["model"]) or (a["expected_effort"] and a["expected_effort"] != observed["effort"]):
                    fail("BLOCKED: MODEL_OVERRIDE_NOT_APPLIED; observed process arguments differ")
                conn.execute("UPDATE attempt SET state='OBSERVED',pane=?,observed_agent=?,observed_model=?,observed_effort=?,terminal_id=?,process_pid=? WHERE id=?",
                             (observed["pane"],observed["agent"],observed["model"],observed["effort"],observed["terminal"],observed["pid"],args.attempt))
                return f"OK attempt={args.attempt} state=OBSERVED pane={observed['pane']} pid={observed['pid']}"
            return action(conn,root,name,request,data,act)
        if name in ("attempt-ready", "attempt-start"):
            a = get(conn, "attempt", args.attempt)
            run = one(conn, "SELECT * FROM run LIMIT 1")
            kind = "ROLE_READY" if name == "attempt-ready" else "TASK_START"
            sender = a["agent_hint"] if kind == "ROLE_READY" else args.operator
            recipient = args.operator if kind == "ROLE_READY" else a["agent_hint"]
            pane = a["pane"] if kind == "ROLE_READY" else args.sender_pane
            artifact, artifact_sha, path, digest = signal_receipt(root, run["id"], kind, args.attempt, args.event_id,
                                          sender, pane, recipient, args.evidence, args.receipt)
            data=dict(attempt=args.attempt,event=args.event_id,evidence=artifact,evidence_sha=artifact_sha,receipt=path,hash=digest,operator=args.operator,operator_pane=args.operator_pane,sender_pane=args.sender_pane)
            prior=replay(conn,name,request,data)
            if prior:return prior
            reobserve_attempt(a)
            observe_recipient(args.operator,args.operator_pane)
            def act():
                current = get(conn, "attempt", args.attempt)
                required = "OBSERVED" if kind == "ROLE_READY" else "READY"
                if current["state"] != required:fail(f"attempt not {required}")
                if not current["pane"] or not current["process_pid"]:fail("process observation missing")
                if kind == "TASK_START" and not args.sender_pane:fail("sender pane required")
                if kind == "TASK_START" and args.sender_pane!=args.operator_pane:
                    fail("TASK_START sender is not observed operator pane")
                if kind == "ROLE_READY":
                    ready = (root / artifact).read_text(encoding="utf-8").splitlines()
                    required_lines = (f"attempt={args.attempt}", f"agent={current['agent_hint']}",
                                      f"pane={current['pane']}", f"observed_model={current['expected_model']}",
                                      f"observed_effort={current['expected_effort']}")
                    if any(line not in ready for line in required_lines):
                        fail("READY evidence does not match reserved attempt/model")
                else:
                    ack=(root / artifact).read_text(encoding="utf-8").splitlines()
                    required_lines=(f"attempt={args.attempt}",f"event={args.event_id}",
                                    f"agent={current['agent_hint']}",f"pane={current['pane']}",
                                    f"plan_sha={run['plan_sha']}")
                    if any(line not in ack for line in required_lines):
                        fail("TASK_START acknowledgment does not match attempt and plan")
                conn.execute("INSERT INTO signal VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (args.event_id,args.attempt,kind,sender,recipient,pane,artifact,artifact_sha,path,digest))
                column = "ready_event" if kind == "ROLE_READY" else "start_event"
                state = "READY" if kind == "ROLE_READY" else "STARTED"
                conn.execute(f"UPDATE attempt SET state=?,{column}=? WHERE id=?",(state,args.event_id,args.attempt))
                return f"OK attempt={args.attempt} state={state} signal={args.event_id}"
            return action(conn,root,name,request,data,act)
        if name == "attempt-report":
            path,digest=relative_evidence(root,args.evidence)
            receipt,receipt_sha=relative_evidence(root,args.receipt)
            data=dict(attempt=args.attempt,evidence=path,hash=digest,source=args.source_sha,event=args.event_id,receipt=receipt,receipt_sha=receipt_sha)
            prior=replay(conn,name,request,data)
            if prior:return prior
            with closing(connect(root, True)) as read:
                a = get(read, "attempt", args.attempt)
                if a["state"]!="STARTED":fail("attempt not STARTED")
                run = one(read, "SELECT * FROM run LIMIT 1")
                _, head, _, _ = git(a["checkout"])
                if head != args.source_sha:fail("source commit not observed at assigned checkout")
                artifact, artifact_sha, receipt, receipt_sha = signal_receipt(root, run["id"], "REPORT_READY",
                    args.attempt, args.event_id, a["agent_hint"], a["pane"], args.recipient, args.evidence, args.receipt)
            def act():
                a=get(conn,"attempt",args.attempt)
                if a["state"]!="STARTED":fail("attempt not STARTED")
                if not re.fullmatch(r"[a-f0-9]{40,64}",args.source_sha):fail("invalid source commit")
                conn.execute("INSERT INTO signal VALUES(?,?,?,?,?,?,?,?,?,?)",
                             (args.event_id,args.attempt,"REPORT_READY",a["agent_hint"],args.recipient,a["pane"],artifact,artifact_sha,receipt,receipt_sha))
                conn.execute("UPDATE attempt SET state='REPORTED',report_path=?,report_sha=?,source_sha=? WHERE id=?",
                             (path,digest,args.source_sha,args.attempt))
                conn.execute("UPDATE task SET state='REPORTED' WHERE id=?",(a["task_id"],))
                return f"OK attempt={args.attempt} state=REPORTED report={path}"
            return action(conn,root,name,request,data,act)
        if name == "attempt-release":
            with closing(connect(root, True)) as read:
                prior=one(read,"SELECT receipt FROM event WHERE request_id=?",(request,))
                a=get(read,"attempt",args.attempt)
                if prior:
                    # The action checks the original payload digest; do not re-observe a released pane.
                    observation=args.observation
                else:
                    observation=(observe_unstarted(a["agent_hint"]) if a["state"]=="RESERVED" else
                                 observe_stopped(a["pane"],a["process_pid"]))
                    _,_,_,clean=git(a["checkout"])
                    if not clean:fail("BLOCKED: checkout dirty; preserve writer claim")
            data=dict(attempt=args.attempt,generation=args.generation,observed=args.observation)
            def act():
                a=get(conn,"attempt",args.attempt)
                if a["state"] not in ("RESERVED","REPORTED","OBSERVED","READY","STARTED","UNCERTAIN","BLOCKED"):
                    fail("attempt cannot release from this state")
                if not args.observation.strip():fail("must reference observed agent quiescence/stop")
                release(conn,a["checkout"],args.attempt,args.generation)
                if a["state"]!="REPORTED":
                    conn.execute("UPDATE attempt SET state='INTERRUPTED' WHERE id=?",(args.attempt,))
                    conn.execute("UPDATE task SET state='BLOCKED' WHERE id=?",(a["task_id"],))
                return f"OK attempt={args.attempt} writer-released fence={args.generation} observed={observation}"
            return action(conn,root,name,request,data,act,
                          allowed_states=("EXECUTING","CANCELLING","CANCELLATION_BLOCKED"),check_plan=False)
        if name == "candidate-freeze":
            data=dict(candidate=args.candidate,task=args.task,checkout=str(Path(args.checkout).resolve()))
            with closing(connect(root, True)) as read:
                a=one(read,"SELECT * FROM attempt WHERE task_id=? AND state='REPORTED' ORDER BY rowid DESC LIMIT 1",(args.task,))
                if not a or not a["source_sha"]:fail("reported source attempt missing")
                path,head,tree,clean=git(args.checkout)
                if not clean:fail("candidate checkout is dirty")
                source_patch=patch_identity(a["checkout"],a["source_sha"])
                integrated_patch=patch_identity(path,head)
                if source_patch != integrated_patch:fail("candidate does not contain reported source patch")
            def act():
                t=get(conn,"task",args.task)
                if t["state"]!="REPORTED":fail("task not REPORTED")
                if not clean:fail("candidate checkout is dirty")
                if one(conn,"SELECT 1 FROM claim WHERE resource=? AND active=1",(path,)):
                    fail("candidate checkout is claimed")
                conn.execute("INSERT INTO candidate(id,task_id,checkout,head,tree,source_sha,integration_proof) VALUES(?,?,?,?,?,?,?)",
                             (args.candidate,args.task,path,head,tree,a["source_sha"],source_patch))
                return f"OK candidate={args.candidate} head={head[:12]} clean=true"
            return action(conn,root,name,request,data,act)
        if name == "verify-start":
            data=dict(verification=args.verification,candidate=args.candidate,verifier=args.verifier,gate=args.gate)
            with closing(connect(root,True)) as read:
                observed_candidate=get(read,"candidate",args.candidate)
                same=candidate_identity(observed_candidate)
            def act():
                c=get(conn,"candidate",args.candidate)
                if c["state"]!="FROZEN" or c["head"]!=observed_candidate["head"] or not same:
                    fail("candidate changed; cannot verify")
                worker=one(conn,"SELECT agent_hint FROM attempt WHERE task_id=? AND source_sha=? ORDER BY rowid DESC LIMIT 1",(c["task_id"],c["source_sha"]))
                if worker and worker["agent_hint"]==args.verifier:fail("worker cannot verify own change")
                gen=claim(conn,c["checkout"],args.verification,"verification")
                heavy=claim(conn,"heavy-local-verification",args.verification,"heavy-local-verification")
                conn.execute("INSERT INTO verification(id,candidate_id,verifier,claim_generation,heavy_generation,gate) VALUES(?,?,?,?,?,?)",
                             (args.verification,args.candidate,args.verifier,gen,heavy,args.gate))
                return f"OK verification={args.verification} fence={gen} candidate={args.candidate} VERIFY_START-required"
            return action(conn,root,name,request,data,act)
        if name == "verify-exec":
            v=get(conn,"verification",args.verification)
            c=get(conn,"candidate",v["candidate_id"])
            if not args.argv or args.argv == ["--"]:fail("verification command required")
            command=args.argv[1:] if args.argv[0]=="--" else args.argv
            if args.timeout < 1 or args.timeout > 600:fail("timeout must be 1..600 seconds")
            data=dict(verification=args.verification,command=command,timeout=args.timeout)
            prior=one(conn,"SELECT digest,receipt FROM event WHERE request_id=?",(args.request_id,))
            if prior:
                return action(conn,root,name,args.request_id,data,lambda: fail("unexpected verification replay"))
            if v["state"]!="STARTED" or not candidate_identity(c):fail("verification not active at clean candidate")
            output=root / "verifications" / (args.verification+".log")
            if output.exists():fail("verification output already exists; reconcile before retry")
            output.parent.mkdir(exist_ok=True)
            def begin():
                if get(conn,"verification",args.verification)["state"]!="STARTED":fail("verification already executing")
                conn.execute("UPDATE verification SET state='RUNNING',command=? WHERE id=?",(json.dumps(command),args.verification))
                return f"OK verification={args.verification} RUNNING"
            begin_id="begin_"+hashlib.sha256(args.request_id.encode()).hexdigest()
            action(conn,root,"verify-exec-begin",begin_id,data,begin)
            try:
                result=subprocess.run(command,cwd=c["checkout"],capture_output=True,timeout=args.timeout,check=False)
                exit_code=result.returncode
                raw=result.stdout+b"\n--- stderr ---\n"+result.stderr
            except subprocess.TimeoutExpired as exc:
                exit_code=None
                raw=(exc.stdout or b"")+b"\n--- timeout ---\n"+(exc.stderr or b"")
            if len(raw)>1_000_000:
                raw=raw[:1_000_000]+b"\n--- output truncated; result indeterminate ---\n"
                exit_code=None
            output.write_bytes(raw)
            rel,digest=relative_evidence(root,output.relative_to(root).as_posix())
            state="STALE" if not candidate_identity(c) else ("PASS" if exit_code==0 else "FAIL" if exit_code is not None else "INDETERMINATE")
            def act():
                current=get(conn,"verification",args.verification)
                if current["state"]!="RUNNING":fail("verification no longer running")
                candidate=get(conn,"candidate",current["candidate_id"])
                release(conn,candidate["checkout"],args.verification,current["claim_generation"])
                release(conn,"heavy-local-verification",args.verification,current["heavy_generation"])
                conn.execute("UPDATE verification SET state=?,exit_code=?,evidence_path=?,evidence_sha=?,command=? WHERE id=?",
                             (state,exit_code,rel,digest,json.dumps(command),args.verification))
                if state=="STALE":conn.execute("UPDATE candidate SET state='STALE' WHERE id=?",(candidate["id"],))
                return f"OK verification={args.verification} result={state} candidate={current['candidate_id']}"
            return action(conn,root,name,request,data,act)
        if name == "verify-record":
            path,digest=relative_evidence(root,args.evidence)
            data=dict(verification=args.verification,result=args.result,exit=args.exit_code,evidence=path,hash=digest)
            with closing(connect(root,True)) as read:
                previous=get(read,"verification",args.verification)
                same=candidate_identity(get(read,"candidate",previous["candidate_id"]))
            def act():
                v=get(conn,"verification",args.verification)
                if v["state"]!="STARTED":fail("verification not STARTED")
                c=get(conn,"candidate",v["candidate_id"])
                state="STALE" if not same else args.result
                if state=="PASS":fail("PASS requires verify-exec captured command result")
                release(conn,c["checkout"],args.verification,v["claim_generation"])
                release(conn,"heavy-local-verification",args.verification,v["heavy_generation"])
                conn.execute("UPDATE verification SET state=?,exit_code=?,evidence_path=?,evidence_sha=? WHERE id=?",
                             (state,args.exit_code,path,digest,args.verification))
                if state=="STALE":conn.execute("UPDATE candidate SET state='STALE' WHERE id=?",(c["id"],))
                return f"OK verification={args.verification} result={state} candidate={v['candidate_id']}"
            return action(conn,root,name,request,data,act)
        if name == "verify-abort":
            data=dict(verification=args.verification,reason=args.reason)
            def act():
                v=get(conn,"verification",args.verification)
                if v["state"]!="STARTED":fail("running or uncertain verification requires process reconciliation")
                c=get(conn,"candidate",v["candidate_id"])
                release(conn,c["checkout"],args.verification,v["claim_generation"])
                release(conn,"heavy-local-verification",args.verification,v["heavy_generation"])
                conn.execute("UPDATE verification SET state='INDETERMINATE' WHERE id=?",(args.verification,))
                return f"OK verification={args.verification} state=INDETERMINATE"
            return action(conn,root,name,request,data,act,
                          allowed_states=("EXECUTING","CANCELLING","CANCELLATION_BLOCKED"),check_plan=False)
        if name == "task-accept":
            data=dict(task=args.task,candidate=args.candidate,decision=args.decision_id)
            with closing(connect(root,True)) as read:
                observed=get(read,"candidate",args.candidate)
                if not candidate_identity(observed):fail("candidate changed after verification")
                decision=get(read,"decision",args.decision_id)
                if relative_evidence(root,decision["rationale_path"])[1]!=decision["rationale_sha"]:
                    fail("decision rationale changed")
                source=one(read,"SELECT * FROM attempt WHERE task_id=? AND source_sha=? AND state='REPORTED' ORDER BY rowid DESC LIMIT 1",(args.task,observed["source_sha"]))
                if not source or not source["report_path"]:fail("reported source provenance missing")
                if relative_evidence(root,source["report_path"])[1]!=source["report_sha"]:
                    fail("worker report changed")
                handshakes=read.execute("SELECT * FROM signal WHERE attempt_id=? ORDER BY rowid",(source["id"],)).fetchall()
                if {s["kind"] for s in handshakes}!={"ROLE_READY","TASK_START","REPORT_READY"}:
                    fail("required attempt signal receipts missing")
                for signal in handshakes:
                    if (relative_evidence(root,signal["evidence_path"])[1]!=signal["evidence_sha"] or
                            relative_evidence(root,signal["receipt_path"])[1]!=signal["receipt_sha"]):
                        fail("attempt signal evidence changed")
                reviews=read.execute("SELECT * FROM critique WHERE candidate_id=? ORDER BY rowid",(args.candidate,)).fetchall()
                for review in reviews:
                    if relative_evidence(root,review["report_path"])[1]!=review["report_sha"]:
                        fail("critique report changed")
                ver=one(read,"SELECT * FROM verification WHERE candidate_id=? AND state='PASS' ORDER BY rowid DESC LIMIT 1",(args.candidate,))
                if ver and ver["evidence_path"] and relative_evidence(root,ver["evidence_path"])[1]!=ver["evidence_sha"]:
                    fail("verification evidence changed")
                checked=(observed["head"],observed["tree"],source["id"],
                         tuple(s["event_id"] for s in handshakes),tuple(r["id"] for r in reviews),ver["id"] if ver else None)
            def act():
                t=get(conn,"task",args.task);c=get(conn,"candidate",args.candidate)
                if t["state"]!="REPORTED" or c["task_id"]!=t["id"] or c["state"]!="FROZEN":
                    fail("task/candidate mismatch or task not reported")
                if (c["head"],c["tree"])!=checked[:2]:fail("candidate changed during acceptance")
                decision=get(conn,"decision",args.decision_id)
                if decision["subject"]!=c["id"] or decision["authority"]!="MANAGER" or decision["disposition"]!="APPROVED":
                    fail("approved Manager decision required for this candidate")
                source=one(conn,"SELECT * FROM attempt WHERE task_id=? AND source_sha=? AND state='REPORTED' ORDER BY rowid DESC LIMIT 1",(t["id"],c["source_sha"]))
                if not source or source["id"]!=checked[2]:
                    fail("reported source provenance missing")
                handshakes=conn.execute("SELECT * FROM signal WHERE attempt_id=? ORDER BY rowid",(source["id"],)).fetchall()
                if tuple(s["event_id"] for s in handshakes)!=checked[3]:fail("attempt signals changed during acceptance")
                reviews=conn.execute("SELECT * FROM critique WHERE candidate_id=? ORDER BY rowid",(c["id"],)).fetchall()
                if tuple(r["id"] for r in reviews)!=checked[4]:fail("critique changed during acceptance")
                if t["critique_required"]:
                    if not reviews:fail("required independent critique missing")
                unresolved=one(conn,"SELECT id FROM finding WHERE candidate_id=? AND disposition='OPEN' AND severity IN ('BLOCKING','HIGH') LIMIT 1",(c["id"],))
                if unresolved:fail("unresolved blocking critique finding")
                ver=one(conn,"SELECT * FROM verification WHERE candidate_id=? AND state='PASS' ORDER BY rowid DESC LIMIT 1",(c["id"],))
                if not ver or not ver["evidence_path"] or ver["id"]!=checked[5]:fail("no independent passing verification")
                worker=one(conn,"SELECT agent_hint FROM attempt WHERE task_id=? AND source_sha=? ORDER BY rowid DESC LIMIT 1",(t["id"],c["source_sha"]))
                if worker and ver["verifier"]==worker["agent_hint"]:fail("worker cannot verify own change")
                conn.execute("UPDATE task SET state='ACCEPTED',accepted_candidate=? WHERE id=?",(c["id"],t["id"]))
                return f"OK task={t['id']} ACCEPTED candidate={c['id']} decision={args.decision_id}"
            return action(conn,root,name,request,data,act)
        fail("unsupported command")


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir",required=True,type=Path,help="One fresh pilot directory; never use an active SESSION.md run")
    sub=p.add_subparsers(dest="command",required=True)
    def add(name, **keys):
        q=sub.add_parser(name)
        for arg,kwargs in keys.items():q.add_argument("--"+arg.replace("_","-"),**kwargs)
        return q
    add("init",run_id=dict(required=True),repo=dict(required=True),plan=dict(required=True),grant_ref=dict(required=True),max_workers=dict(type=int,default=1)).add_argument("--parallel-approved",action="store_true")
    add("upgrade")
    add("run-cancel",decision_id=dict(required=True),actor=dict(required=True),attestor=dict(required=True),authority_source=dict(required=True),reason=dict(required=True),request_id=dict(required=True))
    add("cancel-block",evidence=dict(required=True),request_id=dict(required=True))
    add("cancel-finalize",decision_id=dict(required=True),request_id=dict(required=True))
    add("status").add_argument("--human",action="store_true")
    sub.choices["status"].add_argument("--ids",action="store_true",help="Include internal task IDs in human view")
    add("export")
    add("next")
    add("recover")
    add("events").add_argument("--limit",type=int,default=8,choices=range(1,21),metavar="1..20")
    add("task-show",task=dict(required=True))
    add("task-add",task=dict(required=True),title=dict(required=True),outcome=dict(required=True),brief=dict(required=True),deps=dict(default=""),request_id=dict(required=True)).add_argument("--critique-required",action="store_true")
    add("decision-record",decision_id=dict(required=True),subject=dict(required=True),authority=dict(required=True,choices=["MANAGER","HUMAN"]),actor=dict(required=True),attestor=dict(required=True),disposition=dict(required=True,choices=["APPROVED","REJECTED"]),provenance=dict(required=True),rationale=dict(required=True),request_id=dict(required=True))
    add("task-retry",task=dict(required=True),decision_id=dict(required=True),request_id=dict(required=True))
    add("critique-record",review=dict(required=True),candidate=dict(required=True),critic=dict(required=True),report=dict(required=True),request_id=dict(required=True))
    add("finding-record",finding=dict(required=True),review=dict(required=True),actor=dict(required=True),severity=dict(required=True,choices=["BLOCKING","HIGH","MEDIUM","LOW"]),disposition=dict(required=True,choices=["OPEN","RESOLVED","DISMISSED"]),evidence=dict(required=True),decision_id=dict(default=""),request_id=dict(required=True))
    add("attempt-reserve",task=dict(required=True),attempt=dict(required=True),checkout=dict(required=True),agent=dict(required=True),provider=dict(required=True),expected_model=dict(default=""),expected_effort=dict(default=""),request_id=dict(required=True))
    add("attempt-attach",attempt=dict(required=True),pane=dict(required=True),agent=dict(required=True),request_id=dict(required=True))
    add("attempt-ready",attempt=dict(required=True),operator=dict(required=True),operator_pane=dict(required=True),sender_pane=dict(default=""),event_id=dict(required=True),evidence=dict(required=True),receipt=dict(required=True),request_id=dict(required=True))
    add("attempt-start",attempt=dict(required=True),operator=dict(required=True),operator_pane=dict(required=True),sender_pane=dict(required=True),event_id=dict(required=True),evidence=dict(required=True),receipt=dict(required=True),request_id=dict(required=True))
    add("attempt-report",attempt=dict(required=True),evidence=dict(required=True),source_sha=dict(required=True),event_id=dict(required=True),recipient=dict(required=True),receipt=dict(required=True),request_id=dict(required=True))
    add("attempt-release",attempt=dict(required=True),generation=dict(required=True,type=int),observation=dict(required=True),request_id=dict(required=True))
    add("candidate-freeze",task=dict(required=True),candidate=dict(required=True),checkout=dict(required=True),request_id=dict(required=True))
    add("verify-start",verification=dict(required=True),candidate=dict(required=True),verifier=dict(required=True),gate=dict(default="local-check"),request_id=dict(required=True))
    add("verify-exec",verification=dict(required=True),timeout=dict(type=int,default=300),request_id=dict(required=True)).add_argument("argv",nargs=argparse.REMAINDER)
    add("verify-record",verification=dict(required=True),result=dict(choices=["PASS","FAIL","INDETERMINATE"],required=True),exit_code=dict(type=int,required=True),evidence=dict(required=True),request_id=dict(required=True))
    add("verify-abort",verification=dict(required=True),reason=dict(required=True),request_id=dict(required=True))
    add("task-accept",task=dict(required=True),candidate=dict(required=True),decision_id=dict(required=True),request_id=dict(required=True))
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        print(operation(args.run_dir.resolve(),args))
        return 0
    except (LedgerError,sqlite3.DatabaseError,FileNotFoundError,subprocess.TimeoutExpired,OSError) as exc:
        message="BLOCKED: LEDGER_BUSY" if isinstance(exc,sqlite3.OperationalError) and "locked" in str(exc).lower() else str(exc)
        print("ERROR " + message[:250],file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
