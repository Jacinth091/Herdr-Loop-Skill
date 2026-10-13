"""Offline proof for the opt-in SQLite ledger. Never launches Herdr or AI tools."""
import hashlib
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import sqlite3
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'skills' / 'herdr-engineering-loop' / 'scripts' / 'herdr_ledger.py'
spec = importlib.util.spec_from_file_location('herdr_ledger_pilot', CLI)
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)
REAL_OBSERVER = ledger.observe_herdr
REAL_STOPPED = ledger.observe_stopped


def shell(*args, cwd=None):
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, check=True)


class LedgerPilotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        observation = mock.patch.object(ledger, 'observe_herdr', return_value={
            'agent': 'worker_a1', 'pane': 'w3:p1', 'terminal': 'term-test', 'pid': 1234,
            'model': 'gpt-6-luna', 'effort': 'xhigh'})
        observation.start()
        self.addCleanup(observation.stop)
        stopped = mock.patch.object(ledger, 'observe_stopped', return_value='Herdr pane=w3:p1 pid=1234 absent')
        stopped.start()
        self.addCleanup(stopped.stop)
        recipient = mock.patch.object(ledger, 'observe_recipient')
        recipient.start()
        self.addCleanup(recipient.stop)
        unstarted = mock.patch.object(ledger, 'observe_unstarted', return_value='Herdr agent=worker_a1 absent')
        unstarted.start()
        self.addCleanup(unstarted.stop)
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
        (self.run_dir / 'READY.md').write_text(
            'attempt=T01-A1\nagent=worker_a1\npane=w3:p1\nobserved_model=gpt-6-luna\nobserved_effort=xhigh\n', encoding='utf8')
        (self.run_dir / 'TASK-ACK.md').write_text(
            'attempt=T01-A1\nevent=start1\nagent=worker_a1\npane=w3:p1\nplan_sha=' +
            hashlib.sha256((self.run_dir/'PLAN.md').read_bytes()).hexdigest() + '\n',encoding='utf8')

    def receipt(self, kind, event, evidence, sender, pane, recipient):
        digest = hashlib.sha256((self.run_dir / evidence).read_bytes()).hexdigest()
        name = f'{event}.receipt'
        (self.run_dir / name).write_text(
            f'{kind} sample T01-A1 {event} {sender} {pane} {recipient}\nevidence_sha={digest}\n', encoding='utf8')
        return name

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
        result = self.cmd('attempt-attach', '--attempt', 'T01-A1', '--pane', 'w3:p1',
                          '--agent', 'worker_a1', '--request-id', 'attach1')
        ready = self.receipt('ROLE_READY', 'ready1', 'READY.md', 'worker_a1', 'w3:p1', 'delegator')
        self.cmd('attempt-ready', '--attempt', 'T01-A1', '--operator', 'delegator', '--operator-pane','w1:p1','--event-id', 'ready1',
                 '--evidence', 'READY.md', '--receipt', ready, '--request-id', 'record-ready1')
        start = self.receipt('TASK_START', 'start1', 'TASK-ACK.md', 'delegator', 'w1:p1', 'worker_a1')
        self.cmd('attempt-start', '--attempt', 'T01-A1', '--operator', 'delegator', '--operator-pane','w1:p1','--sender-pane', 'w1:p1',
                 '--event-id', 'start1', '--evidence', 'TASK-ACK.md', '--receipt', start, '--request-id', 'record-start1')
        return result

    def report(self, request='report1', evidence='REPORT.md', ok=True):
        head = shell('git', '-C', str(self.worker), 'rev-parse', 'HEAD').stdout.strip()
        receipt = self.receipt('REPORT_READY', 'report-event1', evidence, 'worker_a1', 'w3:p1', 'delegator')
        return self.cmd('attempt-report', '--attempt', 'T01-A1', '--source-sha', head,
                        '--evidence', evidence, '--event-id', 'report-event1', '--recipient', 'delegator',
                        '--receipt', receipt, '--request-id', request, ok=ok)

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

    def approve(self):
        return self.cmd('decision-record','--decision-id','D01','--subject','C01','--authority','MANAGER',
                        '--actor','manager','--attestor','delegator','--disposition','APPROVED',
                        '--provenance','observed-manager-receipt','--rationale','MANAGER.txt','--request-id','decision1')

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
        self.assertIn('PASS', self.cmd('verify-exec', '--verification', 'V01', '--request-id', 'verified1', '--',
                                   sys.executable, '-c', "assert open('code.txt').read().strip() == 'implemented worker patch'"))
        self.approve()
        self.assertIn('ACCEPTED', self.cmd('task-accept', '--task', 'T01', '--candidate', 'C01', '--decision-id', 'D01', '--request-id', 'accept1'))
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
            '--pane','w9:p9','--agent','wrong','--request-id','bad',ok=False))
        self.assertIn('RESERVED', self.cmd('recover'))
        self.assertIn('not STARTED', self.report(ok=False))
        with mock.patch.object(ledger, 'observe_herdr', return_value={'agent':'worker_a1','pane':'w3:p1','terminal':'term-test','pid':1234,'model':'gpt-6-luna','effort':'max'}):
            self.assertIn('MODEL_OVERRIDE_NOT_APPLIED', self.cmd('attempt-attach','--attempt','T01-A1','--pane','w3:p1','--agent','worker_a1','--request-id','bad-effort',ok=False))

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
        error=self.cmd('task-accept','--task','T01','--candidate','C01','--decision-id','D01','--request-id','accept1',ok=False)
        self.assertIn('candidate changed', error)

    def test_evidence_modified_after_pass_blocks_acceptance(self):
        self.verifying()
        self.cmd('verify-exec','--verification','V01','--request-id','pass1','--',sys.executable,'-c','print("pass")')
        self.approve()
        (self.run_dir/'verifications/V01.log').write_text('tampered after PASS', encoding='utf8')
        self.assertIn('verification evidence changed',self.cmd('task-accept',
            '--task','T01','--candidate','C01','--decision-id','D01',
            '--request-id','accept1',ok=False))

    def test_changed_ready_receipt_blocks_acceptance(self):
        self.verifying()
        self.cmd('verify-exec','--verification','V01','--request-id','pass1','--',sys.executable,'-c','print("pass")')
        self.approve()
        (self.run_dir/'ready1.receipt').write_text('tampered receipt',encoding='utf8')
        self.assertIn('attempt signal evidence changed',self.cmd('task-accept','--task','T01',
            '--candidate','C01','--decision-id','D01','--request-id','accept-tampered',ok=False))

    def test_test_failure_cannot_record_pass(self):
        self.verifying()
        self.assertIn('requires verify-exec', self.cmd('verify-record','--verification','V01',
            '--result','PASS','--exit-code','1','--evidence','VERIFY.log','--request-id','record1',ok=False))
        self.assertIn('lock-held', self.cmd('recover'))

    def test_captured_nonzero_verification_is_fail(self):
        self.verifying()
        result=self.cmd('verify-exec','--verification','V01','--request-id','failed-check','--',
                        sys.executable,'-c','import sys; sys.exit(7)')
        self.assertIn('result=FAIL',result)
        with closing(sqlite3.connect(self.run_dir/'ledger.sqlite')) as db:
            self.assertEqual(7,db.execute("SELECT exit_code FROM verification WHERE id='V01'").fetchone()[0])
            self.assertEqual(0,db.execute("SELECT active FROM claim WHERE resource='heavy-local-verification'").fetchone()[0])

    def test_interrupted_verification_holds_global_lock(self):
        self.verifying()
        real_run=ledger.subprocess.run
        def interrupted(argv,*args,**kwargs):
            if argv and argv[0]==sys.executable:
                raise OSError('simulated launch ambiguity')
            return real_run(argv,*args,**kwargs)
        with mock.patch.object(ledger.subprocess,'run',side_effect=interrupted):
            with self.assertRaisesRegex(OSError,'simulated launch ambiguity'):
                self.cmd('verify-exec','--verification','V01','--request-id','interrupted','--',
                         sys.executable,'-c','print("pass")')
        self.assertIn('lock-held',self.cmd('recover'))
        self.assertIn('requires process reconciliation',self.cmd('verify-abort','--verification','V01',
            '--reason','uncertain process','--request-id','abort1',ok=False))
        with closing(sqlite3.connect(self.run_dir/'ledger.sqlite')) as db:
            self.assertEqual('RUNNING',db.execute("SELECT state FROM verification WHERE id='V01'").fetchone()[0])
            self.assertEqual(1,db.execute("SELECT active FROM claim WHERE resource='heavy-local-verification'").fetchone()[0])
        recon = self.cmd('verify-reconcile', '--verification', 'V01', '--reason', 'process confirmed dead', '--request-id', 'reconcile1')
        self.assertIn('reconciled=true', recon)
        self.assertIn('claims-released', recon)
        with closing(sqlite3.connect(self.run_dir/'ledger.sqlite')) as db:
            self.assertEqual('INDETERMINATE',db.execute("SELECT state FROM verification WHERE id='V01'").fetchone()[0])
            self.assertEqual(0,db.execute("SELECT active FROM claim WHERE resource='heavy-local-verification'").fetchone()[0])

    def test_unauthorized_evidence_path_rejected(self):
        self.init(); self.task()
        self.assertIn('relative path',self.cmd('attempt-report','--attempt','T01-A1',
            '--source-sha','0'*40,'--evidence','../PLAN.md','--event-id','report1','--recipient','delegator','--receipt','REPORT.md','--request-id','r1',ok=False))
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

    def test_asserted_attach_cannot_skip_herdr_observation_or_ready(self):
        self.init();self.task();self.reserve()
        with mock.patch.object(ledger, 'observe_herdr', side_effect=ledger.LedgerError('BLOCKED: HERDR_TRANSPORT_UNAVAILABLE')):
            self.assertIn('HERDR_TRANSPORT_UNAVAILABLE', self.cmd('attempt-attach','--attempt','T01-A1',
                '--pane','w3:p1','--agent','worker_a1','--request-id','attach-denied',ok=False))
        self.cmd('attempt-attach','--attempt','T01-A1','--pane','w3:p1','--agent','worker_a1','--request-id','attach1')
        self.assertIn('not READY',self.cmd('attempt-start','--attempt','T01-A1','--operator','delegator',
            '--operator-pane','w1:p1','--sender-pane','w1:p1','--event-id','start1','--evidence','TASK-ACK.md',
            '--receipt',self.receipt('TASK_START','start1','TASK-ACK.md','delegator','w1:p1','worker_a1'),
            '--request-id','early-start',ok=False))
        self.assertIn('OBSERVED',self.cmd('recover'))

    def test_observer_checks_actual_agent_pane_process_and_model_flags(self):
        agent={'agent':{'name':'worker_a1','pane_id':'w3:p1','cwd':str(self.worker),'terminal_id':'term-test'}}
        process={'process_info':{'pane_id':'w3:p1','foreground_processes':[{'name':'codex.exe','pid':1234,
            'cwd':str(self.worker),'argv':['codex.exe','-m','gpt-6-luna','-c','model_reasoning_effort="xhigh"']}]}}
        with mock.patch.object(ledger,'herdr_json',side_effect=[agent,process]):
            self.assertEqual('xhigh',REAL_OBSERVER('worker_a1','w3:p1',self.worker,'codex')['effort'])
        agent['agent']['pane_id']='wrong'
        with mock.patch.object(ledger,'herdr_json',side_effect=[agent,process]):
            with self.assertRaisesRegex(ledger.LedgerError,'HERDR_IDENTITY_MISMATCH'):
                REAL_OBSERVER('worker_a1','w3:p1',self.worker,'codex')

    def test_observer_checks_stopped_or_closed_pane(self):
        with mock.patch.object(ledger, 'herdr_json', return_value={'panes': [{'pane_id': 'w1:p1'}]}):
            msg = REAL_STOPPED('w3:p1', 1234)
            self.assertIn('closed and pid=1234 absent', msg)
        panes = {'panes': [{'pane_id': 'w3:p1'}]}
        process = {'process_info': {'pane_id': 'w3:p1', 'foreground_processes': [{'pid': 1234}]}}
        with mock.patch.object(ledger, 'herdr_json', side_effect=[panes, process]):
            with self.assertRaisesRegex(ledger.LedgerError, 'UNCERTAIN: observed agent process still present'):
                REAL_STOPPED('w3:p1', 1234)
        process_idle = {'process_info': {'pane_id': 'w3:p1', 'foreground_processes': [{'pid': 5678}]}}
        with mock.patch.object(ledger, 'herdr_json', side_effect=[panes, process_idle]):
            msg = REAL_STOPPED('w3:p1', 1234)
            self.assertIn('Herdr pane=w3:p1 pid=1234 absent', msg)

    def test_global_heavy_lock_blocks_another_checkout(self):
        self.frozen()
        other=self.root/'integration-two'
        shell('git','-C',str(self.repo),'worktree','add','-q','-b','integration-two',str(other),'HEAD')
        self.cmd('candidate-freeze','--task','T01','--candidate','C02','--checkout',str(other),'--request-id','freeze2')
        self.cmd('verify-start','--verification','V01','--candidate','C01','--verifier','haiku','--request-id','verify1')
        error=self.cmd('verify-start','--verification','V02','--candidate','C02','--verifier','haiku2','--request-id','verify2',ok=False)
        self.assertIn('resource already claimed',error)
        with closing(sqlite3.connect(self.run_dir/'ledger.sqlite')) as db:
            self.assertEqual(1,db.execute("SELECT COUNT(*) FROM claim WHERE resource='heavy-local-verification' AND active=1").fetchone()[0])

    def test_unrelated_clean_candidate_rejected(self):
        self.reported()
        self.assertIn('does not contain reported source patch',self.cmd('candidate-freeze','--task','T01',
            '--candidate','C01','--checkout',str(self.repo),'--request-id','wrong-freeze',ok=False))

    def test_open_high_finding_blocks_acceptance_until_resolved(self):
        self.frozen()
        (self.run_dir/'CRITIQUE.md').write_text('Important issue in edge case\n',encoding='utf8')
        self.cmd('critique-record','--review','R01','--candidate','C01','--critic','sonnet_critic',
                 '--report','CRITIQUE.md','--request-id','review1')
        self.cmd('finding-record','--finding','F01','--review','R01','--actor','sonnet_critic','--severity','HIGH',
                 '--disposition','OPEN','--evidence','CRITIQUE.md','--request-id','finding1')
        self.cmd('verify-start','--verification','V01','--candidate','C01','--verifier','haiku',
                 '--request-id','verify1')
        self.cmd('verify-exec','--verification','V01','--request-id','verify-run1','--',sys.executable,'-c','print("pass")')
        self.approve()
        self.assertIn('unresolved blocking',self.cmd('task-accept','--task','T01','--candidate','C01',
            '--decision-id','D01','--request-id','blocked-accept',ok=False))
        self.cmd('finding-record','--finding','F01','--review','R01','--actor','sonnet_critic','--severity','HIGH',
                 '--disposition','RESOLVED','--evidence','CRITIQUE.md','--request-id','finding2')
        self.assertIn('ACCEPTED',self.cmd('task-accept','--task','T01','--candidate','C01',
            '--decision-id','D01','--request-id','accepted'))

    def test_cancellation_fences_dispatch_and_waits_for_claims(self):
        self.init();self.task();self.reserve()
        (self.run_dir/'CANCEL.md').write_text('Human cancelled this run\n',encoding='utf8')
        self.cmd('run-cancel','--decision-id','D-CANCEL','--actor','human','--attestor','operator',
                 '--authority-source','user-request','--reason','CANCEL.md','--request-id','cancel1')
        self.assertIn('run is not executing',self.reserve(task='T01',attempt='T01-A2',request='reserve2',ok=False))
        self.assertIn('ownership remains',self.cmd('cancel-finalize','--decision-id','D-CANCEL',
            '--request-id','finish1',ok=False))
        self.assertIn('state=CANCELLING',self.cmd('status'))
        self.cmd('attempt-release','--attempt','T01-A1','--generation','1',
                 '--observation','confirmed absent','--request-id','release-cancel1')
        self.assertIn('state=CANCELLED',self.cmd('cancel-finalize','--decision-id','D-CANCEL',
            '--request-id','finish2'))
        self.assertEqual('NO_READY_TASK',self.cmd('next'))

    def test_confirmed_stopped_attempt_needs_manager_decision_and_new_id(self):
        self.init();self.task();self.reserve()
        self.cmd('attempt-release','--attempt','T01-A1','--generation','1',
                 '--observation','confirmed absent','--request-id','released1')
        self.assertIn('task not ready',self.reserve(attempt='T01-A2',request='reserve-early',ok=False))
        self.assertIn('decision missing',self.cmd('task-retry','--task','T01',
            '--decision-id','missing','--request-id','retry-early',ok=False))
        self.cmd('decision-record','--decision-id','D-RETRY','--subject','T01','--authority','MANAGER',
                 '--actor','manager','--attestor','delegator','--disposition','APPROVED',
                 '--provenance','observed-manager-retry','--rationale','MANAGER.txt','--request-id','retry-decision')
        self.assertIn('state=PLANNED',self.cmd('task-retry','--task','T01','--decision-id','D-RETRY',
            '--request-id','retry1'))
        self.assertIn('fence=2',self.cmd('attempt-reserve','--task','T01','--attempt','T01-A2',
            '--checkout',str(self.worker),'--agent','worker_a2','--provider','codex',
            '--expected-model','gpt-6-luna','--expected-effort','xhigh','--request-id','reserve2'))
        with closing(sqlite3.connect(self.run_dir/'ledger.sqlite')) as db:
            self.assertEqual('INTERRUPTED',db.execute("SELECT state FROM attempt WHERE id='T01-A1'").fetchone()[0])

    def test_v1_upgrade_backups_and_quarantines_asserted_attempts(self):
        db=self.run_dir/'ledger.sqlite'
        with closing(sqlite3.connect(db)) as conn:
            conn.executescript((ROOT/'tests/fixtures/ledger_v1.sql').read_text(encoding='utf8'))
            conn.execute("INSERT INTO run(id,repo,plan_path,plan_sha,grant_ref,max_workers) VALUES(?,?,?,?,?,?)",
                         ('sample',str(self.repo),'PLAN.md',hashlib.sha256((self.run_dir/'PLAN.md').read_bytes()).hexdigest(),'human',1))
            conn.execute("INSERT INTO task(id,title,outcome,deps,brief_path,brief_sha) VALUES(?,?,?,?,?,?)",
                         ('T01','Legacy task','Safe result','[]','TASK.md',hashlib.sha256((self.run_dir/'TASK.md').read_bytes()).hexdigest()))
            conn.execute("INSERT INTO attempt(id,task_id,kind,agent_hint,checkout,expected_model,expected_effort,state,claim_generation) VALUES(?,?,?,?,?,?,?,?,?)",
                         ('T01-A1','T01','codex','worker_a1',str(self.worker),'gpt-6-luna','xhigh','STARTED',1))
            conn.commit()
        self.assertIn('v1-live-attempts=UNCERTAIN',self.cmd('upgrade'))
        self.assertTrue((self.run_dir/'ledger.v1.backup.sqlite').exists())
        self.assertIn('UNCERTAIN',self.cmd('recover'))
        with closing(sqlite3.connect(self.run_dir/'ledger.v1.backup.sqlite')) as backup:
            self.assertEqual(1,backup.execute('PRAGMA user_version').fetchone()[0])
        with closing(sqlite3.connect(db)) as future:
            future.execute('PRAGMA user_version=99')
        self.assertIn('schema is not v2',self.cmd('status',ok=False))
        self.assertIn('unknown ledger schema',self.cmd('upgrade',ok=False))

    def test_progress_and_export_are_read_only_projections(self):
        self.init();self.task()
        view=subprocess.run([sys.executable,str(ROOT/'tools/progress_view.py'),'--run-dir',str(self.run_dir)],
                            capture_output=True,text=True,check=False)
        self.assertEqual(0,view.returncode,view.stderr)
        self.assertIn('Prevent duplicate writes',view.stdout)
        self.assertNotIn('T01',view.stdout)
        exported=self.cmd('export')
        self.assertIn('non-authoritative',exported)
        self.assertIn('Plan SHA-256',exported)
        self.assertFalse((self.run_dir/'SESSION.md').exists())

    def test_independent_checkouts_budget_enforced(self):
        self.init()
        self.task()
        # Add a second task
        (self.run_dir / 'TASK2.md').write_text('# Task 2\nSafe outcome\n', encoding='utf-8')
        self.cmd('task-add', '--task', 'T02', '--title', 'Second Task', '--outcome', 'Second outcome',
                 '--brief', 'TASK2.md', '--request-id', 'task2')
        # Create a second independent worktree
        worker2 = self.root / 'worker2'
        shell('git', '-C', str(self.repo), 'worktree', 'add', '-b', 'worker2-branch', str(worker2), 'HEAD')

        # First worker reservation succeeds
        self.reserve('T01', 'T01-A1', checkout=self.worker)

        # Second worker reservation on a distinct checkout must fail when max_workers=1
        err = self.reserve('T02', 'T02-A1', checkout=worker2, request='reserve2', ok=False)
        self.assertIn('worker budget full', err)


if __name__ == '__main__':
    unittest.main()
