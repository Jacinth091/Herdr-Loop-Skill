#!/usr/bin/env python3
"""EXPERIMENTAL offline Herdr run ledger, no agent launches or live cutover.

Python standard library only. Named transactions; the CLI never operates Herdr,
starts tests, elevates privileges, or reads historic Markdown for acceptance.
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
from datetime import datetime, timezone

VERSION = 1
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
    if full.stat().st_size > 20_000_000:
        fail("evidence too large for pilot capture")
    return full.relative_to(root).as_posix(), fingerprint(full)


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
    conn = sqlite3.connect(uri, uri=read_only, timeout=3.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=3000")
    if conn.execute("PRAGMA user_version").fetchone()[0] != VERSION:
        conn.close()
        fail("unknown ledger schema; do not guess a migration")
    return conn


def one(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()


def get(conn, table, key):
    if table not in ("run", "task", "attempt", "candidate", "verification"):
        fail("unsupported entity type")
    row = one(conn, f"SELECT * FROM {table} WHERE id=?", (key,))
    if row is None: fail(f"{table} {key} not found")
    return row


def guarded(conn, root):
    run = one(conn, "SELECT * FROM run LIMIT 1")
    if not run: fail("run metadata missing")
    plan = (root / run["plan_path"]).resolve()
    if not plan.is_file() or fingerprint(plan) != run["plan_sha"]:
        fail("approved PLAN.md changed; halt until new authorized generation")
    if run["state"] != "EXECUTING":
        fail("run is not executing; no new transitions allowed")
    return run


def action(conn, root, op, request_id, data, fn):
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
        guarded(conn, root)
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


def candidate_identity(c):
    path, head, tree, clean = git(c["checkout"])
    return path == c["checkout"] and head == c["head"] and tree == c["tree"] and clean


def operation(root, args):
    name = args.command
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
    with closing(connect(root, name in ("status", "next", "task-show", "recover", "events"))) as conn:
        if name in ("status", "next", "task-show", "recover", "events"):
            run = one(conn, "SELECT * FROM run LIMIT 1")
            if not run:fail("run metadata missing")
            if name == "task-show":
                t=get(conn, "task", args.task)
                return f"task={t['id']} state={t['state']} title={t['title']}\noutcome={t['outcome']} deps={','.join(json.loads(t['deps'])) or '-'} brief={t['brief_path']}"
            if name == "events":
                rows=conn.execute("SELECT seq,op,receipt FROM event ORDER BY seq DESC LIMIT ?", (args.limit,)).fetchall()
                return "\n".join(f"{r['seq']} {r['op']} {r['receipt']}" for r in rows)
            tasks = conn.execute("SELECT * FROM task ORDER BY id").fetchall()
            if name == "next":
                can=[t for t in tasks if ready(conn,t["id"])]
                return "\n".join(f"READY {t['id']} {t['title']}" for t in can[:5]) or "NO_READY_TASK"
            if name == "status":
                counts={s:sum(1 for t in tasks if t["state"]==s) for s in ("PLANNED","ACTIVE","REPORTED","ACCEPTED","BLOCKED")}
                nexts=[t["id"] for t in tasks if ready(conn,t["id"])]
                active=[t["id"] for t in tasks if t["state"] in ("ACTIVE","REPORTED")]
                if args.human:
                    lines=["# Work progress"]
                    labels={"PLANNED":"Planned", "ACTIVE":"In progress", "REPORTED":"Awaiting independent review", "BLOCKED":"Blocked", "ACCEPTED":"Accepted"}
                    for t in tasks[:12]:
                        lines.append(f"- **{t['title']}**: {labels.get(t['state'],'Needs reconciliation')} | {t['outcome']}")
                    if len(tasks)>12:lines.append(f"({len(tasks)-12} more; use task-show)")
                    return "\n".join(lines)
                return f"run={run['id']} mode=sqlite-pilot next={','.join(nexts[:3]) or '-'} active={','.join(active[:3]) or '-'} accepted={counts['ACCEPTED']} blocked={counts['BLOCKED']} tasks={len(tasks)}"
            unknown=conn.execute("SELECT id,state,pane FROM attempt WHERE state IN ('RESERVED','STARTED') ORDER BY id LIMIT 5").fetchall()
            checks=conn.execute("SELECT id FROM verification WHERE state='STARTED' ORDER BY id LIMIT 5").fetchall()
            return (f"recovery={run['id']} never-auto-respawn\n"+
                    "\n".join(f"ATTENTION attempt={a['id']} state={a['state']} pane={a['pane'] or '?'} observe-Herdr-first" for a in unknown)+
                    ("\n" if unknown else "")+
                    "\n".join(f"ATTENTION verify={c['id']} lock-held inspect-process" for c in checks))
        request = args.request_id
        if name == "task-add":
            if not TASK.fullmatch(args.task):fail("invalid task ID")
            if (not args.title.strip() or len(args.title)>90 or not args.outcome.strip() or len(args.outcome)>240
                    or any(ord(c)<32 or c in "<>|" for c in args.title+args.outcome)):
                fail("approved safe one-line title/outcome required")
            path, digest=relative_evidence(root,args.brief)
            deps=[d.strip() for d in args.deps.split(",") if d.strip()]
            if len(set(deps))!=len(deps) or args.task in deps:fail("duplicate/self dependency")
            data=dict(task=args.task,title=args.title,outcome=args.outcome,deps=deps,brief=path,hash=digest)
            def act():
                for dep in deps:
                    if not TASK.fullmatch(dep):fail("invalid dependency")
                    get(conn,"task",dep)
                conn.execute("INSERT INTO task(id,title,outcome,deps,brief_path,brief_sha) VALUES(?,?,?,?,?,?)",
                             (args.task,args.title,args.outcome,json.dumps(deps),path,digest))
                return f"OK task={args.task} state=PLANNED"
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
                if not ready(conn,args.task):fail("task not ready")
                run=guarded(conn,root)
                running=one(conn,"SELECT COUNT(*) AS n FROM attempt WHERE kind='worker' AND state IN ('RESERVED','STARTED')")["n"]
                if running >= run["max_workers"]:fail("worker budget full")
                if one(conn,"SELECT id FROM attempt WHERE task_id=? AND state IN ('RESERVED','STARTED')",(args.task,)):
                    fail("task already has a live worker attempt")
                gen=claim(conn,checkout,args.attempt,"writer")
                conn.execute("INSERT INTO attempt(id,task_id,kind,agent_hint,checkout,expected_model,expected_effort,claim_generation) VALUES(?,?,?,?,?,?,?,?)",
                             (args.attempt,args.task,args.provider,args.agent,checkout,args.expected_model,args.expected_effort,gen))
                conn.execute("UPDATE task SET state='ACTIVE' WHERE id=?",(args.task,))
                return f"OK attempt={args.attempt} state=RESERVED fence={gen} launch-through-Herdr-then-observe"
            return action(conn,root,name,request,data,act)
        if name == "attempt-attach":
            data=dict(attempt=args.attempt,pane=args.pane,agent=args.agent,model=args.model,effort=args.effort)
            def act():
                a=get(conn,"attempt",args.attempt)
                if a["state"]!="RESERVED":fail("attempt not RESERVED")
                if not args.pane.strip() or not args.agent.strip() or not args.model.strip():
                    fail("observed pane/agent/model required")
                if args.agent!=a["agent_hint"]:fail("unexpected Herdr agent identity")
                if (a["expected_model"] and a["expected_model"]!=args.model) or (a["expected_effort"] and a["expected_effort"]!=args.effort):
                    fail("BLOCKED: MODEL_OVERRIDE_NOT_APPLIED; compare observed Herdr runtime flags")
                conn.execute("UPDATE attempt SET state='STARTED',pane=?,observed_agent=?,observed_model=?,observed_effort=? WHERE id=?",
                             (args.pane,args.agent,args.model,args.effort,args.attempt))
                return f"OK attempt={args.attempt} state=STARTED observed-pane={args.pane} model={args.model} effort={args.effort or 'default'}"
            return action(conn,root,name,request,data,act)
        if name == "attempt-report":
            path,digest=relative_evidence(root,args.evidence)
            data=dict(attempt=args.attempt,evidence=path,hash=digest,source=args.source_sha)
            def act():
                a=get(conn,"attempt",args.attempt)
                if a["state"]!="STARTED":fail("attempt not STARTED")
                if not re.fullmatch(r"[a-f0-9]{40,64}",args.source_sha):fail("invalid source commit")
                _,head,_,_=git(a["checkout"])
                if head!=args.source_sha:fail("source commit not observed at assigned checkout")
                conn.execute("UPDATE attempt SET state='REPORTED',report_path=?,report_sha=?,source_sha=? WHERE id=?",
                             (path,digest,args.source_sha,args.attempt))
                conn.execute("UPDATE task SET state='REPORTED' WHERE id=?",(a["task_id"],))
                return f"OK attempt={args.attempt} state=REPORTED report={path}"
            return action(conn,root,name,request,data,act)
        if name == "attempt-release":
            data=dict(attempt=args.attempt,generation=args.generation,observed=args.observation)
            def act():
                a=get(conn,"attempt",args.attempt)
                if a["state"] not in ("REPORTED",):fail("only reported attempts can release via this pilot")
                if not args.observation.strip():fail("must reference observed agent quiescence/stop")
                release(conn,a["checkout"],args.attempt,args.generation)
                return f"OK attempt={args.attempt} writer-released fence={args.generation}"
            return action(conn,root,name,request,data,act)
        if name == "candidate-freeze":
            data=dict(candidate=args.candidate,task=args.task,checkout=str(Path(args.checkout).resolve()))
            def act():
                t=get(conn,"task",args.task)
                if t["state"]!="REPORTED":fail("task not REPORTED")
                path,head,tree,clean=git(args.checkout)
                if not clean:fail("candidate checkout is dirty")
                if one(conn,"SELECT 1 FROM claim WHERE resource=? AND active=1",(path,)):
                    fail("candidate checkout is claimed")
                conn.execute("INSERT INTO candidate(id,task_id,checkout,head,tree) VALUES(?,?,?,?,?)",
                             (args.candidate,args.task,path,head,tree))
                return f"OK candidate={args.candidate} head={head[:12]} clean=true"
            return action(conn,root,name,request,data,act)
        if name == "verify-start":
            data=dict(verification=args.verification,candidate=args.candidate,verifier=args.verifier)
            def act():
                c=get(conn,"candidate",args.candidate)
                if c["state"]!="FROZEN" or not candidate_identity(c):fail("candidate changed; cannot verify")
                gen=claim(conn,c["checkout"],args.verification,"verification")
                conn.execute("INSERT INTO verification(id,candidate_id,verifier,claim_generation) VALUES(?,?,?,?)",
                             (args.verification,args.candidate,args.verifier,gen))
                return f"OK verification={args.verification} fence={gen} candidate={args.candidate} VERIFY_START-required"
            return action(conn,root,name,request,data,act)
        if name == "verify-record":
            path,digest=relative_evidence(root,args.evidence)
            data=dict(verification=args.verification,result=args.result,exit=args.exit_code,evidence=path,hash=digest)
            def act():
                v=get(conn,"verification",args.verification)
                if v["state"]!="STARTED":fail("verification not STARTED")
                c=get(conn,"candidate",v["candidate_id"])
                state="STALE" if not candidate_identity(c) else args.result
                if state=="PASS" and args.exit_code!=0:fail("PASS requires exit code 0")
                release(conn,c["checkout"],args.verification,v["claim_generation"])
                conn.execute("UPDATE verification SET state=?,exit_code=?,evidence_path=?,evidence_sha=? WHERE id=?",
                             (state,args.exit_code,path,digest,args.verification))
                if state=="STALE":conn.execute("UPDATE candidate SET state='STALE' WHERE id=?",(c["id"],))
                return f"OK verification={args.verification} result={state} candidate={v['candidate_id']}"
            return action(conn,root,name,request,data,act)
        if name == "task-accept":
            path,digest=relative_evidence(root,args.decision)
            data=dict(task=args.task,candidate=args.candidate,decision=path,hash=digest)
            def act():
                t=get(conn,"task",args.task);c=get(conn,"candidate",args.candidate)
                if t["state"]!="REPORTED" or c["task_id"]!=t["id"] or c["state"]!="FROZEN":
                    fail("task/candidate mismatch or task not reported")
                if not candidate_identity(c):fail("candidate changed after verification")
                ver=one(conn,"SELECT * FROM verification WHERE candidate_id=? AND state='PASS' ORDER BY rowid DESC LIMIT 1",(c["id"],))
                if not ver or not ver["evidence_path"]:fail("no independent passing verification")
                ev=(root/ver["evidence_path"]).resolve()
                if not ev.is_file() or fingerprint(ev)!=ver["evidence_sha"]:fail("verification evidence changed")
                conn.execute("UPDATE task SET state='ACCEPTED',accepted_candidate=? WHERE id=?",(c["id"],t["id"]))
                return f"OK task={t['id']} ACCEPTED candidate={c['id']} decision={path}"
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
    add("status").add_argument("--human",action="store_true")
    add("next")
    add("recover")
    add("events").add_argument("--limit",type=int,default=8,choices=range(1,21),metavar="1..20")
    add("task-show",task=dict(required=True))
    add("task-add",task=dict(required=True),title=dict(required=True),outcome=dict(required=True),brief=dict(required=True),deps=dict(default=""),request_id=dict(required=True))
    add("attempt-reserve",task=dict(required=True),attempt=dict(required=True),checkout=dict(required=True),agent=dict(required=True),provider=dict(required=True),expected_model=dict(default=""),expected_effort=dict(default=""),request_id=dict(required=True))
    add("attempt-attach",attempt=dict(required=True),pane=dict(required=True),agent=dict(required=True),model=dict(required=True),effort=dict(default=""),request_id=dict(required=True))
    add("attempt-report",attempt=dict(required=True),evidence=dict(required=True),source_sha=dict(required=True),request_id=dict(required=True))
    add("attempt-release",attempt=dict(required=True),generation=dict(required=True,type=int),observation=dict(required=True),request_id=dict(required=True))
    add("candidate-freeze",task=dict(required=True),candidate=dict(required=True),checkout=dict(required=True),request_id=dict(required=True))
    add("verify-start",verification=dict(required=True),candidate=dict(required=True),verifier=dict(required=True),request_id=dict(required=True))
    add("verify-record",verification=dict(required=True),result=dict(choices=["PASS","FAIL","INDETERMINATE"],required=True),exit_code=dict(type=int,required=True),evidence=dict(required=True),request_id=dict(required=True))
    add("task-accept",task=dict(required=True),candidate=dict(required=True),decision=dict(required=True),request_id=dict(required=True))
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        print(operation(args.run_dir.resolve(),args))
        return 0
    except (LedgerError,sqlite3.IntegrityError,sqlite3.OperationalError,FileNotFoundError,subprocess.TimeoutExpired,OSError) as exc:
        print("ERROR " + str(exc)[:250],file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
