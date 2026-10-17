"""User-facing task description projection, independent of live Herdr/Git."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIND = ROOT / 'tools' / 'role_bind.py'
PROGRESS = ROOT / 'tools' / 'progress_view.py'
BACKEND = ROOT / 'skills' / 'herdr-engineering-loop' / 'scripts' / 'run_backend.py'
CONFIG = ROOT / 'examples' / 'roles.example.json'


class HumanProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        self.config = json.loads(CONFIG.read_text(encoding='utf-8'))
        self.config['state_backend'] = 'markdown'
        self.config_path = self.repo / 'roles.json'
        self.run = self.repo / '.herdr-loop' / self.config['run_id']

    def bind(self):
        self.config_path.write_text(json.dumps(self.config), encoding='utf-8')
        return subprocess.run([sys.executable, str(BIND), '--repo', str(self.repo), '--config', str(self.config_path)], capture_output=True, text=True)

    def progress(self, *args):
        return subprocess.run([sys.executable, str(PROGRESS), '--session', str(self.run / 'SESSION.md'), *args], capture_output=True, text=True)

    def test_labels_propagate_without_changing_internal_ids(self):
        self.assertEqual(self.bind().returncode, 0)
        manifest = json.loads((self.run / 'ACTIVATION.json').read_text(encoding='utf-8'))
        worker = next(a for a in manifest['agents'] if a['role'] == 'worker')
        self.assertEqual(worker['task_id'], 'T01')
        self.assertEqual(worker['attempt_id'], 'T01-A1')
        self.assertEqual(worker['task_title'], 'Improve account sign-in error handling')
        self.assertIn(worker['task_title'], worker['prompt'])
        self.assertIn(worker['task_outcome'], worker['prompt'])
        self.assertIn('User-facing slice title:', (self.run / 'role-packets/worker_t01.md').read_text(encoding='utf-8'))
        self.assertIn(worker['task_title'], (self.run / 'ROSTER.md').read_text(encoding='utf-8'))
        session = (self.run / 'SESSION.md').read_text(encoding='utf-8')
        self.assertIn('| Task | User-facing title | Expected outcome |', session)
        self.assertIn('No work slices have been recorded yet.', self.progress().stdout)

    def test_legacy_config_binds_but_warns_before_dispatch(self):
        worker = self.config['agents']['workers'][0]
        worker.pop('task_title')
        worker.pop('task_outcome')
        self.assertEqual(self.bind().returncode, 0)
        prompt = (self.run / 'role-packets/worker_t01.md').read_text(encoding='utf-8')
        self.assertIn('REQUIRED: obtain accepted PLAN title before TASK_START', prompt)
        self.assertIn('REQUIRED: obtain accepted PLAN outcome before TASK_START', prompt)

    def test_invalid_descriptions_fail_before_output(self):
        for patch in [
            {'task_title': 'A|B'},
            {'task_title': 'A\nB'},
            {'task_title': 'x' * 91},
            {'task_title': ''},
            {'task_title': '   '},
            {'task_outcome': 'Problem\rsolved'},
            {'task_outcome': 'y' * 241},
        ]:
            with self.subTest(patch=patch):
                worker = self.config['agents']['workers'][0]
                original = {k: worker[k] for k in patch}
                worker.update(patch)
                res = self.bind()
                self.assertNotEqual(res.returncode, 0)
                self.assertFalse(self.run.exists())
                worker.update(original)

    def test_user_view_hides_ids_and_translates_states(self):
        self.assertEqual(self.bind().returncode, 0)
        path = self.run / 'SESSION.md'
        base = path.read_text(encoding='utf-8')
        header = '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n'
        row1 = '| T01 | Improve login recovery | Expired sessions show recovery instructions | none | working | worker_t01 / w2:p1 | /tmp/worktrees/T01 | feature/T01 | abc | def | reports/T01.md | pending | pending |\n'
        row2 = '| T02 | Prevent double charges | A repeat payment request is idempotent | T01 | verifying | worker_t02 | /tmp/worktrees/T02 | feature/T02 | abc | def | reports/T02.md | running | pending |\n'
        self.assertIn(header, base)
        base = base.replace(header, header + row1 + row2, 1)
        path.write_text(base, encoding='utf-8')
        out = self.progress()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('Improve login recovery', out.stdout)
        self.assertIn('Expired sessions show recovery instructions', out.stdout)
        self.assertIn('In progress', out.stdout)
        self.assertIn('Prevent double charges', out.stdout)
        self.assertIn('Testing', out.stdout)
        for private in ('T01', 'T02', 'T01-A1', 'w2:p1', 'abc', '/tmp/worktrees', 'reports/T02.md'):
            self.assertNotIn(private, out.stdout)
        debug = self.progress('--include-internal-ids')
        self.assertEqual(debug.returncode, 0, debug.stderr)
        self.assertIn('(T01)', debug.stdout)
        self.assertIn('(T02)', debug.stdout)

    def test_no_invented_titles_from_unlabeled_old_frontier(self):
        self.assertEqual(self.bind().returncode, 0)
        path = self.run / 'SESSION.md'
        content = path.read_text(encoding='utf-8')
        sep = '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n'
        content = content.replace(sep, sep + '| T01 | - | - | none | accepted | - | - | - | abc | def | - | pass | pass |\n')
        path.write_text(content, encoding='utf-8')
        rendered = self.progress()
        self.assertEqual(rendered.returncode, 2)
        self.assertEqual(rendered.stdout, '')
        self.assertIn('missing descriptive title/outcome', rendered.stderr)

    def test_accepted_and_pending_are_not_confused(self):
        self.assertEqual(self.bind().returncode, 0)
        path = self.run / 'SESSION.md'
        content = path.read_text(encoding='utf-8')
        sep = '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n'
        content = content.replace(sep, sep + '| T01 | Restore account access | Clear message after expiry | none | content-accepted-verification-pending | - | - | - | abc | def | - | pending | pending |\n' +
                                   '| T02 | Fix recovery links | Recovery links route correctly | T01 | accepted | - | - | - | abc | def | - | pass | pass |\n')
        path.write_text(content, encoding='utf-8')
        rendered = self.progress()
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertIn('Restore account access**: Implementation approved; checks pending', rendered.stdout)
        self.assertIn('Fix recovery links**: Complete', rendered.stdout)
        self.assertNotIn('T01', rendered.stdout)

    def test_full_pack_docs_use_user_labels(self):
        manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['version'], '2.9.0')
        self.assertTrue(manifest['policy']['task_ids_internal_only_in_user_updates'])
        self.assertIn('tools/progress_view.py', manifest['optional_tools'])
        for skill in ('herdr-manager', 'herdr-delegator', 'herdr-engineering-loop'):
            self.assertTrue((ROOT / 'skills' / skill / 'references/user-status.md').exists())
            self.assertIn('references/user-status.md', (ROOT / 'skills' / skill / 'SKILL.md').read_text(encoding='utf-8'))

    def test_progress_view_run_dir_markdown(self):
        self.assertEqual(self.bind().returncode, 0)
        res = subprocess.run([sys.executable, str(PROGRESS), '--run-dir', str(self.run)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('No work slices have been recorded yet.', res.stdout)

    def test_progress_view_run_dir_sqlite(self):
        self.config['state_backend'] = 'sqlite'
        self.config['run_id'] = 'sqlite-progress'
        sqlite_run = self.repo / '.herdr-loop' / 'sqlite-progress'
        self.assertEqual(self.bind().returncode, 0)
        res = subprocess.run([sys.executable, str(PROGRESS), '--run-dir', str(sqlite_run)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('Work progress', res.stdout)

    def test_legacy_markdown_stays_selected_when_config_omits_backend(self):
        self.assertEqual(self.bind().returncode, 0)
        session = self.run / 'SESSION.md'
        session.write_text(session.read_text(encoding='utf-8') + '\nHuman note.\n', encoding='utf-8')
        before = session.read_bytes()
        (self.run / 'backend.active.json').unlink()
        self.config.pop('state_backend')
        self.assertEqual(self.bind().returncode, 0)
        self.assertEqual(session.read_bytes(), before)
        self.assertFalse((self.run / 'ledger.sqlite').exists())

    def test_damaged_selector_blocks_rebind_and_progress(self):
        self.assertEqual(self.bind().returncode, 0)
        (self.run / 'backend.active.json').write_text('{broken', encoding='utf-8')
        before = (self.run / 'SESSION.md').read_bytes()
        self.assertIn('BACKEND_INTEGRITY_BLOCKED', self.bind().stderr)
        result = subprocess.run([sys.executable, str(PROGRESS), '--run-dir', str(self.run)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('BACKEND_INTEGRITY_BLOCKED', result.stderr)
        self.assertEqual((self.run / 'SESSION.md').read_bytes(), before)

    def test_switch_without_approval_writes_nothing(self):
        self.assertEqual(self.bind().returncode, 0)
        before = (self.run / 'SESSION.md').read_bytes()
        result = subprocess.run([sys.executable, str(BACKEND), '--run-dir', str(self.run),
                                 'switch', '--to', 'sqlite', '--approval-ref', 'approval.md'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('APPROVAL_INVALID', result.stdout + result.stderr)
        self.assertEqual((self.run / 'SESSION.md').read_bytes(), before)
        self.assertFalse((self.run / 'ledger.sqlite').exists())
        self.assertFalse((self.run / 'transfers').exists())


if __name__ == '__main__':
    unittest.main()
