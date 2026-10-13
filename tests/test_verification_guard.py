"""Real process ownership and shared cross-run exclusion, Windows and Linux."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/herdr-engineering-loop/scripts'
sys.path.insert(0, str(SCRIPTS))
import verification_guard as guard


class VerificationGuardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = mock.patch.dict(os.environ, HERDR_LOCKS_DIR=str(self.root / 'locks'))
        env.start()
        self.addCleanup(env.stop)

    def test_two_run_directories_share_one_gate(self):
        def acquire(run):
            try:
                return guard.acquire(str(self.root / run), 'check', os.getpid())
            except RuntimeError:
                return False
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(acquire, ['sqlite-run', 'markdown-run']))
        self.assertEqual(sum(results), 1)

    def test_successful_owned_command_releases_gate(self):
        guard.acquire('run', 'check', os.getpid())
        raw, code, stopped = guard.execute('run', 'check', [sys.executable, '-c', 'print("verified")'], self.root, 5)
        self.assertIn(b'verified', raw)
        self.assertEqual(code, 0)
        self.assertTrue(stopped)
        guard.release('run', 'check')
        self.assertFalse(guard.lock_path().exists())

    def test_lost_parent_with_surviving_child_retains_gate(self):
        guard.acquire('run', 'check', os.getpid())
        script = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(2)"]); print("parent done",flush=True)'
        raw, code, stopped = guard.execute('run', 'check', [sys.executable, '-c', script], self.root, 5)
        self.assertIn(b'parent done', raw)
        self.assertIsNone(code)
        self.assertFalse(stopped)
        with self.assertRaisesRegex(RuntimeError, 'descendants alive'):
            guard.release('run', 'check')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not guard.scope_stopped(guard.read_owner('run', 'check')):
            time.sleep(0.1)
        self.assertTrue(guard.scope_stopped(guard.read_owner('run', 'check')))
        guard.release('run', 'check')

    def test_unknown_scope_and_partial_lock_never_auto_clear(self):
        guard.acquire('run', 'check', 99999999)
        with self.assertRaisesRegex(RuntimeError, 'no descendant ownership'):
            guard.scope_stopped(guard.read_owner('run', 'check'))
        with self.assertRaisesRegex(RuntimeError, 'LOCK_HELD'):
            guard.acquire('other-run', 'check', os.getpid())
        guard.lock_path().write_text('{', encoding='utf8')
        with self.assertRaisesRegex(RuntimeError, 'UNREADABLE'):
            guard.acquire('run', 'check', os.getpid())

    def test_uncertain_launch_keeps_gate(self):
        guard.acquire('run', 'check', os.getpid())
        guard.update('run', 'check', launching=True)
        with self.assertRaisesRegex(RuntimeError, 'launch uncertain'):
            guard.release('run', 'check')

    def test_missing_windows_job_never_proves_stopped(self):
        kernel = mock.Mock()
        kernel.OpenJobObjectW.return_value = None
        with mock.patch.object(guard.os, 'name', 'nt'), mock.patch.object(guard, '_kernel', return_value=kernel):
            with self.assertRaisesRegex(RuntimeError, 'job inaccessible'):
                guard.scope_stopped({'scope': {'kind': 'windows-job', 'id': 'missing'}})

    def test_same_owner_cannot_execute_twice_concurrently(self):
        guard.acquire('run', 'check', os.getpid())
        with guard.execution_lock():
            with ThreadPoolExecutor(1) as pool:
                future = pool.submit(guard.execute, 'run', 'check', [sys.executable, '-c', 'pass'], self.root, 5)
                with self.assertRaisesRegex(RuntimeError, 'already executing'):
                    future.result()

    def test_observation_denied_keeps_gate(self):
        guard.acquire('run', 'check', os.getpid())
        guard.update('run', 'check', scope={'kind': 'windows-job' if os.name == 'nt' else 'posix-group', 'id': 'denied' if os.name == 'nt' else 123})
        with mock.patch.object(guard, 'scope_stopped', side_effect=RuntimeError('access denied')):
            with self.assertRaisesRegex(RuntimeError, 'access denied'):
                guard.release('run', 'check')
        self.assertTrue(guard.lock_path().exists())

    def test_markdown_cli_shares_gate_and_reports_exact_sha(self):
        import run_backend
        repo = self.root / 'checkout'
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        (repo / '.gitignore').write_text('.herdr-loop/\n', encoding='utf8')
        subprocess.run(['git', '-C', str(repo), 'add', '.gitignore'], check=True)
        subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-q', '-m', 'fixture'], check=True)
        sha = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True, check=True).stdout.strip()
        run = repo / '.herdr-loop' / 'markdown-run'
        run.mkdir(parents=True)
        config = json.loads((ROOT / 'examples/roles.example.json').read_text(encoding='utf8'))
        config['run_id'] = run.name
        profile = run_backend.profile_from_config(config, repo)
        (run / 'SESSION.md').write_text(run_backend.render_session(profile), encoding='utf8')
        run_backend.init_selector(run, run.name, 'markdown', profile=profile)
        argv = [sys.executable, str(SCRIPTS / 'verification_guard.py'), '--run-dir', str(run), '--verification', 'check',
                '--checkout', str(repo), '--', sys.executable, '-c', 'print("CHECK_EXECUTED")']
        guard.acquire(str(self.root / 'sqlite-run'), 'sqlite-check', os.getpid())
        blocked = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(blocked.returncode, 2)
        self.assertIn('MACHINE_VERIFICATION_LOCK_HELD', blocked.stderr)
        self.assertNotIn('CHECK_EXECUTED', blocked.stdout)
        guard.release(str(self.root / 'sqlite-run'), 'sqlite-check')
        passed = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertIn('tested SHA=' + sha, passed.stdout)
