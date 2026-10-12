"""Offline proof for the opt-in SQLite ledger. Never launches Herdr or AI tools."""
import hashlib
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'skills' / 'herdr-engineering-loop' / 'scripts' / 'herdr_ledger.py'
spec = importlib.util.spec_from_file_location('herdr_ledger_pilot', CLI)
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


def shell(*args, cwd=None):
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, check=True)


class LedgerPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'checkout'
        self.repo.mkdir()
        shell('git', 'init', '-q', str(self.repo))
        shell('git', '-C', str(self.repo), 'config', 'user.name', 'Test')
        shell('git', '-C', str(self.repo), 'config', 'user.email', 'test@example.invalid')
        self.commit('initial')
        self.worker = self.root / 'worker'
        shell('git', '-C', str(self.repo), 'worktree', 'add', '-q', '-b', 'worker-branch', str(self.worker), 'HEAD')
        self.commit('implemented worker patch', checkout=self.worker)
        self.run_dir = self.root / 'run'
        self.run_dir.mkdir()
        (self.run_dir / 'PLAN.md').write_text('Approved scope and acceptance\n', encoding='utf8')
        (self.run_dir / 'TASK.md').write_text('Implement scoped task\n', encoding='utf8')
        (self.run_dir / 'REPORT.md').write_text('Behavior changed; independent tests pending.\n', encoding='utf8')
        (self.run_dir / 'VERIFY.log').write_text('1 test passed\n', encoding='utf8')
        (self.run_dir / 'MANAGER.txt').write_text('Manager reviewed verified candidate\n', encoding='utf8')

    def commit(self, content, checkout=None):
        checkout = checkout or self.repo
        (checkout / 'code.txt').write_text(content + '\n', encoding='utf8')
        shell('git', '-C', str(checkout), 'add', 'code.txt')
        shell('git', '-C', str(checkout), 'commit', '-q', '-m', 'fixture')
        return shell('git', '-C', str(checkout), 'rev-parse', 'HEAD').stdout.strip()

    def cmd(self, *args, ok=True):
        parsed = ledger.parser().parse_args(['--run-dir', str(self.run_dir), *args])
        if ok:
            return ledger.operation(self.run_dir.resolve(), parsed)
        with self.assertRaises((ledger.LedgerError, __import__("sqlite3").IntegrityError)) as context:
            ledger.operation(self.run_dir.resolve(), parsed)
        return 'ERROR ' + str(context.exception)

    def init(self, parallel=False):
        args = ['init', '--run-id', 'sample', '--repo', str(self.repo), '--plan', 'PLAN.md',
                '--grant-ref', 'human-request', '--max-workers', '2' if parallel else '1']
        if parallel:args.append('--parallel-approved')
        return self.cmd(*args)

    def task(self, id='T01', deps='', title='Prevent duplicate writes'):
        return self.cmd('task-add', '--task', id, '--title', title, '--outcome', 'Only one record remains',
                        '--brief', 'TASK.md', '--deps', deps, '--request-id', 'add-'+id)

    def reserve(self, task='T01', attempt='T01-A1', checkout=None, request='reserve1', ok=True):
        return self.cmd('attempt-reserve', '--task', task, '--attempt', attempt,
                        '--checkout', str(checkout or self.worker), '--agent', 'worker_a1',
                        '--provider', 'codex', '--expected-model', 'gpt-6-luna', '--expected-effort', 'xhigh', '--request-id', request, ok=ok)

    def attach(self):
        return self.cmd('attempt-attach', '--attempt', 'T01-A1', '--pane', 'w3:p1',
                        '--agent', 'worker_a1', '--model', 'gpt-6-luna', '--effort', 'xhigh', '--request-id', 'attach1')

    def report(self, request='report1', evidence='REPORT.md', ok=True):
        head = shell('git', '-C', str(self.worker), 'rev-parse', 'HEAD').stdout.strip()
        return self.cmd('attempt-report', '--attempt', 'T01-A1', '--source-sha', head,
                        '--evidence', evidence, '--request-id', request, ok=ok)

    def reported(self):
        self.init();self.task();self.reserve();self.attach();self.report()

    def frozen(self):
        self.reported()
        self.integrate()
        self.cmd('candidate-freeze', '--task', 'T01', '--candidate', 'C01',
                 '--checkout', str(self.repo), '--request-id', 'freeze1')

    def integrate(self):
        head = shell('git', '-C', str(self.worker), 'rev-parse', 'HEAD').stdout.strip()
        shell('git', '-C', str(self.repo), 'cherry-pick', head)

    def verifying(self):
        self.frozen()
        self.cmd('verify-start', '--verification', 'V01', '--candidate', 'C01',
                 '--verifier', 'haiku_verify', '--request-id', 'verify1')

    def test_dependency_tracer_to_exact_candidate_acceptance(self):
        self.init();self.task();self.task('T02', 'T01', 'Add recovery message')
        self.assertIn('T01', self.cmd('next'))
        self.assertNotIn('T02', self.cmd('next'))
        self.assertIn('fence=1', self.reserve())
        self.attach(); self.report()
        self.assertNotIn('T02', self.cmd('next'))
        self.integrate()
        self.cmd('candidate-freeze', '--task', 'T01', '--candidate', 'C01', '--checkout', str(self.repo), '--request-id', 'freeze1')
        self.cmd('verify-start', '--verification', 'V01', '--candidate', 'C01', '--verifier', 'haiku_verify', '--request-id', 'verify1')
        # An actual deterministic fixture check is run OUTSIDE the ledger.
        check = subprocess.run([sys.executable, '-c',
             "assert open('code.txt').read().strip() == 'implemented worker patch'; print('CHECK_PASS')"],
             cwd=str(self.repo), capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        (self.run_dir/'VERIFY.log').write_text(check.stdout, encoding='utf8')
        self.assertIn('PASS', self.cmd('verify-record', '--verification', 'V01', '--result', 'PASS', '--exit-code', '0', '--evidence', 'VERIFY.log', '--request-id', 'verified1'))
        self.assertIn('ACCEPTED', self.cmd('task-accept', '--task', 'T01', '--candidate', 'C01', '--decision', 'MANAGER.txt', '--request-id', 'accept1'))
        self.assertIn('T02', self.cmd('next'))
        view=self.cmd('status', '--human')
        self.assertIn('Prevent duplicate writes', view)
        self.assertNotIn('T01', view)
        self.assertFalse((self.run_dir/'SESSION.md').exists())

    def test_idempotent_report_and_conflicting_replay(self):
        self.reported()
        result=self.report()
        self.assertIn('state=REPORTED',result)
        (self.run_dir/'OTHER.md').write_text('Different report',encoding='utf8')
        self.assertIn('CONFLICT', self.report(evidence='OTHER.md',ok=False))
        events=self.cmd('events','--limit','20')
        self.assertEqual(sum('attempt-report' in line for line in events.splitlines()),1)

    def test_legacy_markdown_run_cannot_initialize(self):
        (self.run_dir/'SESSION.md').write_text('legacy truth\n',encoding='utf8')
        self.assertIn('competing ledger', self.cmd('init','--run-id','sample','--repo',str(self.repo),'--plan','PLAN.md','--grant-ref','grant',ok=False))
        self.assertFalse((self.run_dir/'ledger.sqlite').exists())

    def test_resume_reserved_does_not_spawn(self):
        self.init();self.task();self.reserve()
        before=self.cmd('recover')
        self.assertIn('RESERVED',before)
        self.assertIn('never-auto-respawn',before)
        self.assertFalse((self.run_dir/'SESSION.md').exists())

    def test_wrong_agent_and_state_do_not_advance(self):
        self.init();self.task();self.reserve()
        self.assertIn('unexpected Herdr agent', self.cmd('attempt-attach','--attempt','T01-A1',
            '--pane','w9:p9','--agent','wrong','--model','gpt-6-luna','--request-id','bad',ok=False))
        self.assertIn('RESERVED', self.cmd('recover'))
        self.assertIn('not STARTED', self.report(ok=False))
        self.assertIn('MODEL_OVERRIDE_NOT_APPLIED', self.cmd('attempt-attach','--attempt','T01-A1','--pane','w3:p1','--agent','worker_a1','--model','gpt-6-luna','--effort','max','--request-id','bad-effort',ok=False))

    def test_candidate_checkout_dirty_fails(self):
        self.reported()
        (self.repo/'scratch.txt').write_text('untracked',encoding='utf8')
        self.assertIn('dirty', self.cmd('candidate-freeze','--task','T01','--candidate','C01',
                           '--checkout',str(self.repo),'--request-id','freeze1',ok=False))

    def test_candidate_drift_stales_pass(self):
        self.verifying()
        self.commit('changed after frozen candidate')
        self.assertIn('STALE', self.cmd('verify-record','--verification','V01','--result','PASS',
                                      '--exit-code','0','--evidence','VERIFY.log','--request-id','record1'))
        error=self.cmd('task-accept','--task','T01','--candidate','C01','--decision','MANAGER.txt','--request-id','accept1',ok=False)
        self.assertIn('mismatch', error)

    def test_evidence_modified_after_pass_blocks_acceptance(self):
        self.verifying()
        self.cmd('verify-record','--verification','V01','--result','PASS',
                 '--exit-code','0','--evidence','VERIFY.log','--request-id','pass1')
        (self.run_dir/'VERIFY.log').write_text('tampered after PASS', encoding='utf8')
        self.assertIn('verification evidence changed',self.cmd('task-accept',
            '--task','T01','--candidate','C01','--decision','MANAGER.txt',
            '--request-id','accept1',ok=False))

    def test_test_failure_cannot_record_pass(self):
        self.verifying()
        self.assertIn('requires exit code 0', self.cmd('verify-record','--verification','V01',
            '--result','PASS','--exit-code','1','--evidence','VERIFY.log','--request-id','record1',ok=False))
        self.assertIn('lock-held', self.cmd('recover'))

    def test_unauthorized_evidence_path_rejected(self):
        self.init(); self.task()
        self.assertIn('relative path',self.cmd('attempt-report','--attempt','T01-A1',
            '--source-sha','0'*40,'--evidence','../PLAN.md','--request-id','r1',ok=False))
        outside=self.root/'outside.md';outside.write_text('secret',encoding='utf8')
        try:
            (self.run_dir/'link.md').symlink_to(outside)
        except OSError:
            self.skipTest('OS disallows symlink creation in this test account')
        self.assertIn('outside trusted',self.cmd('task-add','--task','T03','--title','Valid title',
            '--outcome','One behavior','--brief','link.md','--request-id','add2',ok=False))

    def test_plan_mutation_blocks_transitions(self):
        self.init();self.task()
        (self.run_dir/'PLAN.md').write_text('Unapproved altered scope', encoding='utf8')
        self.assertIn('PLAN.md changed', self.reserve(ok=False))
        self.assertEqual('READY T01 Prevent duplicate writes',self.cmd('next'))

    def test_conflicting_checkout_claim_and_generation(self):
        self.init(parallel=True);self.task();self.task('T02',title='Independent second slice')
        self.reserve()
        self.assertIn('resource already claimed',self.reserve(task='T02',attempt='T02-A1',request='reserve2',ok=False))
        self.attach();self.report()
        self.assertIn('fencing generation mismatch',self.cmd('attempt-release','--attempt','T01-A1','--generation','2',
                            '--observation','agent stopped','--request-id','relbad',ok=False))
        self.assertIn('writer-released', self.cmd('attempt-release','--attempt','T01-A1','--generation','1',
                            '--observation','agent stopped','--request-id','relgood'))
        self.assertIn('fence=2',self.reserve(task='T02',attempt='T02-A1',request='reserve3'))

    def test_external_cli_smoke(self):
        self.init(); self.task()
        proc = subprocess.run([sys.executable, str(CLI), '--run-dir', str(self.run_dir), 'status'],
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('mode=sqlite-pilot', proc.stdout)

    def test_concurrent_reservations_same_checkout_only_one_wins(self):
        self.init(parallel=True);self.task();self.task('T02',title='Independent second slice')
        def reserve(tid):
            argv=[sys.executable,str(CLI),'--run-dir',str(self.run_dir),'attempt-reserve',
                  '--task',tid,'--attempt',tid+'-A1','--checkout',str(self.worker),
                  '--agent',tid.lower()+'_worker','--provider','codex','--expected-model','gpt-6-luna','--expected-effort','xhigh','--request-id',tid+'-reserve']
            return subprocess.run(argv,capture_output=True,text=True,timeout=15)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(reserve,('T01','T02')))
        self.assertEqual(sorted(r.returncode for r in results),[0,2],results)
        self.assertTrue(any('resource already claimed' in r.stderr for r in results))
        self.assertEqual(1,sum('attempt-reserve' in x for x in self.cmd('events','--limit','20').splitlines()))

    def test_stale_writer_release_denied_after_reassignment(self):
        self.init(parallel=True);self.task();self.task('T02',title='Independent second slice')
        self.reserve();self.attach();self.report()
        self.cmd('attempt-release','--attempt','T01-A1','--generation','1',
                 '--observation','observed stopped','--request-id','released')
        self.reserve(task='T02',attempt='T02-A1',request='reserve2')
        err=self.cmd('attempt-release','--attempt','T01-A1','--generation','1',
                     '--observation','stale claim','--request-id','stale-release',ok=False)
        self.assertIn('fencing generation mismatch',err)

    def test_read_only_status_does_not_mutate_ledger(self):
        self.init();self.task()
        db=self.run_dir/'ledger.sqlite'
        before=hashlib.sha256(db.read_bytes()).hexdigest()
        self.cmd('status');self.cmd('next');self.cmd('recover')
        self.assertEqual(before,hashlib.sha256(db.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
