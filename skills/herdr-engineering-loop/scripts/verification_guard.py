#!/usr/bin/env python3
"""One durable machine gate for approved heavy checks in either backend."""
import argparse
import ctypes
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

_JOB_HANDLES = {}


@contextmanager
def execution_lock():
    with lock_path().with_suffix('.execution.lock').open('a+b') as stream:
        if os.fstat(stream.fileno()).st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError('MACHINE_VERIFICATION_LOCK_HELD: check already executing') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
def lock_path():
    return Path(os.environ.get("HERDR_LOCKS_DIR") or Path.home() / ".herdr-locks") / "heavy_verification.lock"


def read_owner(run, verification, required=True):
    path = lock_path()
    if not path.exists() and not required:
        return None
    if path.is_symlink():
        raise RuntimeError("MACHINE_VERIFICATION_LOCK_UNREADABLE: symlink")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("MACHINE_VERIFICATION_LOCK_UNREADABLE: reconcile before testing") from exc
    if not isinstance(data, dict):
        raise RuntimeError("MACHINE_VERIFICATION_LOCK_UNREADABLE: owner record is malformed")
    if data.get("run_id") != run or data.get("verification_id") != verification:
        raise RuntimeError("MACHINE_VERIFICATION_LOCK_HELD: another run/check owns the gate")
    return data


def acquire(run, verification, pid):
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as out:
            json.dump(dict(run_id=run, verification_id=verification, pid=pid, scope=None), out)
            out.flush()
            os.fsync(out.fileno())
        return True
    except FileExistsError:
        read_owner(run, verification)
        return False


def update(run, verification, **values):
    data = read_owner(run, verification)
    data.update(values)
    path = lock_path()
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        json.dump(data, out)
        out.flush()
        os.fsync(out.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _kernel():
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in (
        ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        ("OpenJobObjectW", [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        ("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
        ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
    ):
        fn = getattr(kernel, name)
        fn.argtypes, fn.restype = args, result
    return kernel


def _job_empty(kernel, handle):
    class Accounting(ctypes.Structure):
        _fields_ = [(name, ctypes.c_int64) for name in ("user", "kernel", "period_user", "period_kernel")] + [
            (name, ctypes.c_uint32) for name in ("faults", "total", "active", "terminated")]
    result = Accounting()
    if not kernel.QueryInformationJobObject(handle, 1, ctypes.byref(result), ctypes.sizeof(result), None):
        raise RuntimeError("VERIFICATION_SCOPE_UNOBSERVABLE: Windows job query failed")
    return result.active == 0


def scope_stopped(data):
    scope = data.get("scope")
    if not scope:
        raise RuntimeError("VERIFICATION_SCOPE_UNOBSERVABLE: no descendant ownership recorded")
    if scope.get("observed_empty"):
        return True
    if scope["kind"] == "posix-group" and os.name != "nt":
        try:
            os.killpg(scope["id"], 0)
            return False
        except ProcessLookupError:
            return True
        except OSError:
            return False
    if scope["kind"] == "windows-job" and os.name == "nt":
        kernel = _kernel()
        handle = kernel.OpenJobObjectW(4, False, scope["id"])  # JOB_OBJECT_QUERY
        if not handle:
            raise RuntimeError("VERIFICATION_SCOPE_UNOBSERVABLE: Windows job inaccessible")
        try:
            return _job_empty(kernel, handle)
        finally:
            kernel.CloseHandle(handle)
    raise RuntimeError("VERIFICATION_SCOPE_UNOBSERVABLE: wrong platform or unknown scope")


def release(run, verification):
    data = read_owner(run, verification)
    if data.get("launching") or (data.get("scope") and not scope_stopped(data)):
        raise RuntimeError("MACHINE_VERIFICATION_LOCK_HELD: descendants alive or launch uncertain")
    lock_path().unlink()
    scope = data.get("scope") or {}
    handle = _JOB_HANDLES.pop(scope.get("id"), None)
    if handle:
        _kernel().CloseHandle(handle)


def execute(run, verification, command, cwd, timeout, on_start=lambda pid: None):
    with execution_lock():
        return _execute(run, verification, command, cwd, timeout, on_start)


def _execute(run, verification, command, cwd, timeout, on_start):
    """Keep ownership after timeout/crash/unknown descendants; never turn those into PASS."""
    handle = None
    kernel = None
    proc = None
    try:
        owner = read_owner(run, verification)
        if owner.get('scope') or owner.get('launching'):
            raise RuntimeError('MACHINE_VERIFICATION_LOCK_HELD: prior launch requires reconciliation')
        update(run, verification, launching=True)
        with tempfile.TemporaryFile() as out:
            if os.name == "nt":
                kernel = _kernel()
                job_name = "Local\\HerdrVerification_" + uuid.uuid4().hex
                handle = kernel.CreateJobObjectW(None, job_name)
                if not handle:
                    raise RuntimeError("VERIFICATION_SCOPE_UNOBSERVABLE: job creation failed")
                # Suspend before assignment so no child can escape the job at startup.
                proc = subprocess.Popen(command, cwd=cwd, stdout=out, stderr=out, creationflags=4)
                if not kernel.AssignProcessToJobObject(handle, int(proc._handle)):
                    proc.kill()
                    proc.wait()
                    raise RuntimeError("VERIFICATION_SCOPE_UNOBSERVABLE: job assignment failed")
                scope = dict(kind="windows-job", id=job_name)
            else:
                proc = subprocess.Popen(command, cwd=cwd, stdout=out, stderr=out, start_new_session=True)
                scope = dict(kind="posix-group", id=proc.pid)
            update(run, verification, scope=scope, process_pid=proc.pid, launching=False)
            on_start(proc.pid)
            if os.name == "nt":
                resume = ctypes.WinDLL("ntdll").NtResumeProcess
                resume.argtypes, resume.restype = [ctypes.c_void_p], ctypes.c_long
                if resume(int(proc._handle)) != 0:
                    raise RuntimeError("VERIFICATION_SCOPE_UNOBSERVABLE: resume failed")
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                code = None
            stopped = scope_stopped(read_owner(run, verification))
            if stopped:
                scope["observed_empty"] = True
                update(run, verification, scope=scope)
            elif handle:
                # Keep the named job observable until reconciliation; a crashed owner fails closed.
                _JOB_HANDLES[job_name] = handle
                handle = None
            out.seek(0)
            raw = out.read(1_000_001)
            if len(raw) > 1_000_000:
                raw, code = raw[:1_000_000] + b"\nOutput truncated; indeterminate.\n", None
            return raw, code if stopped else None, stopped
    except BaseException:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        raise
    finally:
        if handle:
            kernel.CloseHandle(handle)


def main(argv=None):
    import herdr_ledger
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--verification", required=True)
    parser.add_argument("--checkout", type=Path, help="Frozen integration checkout for Markdown checks")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    run = str(args.run_dir.resolve(strict=True))
    try:
        if args.reconcile:
            data = read_owner(run, args.verification)
            if not scope_stopped(data) or data.get("launching"):
                raise RuntimeError("MACHINE_VERIFICATION_LOCK_HELD: process scope uncertain")
            release(run, args.verification)
            print("INDETERMINATE: stopped scope reconciled; no passing result recorded")
            return 0
        import run_backend
        backend, _ = run_backend.get_active_backend(Path(run))
        if backend != "markdown":
            raise RuntimeError("SQLite checks must use herdr_ledger.py verify-exec")
        command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if not command or not 1 <= args.timeout <= 600:
            raise RuntimeError("An approved command and timeout 1..600 are required")
        if not args.checkout:
            raise RuntimeError('A frozen --checkout is required for a Markdown check')
        checkout, head, tree, clean = herdr_ledger.git(args.checkout)
        if not clean:
            raise RuntimeError('Candidate checkout is dirty')
        with run_backend.run_lock(Path(run)):
            if run_backend.get_active_backend(Path(run))[0] != 'markdown':
                raise RuntimeError('Backend changed; refresh the role packet')
            if not acquire(run, args.verification, os.getpid()):
                raise RuntimeError("MACHINE_VERIFICATION_LOCK_HELD: reconcile previous check")
        raw, code, stopped = execute(run, args.verification, command, checkout, args.timeout)
        if herdr_ledger.git(checkout)[1:] != (head, tree, True):
            code = None
        sys.stdout.buffer.write(raw)
        if stopped:
            release(run, args.verification)
        print("\nCHECK_EXIT=" + str(code) + "; tested SHA=" + head + "; record evidence in SESSION.md")
        return 0 if code == 0 and stopped else 2
    except (RuntimeError, OSError, ValueError, herdr_ledger.LedgerError) as exc:
        print("BLOCKED:", exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
