#!/usr/bin/env python3
"""Read-only Herdr origin probe; optional harmless write probe in workspace/.herdr-loop.

No elevation, ACL updates, sandbox changes, remote requests, or persistent files.
Python 3.8+ standard library. The probe never runs product tests and cannot grant
or confirm native command approval.
"""
import argparse
import errno
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


KNOWN_HARNESSES = {"codex": "codex", "claude": "claude", "agy": "agy", "opencode": "opencode"}
SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|secret|api[_-]?key|authorization)(\s*[:=]\s*)([^\s,;]+)"
)
VERSION = re.compile(r"\b\d+\.\d+(?:\.\d+)?(?:[-+][0-9A-Za-z.-]+)?\b")


def sanitize_error(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    text = " ".join(str(value or "").split())
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer <redacted>", text)
    text = SECRET_ASSIGNMENT.sub(r"\1\2<redacted>", text)
    text = re.sub(
        r"\b(?:github_pat_[A-Za-z0-9_]{12,}|gh[pousr]_[A-Za-z0-9]{12,}|sk-[A-Za-z0-9_-]{12,})\b",
        "<redacted>", text,
    )
    return text[:200]


def classify_failure(message, boundary, exception=None):
    if isinstance(exception, FileNotFoundError):
        return "HERDR_CLI_UNAVAILABLE" if boundary.startswith("herdr") else "EXECUTABLE_UNAVAILABLE"
    if isinstance(exception, subprocess.TimeoutExpired):
        return "COMMAND_TIMEOUT"
    if (isinstance(exception, PermissionError)
            or getattr(exception, "winerror", None) == 5
            or getattr(exception, "errno", None) in (errno.EACCES, errno.EPERM)):
        return "OS_ACCESS_DENIED"

    lower = str(message or "").lower()
    denied = any(term in lower for term in ("denied", "permission", "eacces", "winerror 5", "not permitted"))
    if any(term in lower for term in ("approval denied", "user denied", "request rejected", "declined by user", "permission request denied")):
        return "DENIED"
    if denied and ("sandbox" in lower or "policy" in lower or "approval" in lower):
        return "AGENT_SANDBOX_DENIED"
    if boundary.startswith("herdr") and re.search(r"\bcode\s*:\s*5\b", lower) and "permissiondenied" in lower:
        return "HERDR_TRANSPORT_DENIED"
    if boundary.startswith("herdr") and any(term in lower for term in (
            "connection refused", "failed to connect", "no server", "socket", "named pipe", "ipc")):
        return "HERDR_TRANSPORT_DENIED"
    if denied:
        return "ACCESS_DENIED_UNCLASSIFIED"
    if boundary.startswith("herdr"):
        return "HERDR_TRANSPORT_DENIED"
    return "COMMAND_FAILED"


def run(argv, timeout=8, boundary="command", capture_version=False):
    try:
        outcome = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=timeout)
        raw = outcome.stderr or outcome.stdout or ""
        result = {
            "ok": outcome.returncode == 0,
            "exit": outcome.returncode,
            "error": sanitize_error(raw),
            "boundary": boundary,
        }
        if outcome.returncode != 0:
            result["failure_code"] = classify_failure(raw, boundary)
            if not result["error"]:
                result["error"] = "command exited with status " + str(outcome.returncode)
        elif capture_version:
            match = VERSION.search(outcome.stdout or "")
            result["version"] = match.group(0) if match else None
        return result
    except (OSError, subprocess.TimeoutExpired) as exc:
        raw = getattr(exc, "stderr", None) or getattr(exc, "stdout", None)
        message = getattr(exc, "strerror", None) or raw or type(exc).__name__
        return {
            "ok": False,
            "exit": None,
            "error": sanitize_error(message),
            "failure_code": classify_failure(message, boundary, exc),
            "boundary": boundary,
        }


def _add_blocker(result, code, boundary, target_category, error=""):
    result["blockers"].append(code)
    detail = {"code": code, "boundary": boundary, "target_category": target_category}
    if error:
        detail["error"] = sanitize_error(error)
    if detail not in result["blocker_details"]:
        result["blocker_details"].append(detail)


def _probe_harness(result, harness_kind):
    result["harness_kind"] = harness_kind
    result["harness_version"] = None
    result["approval_capability"] = {
        "status": "unverified",
        "evidence": "The active harness tool and its current approval channel are not observable by this probe.",
    }
    executable_name = KNOWN_HARNESSES.get(harness_kind)
    if not executable_name:
        return
    executable = shutil.which(executable_name)
    if executable:
        version = run([executable, "--version"], boundary="harness_version", capture_version=True)
        result["checks"]["harness_version_probe"] = {
            "ok": version["ok"], "version": version.get("version"),
        }
        result["harness_version"] = version.get("version")


def inspect(workspace, agent=None, pane=None, write_probe=False, skip_herdr=False, harness_kind=None):
    if harness_kind is not None and not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", harness_kind):
        raise ValueError("--harness-kind must be a short CLI kind name")
    if workspace.is_symlink():
        raise ValueError("--workspace must not be a symlink")
    root = workspace.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("--workspace must be a directory")

    env_confirmed = os.environ.get("HERDR_ENV") == "1"
    result = {
        "workspace": str(root),
        "platform": sys.platform,
        "herdr_version": None,
        "checks": {},
        "blockers": [],
        "blocker_details": [],
        "herdr_origin": {"env_confirmed": env_confirmed, "agent_confirmed": None, "pane_confirmed": None},
        "server_reachable": None,
        "named_agent_confirmed": None,
        "workspace_write_test": {
            "attempted": bool(write_probe), "ok": None, "target_category": "assigned workspace .herdr-loop",
        },
    }
    _probe_harness(result, harness_kind)

    if skip_herdr:
        _add_blocker(result, "HERDR_ORIGIN_UNCONFIRMED", "herdr_origin", "active Herdr agent")
    elif not env_confirmed:
        _add_blocker(result, "HERDR_ORIGIN_UNCONFIRMED", "herdr_origin", "HERDR_ENV")
    else:
        herdr = shutil.which("herdr")
        if not herdr:
            _add_blocker(result, "HERDR_CLI_UNAVAILABLE", "herdr_executable", "Herdr CLI")
        else:
            version = run([herdr, "--version"], boundary="herdr_version", capture_version=True)
            result["checks"]["herdr_version_probe"] = {"ok": version["ok"], "version": version.get("version")}
            result["herdr_version"] = version.get("version")
            server = run([herdr, "status", "server"], boundary="herdr_server")
            result["checks"]["herdr_server"] = server
            result["server_reachable"] = server["ok"]
            if not server["ok"]:
                _add_blocker(result, server.get("failure_code", "HERDR_TRANSPORT_DENIED"),
                             server["boundary"], "Herdr server or IPC", server.get("error"))
            elif agent:
                named = run([herdr, "agent", "get", agent], boundary="herdr_agent")
                result["checks"]["named_agent"] = named
                result["named_agent_confirmed"] = named["ok"]
                result["herdr_origin"]["agent_confirmed"] = named["ok"]
                if not named["ok"]:
                    _add_blocker(result, "HERDR_ORIGIN_UNCONFIRMED", named["boundary"],
                                 "assigned Herdr agent", named.get("error"))

    observed = os.environ.get("HERDR_PANE_ID", "")
    if pane:
        matches = observed == pane if observed else None
        result["checks"]["pane_matches_env"] = matches
        result["herdr_origin"]["pane_confirmed"] = matches
        if matches is False or matches is None:
            _add_blocker(result, "HERDR_ORIGIN_UNCONFIRMED", "herdr_pane", "assigned Herdr pane")
    if not agent and not pane:
        _add_blocker(result, "HERDR_ORIGIN_UNCONFIRMED", "herdr_identity", "assigned Herdr agent or pane")
    result["herdr_origin"]["confirmed"] = bool(
        env_confirmed and result["server_reachable"]
        and (result["named_agent_confirmed"] is True or result["herdr_origin"]["pane_confirmed"] is True)
    )

    git = run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], boundary="workspace_git")
    result["checks"]["git_workspace"] = {"ok": git["ok"], "exit": git["exit"]}
    if not git["ok"]:
        result["checks"]["git_workspace"]["failure_code"] = git.get("failure_code")

    if write_probe:
        scratch = root / ".herdr-loop"
        if scratch.is_symlink() or not scratch.is_dir():
            check = {"ok": False, "error": "missing, symlinked, or non-directory workspace-local .herdr-loop"}
            result["checks"]["scratch_write"] = check
            result["workspace_write_test"]["ok"] = False
            _add_blocker(result, "WORKTREE_WRITE_DENIED", "workspace_write", "assigned workspace .herdr-loop", check["error"])
        else:
            try:
                with tempfile.NamedTemporaryFile(dir=str(scratch), prefix=".herdr-probe-", delete=True) as f:
                    f.write(b"ready\n")
                    f.flush()
                result["checks"]["scratch_write"] = {"ok": True}
                result["workspace_write_test"]["ok"] = True
            except OSError as exc:
                error = getattr(exc, "strerror", None) or type(exc).__name__
                result["checks"]["scratch_write"] = {
                    "ok": False, "error": sanitize_error(error),
                    "failure_code": classify_failure(error, "workspace_write", exc),
                }
                result["workspace_write_test"]["ok"] = False
                _add_blocker(result, "WORKTREE_WRITE_DENIED", "workspace_write",
                             "assigned workspace .herdr-loop", error)

    result["blockers"] = sorted(set(result["blockers"]))
    result["ready_for_role_handshake"] = len(result["blockers"]) == 0 and not skip_herdr
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", type=Path, required=True, help="Assigned worker workspace, never a parent folder")
    p.add_argument("--agent", help="Observed named Herdr agent, not role label")
    p.add_argument("--pane", help="Expected pane ID, compared to HERDR_PANE_ID when available")
    p.add_argument("--harness-kind", help="Explicit active CLI kind (codex, claude, agy, opencode, or another kind)")
    p.add_argument("--probe-write", action="store_true", help="Create/remove one temporary file ONLY in workspace/.herdr-loop")
    p.add_argument("--skip-herdr", action="store_true", help="Local-only filesystem troubleshooting; NOT proof of agent origin")
    args = p.parse_args(argv)
    try:
        result = inspect(args.workspace, args.agent, args.pane, args.probe_write, args.skip_herdr, args.harness_kind)
        print(json.dumps(result, indent=2))
        return 0 if result["ready_for_role_handshake"] else 2
    except (OSError, ValueError) as exc:
        message = getattr(exc, "strerror", None) or str(exc)
        code = "WORKSPACE_INVALID" if isinstance(exc, ValueError) else classify_failure(message, "workspace_input", exc)
        print(json.dumps({
            "ready_for_role_handshake": False,
            "platform": sys.platform,
            "approval_capability": {"status": "unverified"},
            "blockers": [code],
            "blocker_details": [{"code": code, "boundary": "workspace_input", "target_category": "assigned workspace",
                                 "error": sanitize_error(message)}],
        }))
        return 2


if __name__ == "__main__":
    sys.exit(main())
