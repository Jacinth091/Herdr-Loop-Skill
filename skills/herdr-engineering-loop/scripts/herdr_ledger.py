#!/usr/bin/env python3
"""Opt-in Herdr run ledger; default promotion remains gated on a Windows tracer.

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

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
import run_backend
import verification_guard

VERSION = 3
TASK = re.compile(r"^T[0-9]{2,4}$")
ATTEMPT = re.compile(r"^(T[0-9]{2,4})-A[1-9][0-9]{0,2}$")
KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,69}$")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SCHEMA = """
CREATE TABLE run(id TEXT PRIMARY KEY, repo TEXT NOT NULL, plan_path TEXT,
 plan_sha TEXT, grant_ref TEXT, max_workers INTEGER NOT NULL,
 state TEXT NOT NULL DEFAULT 'PLANNING', generation INTEGER NOT NULL DEFAULT 1);
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
 heavy_generation INTEGER NOT NULL, gate TEXT NOT NULL, command TEXT, process_pid INTEGER);
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


class HerdrCliError(LedgerError):
    """A domain error the Herdr CLI returned, as distinct from an unreachable transport."""

    def __init__(self, message, code=""):
        super().__init__(message)
        self.code = code


def fail(message):
    raise LedgerError(message)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def size_proxy(text):
    """Byte and estimated-token cost of a read, so callers can budget context before reading it."""
    raw = text.encode("utf-8")
    return {"text_bytes": len(raw), "approx_tokens": (len(raw) + 3) // 4}


def as_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


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
        cp = subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True, encoding="utf-8",
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
    backend, _ = run_backend.get_active_backend(root)
    if backend != "sqlite":
        fail("BACKEND_INTEGRITY_BLOCKED: SQLite is not the selected authority")
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
        fail(f"ledger schema v{version} requires explicit upgrade to v{VERSION}")
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
            return f"OK schema={VERSION} already-current"
        if version not in (1, 2):
            fail("unknown ledger schema; upgrade refused")
        if version == 1:
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
                    "ALTER TABLE verification ADD COLUMN process_pid INTEGER",
                    "CREATE TABLE decision(id TEXT PRIMARY KEY, subject TEXT NOT NULL, authority TEXT NOT NULL, actor TEXT NOT NULL, attestor TEXT NOT NULL, disposition TEXT NOT NULL, provenance TEXT NOT NULL, rationale_path TEXT, rationale_sha TEXT)",
                    "CREATE TABLE critique(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidate(id), critic TEXT NOT NULL, report_path TEXT NOT NULL, report_sha TEXT NOT NULL)",
                    "CREATE TABLE finding(id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidate(id), review_id TEXT NOT NULL REFERENCES critique(id), actor TEXT NOT NULL, severity TEXT NOT NULL, disposition TEXT NOT NULL, evidence_path TEXT NOT NULL, evidence_sha TEXT NOT NULL)",
                    "CREATE TABLE signal(event_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempt(id), kind TEXT NOT NULL, sender TEXT NOT NULL, recipient TEXT NOT NULL, sender_pane TEXT NOT NULL, evidence_path TEXT NOT NULL, evidence_sha TEXT NOT NULL, receipt_path TEXT NOT NULL, receipt_sha TEXT NOT NULL)",
                ):
                    conn.execute(sql)
                # v1 asserted STARTED without a Herdr observation; force reconciliation.
                conn.execute("UPDATE attempt SET state='UNCERTAIN' WHERE state IN ('RESERVED','STARTED')")
                conn.execute("UPDATE verification SET state='UNCERTAIN' WHERE state='STARTED'")
                conn.execute(f"PRAGMA user_version={VERSION}")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return f"OK schema={VERSION} backup={backup.name} v1-live-attempts=UNCERTAIN"
        if version == 2:
            backup = root / "ledger.v2.backup.sqlite"
            if backup.exists():
                fail("v2 backup already exists; inspect before retrying")
            with closing(sqlite3.connect(str(backup))) as destination:
                conn.backup(destination)
            try:
                conn.execute("BEGIN IMMEDIATE")
                verification_columns = {row[1] for row in conn.execute("PRAGMA table_info(verification)")}
                if "process_pid" not in verification_columns:
                    conn.execute("ALTER TABLE verification ADD COLUMN process_pid INTEGER")
                conn.execute(f"PRAGMA user_version={VERSION}")
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return f"OK schema={VERSION} backup={backup.name}"


def herdr_cli_error(result):
    """The CLI's structured error, which it may write to either stream.

    Reading only stdout reports an ordinary result such as `agent_not_found` as an unreachable
    transport and truncates the reason at 100 characters. Parse both streams so the caller can
    classify the outcome and act on `code`.
    """
    for stream in (result.stdout, result.stderr):
        try:
            error = json.loads(stream or "").get("error")
        except (ValueError, AttributeError):
            continue
        if isinstance(error, dict):
            return error
    return None


def herdr_json(*argv):
    if os.environ.get("HERDR_ENV") != "1":
        fail("BLOCKED: HERDR_CONTEXT_UNAVAILABLE")
    executable=shutil.which("herdr")
    if not executable:fail("BLOCKED: HERDR_CLI_UNAVAILABLE")
    try:
        result = subprocess.run([executable, *argv], capture_output=True, text=True, encoding="utf-8", timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail("BLOCKED: HERDR_TRANSPORT_UNAVAILABLE: " + type(exc).__name__)
    if result.returncode:
        error = herdr_cli_error(result)
        if error is not None:
            raise HerdrCliError(
                "BLOCKED: HERDR_ERROR: " + (error.get("message") or error.get("code") or "unknown"),
                error.get("code", ""))
        fail("BLOCKED: HERDR_TRANSPORT_UNAVAILABLE: " + (result.stderr.strip() or result.stdout.strip())[:100])
    try:
        return json.loads(result.stdout)["result"]
    except (ValueError, KeyError, TypeError):
        fail("BLOCKED: HERDR_RESPONSE_INVALID")


def unregistered_agent_refusal(name, pane, provider=None, checkout=None):
    """Refuse a name Herdr no longer resolves, distinguishing the two situations behind it.

    Herdr clears an agent's name when that agent exits, is released, or is replaced, so an
    expected name can vanish while its pane still hosts the live agent. Probing the pane is what
    separates "it lost its name, re-name it" from "nothing is there, start it".
    """
    process = None
    try:
        process = herdr_json("pane", "process-info", "--pane", pane)["process_info"]
    except LedgerError:
        pass
    processes = (process or {}).get("foreground_processes", [])
    live = [p for p in processes
            if provider and checkout and p.get("name", "").lower().startswith(provider)
            and Path(p.get("cwd", "")).resolve() == Path(checkout).resolve()]
    if len(live) == 1:
        fail("BLOCKED: HERDR_AGENT_UNNAMED: pane "
             f"{pane} still hosts a live {provider} agent at {checkout}, but no agent is "
             f"registered under the name {name!r}; Herdr clears a name when its agent exits, is "
             f"released, or is replaced, so re-name the live occupant with "
             f"`herdr agent rename {pane} {name}`")
    occupancy = ("still hosts a live process" if processes else
                 "hosts no live process" if process is not None and process.get("pane_id") == pane else
                 "could not be read")
    fail(f"BLOCKED: HERDR_ERROR: no agent is registered under the name {name!r}, and pane {pane} "
         f"{occupancy}; if a live agent already occupies that pane, name it with "
         f"`herdr agent rename {pane} {name}`, otherwise start one with "
         f"`herdr agent start {name} --kind <kind> --pane <idle-shell-pane>`")


def refuse_tracked_opencode_config(checkout):
    # A tracked opencode.json would apply to every checkout of this branch and could override the
    # model the Delegator places per checkout, so it blocks the observation outright.
    try:
        tracked = subprocess.run(["git", "-C", str(checkout), "ls-files", "--error-unmatch", "opencode.json"],
                                 capture_output=True, text=True, encoding="utf-8", timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired):
        fail("BLOCKED: OPENCODE_MODEL_UNOBSERVABLE")
    if tracked.returncode == 0:
        fail("BLOCKED: OPENCODE_PROJECT_CONFIG_PRESENT")
    if tracked.returncode != 1:
        fail("BLOCKED: OPENCODE_MODEL_UNOBSERVABLE")


def opencode_configured_model(checkout):
    """The model OpenCode resolves for this checkout; the last config source that sets one wins."""
    executable = shutil.which("opencode")
    if not executable:
        fail("BLOCKED: OPENCODE_MODEL_UNOBSERVABLE")
    try:
        result = subprocess.run([executable, "debug", "config"], cwd=str(checkout), capture_output=True,
                                text=True, encoding="utf-8", timeout=15, check=False)
        sources = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        fail("BLOCKED: OPENCODE_MODEL_UNOBSERVABLE")
    if not isinstance(sources, list):
        fail("BLOCKED: OPENCODE_MODEL_UNOBSERVABLE")
    model = ""
    for source in sources:
        info = source.get("info") if isinstance(source, dict) else None
        value = info.get("model") if isinstance(info, dict) else None
        if not value:
            continue
        if isinstance(value, str):
            model = value
        elif isinstance(value, dict) and isinstance(value.get("providerID"), str) and isinstance(value.get("model"), str):
            model = value["providerID"] + "/" + value["model"]
        else:
            fail("BLOCKED: OPENCODE_MODEL_UNOBSERVABLE")
    return model


def opencode_session_model(checkout, session_id):
    """The model the live OpenCode session runs, from its export; a config can name another one."""
    executable = shutil.which("opencode")
    if not executable:
        fail("BLOCKED: OPENCODE_MODEL_UNOBSERVABLE")
    try:
        result = subprocess.run([executable, "session", "export", session_id], cwd=str(checkout),
                                capture_output=True, text=True, encoding="utf-8", timeout=15, check=False)
        exported = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        fail("BLOCKED: OPENCODE_MODEL_UNOBSERVABLE")
    info = exported.get("info") if isinstance(exported, dict) else None
    model = info.get("model") if isinstance(info, dict) else None
    if (not isinstance(model, dict) or not isinstance(model.get("providerID"), str) or
            not isinstance(model.get("id"), str) or not model["providerID"] or not model["id"]):
        fail("BLOCKED: OPENCODE_MODEL_UNOBSERVABLE")
    return model["providerID"] + "/" + model["id"]


def observe_herdr(name, pane, checkout, provider):
    """Read installed Herdr state before the SQLite write transaction.

    The model comes from process arguments. For OpenCode without them, it comes from the live
    session export when Herdr reports an OpenCode session, else from the resolved config. Effort
    comes from process arguments only.
    """
    try:
        agent = herdr_json("agent", "get", name)["agent"]
    except HerdrCliError as exc:
        if exc.code != "agent_not_found":
            raise
        unregistered_agent_refusal(name, pane, provider, checkout)
    process = herdr_json("pane", "process-info", "--pane", pane)["process_info"]
    if not agent.get("name"):
        # Governance binds to a registered agent name, never to a bare pane. The pane already
        # hosts an agent, so the remedy is to name that agent -- not to start another one --
        # and not to echo back whatever was passed (which may itself be a pane id).
        fail("BLOCKED: HERDR_AGENT_UNNAMED: the agent in pane "
             f"{agent.get('pane_id') or pane} has no registered name, and the ledger binds to a "
             f"name rather than a pane; name it with `herdr agent rename "
             f"{agent.get('pane_id') or pane} <agent-name>`, then pass that same <agent-name> "
             f"instead of {name!r}")
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
    if provider == "opencode":
        refuse_tracked_opencode_config(checkout)
    argv = p.get("argv", [])
    model = ""
    effort = ""
    for i, arg in enumerate(argv[:-1]):
        if arg in ("-m", "--model"):
            model = argv[i + 1]
        if arg == "-c" and argv[i + 1].startswith("model_reasoning_effort="):
            effort = argv[i + 1].split("=", 1)[1].strip("\"'")
    if provider == "opencode" and not model:
        session = agent.get("agent_session") or {}
        if session.get("source") == "herdr:opencode" and isinstance(session.get("value"), str) and session["value"]:
            model = opencode_session_model(checkout, session["value"])
        else:
            model = opencode_configured_model(checkout)
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
        if is_pid_alive(pid):
            fail("UNCERTAIN: agent PID still alive outside pane foreground")
        fail("UNCERTAIN: external worker descendant ownership unavailable; writer claim retained")
    if is_pid_alive(pid):
        fail("UNCERTAIN: pane closed but agent PID is still alive")
    fail("UNCERTAIN: external worker descendant ownership unavailable; writer claim retained")


def is_pid_alive(pid):
    # Fails toward "alive": only a conclusive no-such-process answer counts as stopped.
    # shortcut: PID reuse blocks release; require process-start identity before relaxing this guard.
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
        kernel32.GetExitCodeProcess.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such process
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # EPERM: exists but owned by someone else
    return True


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
    try:
        agent=herdr_json("agent","get",name)["agent"]
    except HerdrCliError as exc:
        if exc.code != "agent_not_found":
            raise
        unregistered_agent_refusal(name, pane)
    if not agent.get("name"):
        fail("BLOCKED: HERDR_AGENT_UNNAMED: the signal recipient in pane "
             f"{agent.get('pane_id') or pane} has no registered name, and the ledger binds to a "
             f"name rather than a pane; name it with `herdr agent rename "
             f"{agent.get('pane_id') or pane} <agent-name>`, then pass that same <agent-name> "
             f"instead of {name!r}")
    if agent.get("name")!=name or agent.get("pane_id")!=pane:
        fail("BLOCKED: SIGNAL_RECIPIENT_MISMATCH")


def machine_lock_path():
    return verification_guard.lock_path()


def check_machine_verification_lock(run_id, verification_id):
    try:
        verification_guard.read_owner(run_id, verification_id, required=False)
    except RuntimeError as exc:
        fail(str(exc))


def acquire_machine_verification_lock(run_id, verification_id, pid):
    try:
        return verification_guard.acquire(run_id, verification_id, pid)
    except RuntimeError as exc:
        fail(str(exc))


def release_machine_verification_lock(run_id, verification_id):
    try:
        verification_guard.release(run_id, verification_id)
    except RuntimeError as exc:
        fail(str(exc))


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
    if run["state"] not in allowed_states:
        fail("run is not executing; no new transitions allowed")
    if check_plan:
        if not run["plan_path"] or not run["plan_sha"]:
            fail("run has no approved plan; transitions not allowed")
        plan = (root / run["plan_path"]).resolve()
        if not plan.is_file() or fingerprint(plan) != run["plan_sha"]:
            fail("approved PLAN.md changed; halt until new authorized generation")
        grant = one(conn, "SELECT rationale_path,rationale_sha FROM decision WHERE subject=? AND authority='HUMAN' AND provenance='execution-grant' ORDER BY rowid DESC LIMIT 1", (run["id"],))
        if grant and (not run["grant_ref"] or grant["rationale_path"] != run["grant_ref"]
                      or relative_evidence(root, grant["rationale_path"])[1] != grant["rationale_sha"]):
            fail("human execution grant changed; halt until reconciliation")
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


def receipt_hash_matches(lines, digest):
    # Windows Get-FileHash emits uppercase hexadecimal for the same digest.
    return any(line.startswith("evidence_sha=") and line.partition("=")[2].lower() == digest for line in lines)


RECEIPT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def receipt_source(args):
    """Exactly one of --receipt or --compose-receipt; the ledger stays the only writer when composing."""
    path = getattr(args, "receipt", "") or ""
    compose = bool(getattr(args, "compose_receipt", False))
    if bool(path) == compose:
        fail("supply exactly one of --receipt or --compose-receipt")
    return path, compose


def composed_receipt_path(kind, key):
    return f"receipts/{kind.lower()}-{key}.receipt"


def write_receipt(root, kind, key, envelope, evidence_sha):
    """Compose the correlated envelope the ledger already validates, so callers stop authoring boilerplate."""
    if not RECEIPT_NAME.fullmatch(str(key)):
        fail("invalid composed receipt name")
    relative = composed_receipt_path(kind, key)
    directory = root / "receipts"
    directory.mkdir(exist_ok=True)
    target = root / relative
    if target.is_symlink():
        fail("composed receipt target is a symlink")
    staged = root / (relative + ".tmp." + str(os.getpid()))
    staged.write_text(envelope + "\n" + "evidence_sha=" + evidence_sha + "\n", encoding="utf-8")
    os.replace(staged, target)
    return relative


def signal_receipt(root, run_id, kind, attempt, event_id, sender, pane, recipient, evidence, receipt, compose=False):
    if not KEY.fullmatch(event_id) or sender == recipient:
        fail("invalid signal event or recipient")
    if compose and not (sender and pane and recipient):
        fail("cannot compose a receipt without an observed sender, pane and recipient")
    artifact_path, artifact_sha = relative_evidence(root, evidence)
    expected = f"{kind} {run_id} {attempt} {event_id} {sender} {pane} {recipient}"
    if compose:
        receipt = write_receipt(root, kind, event_id, expected, artifact_sha)
    path, digest = relative_evidence(root, receipt)
    lines = (root / path).read_text(encoding="utf-8").splitlines()
    if expected not in lines or not receipt_hash_matches(lines, artifact_sha):
        fail("signal receipt does not contain exact correlated envelope")
    return artifact_path, artifact_sha, path, digest


def activation_receipts(root):
    with closing(connect(root, True)) as conn:
        receipts = {}
        for row in conn.execute("SELECT receipt FROM event WHERE op='role-activation' ORDER BY seq"):
            item = json.loads(row["receipt"])
            receipts[item["operation_id"]] = item
        return list(receipts.values())


def store_activation(root, receipt):
    with closing(connect(root)) as conn:
        revision = one(conn, "SELECT count(*) AS n FROM event WHERE op='role-activation'")["n"]
        return action(conn, root, "role-activation", f"activation_{revision+1}", receipt,
                      lambda: json.dumps(receipt, sort_keys=True),
                      allowed_states=("PLANNING", "EXECUTING"), check_plan=False)


ROLE_OPERATIONS = {
    "plan-approve": {"manager", "lead"},
    "run-authorize": {"delegator", "lead"},
    "decision-record": {"manager", "lead"},
    "critique-record": {"critic"},
    "finding-record": {"critic", "manager", "lead"},
    "verify-exec": {"verifier"},
    "verify-record": {"verifier"},
    "verify-reconcile": {"delegator", "lead", "verifier"},
}
COORDINATOR_OPERATIONS = {
    "task-add", "task-retry", "attempt-reserve", "attempt-attach", "attempt-ready",
    "attempt-start", "attempt-report", "attempt-release", "candidate-freeze",
    "verify-start", "verify-abort", "task-accept", "run-cancel", "cancel-block", "cancel-finalize",
}


def role_guard(root, args):
    """Selected governed runs require a current packet and an observed named caller."""
    packet = root / "ACTIVATION.json"
    if packet.is_symlink():
        fail("ROLE_AUTHORITY_BLOCKED: activation packet is symlinked")
    if not packet.exists():
        if (root / run_backend.SELECTOR_FILE).exists():
            fail("ROLE_AUTHORITY_BLOCKED: selected run requires bound role packets")
        return  # Explicit offline pilot APIs remain compatible; never infer live authority.
    manifest = json.loads(packet.read_text(encoding="utf-8"))
    if manifest.get("generated_by") != "herdr-role-pack" or manifest.get("run_id") != root.name:
        fail("ROLE_AUTHORITY_BLOCKED: activation manifest identity is invalid")
    backend, selector = run_backend.get_active_backend(root)
    epoch = selector["epoch"] if selector else 1
    if (backend != "sqlite" or manifest.get("state_backend") != backend or
            manifest.get("backend_epoch") != epoch or getattr(args, "epoch", None) != epoch):
        fail("BACKEND_INTEGRITY_BLOCKED: refresh packet and supply current --epoch")
    name = getattr(args, "as_agent", None)
    entries = [a for a in manifest["agents"] if a["name"] == name]
    if len(entries) != 1:
        fail("ROLE_AUTHORITY_BLOCKED: --as-agent must name one assigned role")
    entry = entries[0]
    if args.command == "role-confirm":
        return
    allowed = ROLE_OPERATIONS.get(args.command, {"delegator", "lead"} if args.command in COORDINATOR_OPERATIONS else set())
    if entry["role"] not in allowed:
        fail("ROLE_AUTHORITY_BLOCKED: assigned role cannot perform " + args.command)
    matches = [r for r in activation_receipts(root) if r["agent"] == name and r.get("epoch") == epoch
               and r["status"] == "CONFIRMED_APPLIED" and r.get("prompt_sha256") == hashlib.sha256(entry["prompt"].encode()).hexdigest()]
    if not matches:
        fail("ROLE_NOT_READY: correlated role acknowledgment required")
    acknowledgment = matches[-1]
    if (relative_evidence(root, acknowledgment["ready_path"])[1] != acknowledgment["ready_sha"] or
            relative_evidence(root, acknowledgment["receipt_path"])[1] != acknowledgment["receipt_sha"]):
        fail("ROLE_NOT_READY: role acknowledgment evidence changed")
    observed = observe_herdr(name, acknowledgment["pane"], entry.get("worktree") or manifest["repo"], entry["kind"])
    if any(observed[key] != acknowledgment["observed"][key] for key in ("pid", "terminal", "model", "effort")):
        fail("ROLE_NOT_READY: role process changed; acknowledge the new identity")
    caller_pane = herdr_json("pane", "current", "--current")["pane"].get("pane_id")
    if not caller_pane or caller_pane != acknowledgment["pane"]:
        fail("ROLE_AUTHORITY_BLOCKED: caller pane is not the acknowledged role pane")
    for field in ("actor", "critic", "verifier"):
        value = getattr(args, field, None)
        human = getattr(args, "authority", None) == "HUMAN" or args.command in ("run-authorize", "run-cancel")
        if value and not (field == "actor" and human) and value != name:
            # A coordinator allocates verification to the separately assigned Verifier.
            if args.command == "verify-start" and field == "verifier" and any(
                    a["name"] == value and a["role"] == "verifier" for a in manifest["agents"]):
                continue
            fail("ROLE_AUTHORITY_BLOCKED: decision/review actor does not match caller")
    if args.command in ("attempt-ready", "attempt-start") and (args.operator != name or args.operator_pane != acknowledgment["pane"]):
        fail("ROLE_AUTHORITY_BLOCKED: signal operator does not match caller")
    if args.command == "attempt-report" and args.recipient != name:
        fail("ROLE_AUTHORITY_BLOCKED: report recipient does not match coordinator")
    if args.command == "attempt-reserve":
        assigned = [a for a in manifest["agents"] if a["role"] == "worker" and a["name"] == args.agent
                    and a.get("attempt_id") == args.attempt and a.get("task_id") == args.task]
        if (len(assigned) != 1 or Path(assigned[0]["worktree"]).resolve() != Path(args.checkout).resolve()
                or assigned[0]["kind"] != args.provider):
            fail("ROLE_AUTHORITY_BLOCKED: worker attempt/checkout not assigned")


def confirm_role(root, args):
    manifest = json.loads((root / "ACTIVATION.json").read_text(encoding="utf-8"))
    entry = next(a for a in manifest["agents"] if a["name"] == args.as_agent)
    operation_id, prompt_hash = args.operation_id, hashlib.sha256(entry["prompt"].encode()).hexdigest()
    receipts = {r["operation_id"]: r for r in activation_receipts(root)}
    previous = receipts.get(operation_id)
    if (not previous or previous["agent"] != args.as_agent or previous["prompt_sha256"] != prompt_hash
            or previous.get("epoch") != args.epoch):
        fail("ROLE_NOT_READY: no matching activation intent")
    if previous["status"] not in ("SUBMITTING", "SUBMITTED", "OUTCOME_UNCERTAIN", "CONFIRMED_APPLIED"):
        fail("ROLE_NOT_READY: activation was denied or not submitted")
    observed = observe_herdr(entry["name"], args.pane,
                             entry.get("worktree") or manifest["repo"], entry["kind"])
    if not previous.get("observed") or any(observed[key] != previous["observed"][key] for key in ("pid", "terminal")):
        fail("ROLE_NOT_READY: activation process changed; send fresh setup to the observed agent")
    if (entry.get("model") and entry["model"] != observed["model"]) or (entry.get("reasoning_effort") and entry["reasoning_effort"] != observed["effort"]):
        fail("ROLE_NOT_READY: requested model/effort not observed")
    path, digest = relative_evidence(root, args.evidence)
    receipt_arg, receipt_compose = receipt_source(args)
    if not receipt_compose:
        receipt_path, receipt_sha = relative_evidence(root, receipt_arg)
    expected = (f"run={manifest['run_id']}", f"epoch={args.epoch}", f"agent={args.as_agent}",
                f"pane={args.pane}", f"role={entry['role']}", f"operation={operation_id}",
                f"prompt_sha={prompt_hash}")
    if any(line not in (root / path).read_text(encoding="utf-8").splitlines() for line in expected):
        fail("ROLE_NOT_READY: readiness does not match role/epoch/prompt")
    recipient = "human" if entry["role"] == "lead" else entry.get("signal_recipient")
    if not recipient or args.recipient != recipient:
        fail("ROLE_NOT_READY: acknowledgment recipient is not assigned")
    if recipient != "human":
        observe_recipient(recipient, args.recipient_pane)
    envelope = f"ROLE_READY {manifest['run_id']} {operation_id} {args.as_agent} {args.pane} {recipient}"
    if receipt_compose:
        receipt_path, receipt_sha = relative_evidence(root, write_receipt(root, "ROLE_CONFIRM", operation_id, envelope, digest))
    lines = (root / receipt_path).read_text(encoding="utf-8").splitlines()
    if envelope not in lines or not receipt_hash_matches(lines, digest):
        fail("ROLE_NOT_READY: correlated role receipt missing")
    receipt = dict(previous, status="CONFIRMED_APPLIED", epoch=args.epoch, pane=args.pane,
                   observed=observed, ready_path=path, ready_sha=digest,
                   receipt_path=receipt_path, receipt_sha=receipt_sha)
    store_activation(root, receipt)
    return f"OK role={entry['role']} agent={args.as_agent} epoch={args.epoch} CONFIRMED_APPLIED"


PROJECTION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}\.md$")
PROJECTION_RESERVED = {"session.md", "roster.md", "activate.md", "activation.json",
                       "ledger.sqlite", "backend.active.json", "backend.json"}


def render_projection(conn, run, tasks):
    """Human-facing, non-authoritative view rendered from ledger rows; never edited by hand."""
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
    lines += ["", "Generated by herdr_ledger.py export; regenerate instead of editing. The SQLite ledger is authoritative.", ""]
    return "\n".join(lines)


def write_projection(root, name, text):
    """Atomically write one generated view inside the run directory; never touches a canonical ledger."""
    if not isinstance(name, str) or not name or name.lower() in PROJECTION_RESERVED:
        fail("projection name is reserved; choose a new .md filename such as STATUS.md")
    if not PROJECTION_NAME.fullmatch(name):
        fail("projection path must be a single NAME.md filename inside the run directory")
    target = root / name
    if target.is_symlink() or (target.exists() and target.is_dir()):
        fail("projection target is not a regular file")
    staged = root / (name + ".tmp." + str(os.getpid()))
    staged.write_text(text, encoding="utf-8")
    os.replace(staged, target)
    return f"OK wrote={name} bytes={len(text.encode('utf-8'))} non_authoritative=true"


def role_operations(role):
    """Ledger commands this role may perform, mirroring role_guard's maps."""
    allowed = {"status", "next", "task-show", "events", "export", "packet", "role-confirm"}
    for command, roles in ROLE_OPERATIONS.items():
        if role in roles:
            allowed.add(command)
    if role in ("delegator", "lead"):
        allowed |= COORDINATOR_OPERATIONS
    return sorted(allowed)


def status_human(run, tasks, ids=False):
    """One shared human view, so its byte/token cost can be measured before anyone reads it."""
    lines = ["# Work progress", f"Run: {run['state'].replace('_',' ').title()}"]
    labels = {"PLANNED": "Planned", "ACTIVE": "In progress", "REPORTED": "Awaiting independent review",
              "BLOCKED": "Blocked", "ACCEPTED": "Accepted", "CANCELLED": "Cancelled"}
    for t in tasks[:12]:
        suffix = f" ({t['id']})" if ids else ""
        lines.append(f"- **{t['title']}{suffix}**: {labels.get(t['state'],'Needs reconciliation')} | {t['outcome']}")
    if len(tasks) > 12:
        lines.append(f"({len(tasks)-12} more; use task-show)")
    return "\n".join(lines)


ROLE_ORDER = ("manager", "delegator", "lead", "worker", "verifier", "critic")
# What attempt-attach can confirm: the model from process arguments or, for OpenCode, from the
# live session export (else `opencode debug config`); effort only from Codex's flag
# (-c model_reasoning_effort=).
# Reserving an expectation the observer can never read would only block the attach later with
# MODEL_OVERRIDE_NOT_APPLIED, so reserve refuses it up front. This refuses an unconfirmable
# declaration; it deliberately does not mandate one, because a model the pack did not choose for
# the role is still a legitimate reservation.
OBSERVABLE_EFFORT = {"codex"}
READ_ONLY_COMMANDS = ("events", "export", "next", "packet", "recover", "status", "task-show")


def _subcommands():
    for action in parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


# Requiredness comes from nargs alone. argparse changed the default for REMAINDER positionals
# in 3.13, so trusting action.required would drift the generated text by interpreter version.
# Only "?" and "*" make a positional optional; a guarded command is still a required argument.
OPTIONAL_NARGS = (argparse.OPTIONAL, argparse.ZERO_OR_MORE)


def _synopsis(name, sub):
    parts = [name]
    for a in sub._actions:
        if a.dest == "help":
            continue
        if not a.option_strings:
            label = f"<{a.metavar or a.dest}>" + ("..." if a.nargs in (argparse.REMAINDER, argparse.PARSER, "+") else "")
            parts.append(label if a.nargs not in OPTIONAL_NARGS else f"[{label}]")
        elif a.nargs == 0:
            parts.append(f"[{a.option_strings[0]}]")
        else:
            if a.metavar:
                value = a.metavar
            elif a.choices:
                value = "|".join(str(c) for c in a.choices)
            else:
                value = a.dest
            flag = f"{a.option_strings[0]} <{value}>"
            parts.append(flag if a.required else f"[{flag}]")
    return " ".join(parts)


def command_reference(role=None):
    """Generated role-command lists and synopses; the parser and role maps are the only source of truth."""
    commands = _subcommands()
    if role is not None:
        if role not in ROLE_ORDER:
            fail("unknown role")
        lines = [f"# Ledger command reference for role={role} (generated)", "",
                 "Generated from the ledger CLI. Do not hand-edit; print this role's list with",
                 f"`python herdr_ledger.py --run-dir <any> reference --role {role}`.", "",
                 "## Commands this role may run", ""]
        for name in role_operations(role):
            lines.append(f"- `{_synopsis(name, commands[name])}`")
        lines += ["", "Mutations outside this list are refused for your role.", ""]
        return "\n".join(lines)
    lines = ["# Ledger command reference (generated)", "",
             "Generated from the ledger CLI itself (its parser and role maps). Do not hand-edit this file:",
             "regenerate it with `python tools/render_reference.py`. Never regenerate it by shell",
             "redirection; Windows PowerShell re-encodes the stream as UTF-16LE and corrupts the file.", "",
             "Read one role's list with `reference --role <role>`.", "",
             "## Role command lists", ""]
    for role_name in ROLE_ORDER:
        lines.append(f"- `{role_name}`: " + ", ".join(f"`{c}`" for c in role_operations(role_name)))
    lines += ["", "Every role may also run the read-only commands "
              + ", ".join(f"`{c}`" for c in READ_ONLY_COMMANDS) + ".",
              "Each assigned role records its own `role-confirm` activation acknowledgment.", "",
              "## Commands", ""]
    for name in sorted(commands):
        lines.append(f"- `{_synopsis(name, commands[name])}`")
    lines += ["", "## Global options", "",
              "- `--run-dir <path>` (required for every command except `reference`, which ignores it)",
              "- `--as-agent <name>` and `--epoch <n>` (required for governed mutations in a selected run)", ""]
    return "\n".join(lines)


def packet_payload(conn, run, tasks, role, agent, backend, epoch):
    """Structured, role-scoped next-step directive shared by the text and JSON renderings."""
    allowed = role_operations(role)
    ready_ids = [t["id"] for t in tasks if run["state"] == "EXECUTING" and ready(conn, t["id"])]
    active = [f"{t['id']}:{t['state']}" for t in tasks if t["state"] in ("ACTIVE", "REPORTED")]
    reported = [t["id"] for t in tasks if t["state"] == "REPORTED"]
    blockers = [f"{f['id']}:{f['severity']}" for f in conn.execute(
        "SELECT id,severity FROM finding WHERE disposition='OPEN' AND severity IN ('BLOCKING','HIGH') ORDER BY id LIMIT 5")]
    uncertain = [r["id"] for r in conn.execute(
        "SELECT id FROM attempt WHERE state IN ('UNCERTAIN','BLOCKED') ORDER BY id LIMIT 5")]
    if run["state"] == "PLANNING":
        action, text = "plan-approve", "plan-approve PLAN.md"
    elif run["state"] in ("CANCELLING", "CANCELLED"):
        action, text = ("cancel-finalize", "cancel-block"), "cancel-finalize or cancel-block"
    elif not tasks and run["state"] == "EXECUTING":
        action, text = "task-add", "task-add <slice>"
    elif ready_ids:
        action, text = "attempt-reserve", "attempt-reserve " + ready_ids[0]
    elif reported:
        action, text = "candidate-freeze", "candidate-freeze " + reported[0]
    elif active:
        action, text = None, "monitor " + active[0]
    elif tasks and all(t["state"] == "ACCEPTED" for t in tasks):
        action, text = None, "no open work; all tasks accepted"
    else:
        action, text = None, "await state=" + run["state"]
    if action is None:
        nxt = text
    else:
        names = (action,) if isinstance(action, str) else tuple(action)
        nxt = text if any(n in allowed for n in names) else f"none for role={role}; pending {text}"
    return {"run": run["id"], "role": role, "agent": agent, "state": run["state"], "backend": backend,
            "epoch": epoch, "next": nxt, "may_run": allowed, "ready": ready_ids, "active": active,
            "blockers": blockers + uncertain}


def render_packet(conn, run, tasks, role, agent, backend, epoch):
    """Bounded, role-scoped next-step directive so agents never re-read a Markdown ledger."""
    p = packet_payload(conn, run, tasks, role, agent, backend, epoch)
    identity = f"PACKET run={p['run']} role={p['role']}" + (f" agent={p['agent']}" if p["agent"] else "")
    return "\n".join([
        identity + f" state={p['state']} backend={p['backend']} epoch={p['epoch']}",
        "NEXT: " + p["next"],
        "MAY-RUN: " + ",".join(p["may_run"]),
        "READY: " + (",".join(p["ready"]) or "-"),
        "ACTIVE: " + (",".join(p["active"]) or "-"),
        "BLOCKERS: " + (",".join(p["blockers"]) or "NONE"),
    ])


def operation(root, args):
    if args.command in ("status", "next", "task-show", "recover", "events", "packet", "verify-exec", "reference") or (
            args.command == "export" and not getattr(args, "write", None)):
        return _operation(root, args)
    try:
        with run_backend.run_lock(root):
            return _operation(root, args)
    except RuntimeError as exc:
        fail(str(exc))


def _operation(root, args):
    name = args.command
    if name not in ("init", "upgrade", "status", "next", "task-show", "recover", "events", "export", "packet", "reference"):
        role_guard(root, args)
    if name == "reference":
        return command_reference(getattr(args, "role", None))
    if name == "role-confirm":
        return confirm_role(root, args)
    if name == "upgrade":
        return upgrade(root)
    if name == "init":
        if not RUN_ID.fullmatch(args.run_id): fail("invalid run ID")
        if (root / run_backend.SELECTOR_FILE).exists():
            fail("BACKEND_INTEGRITY_BLOCKED: existing selector must be reconciled before initialization")
        if args.max_workers not in (1,2,3): fail("max-workers must be 1, 2 or 3")
        if args.max_workers > 1 and not args.parallel_approved:
            fail("parallel worker budget requires explicit approval")
        if (root / "SESSION.md").exists():
            fail("refusing to create a competing ledger for an existing SESSION.md run")
        if (root / "ledger.sqlite").exists(): fail("ledger already initialized")
        if not root.is_dir(): fail("run directory must be created explicitly")
        repo_path = Path(args.repo).resolve(strict=True)
        is_bootstrap = getattr(args, "bootstrap", False)
        if not is_bootstrap and (not getattr(args, "plan", None) or not getattr(args, "grant_ref", None)):
            fail("init requires both an approved plan and grant, or explicit --bootstrap")
        if not is_bootstrap and not (repo_path / ".git").exists():
            fail("repo must be an existing Git checkout")
        elif not repo_path.is_dir():
            fail("repo must be an existing directory")
        if not is_bootstrap:
            plan_path, plan_hash = relative_evidence(root, args.plan)
            grant_ref = args.grant_ref.strip()
            if not grant_ref: fail("explicit execution grant reference required")
            state = "EXECUTING"
        else:
            plan_path, plan_hash = None, None
            grant_ref = None
            state = "PLANNING"
        conn = sqlite3.connect(str(root / "ledger.sqlite"), timeout=3.0)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version={VERSION}")
            conn.execute("INSERT INTO run(id,repo,plan_path,plan_sha,grant_ref,max_workers,state) VALUES(?,?,?,?,?,?,?)",
                         (args.run_id, str(repo_path), plan_path, plan_hash, grant_ref, args.max_workers, state))
            conn.execute("INSERT INTO event(request_id,digest,op,receipt,created) VALUES(?,?,?,?,?)",
                         ("init:"+args.run_id, "init", "init", f"OK run={args.run_id} schema={VERSION} state={state}", stamp()))
            conn.commit()
            return f"OK run={args.run_id} schema={VERSION} mode=sqlite state={state}"
        finally:
            conn.close()
    with closing(connect(root, name in ("status", "next", "task-show", "recover", "events", "export", "packet"))) as conn:
        if name in ("status", "next", "task-show", "recover", "events", "export", "packet"):
            run = one(conn, "SELECT * FROM run LIMIT 1")
            if not run:fail("run metadata missing")
            wants_json = bool(getattr(args, "json", False))
            if name == "task-show":
                t=get(conn, "task", args.task)
                deps=json.loads(t["deps"])
                text=(f"task={t['id']} state={t['state']} title={t['title']}\n"
                      f"outcome={t['outcome']} deps={','.join(deps) or '-'} brief={t['brief_path']}")
                if wants_json:
                    return as_json({"command":name,"task":t["id"],"state":t["state"],"title":t["title"],
                                    "outcome":t["outcome"],"deps":deps,"brief":t["brief_path"],
                                    "read_cost":size_proxy(text)})
                return text
            if name == "events":
                rows=conn.execute("SELECT seq,op,receipt FROM event ORDER BY seq DESC LIMIT ?", (args.limit,)).fetchall()
                text="\n".join(f"{r['seq']} {r['op']} {r['receipt']}" for r in rows)
                if wants_json:
                    return as_json({"command":name,"limit":args.limit,"events":[dict(r) for r in rows],
                                    "read_cost":size_proxy(text)})
                return text
            tasks = conn.execute("SELECT * FROM task ORDER BY id").fetchall()
            if name == "export":
                text = render_projection(conn, run, tasks)
                if getattr(args, "write", None):
                    return write_projection(root, args.write, text)
                return text
            if name == "packet":
                backend, selector = run_backend.get_active_backend(root)
                epoch = selector["epoch"] if selector else 1
                ident, agent = args.role, getattr(args, "agent", "") or ""
                text = render_packet(conn, run, tasks, ident, agent, backend, epoch)
                if wants_json:
                    payload = packet_payload(conn, run, tasks, ident, agent, backend, epoch)
                    payload["command"] = name
                    payload["read_cost"] = size_proxy(text)
                    return as_json(payload)
                return text
            if name == "next":
                can=[t for t in tasks if run["state"]=="EXECUTING" and ready(conn,t["id"])]
                text="\n".join(f"READY {t['id']} {t['title']}" for t in can[:5]) or "NO_READY_TASK"
                if wants_json:
                    return as_json({"command":name,"ready":[{"id":t["id"],"title":t["title"]} for t in can[:5]],
                                    "read_cost":size_proxy(text)})
                return text
            if name == "status":
                counts={s:sum(1 for t in tasks if t["state"]==s) for s in ("PLANNED","ACTIVE","REPORTED","ACCEPTED","BLOCKED")}
                nexts=[t["id"] for t in tasks if run["state"]=="EXECUTING" and ready(conn,t["id"])]
                active=[t["id"] for t in tasks if t["state"] in ("ACTIVE","REPORTED")]
                view = status_human(run, tasks, args.ids)
                if args.human:
                    return view
                text=(f"run={run['id']} state={run['state']} mode=sqlite-pilot next={','.join(nexts[:3]) or '-'} "
                      f"active={','.join(active[:3]) or '-'} accepted={counts['ACCEPTED']} blocked={counts['BLOCKED']} "
                      f"tasks={len(tasks)}")
                cost=size_proxy(view)
                if wants_json:
                    payload={"command":name,"run":run["id"],"state":run["state"],"mode":"sqlite-pilot",
                             "next":nexts[:3],"active":active[:3],"tasks":len(tasks),"counts":counts,
                             "human_view_cost":cost,"read_cost":size_proxy(text)}
                    return as_json(payload)
                return f"{text} view_bytes={cost['text_bytes']} view_tokens={cost['approx_tokens']}"
            unknown=conn.execute("SELECT id,state,pane FROM attempt WHERE state IN ('RESERVED','OBSERVED','READY','STARTED','UNCERTAIN','BLOCKED') ORDER BY id LIMIT 5").fetchall()
            checks=conn.execute("SELECT id FROM verification WHERE state IN ('STARTED','RUNNING','UNCERTAIN') ORDER BY id LIMIT 5").fetchall()
            text=(f"recovery={run['id']} state={run['state']} never-auto-respawn\n"+
                    "\n".join(f"ATTENTION attempt={a['id']} state={a['state']} pane={a['pane'] or '?'} observe-Herdr-first" for a in unknown)+
                    ("\n" if unknown else "")+
                    "\n".join(f"ATTENTION verify={c['id']} lock-held inspect-process" for c in checks))
            if wants_json:
                return as_json({"command":name,"run":run["id"],"state":run["state"],"never_auto_respawn":True,
                                "attempts":[dict(a) for a in unknown],"verifications":[c["id"] for c in checks],
                                "read_cost":size_proxy(text)})
            return text
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
        if name == "plan-approve":
            plan_path, plan_hash = relative_evidence(root, args.plan)
            rat_path, rat_hash = relative_evidence(root, args.rationale)
            data = dict(plan=plan_path, plan_sha=plan_hash, decision=args.decision_id,
                        authority=args.authority, actor=args.actor, attestor=args.attestor,
                        rationale=rat_path, rationale_sha=rat_hash)
            def act():
                run = one(conn, "SELECT * FROM run LIMIT 1")
                if not run: fail("run metadata missing")
                if not args.actor.strip() or not args.attestor.strip() or args.actor == args.attestor:
                    fail("plan approval needs distinct decision actor and recorder")
                gen = run["generation"] + (1 if run["plan_path"] else 0)
                conn.execute("INSERT INTO decision(id,subject,authority,actor,attestor,disposition,provenance,rationale_path,rationale_sha) VALUES(?,?,?,?,?,?,?,?,?)",
                             (args.decision_id, "plan", args.authority, args.actor, args.attestor, "APPROVED", "plan-approval", rat_path, rat_hash))
                conn.execute("UPDATE run SET plan_path=?, plan_sha=?, generation=? WHERE id=?",
                             (plan_path, plan_hash, gen, run["id"]))
                return f"OK plan={plan_path} sha={plan_hash} decision={args.decision_id} generation={gen}"
            return action(conn, root, name, request, data, act, allowed_states=("PLANNING",), check_plan=False)
        if name == "run-authorize":
            grant_path, grant_sha = relative_evidence(root, args.grant_ref)
            data = dict(grant_ref=grant_path, grant_sha=grant_sha, decision=args.decision_id,
                        actor=args.actor, attestor=args.attestor)
            def act():
                run = one(conn, "SELECT * FROM run LIMIT 1")
                if not run: fail("run metadata missing")
                if not args.actor.strip() or not args.attestor.strip() or args.actor == args.attestor:
                    fail("execution grant needs distinct human actor and recorder")
                if not run["plan_path"] or not run["plan_sha"]:
                    fail("run has no approved plan; approve plan before authorizing execution")
                plan = (root / run["plan_path"]).resolve()
                if not plan.is_file() or fingerprint(plan) != run["plan_sha"]:
                    fail("approved PLAN.md changed; halt until new authorized generation")
                conn.execute("INSERT INTO decision(id,subject,authority,actor,attestor,disposition,provenance,rationale_path,rationale_sha) VALUES(?,?,?,?,?,?,?,?,?)",
                             (args.decision_id, run["id"], "HUMAN", args.actor, args.attestor, "APPROVED", "execution-grant", grant_path, grant_sha))
                conn.execute("UPDATE run SET grant_ref=?, state='EXECUTING' WHERE id=?",
                             (grant_path, run["id"]))
                return f"OK run={run['id']} EXECUTING grant={grant_path} decision={args.decision_id}"
            return action(conn, root, name, request, data, act, allowed_states=("PLANNING",), check_plan=False)
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
                if args.provider not in ("codex", "claude", "agy", "opencode"):
                    fail("provider must be codex, claude, agy or opencode")
                if args.provider=="codex" and (not args.expected_model or args.expected_effort not in ("xhigh", "max")):
                    fail("Codex worker must declare expected model and xhigh/max effort")
                if args.expected_effort and args.provider not in OBSERVABLE_EFFORT:
                    fail(f"--expected-effort is only observable for Codex (-c model_reasoning_effort=); omit it for {args.provider} rather than reserve an expectation attempt-attach can never confirm")
                run=guarded(conn,root)
                if not ready(conn,args.task):fail("task not ready")
                running=one(conn,"SELECT COUNT(*) AS n FROM attempt WHERE state IN ('RESERVED','OBSERVED','READY','STARTED','UNCERTAIN','BLOCKED')")["n"]
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
            receipt_arg, receipt_compose = receipt_source(args)
            artifact, artifact_sha, path, digest = signal_receipt(root, run["id"], kind, args.attempt, args.event_id,
                                          sender, pane, recipient, args.evidence, receipt_arg, receipt_compose)
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
                                      f"pane={current['pane']}")
                    for field in ("model", "effort"):
                        if current[f"expected_{field}"]:
                            required_lines += (f"observed_{field}={current[f'expected_{field}']}",)
                    if (any(line not in ready for line in required_lines) or
                            any(not any(line.startswith(f"observed_{field}=") for line in ready)
                                for field in ("model", "effort"))):
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
            receipt_arg, receipt_compose = receipt_source(args)
            if receipt_compose:
                receipt, receipt_sha = composed_receipt_path("REPORT_READY", args.event_id), ""
            else:
                receipt, receipt_sha = relative_evidence(root, receipt_arg)
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
                    args.attempt, args.event_id, a["agent_hint"], a["pane"], args.recipient, args.evidence, receipt_arg, receipt_compose)
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
            previous = replay(conn,name,request,data)
            if previous is not None:return previous
            lock_created=acquire_machine_verification_lock(str(root.resolve()),args.verification,os.getpid())
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
            try:
                return action(conn,root,name,request,data,act)
            except Exception:
                if lock_created:
                    release_machine_verification_lock(str(root.resolve()),args.verification)
                raise
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
            lock_path=machine_lock_path()
            if not lock_path.is_file():fail("BLOCKED: MACHINE_VERIFICATION_LOCK_MISSING")
            check_machine_verification_lock(str(root.resolve()),args.verification)
            output=root / "verifications" / (args.verification+".log")
            if output.exists():fail("verification output already exists; reconcile before retry")
            output.parent.mkdir(exist_ok=True)
            def begin(pid):
                def act():
                    if get(conn,"verification",args.verification)["state"]!="STARTED":
                        fail("verification already executing")
                    conn.execute("UPDATE verification SET state='RUNNING',command=?,process_pid=? WHERE id=?",
                                 (json.dumps(command),pid,args.verification))
                    return f"OK verification={args.verification} RUNNING pid={pid}"
                begin_id="begin_"+hashlib.sha256(args.request_id.encode()).hexdigest()
                action(conn,root,"verify-exec-begin",begin_id,data,act)
            try:
                raw, exit_code, stopped = verification_guard.execute(
                    str(root.resolve()), args.verification, command, c["checkout"], args.timeout, begin)
            except (RuntimeError, OSError) as exc:
                fail("UNCERTAIN: " + str(exc) + "; gate retained")
            output.write_bytes(raw)
            rel,digest=relative_evidence(root,output.relative_to(root).as_posix())
            state="UNCERTAIN" if not stopped else ("STALE" if not candidate_identity(c) else ("PASS" if exit_code==0 else "FAIL" if exit_code is not None else "INDETERMINATE"))
            def act():
                current=get(conn,"verification",args.verification)
                if current["state"]!="RUNNING":fail("verification no longer running")
                candidate=get(conn,"candidate",current["candidate_id"])
                if stopped:
                    release(conn,candidate["checkout"],args.verification,current["claim_generation"])
                    release(conn,"heavy-local-verification",args.verification,current["heavy_generation"])
                conn.execute("UPDATE verification SET state=?,exit_code=?,evidence_path=?,evidence_sha=?,command=? WHERE id=?",
                             (state,exit_code,rel,digest,json.dumps(command),args.verification))
                if state=="STALE":conn.execute("UPDATE candidate SET state='STALE' WHERE id=?",(candidate["id"],))
                return f"OK verification={args.verification} result={state} candidate={current['candidate_id']}"
            result=action(conn,root,name,request,data,act)
            if stopped:
                release_machine_verification_lock(str(root.resolve()),args.verification)
            return result
        if name == "verify-record":
            path,digest=relative_evidence(root,args.evidence)
            data=dict(verification=args.verification,result=args.result,exit=args.exit_code,evidence=path,hash=digest)
            previous=replay(conn,name,request,data)
            if previous is not None:return previous
            owner = verification_guard.read_owner(str(root.resolve()), args.verification)
            if owner.get("scope") or owner.get("launching"):
                fail("running or uncertain verification requires process reconciliation")
            with closing(connect(root,True)) as read:
                previous=get(read,"verification",args.verification)
                same=candidate_identity(get(read,"candidate",previous["candidate_id"]))
            def act():
                v=get(conn,"verification",args.verification)
                if v["state"]!="STARTED":fail("verification not STARTED")
                c=get(conn,"candidate",v["candidate_id"])
                state="STALE" if not same else args.result
                if state=="PASS":fail("PASS requires verify-exec captured command result")
                run_id=one(conn,"SELECT id FROM run LIMIT 1")["id"]
                release(conn,c["checkout"],args.verification,v["claim_generation"])
                release(conn,"heavy-local-verification",args.verification,v["heavy_generation"])
                conn.execute("UPDATE verification SET state=?,exit_code=?,evidence_path=?,evidence_sha=? WHERE id=?",
                             (state,args.exit_code,path,digest,args.verification))
                if state=="STALE":conn.execute("UPDATE candidate SET state='STALE' WHERE id=?",(c["id"],))
                return f"OK verification={args.verification} result={state} candidate={v['candidate_id']}"
            result=action(conn,root,name,request,data,act)
            release_machine_verification_lock(str(root.resolve()),args.verification)
            return result
        if name == "verify-abort":
            data=dict(verification=args.verification,reason=args.reason)
            previous=replay(conn,name,request,data)
            if previous is not None:return previous
            owner = verification_guard.read_owner(str(root.resolve()), args.verification)
            if owner.get("scope") or owner.get("launching"):
                fail("running or uncertain verification requires process reconciliation")
            def act():
                v=get(conn,"verification",args.verification)
                if v["state"]!="STARTED":fail("running or uncertain verification requires process reconciliation")
                c=get(conn,"candidate",v["candidate_id"])
                run_id=one(conn,"SELECT id FROM run LIMIT 1")["id"]
                release(conn,c["checkout"],args.verification,v["claim_generation"])
                release(conn,"heavy-local-verification",args.verification,v["heavy_generation"])
                conn.execute("UPDATE verification SET state='INDETERMINATE' WHERE id=?",(args.verification,))
                return f"OK verification={args.verification} state=INDETERMINATE"
            result=action(conn,root,name,request,data,act,
                          allowed_states=("EXECUTING","CANCELLING","CANCELLATION_BLOCKED"),check_plan=False)
            release_machine_verification_lock(str(root.resolve()),args.verification)
            return result
        if name == "verify-reconcile":
            data=dict(verification=args.verification,reason=args.reason)
            previous=replay(conn,name,request,data)
            if previous is not None:return previous
            def require_stopped(v):
                if v["state"] not in ("RUNNING","UNCERTAIN"):
                    fail(f"only running or uncertain verification can be reconciled; current state is {v['state']}")
                pid=v["process_pid"]
                if not pid:
                    fail("UNCERTAIN: no recorded verification PID; liveness unknown, claims stay held")
                if is_pid_alive(pid):
                    fail(f"UNCERTAIN: verification process {pid} still running; cannot release claims")
                try:
                    owner = verification_guard.read_owner(str(root.resolve()), args.verification)
                    if owner.get("launching") or not verification_guard.scope_stopped(owner):
                        fail("UNCERTAIN: verification descendants still running; claims retained")
                except RuntimeError as exc:
                    fail("UNCERTAIN: " + str(exc))
            with closing(connect(root, True)) as read:
                require_stopped(get(read,"verification",args.verification))
            def act():
                v=get(conn,"verification",args.verification)
                require_stopped(v)
                c=get(conn,"candidate",v["candidate_id"])
                output=root / "verifications" / (args.verification+".log")
                if not output.exists():
                    output.write_bytes(f"--- verification reconciled: {args.reason} ---\n".encode("utf-8"))
                rel,digest=relative_evidence(root,output.relative_to(root).as_posix())
                run_id=one(conn,"SELECT id FROM run LIMIT 1")["id"]
                release(conn,c["checkout"],args.verification,v["claim_generation"])
                release(conn,"heavy-local-verification",args.verification,v["heavy_generation"])
                conn.execute("UPDATE verification SET state='INDETERMINATE',exit_code=NULL,evidence_path=?,evidence_sha=? WHERE id=?",
                             (rel,digest,args.verification))
                return f"OK verification={args.verification} state=INDETERMINATE reconciled=true claims-released"
            result=action(conn,root,name,request,data,act,
                          allowed_states=("EXECUTING","CANCELLING","CANCELLATION_BLOCKED"),check_plan=False)
            release_machine_verification_lock(str(root.resolve()),args.verification)
            return result
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
    p.add_argument("--run-dir",required=True,type=Path,help="Selected SQLite run directory; never write an active Markdown ledger")
    p.add_argument("--as-agent", help="Observed assigned role name for governed mutations")
    p.add_argument("--epoch", type=int, help="Current backend epoch from refreshed role packet")
    sub=p.add_subparsers(dest="command",required=True)
    def add(name, **keys):
        q=sub.add_parser(name)
        for arg,kwargs in keys.items():q.add_argument("--"+arg.replace("_","-"),**kwargs)
        return q
    init_cmd = add("init",run_id=dict(required=True),repo=dict(required=True),max_workers=dict(type=int,default=1))
    init_cmd.add_argument("--plan",default=None)
    init_cmd.add_argument("--grant-ref",default=None)
    init_cmd.add_argument("--parallel-approved",action="store_true")
    init_cmd.add_argument("--bootstrap",action="store_true")
    add("plan-approve",plan=dict(required=True),decision_id=dict(required=True),authority=dict(required=True,choices=["MANAGER","HUMAN"]),actor=dict(required=True),attestor=dict(required=True),rationale=dict(required=True),request_id=dict(required=True))
    add("run-authorize",grant_ref=dict(required=True),decision_id=dict(required=True),actor=dict(required=True),attestor=dict(required=True),request_id=dict(required=True))
    add("role-confirm", operation_id=dict(required=True), pane=dict(required=True), recipient=dict(required=True), recipient_pane=dict(default=""), evidence=dict(required=True), receipt=dict(default="")).add_argument("--compose-receipt",action="store_true",help="Let the ledger write the correlated receipt instead of supplying --receipt")
    add("upgrade")
    add("run-cancel",decision_id=dict(required=True),actor=dict(required=True),attestor=dict(required=True),authority_source=dict(required=True),reason=dict(required=True),request_id=dict(required=True))
    add("cancel-block",evidence=dict(required=True),request_id=dict(required=True))
    add("cancel-finalize",decision_id=dict(required=True),request_id=dict(required=True))
    add("status").add_argument("--human",action="store_true")
    sub.choices["status"].add_argument("--ids",action="store_true",help="Include internal task IDs in human view")
    sub.choices["status"].add_argument("--json",action="store_true",help="Machine-readable JSON plus a read-cost proxy")
    add("export").add_argument("--write", metavar="NAME.md", help="Also write the generated view to NAME.md inside the run directory")
    add("reference").add_argument("--role", choices=list(ROLE_ORDER), help="Print only this role's command list")
    add("next").add_argument("--json",action="store_true",help="Machine-readable JSON plus a read-cost proxy")
    packet_cmd = add("packet", role=dict(required=True,choices=["manager","delegator","lead","worker","verifier","critic"]), agent=dict(default=""))
    packet_cmd.add_argument("--json",action="store_true",help="Machine-readable JSON plus a read-cost proxy")
    add("recover").add_argument("--json",action="store_true",help="Machine-readable JSON plus a read-cost proxy")
    events_cmd = add("events")
    events_cmd.add_argument("--limit",type=int,default=8,choices=range(1,21),metavar="1..20")
    events_cmd.add_argument("--json",action="store_true",help="Machine-readable JSON plus a read-cost proxy")
    show_cmd = add("task-show",task=dict(required=True))
    show_cmd.add_argument("--json",action="store_true",help="Machine-readable JSON plus a read-cost proxy")
    add("task-add",task=dict(required=True),title=dict(required=True),outcome=dict(required=True),brief=dict(required=True),deps=dict(default=""),request_id=dict(required=True)).add_argument("--critique-required",action="store_true")
    add("decision-record",decision_id=dict(required=True),subject=dict(required=True),authority=dict(required=True,choices=["MANAGER","HUMAN"]),actor=dict(required=True),attestor=dict(required=True),disposition=dict(required=True,choices=["APPROVED","REJECTED"]),provenance=dict(required=True),rationale=dict(required=True),request_id=dict(required=True))
    add("task-retry",task=dict(required=True),decision_id=dict(required=True),request_id=dict(required=True))
    add("critique-record",review=dict(required=True),candidate=dict(required=True),critic=dict(required=True),report=dict(required=True),request_id=dict(required=True))
    add("finding-record",finding=dict(required=True),review=dict(required=True),actor=dict(required=True),severity=dict(required=True,choices=["BLOCKING","HIGH","MEDIUM","LOW"]),disposition=dict(required=True,choices=["OPEN","RESOLVED","DISMISSED"]),evidence=dict(required=True),decision_id=dict(default=""),request_id=dict(required=True))
    add("attempt-reserve",task=dict(required=True),attempt=dict(required=True),checkout=dict(required=True),agent=dict(required=True),provider=dict(required=True),expected_model=dict(default=""),expected_effort=dict(default=""),request_id=dict(required=True))
    add("attempt-attach",attempt=dict(required=True),pane=dict(required=True),agent=dict(required=True),request_id=dict(required=True))
    add("attempt-ready",attempt=dict(required=True),operator=dict(required=True),operator_pane=dict(required=True),sender_pane=dict(default=""),event_id=dict(required=True),evidence=dict(required=True),receipt=dict(default=""),request_id=dict(required=True)).add_argument("--compose-receipt",action="store_true",help="Let the ledger write the correlated receipt instead of supplying --receipt")
    add("attempt-start",attempt=dict(required=True),operator=dict(required=True),operator_pane=dict(required=True),sender_pane=dict(required=True),event_id=dict(required=True),evidence=dict(required=True),receipt=dict(default=""),request_id=dict(required=True)).add_argument("--compose-receipt",action="store_true",help="Let the ledger write the correlated receipt instead of supplying --receipt")
    add("attempt-report",attempt=dict(required=True),evidence=dict(required=True),source_sha=dict(required=True),event_id=dict(required=True),recipient=dict(required=True),receipt=dict(default=""),request_id=dict(required=True)).add_argument("--compose-receipt",action="store_true",help="Let the ledger write the correlated receipt instead of supplying --receipt")
    add("attempt-release",attempt=dict(required=True),generation=dict(required=True,type=int),observation=dict(required=True),request_id=dict(required=True))
    add("candidate-freeze",task=dict(required=True),candidate=dict(required=True),checkout=dict(required=True),request_id=dict(required=True))
    add("verify-start",verification=dict(required=True),candidate=dict(required=True),verifier=dict(required=True),gate=dict(default="local-check"),request_id=dict(required=True))
    add("verify-exec",verification=dict(required=True),timeout=dict(type=int,default=300),request_id=dict(required=True)).add_argument("argv",nargs=argparse.REMAINDER)
    add("verify-record",verification=dict(required=True),result=dict(choices=["PASS","FAIL","INDETERMINATE"],required=True),exit_code=dict(type=int,required=True),evidence=dict(required=True),request_id=dict(required=True))
    add("verify-abort",verification=dict(required=True),reason=dict(required=True),request_id=dict(required=True))
    add("verify-reconcile",verification=dict(required=True),reason=dict(required=True),request_id=dict(required=True))
    add("task-accept",task=dict(required=True),candidate=dict(required=True),decision_id=dict(required=True),request_id=dict(required=True))
    return p


def main(argv=None):
    args=parser().parse_args(argv)
    try:
        print(operation(args.run_dir.resolve(),args))
        return 0
    except (LedgerError,sqlite3.DatabaseError,FileNotFoundError,subprocess.TimeoutExpired,OSError,RuntimeError,ValueError,KeyError,TypeError) as exc:
        message="BLOCKED: LEDGER_BUSY" if isinstance(exc,sqlite3.OperationalError) and "locked" in str(exc).lower() else str(exc)
        print("ERROR " + message[:250],file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
