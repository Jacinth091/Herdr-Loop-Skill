#!/usr/bin/env python3
"""Select a run's authoritative ledger and perform guarded backend transfers.

Transfer supports pristine planning and strictly validated structured checkpoints.
Dispatched history requires reachable Herdr observations, inactive ownership and
unchanged evidence. External worker descendants without persistent observation
remain reconciliation blockers. Cutovers are journaled under transfers/<id>.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))  # lets lazy `import herdr_ledger` resolve from any caller

import transfer_checkpoint

SELECTOR_FILE = "backend.active.json"
VALID_BACKENDS = ("sqlite", "markdown")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
TRANSFER_ID = re.compile(r"^[0-9a-f]{16}$")
APPROVAL_LINE = re.compile(
    r"^Backend transfer approval: run=(\S+) from=(sqlite|markdown) to=(sqlite|markdown) "
    r"epoch=([0-9]+) source-fingerprint=([0-9a-f]{64})$", re.M)
PHASES = ("PREPARING", "PREPARED", "SOURCE_RETIRED", "TARGET_ACTIVE", "COMPLETE", "ROLLED_BACK")
PENDING_PHASES = ("PREPARING", "PREPARED", "SOURCE_RETIRED", "TARGET_ACTIVE")
PLANNING_TABLES = ("task", "attempt", "candidate", "verification", "decision",
                   "critique", "finding", "signal", "claim")
SIDECARS = ("ledger.sqlite-wal", "ledger.sqlite-shm", "ledger.sqlite-journal")
SCHEMA_VERSION = 3
PARTIAL_TEMPLATE = {"base": "<unresolved>", "integration": "<not-created>"}


class TransferRefused(RuntimeError):
    """A state or approval that must not change; the message is the operator-facing reason."""


# --- locking and selector ---------------------------------------------------

@contextmanager
def run_lock(run_dir):
    path = run_dir / "backend.lock"
    token = f"{os.getpid()}:{os.urandom(8).hex()}"
    try:
        with path.open("x", encoding="ascii") as output:
            output.write(token)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as exc:
        raise RuntimeError("BACKEND_LOCK_HELD: inspect the run before retrying") from exc
    try:
        yield
    finally:
        if path.exists() and path.read_text(encoding="ascii") == token:
            path.unlink()


def read_selector(run_dir):
    path = run_dir / SELECTOR_FILE
    if path.is_symlink():
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: selector is a symlink")
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: selector is unreadable or invalid JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: selector must be an object")
    return data


def write_selector(run_dir, data):
    path = run_dir / SELECTOR_FILE
    staged = run_dir / (SELECTOR_FILE + ".tmp." + str(os.getpid()))
    with staged.open("x", encoding="utf-8") as output:
        json.dump(data, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(staged, path)


def pending_transfers(run_dir):
    """Return non-terminal receipts; any malformed transfer entry blocks the run."""
    base = run_dir / "transfers"
    if base.is_symlink():
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: transfer directory is a symlink")
    if not base.exists():
        return []
    pending = []
    for entry in sorted(base.iterdir()):
        if not TRANSFER_ID.fullmatch(entry.name) or entry.is_symlink() or not entry.is_dir():
            raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: unmanaged entry in transfers/: " + entry.name)
        receipt = read_receipt(entry)
        if receipt["phase"] in PENDING_PHASES:
            pending.append(receipt)
    return pending


def read_receipt(tdir):
    path = tdir / "receipt.json"
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: transfer receipt unreadable: " + tdir.name) from exc
    if (not isinstance(receipt, dict) or receipt.get("transfer_id") != tdir.name
            or receipt.get("phase") not in PHASES or receipt.get("schema") != 1):
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: transfer receipt is invalid: " + tdir.name)
    return receipt


def write_receipt(tdir, receipt):
    staged = tdir / ("receipt.json.tmp." + str(os.getpid()))
    with staged.open("x", encoding="utf-8") as output:
        json.dump(receipt, output, indent=2, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(staged, tdir / "receipt.json")


def get_active_backend(run_dir, ignore_transfers=False):
    if run_dir.is_symlink():
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: run directory is a symlink")
    selector = read_selector(run_dir)
    session = run_dir / "SESSION.md"
    db = run_dir / "ledger.sqlite"
    if session.is_symlink() or db.is_symlink():
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: canonical ledger is a symlink")
    if not ignore_transfers and pending_transfers(run_dir):
        raise RuntimeError("BACKEND_TRANSFER_IN_PROGRESS: a backend transfer is unfinished; run run_backend.py recover")
    if selector is not None:
        backend = selector.get("selected_backend")
        receipt_id = selector.get("transfer_receipt_id")
        profile = selector.get("planning_profile")
        if (backend not in VALID_BACKENDS or selector.get("run_id") != run_dir.name
                or type(selector.get("epoch")) is not int or selector["epoch"] < 1
                or selector.get("schema_version") != SCHEMA_VERSION
                or not (receipt_id is None or (isinstance(receipt_id, str) and TRANSFER_ID.fullmatch(receipt_id)))
                or not (profile is None or isinstance(profile, dict))):
            raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: invalid selector identity, epoch, or format")
        selected = db if backend == "sqlite" else session
        other = session if backend == "sqlite" else db
        if not selected.is_file() or other.exists():
            raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: selected ledger missing or competing ledger exists")
        if backend == "sqlite":
            try:
                with closing_readonly(db) as conn:
                    version = conn.execute("PRAGMA user_version").fetchone()[0]
                    row = conn.execute("SELECT id FROM run LIMIT 1").fetchone()
                if version != SCHEMA_VERSION or not row or row[0] != selector["run_id"]:
                    raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: SQLite identity or schema differs")
            except sqlite3.DatabaseError as exc:
                raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: SQLite ledger unreadable") from exc
        return backend, selector
    if session.exists() and db.exists():
        raise RuntimeError("BACKEND_CONFLICT: both canonical ledgers exist without a selector")
    if session.exists():
        return "markdown", None
    if db.exists():
        return "sqlite", None  # Existing offline pilot; binding must not promote it.
    return None, None


def init_selector(run_dir, run_id, backend, epoch=1, profile=None):
    if not RUN_ID.fullmatch(run_id) or backend not in VALID_BACKENDS or epoch != 1:
        raise ValueError("invalid new-run backend selection")
    path = run_dir / SELECTOR_FILE
    if path.exists() or path.is_symlink():
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: selector already exists")
    write_selector(run_dir, {"run_id": run_id, "selected_backend": backend, "epoch": epoch,
                             "schema_version": SCHEMA_VERSION, "transfer_receipt_id": None,
                             "planning_profile": profile})


# --- planning profile and the canonical Markdown template ---------------------

def profile_from_config(config, repo):
    """The facts a planning-checkpoint ledger must carry to re-render SESSION.md."""
    return {"run_id": config["run_id"], "repo": str(repo),
            "base_ref": str(config.get("base_ref", PARTIAL_TEMPLATE["base"])),
            "integration_branch": str(config.get("integration_branch", PARTIAL_TEMPLATE["integration"])),
            "max_active_workers": config["max_active_workers"],
            "parallel_workers_authorized": bool(config.get("parallel_workers_authorized", False)),
            "lead": "lead" in config["agents"]}


def profile_blockers(profile):
    problems = []
    if not isinstance(profile, dict):
        return ["PROFILE_MISSING: selector has no planning profile"]
    if not RUN_ID.fullmatch(str(profile.get("run_id", ""))):
        problems.append("PROFILE_INVALID: run_id")
    workers = profile.get("max_active_workers")
    if type(workers) is not int or workers not in (1, 2, 3):
        problems.append("PROFILE_INVALID: max_active_workers")
    elif type(profile.get("parallel_workers_authorized")) is not bool or (
            workers > 1 and not profile["parallel_workers_authorized"]):
        problems.append("PROFILE_INVALID: parallel worker authorization")
    for key in ("repo", "base_ref", "integration_branch"):
        if not isinstance(profile.get(key), str) or not profile.get(key):
            problems.append("PROFILE_INVALID: " + key)
    if type(profile.get("lead")) is not bool:
        problems.append("PROFILE_INVALID: lead")
    return problems


def render_session(profile):
    """Canonical planning SESSION.md text; byte-for-byte what role binding writes."""
    p = profile
    return ("# Herdr Run: " + p["run_id"] + "\n"
            "Status: unstarted (configuration only; reconcile live Herdr/Git)\n"
            "Repo: `" + str(p["repo"]) + "`\n"
            "Base: `" + str(p["base_ref"]) + "` @ UNRESOLVED\n"
            "Integration: `" + str(p["integration_branch"]) + "` @ UNRESOLVED (worktree not observed)\n"
            "Plan revision: NONE ACCEPTED\n"
            "Approved intent: goal/requirements/constraints/non-goals/acceptance NOT RECORDED\n"
            "Execution grant: NOT RECORDED (an explicit human request to execute is sufficient; planning-only is not)\n"
            "Resource budget: active implementation workers " + str(p["max_active_workers"]) +
            " (parallel authorized: " + str(p["parallel_workers_authorized"]) + "); concurrent heavy local verification jobs 1; remote CI separately observed\n"
            + ("Lead: inspect Herdr name/kind/pane (Manager + Delegator duties; human approves plan, scope changes and final acceptance)\n"
               if p["lead"] else
               "Manager: inspect Herdr name/kind/pane\n"
               "Delegator: inspect Herdr name/kind/pane\n") +
            "Critic: optional named Claude Sonnet 5.5, ROLE_READY then CRITIQUE_START/CRITIQUE_READY\n"
            "Verifier: optional dedicated Claude Haiku 5.5 tester or other explicitly assigned capable verifier; exact integrated SHA, ROLE_READY then VERIFY_START + exclusive lock, and VERIFY_READY report\n"
            "Model overrides: inspect requested worker model/effort, authorized non-Codex spawn owner, SPAWN_REQUEST/READY receipt and runtime confirmation\n"
            "Verification lock: NONE (record task/owner/pane/tested SHA when held)\n"
            "Candidate: UNFROZEN (record integration checkout + full SHA + expected diff)\n"
            "Candidate writer state: OPEN (freeze checkout before verification)\n"
            "Communication: ROLE_READY -> TASK_START (worker), ROLE_READY -> VERIFY_START + lock (verifier), REPORT_READY / VERIFY_READY / decisions / blockers; avoid polling loops\n\n"
            "## Task frontier\n"
            "| Task | User-facing title | Expected outcome | Depends on | State | Worker / Herdr pane | Workspace or worktree | Branch | Source SHA(s) | Integrated SHA(s) | Report / Manager verdict | Local verification (SHA) | Latest-head CI |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "\nHuman summaries: show User-facing title + Expected outcome + verified State; never expose Task or Attempt IDs unless explicitly requested. These descriptions must come from approved PLAN slices, not guesses.\n"
            "\n## Active attempts and write ownership\n"
            "| Task/attempt | Agent / pane | Worktree | State | Previous attempt / evidence |\n"
            "| --- | --- | --- | --- | --- |\n"
            "Write ownership: one active writer per checkout; no concurrent writers\n"
            "Retry policy: preserve prior attempt evidence; escalate repeated failures\n\n"
            "## Signal readiness and receipts\n"
            "- No ROLE_READY receipts observed; log recipient, event, task, pane, artifact, and observed vs pending state.\n"
            "- Do not dispatch Worker edits before observed READY.md + ROLE_READY and separate TASK_START.\n"
            "- Transport blocked: NONE OBSERVED (explicit human approval required for file-only mode)\n\n"
            "## Decisions and exceptions\n"
            "- Record human-approved budgets, narrowly scoped deviations and Manager plan revisions here; link evidence rather than duplicating logs.\n\n"
            "## Cancellation state\n"
            "- None; when authorized record actor, reason, CANCELLING, affected processes, final CANCELLED/CANCELLATION_BLOCKED; preserve artifacts.\n\n"
            "## Recoverable ownership\n"
            "- Workspaces/panes created by run: NONE OBSERVED\n"
            "- Human-owned workspace/panes NEVER auto-close: inspect and record\n"
            "- Pending report signals, decisions and blocked states: NONE OBSERVED\n\n"
            "## Agent prompt submission receipts\n"
            "Herdr prompt success records submission only. Reconcile the same target and task before replaying.\n\n"
            "<!-- HERDR_ACTIVATION_RECEIPTS_START -->\n"
            "```json\n[]\n```\n"
            "<!-- HERDR_ACTIVATION_RECEIPTS_END -->\n")


def profile_from_markdown(text, run_id):
    """Recover the planning profile from a SESSION.md that is still the untouched template."""
    def one(pattern):
        found = re.findall(pattern, text, re.M)
        if len(found) != 1:
            raise TransferRefused("MARKDOWN_NOT_PRISTINE: expected one '" + pattern[:40] + "' line")
        return found[0]
    budget = re.findall(r"^Resource budget: active implementation workers ([123]) \(parallel authorized: (True|False)\);", text, re.M)
    if len(budget) != 1:
        raise TransferRefused("MARKDOWN_NOT_PRISTINE: resource budget line is not the planning template")
    lead = "\nLead: inspect Herdr name/kind/pane" in text
    if lead == ("\nManager: inspect Herdr name/kind/pane" in text):
        raise TransferRefused("MARKDOWN_NOT_PRISTINE: exactly one of Lead or Manager/Delegator roles is required")
    profile = {"run_id": one(r"^# Herdr Run: (\S+)$"), "repo": one(r"^Repo: `(.*)`$"),
               "base_ref": one(r"^Base: `(.*)` @ UNRESOLVED$"),
               "integration_branch": one(r"^Integration: `(.*)` @ UNRESOLVED \(worktree not observed\)$"),
               "max_active_workers": int(budget[0][0]), "parallel_workers_authorized": budget[0][1] == "True",
               "lead": lead}
    if profile["run_id"] != run_id:
        raise TransferRefused("MARKDOWN_NOT_PRISTINE: header run ID differs from the run directory")
    if render_session(profile) != text:
        raise TransferRefused("MARKDOWN_NOT_PRISTINE: SESSION.md changed after planning (edits, progress, or activation receipts)")
    return profile


# --- source inspection (read-only) -------------------------------------------

def closing_readonly(path):
    return _Closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=3.0))


class _Closing:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        self.conn.close()
        return False


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sqlite_planning_state(db):
    """Return (blockers, run_row) for a SQLite ledger that is a pristine planning checkpoint."""
    blockers = []
    row = None
    try:
        with closing_readonly(db) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                blockers.append(f"UNSUPPORTED_SCHEMA: user_version {version} (explicit upgrade required)")
            runs = conn.execute("SELECT id, repo, plan_path, plan_sha, grant_ref, max_workers, state FROM run").fetchall()
            if len(runs) != 1:
                blockers.append("UNSUPPORTED_STATE: expected exactly one run row")
            else:
                run_id, repo, plan_path, plan_sha, grant, workers, state = runs[0]
                row = {"id": run_id, "repo": repo, "max_workers": workers}
                if state != "PLANNING":
                    blockers.append("UNSUPPORTED_STATE: run state " + str(state))
                if plan_path or plan_sha or grant:
                    blockers.append("UNSUPPORTED_STATE: approved plan or execution grant is recorded")
            for table in PLANNING_TABLES:
                count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                if count:
                    blockers.append(f"UNSUPPORTED_STATE: {table} has {count} row(s)")
            ops = conn.execute("SELECT op, count(*) FROM event GROUP BY op ORDER BY op").fetchall()
            if [tuple(x) for x in ops] != [("init", 1)]:
                blockers.append("UNSUPPORTED_STATE: ledger has events beyond the planning bootstrap")
    except sqlite3.DatabaseError as exc:
        blockers.append("SQLITE_UNREADABLE: " + str(exc))
    return blockers, row


def sqlite_fingerprint(db):
    with closing_readonly(db) as conn:
        return sha256_bytes("\n".join(conn.iterdump()).encode("utf-8"))


def source_fingerprint(run_dir, backend):
    selector_path = run_dir / SELECTOR_FILE
    selector = selector_path.read_bytes() if selector_path.exists() else b""
    if backend == "markdown":
        body = (run_dir / "SESSION.md").read_bytes()
    else:
        body = sqlite_fingerprint(run_dir / "ledger.sqlite").encode("ascii")
    return sha256_bytes(body + b"\x00" + selector)


def assess(run_dir, target):
    """Read-only transfer analysis. Writes nothing and creates no lock."""
    if target not in VALID_BACKENDS:
        raise TransferRefused("TARGET_INVALID: " + str(target))
    source, selector = get_active_backend(run_dir)
    if source is None:
        raise TransferRefused("RUN_UNINITIALIZED: no canonical ledger to transfer")
    epoch = selector["epoch"] if selector else 1
    blockers = []
    import verification_guard
    machine_lock = verification_guard.lock_path()
    if machine_lock.exists():
        try:
            owner = json.loads(machine_lock.read_text(encoding="utf8"))
            if owner.get("run_id") == str(run_dir.resolve()):
                blockers.append("TRANSFER_REQUIRES_RECONCILIATION: machine verification gate held by run")
        except (OSError, ValueError):
            blockers.append("TRANSFER_REQUIRES_RECONCILIATION: machine verification owner unreadable")
    profile = None
    row = None
    state = None
    if source == "markdown":
        try:
            text = (run_dir / "SESSION.md").read_text(encoding="utf-8")
            if transfer_checkpoint.MARKER in text:
                profile, state = transfer_checkpoint.parse(text)
            else:
                profile = profile_from_markdown(text, run_dir.name)
        except (RuntimeError, UnicodeDecodeError, ValueError, KeyError, TypeError) as exc:
            blockers.append(str(exc))
    else:
        if selector is None:
            blockers.append("SQLITE_PILOT_NOT_LIVE: legacy SQLite pilot requires explicit audit before transfer")
        sql_blockers, row = sqlite_planning_state(run_dir / "ledger.sqlite")
        if sql_blockers and selector is not None:
            try:
                state = transfer_checkpoint.snapshot(run_dir / "ledger.sqlite")
            except (RuntimeError, sqlite3.DatabaseError) as exc:
                blockers.append(str(exc))
        else:
            blockers += sql_blockers
        profile = selector.get("planning_profile") if selector else None
        if row and profile and (row["id"] != run_dir.name or row["max_workers"] != profile.get("max_active_workers")
                                or row["repo"] != str(Path(str(profile.get("repo", ""))).resolve())):
            blockers.append("PROFILE_MISMATCH: ledger row and planning profile disagree")
    if state is not None:
        try:
            transfer_checkpoint.validate(run_dir, state)
        except Exception as exc:
            blockers.append("TRANSFER_REQUIRES_RECONCILIATION: " + str(exc))
    if profile is not None:
        blockers += profile_blockers(profile)
        if state and state.get("run"):
            current = state["run"][0]
            if (profile.get("run_id") != current["id"] or profile.get("max_active_workers") != current["max_workers"]
                    or str(Path(profile.get("repo", "")).resolve()) != current["repo"]):
                blockers.append("TRANSFER_REQUIRES_RECONCILIATION: checkpoint profile and run metadata differ")
    else:
        blockers.append("TRANSFER_REQUIRES_RECONCILIATION: planning profile is missing")
    for name in SIDECARS:
        if (run_dir / name).exists():
            blockers.append("SIDECAR_PRESENT: " + name + " (close writers and checkpoint before transfer)")
    if target == "markdown" and source != "markdown" and (run_dir / "SESSION.md").exists():
        blockers.append("TARGET_OCCUPIED: SESSION.md exists")
    if target == "sqlite" and source != "sqlite" and (run_dir / "ledger.sqlite").exists():
        blockers.append("TARGET_OCCUPIED: ledger.sqlite exists")
    fingerprint = source_fingerprint(run_dir, source)
    return {"run_id": run_dir.name, "source": source, "target": target, "epoch": epoch,
            "fingerprint": fingerprint, "blockers": blockers, "profile": profile, "checkpoint": state}


def validate_ledger(path_dir, backend, profile, checkpoint_digest=None):
    """Strict check of a staged or active canonical ledger against the planning profile."""
    if checkpoint_digest:
        if backend == "markdown":
            parsed_profile, state = transfer_checkpoint.parse((path_dir / "SESSION.md").read_text(encoding="utf-8"))
            if parsed_profile != profile:
                raise TransferRefused("TARGET_INVALID: checkpoint profile differs")
        else:
            state = transfer_checkpoint.snapshot(path_dir / "ledger.sqlite")
        if transfer_checkpoint.digest(state) != checkpoint_digest:
            raise TransferRefused("TARGET_INVALID: checkpoint history differs")
        return
    if backend == "markdown":
        text = (path_dir / "SESSION.md").read_text(encoding="utf-8")
        if render_session(profile) != text:
            raise TransferRefused("TARGET_INVALID: staged SESSION.md does not match the planning profile")
        return
    blockers, row = sqlite_planning_state(path_dir / "ledger.sqlite")
    if blockers:
        raise TransferRefused("TARGET_INVALID: " + "; ".join(blockers))
    if row["id"] != profile["run_id"] or row["max_workers"] != profile["max_active_workers"]:
        raise TransferRefused("TARGET_INVALID: staged run row differs from the planning profile")
    if row["repo"] != str(Path(profile["repo"]).resolve()):
        raise TransferRefused("TARGET_INVALID: staged repo differs from the planning profile")


def approval_fingerprint_check(path, assessment, epoch_from):
    """Return the approval file's SHA-256 after it matches this exact transfer."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise TransferRefused("APPROVAL_INVALID: approval reference must be a regular file")
    raw = path.read_bytes()
    if len(raw) > 65536:
        raise TransferRefused("APPROVAL_INVALID: approval reference is too large")
    try:
        text = raw.decode("utf-8").replace(chr(13) + chr(10), chr(10))
    except UnicodeDecodeError as exc:
        raise TransferRefused("APPROVAL_INVALID: approval reference is not UTF-8") from exc
    found = APPROVAL_LINE.findall(text)
    if len(found) != 1:
        raise TransferRefused("APPROVAL_INVALID: expected exactly one 'Backend transfer approval:' line")
    run_id, source, target, epoch, fingerprint = found[0]
    if (run_id != assessment["run_id"] or source != assessment["source"]
            or target != assessment["target"] or int(epoch) != epoch_from):
        raise TransferRefused("APPROVAL_MISMATCH: approval names a different run, direction, or epoch")
    if fingerprint != assessment["fingerprint"]:
        raise TransferRefused("SOURCE_CHANGED: source state no longer matches the approved fingerprint; re-run dry-run")
    return sha256_bytes(raw)


def approval_line(assessment):
    return (f"Backend transfer approval: run={assessment['run_id']} from={assessment['source']} "
            f"to={assessment['target']} epoch={assessment['epoch']} "
            f"source-fingerprint={assessment['fingerprint']}")


# --- transfer -----------------------------------------------------------------

def _receipt_base(assessment, approval_sha, tid):
    return {"schema": 1, "transfer_id": tid, "run_id": assessment["run_id"],
            "from": assessment["source"], "to": assessment["target"],
            "from_epoch": assessment["epoch"], "to_epoch": assessment["epoch"] + 1,
            "source_fingerprint": assessment["fingerprint"], "approval_sha256": approval_sha,
            "profile": assessment["profile"], "checkpoint_digest": transfer_checkpoint.digest(assessment["checkpoint"]) if assessment["checkpoint"] else None,
            "archive": {}, "staged": None, "phase": "PREPARING"}


def _ledger_name(backend):
    return "ledger.sqlite" if backend == "sqlite" else "SESSION.md"


def _stage_target(staged_dir, assessment):
    profile = assessment["profile"]
    if assessment["checkpoint"]:
        if assessment["target"] == "markdown":
            (staged_dir / "SESSION.md").write_text(transfer_checkpoint.render(profile, assessment["checkpoint"]), encoding="utf-8", newline="")
        else:
            transfer_checkpoint.restore(staged_dir / "ledger.sqlite", assessment["checkpoint"])
        return
    if assessment["target"] == "markdown":
        (staged_dir / "SESSION.md").write_text(render_session(profile), encoding="utf-8")
    else:
        import herdr_ledger  # lazy: herdr_ledger imports this module
        herdr_ledger.operation(staged_dir, argparse.Namespace(
            command="init", run_id=profile["run_id"], repo=profile["repo"],
            plan=None, grant_ref=None, max_workers=profile["max_active_workers"],
            parallel_approved=profile["parallel_workers_authorized"], bootstrap=True))


def _archive_source(run_dir, archive, source):
    name = _ledger_name(source)
    if source == "sqlite":
        with closing_readonly(run_dir / name) as src:
            dst = sqlite3.connect(str(archive / name))
            try:
                src.backup(dst)
            finally:
                dst.close()
    else:
        shutil.copyfile(run_dir / name, archive / name)
    os.chmod(archive / name, 0o444)
    return {name: logical_digest(archive / name, source)}


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def logical_digest(path, backend):
    """Byte digest for Markdown; schema-and-data digest for SQLite (page layout may differ after backup)."""
    if backend == "sqlite":
        return sqlite_fingerprint(path)
    return sha256_file(path)


def switch(run_dir, target, approval=None, checkpoint=None):
    """Execute an approved transfer. checkpoint(phase) is a test seam for crash injection."""
    checkpoint = checkpoint or (lambda phase: None)
    assessment = assess(run_dir, target)
    if assessment["source"] == target:
        return "ALREADY_ACTIVE: run is already on " + target + "; nothing changed"
    if assessment["blockers"]:
        raise TransferRefused("TRANSFER_REFUSED: " + "; ".join(assessment["blockers"]))
    if approval is None:
        raise TransferRefused("APPROVAL_REQUIRED: a human approval file is required; see switch --dry-run")
    approval_sha = approval_fingerprint_check(approval, assessment, assessment["epoch"])
    tid = sha256_bytes(f"{assessment['run_id']}|{assessment['source']}|{target}|"
                       f"{assessment['epoch']}|{assessment['fingerprint']}|{approval_sha}".encode("ascii"))[:16]
    with run_lock(run_dir):
        again = assess(run_dir, target)
        if again["fingerprint"] != assessment["fingerprint"]:
            raise TransferRefused("SOURCE_CHANGED: source changed while acquiring the transfer lock")
        if again["blockers"]:
            raise TransferRefused("TRANSFER_REFUSED: " + "; ".join(again["blockers"]))
        tdir = run_dir / "transfers" / tid
        if tdir.exists():
            raise TransferRefused("TRANSFER_ID_EXISTS: " + tid + " already recorded; inspect before approving again")
        (run_dir / "transfers").mkdir(exist_ok=True)
        tdir.mkdir()
        (tdir / "archive").mkdir()
        (tdir / "staged").mkdir()
        receipt = _receipt_base(assessment, approval_sha, tid)
        write_receipt(tdir, receipt)
        try:
            receipt["archive"] = _archive_source(run_dir, tdir / "archive", assessment["source"])
            if (run_dir / SELECTOR_FILE).exists():
                shutil.copyfile(run_dir / SELECTOR_FILE, tdir / "archive" / SELECTOR_FILE)
            _stage_target(tdir / "staged", assessment)
            validate_ledger(tdir / "staged", target, assessment["profile"], receipt.get("checkpoint_digest"))
        except Exception as exc:
            # The source has not been retired, so abandon this attempt in-process.
            _rollback(tdir, receipt)
            raise TransferRefused("TARGET_INVALID: staging failed; source unchanged: " + str(exc)) from exc
        receipt["staged"] = _ledger_name(target)
        receipt["staged_sha256"] = sha256_file(tdir / "staged" / _ledger_name(target))
        receipt["phase"] = "PREPARED"
        write_receipt(tdir, receipt)
        checkpoint("PREPARED")

        if assessment["checkpoint"]:
            try:
                transfer_checkpoint.validate(run_dir, assessment["checkpoint"])
            except Exception as exc:
                _rollback(tdir, receipt)
                raise TransferRefused("TRANSFER_REQUIRES_RECONCILIATION: observation changed before retirement: " + str(exc)) from exc
        name = _ledger_name(assessment["source"])
        retired = tdir / "retired"
        retired.mkdir()
        if receipt["archive"][name] != logical_digest(run_dir / name, assessment["source"]):
            raise TransferRefused("SOURCE_CHANGED: source no longer matches the archived snapshot")
        os.replace(run_dir / name, retired / name)
        receipt["phase"] = "SOURCE_RETIRED"
        write_receipt(tdir, receipt)
        checkpoint("SOURCE_RETIRED")

        _activate_target(run_dir, tdir, receipt)
        receipt["phase"] = "TARGET_ACTIVE"
        write_receipt(tdir, receipt)
        checkpoint("TARGET_ACTIVE")

        return _commit(run_dir, tdir, receipt)


def _rollback(tdir, receipt):
    receipt["phase"] = "ROLLED_BACK"
    write_receipt(tdir, receipt)
    shutil.rmtree(tdir / "staged", ignore_errors=True)


def _activate_target(run_dir, tdir, receipt):
    target_name = receipt["staged"]
    if (run_dir / target_name).exists():
        validate_ledger(run_dir, receipt["to"], receipt["profile"], receipt.get("checkpoint_digest"))
        return
    os.replace(tdir / "staged" / target_name, run_dir / target_name)


def _commit(run_dir, tdir, receipt):
    write_selector(run_dir, {"run_id": receipt["run_id"], "selected_backend": receipt["to"],
                             "epoch": receipt["to_epoch"], "schema_version": SCHEMA_VERSION,
                             "transfer_receipt_id": receipt["transfer_id"],
                             "planning_profile": receipt["profile"]})
    shutil.rmtree(tdir / "staged", ignore_errors=True)
    receipt["phase"] = "COMPLETE"
    write_receipt(tdir, receipt)
    return (f"COMPLETE: run {receipt['run_id']} is now {receipt['to']} at epoch {receipt['to_epoch']}; "
            f"archive kept in transfers/{receipt['transfer_id']}")


def recover(run_dir, clear_stale_lock=False):
    """Finish or roll back an interrupted transfer from its journal only."""
    pending = pending_transfers(run_dir)
    if not pending:
        return "RECOVERY_NOT_NEEDED: no unfinished transfer"
    if len(pending) > 1:
        raise RuntimeError("BACKEND_INTEGRITY_BLOCKED: more than one unfinished transfer; resolve manually")
    if (run_dir / "backend.lock").exists():
        if not clear_stale_lock:
            raise RuntimeError("BACKEND_LOCK_HELD: confirm no transfer process is running, then retry with --clear-stale-lock")
        (run_dir / "backend.lock").unlink()
    receipt = pending[0]
    tdir = run_dir / "transfers" / receipt["transfer_id"]
    with run_lock(run_dir):
        name = _ledger_name(receipt["from"])
        if receipt["phase"] in ("PREPARING", "PREPARED"):
            if (run_dir / name).exists() and source_fingerprint(run_dir, receipt["from"]) != receipt["source_fingerprint"]:
                raise RuntimeError("RECOVERY_SOURCE_CHANGED: source differs from the journaled fingerprint; resolve manually")
            if not (run_dir / name).exists():
                raise RuntimeError("RECOVERY_BLOCKED: source ledger is missing before retirement")
            _rollback(tdir, receipt)
            return f"ROLLED_BACK: transfer {receipt['transfer_id']} abandoned; {receipt['from']} remains active"
        # SOURCE_RETIRED or TARGET_ACTIVE: the source was retired, so finish forward.
        archived = receipt["archive"][name]
        if logical_digest(tdir / "retired" / name, receipt["from"]) != archived:
            raise RuntimeError("RECOVERY_BLOCKED: retired source does not match the archive")
        if not (run_dir / receipt["staged"]).exists():
            if not (tdir / "staged" / receipt["staged"]).exists():
                raise RuntimeError("RECOVERY_BLOCKED: staged target is missing")
            os.replace(tdir / "staged" / receipt["staged"], run_dir / receipt["staged"])
        validate_ledger(run_dir, receipt["to"], receipt["profile"], receipt.get("checkpoint_digest"))
        if receipt.get("checkpoint_digest"):
            state = (transfer_checkpoint.snapshot(run_dir / "ledger.sqlite") if receipt["to"] == "sqlite" else
                     transfer_checkpoint.parse((run_dir / "SESSION.md").read_text(encoding="utf8"))[1])
            transfer_checkpoint.validate(run_dir, state, observe=False)
        return _commit(run_dir, tdir, receipt)


def inspect_run(run_dir):
    """Bounded, read-only status: authority, epoch, run state and transfer eligibility."""
    pending = pending_transfers(run_dir)
    backend, selector = get_active_backend(run_dir, ignore_transfers=True)
    info = {"run_id": selector["run_id"] if selector else run_dir.name,
            "active_backend": backend, "epoch": selector["epoch"] if selector else (1 if backend else None),
            "run_state": "UNINITIALIZED", "task_count": 0, "transfer_eligible": False,
            "transfer_blockers": []}
    if backend == "sqlite":
        try:
            with closing_readonly(run_dir / "ledger.sqlite") as conn:
                row = conn.execute("SELECT state FROM run LIMIT 1").fetchone()
                info["run_state"] = row[0] if row else "UNKNOWN"
                info["task_count"] = conn.execute("SELECT count(*) FROM task").fetchone()[0]
        except sqlite3.DatabaseError:
            info["run_state"] = "UNREADABLE"
    elif backend == "markdown":
        info["run_state"] = "RECORDED_IN_SESSION"
    if pending:
        info["transfer_blockers"] = ["BACKEND_TRANSFER_IN_PROGRESS: transfer " + pending[0]["transfer_id"]
                                     + " is at phase " + pending[0]["phase"] + "; run recover"]
    elif backend is None:
        info["transfer_blockers"] = ["run has no canonical ledger"]
    else:
        try:
            info["transfer_blockers"] = assess(run_dir, "markdown" if backend == "sqlite" else "sqlite")["blockers"]
        except (TransferRefused, RuntimeError) as exc:
            info["transfer_blockers"] = [str(exc)]
    info["transfer_eligible"] = not info["transfer_blockers"] and backend is not None
    return info


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect")
    switch_cmd = commands.add_parser("switch")
    switch_cmd.add_argument("--to", choices=VALID_BACKENDS, required=True)
    switch_cmd.add_argument("--dry-run", action="store_true")
    switch_cmd.add_argument("--approval-ref")
    recover_cmd = commands.add_parser("recover")
    recover_cmd.add_argument("--clear-stale-lock", action="store_true")
    args = parser.parse_args(argv)
    try:
        run_dir = args.run_dir.expanduser()
        if not run_dir.is_dir():
            raise RuntimeError("run directory is missing")
        if args.command == "inspect":
            info = inspect_run(run_dir)
            print(f"Run: {info['run_id']} | backend: {info['active_backend'] or 'none'} | epoch: {info['epoch']} | state: {info['run_state']} | tasks: {info['task_count']}")
            print("Transfer to other backend eligible: " + ("yes" if info["transfer_eligible"] else "no"))
            for blocker in info["transfer_blockers"]:
                print("- " + blocker)
            return 0
        if args.command == "recover":
            print(recover(run_dir, args.clear_stale_lock))
            return 0
        if args.dry_run:
            plan = assess(run_dir, args.to)
            print(f"DRY RUN (no files changed): run {plan['run_id']} {plan['source']} -> {plan['target']} at epoch {plan['epoch']}")
            print("Source fingerprint: " + plan["fingerprint"])
            if plan["source"] == plan["target"]:
                print("ALREADY_ACTIVE: nothing to transfer")
                return 0
            for blocker in plan["blockers"]:
                print("- " + blocker)
            if plan["blockers"]:
                print("Transfer refused: reconcile the source first; the source is unchanged.")
                return 2
            print("Eligible. Create a human approval file containing exactly this line:")
            print(approval_line(plan))
            return 0
        if args.approval_ref is None and args.command == "switch":
            raise TransferRefused("APPROVAL_REQUIRED: pass --approval-ref or run --dry-run first")
        result = switch(run_dir, args.to, Path(args.approval_ref).expanduser() if args.approval_ref else None)
        print(result)
        return 0
    except (OSError, RuntimeError, TransferRefused, ValueError, sqlite3.DatabaseError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
