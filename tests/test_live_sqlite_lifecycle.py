"""Mocked live contracts; these are not real Herdr E2E evidence."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

from tests import test_ledger_pilot as pilot

ledger = pilot.ledger
ROOT = pilot.ROOT


class LiveSQLiteTests(unittest.TestCase):
    def setUp(self):
        pilot.LedgerPilotTests.setUp(self)
        observation = mock.patch.object(ledger, 'observe_herdr', side_effect=self.observed)
        observation.start()
        self.addCleanup(observation.stop)

    def observed(self, name, pane, checkout, provider):
        return dict(agent=name, pane=pane, terminal='term-test', pid=1234,
                    model='gpt-6-luna' if provider == 'codex' else '',
                    effort='xhigh' if provider == 'codex' else '')
    commit = pilot.LedgerPilotTests.commit
    task = pilot.LedgerPilotTests.task
    reserve = pilot.LedgerPilotTests.reserve
    attach = pilot.LedgerPilotTests.attach
    report = pilot.LedgerPilotTests.report
    integrate = pilot.LedgerPilotTests.integrate

    def cmd(self, *argv, ok=True):
        if not hasattr(self, 'manifest') or argv[0] in ('status', 'next', 'recover', 'events', 'export', 'task-show'):
            return pilot.LedgerPilotTests.cmd(self, *argv, ok=ok)
        if ok:
            return self.live(self.caller, *argv)
        with self.assertRaises(ledger.LedgerError) as caught:
            self.live(self.caller, *argv)
        return str(caught.exception)

    def receipt(self, *args):
        path = pilot.LedgerPilotTests.receipt(self, *args)
        artifact = self.run_dir / path
        artifact.write_text(artifact.read_text(encoding='utf8').replace(' sample ', ' run '), encoding='utf8')
        return path

    def pane(self, name):
        return 'w1:p1' if name == 'delegator' else 'w3:p1'

    def bootstrap(self):
        self.cmd('init', '--bootstrap', '--run-id', 'run', '--repo', str(self.repo))
        ledger.run_backend.init_selector(self.run_dir, 'run', 'sqlite')
        entries = [dict(name=role, role=role, kind='claude', prompt='Setup only: ' + role,
                        worktree='', task_id='', attempt_id='', signal_recipient='manager' if role == 'delegator' else 'delegator')
                   for role in ('manager', 'delegator', 'worker', 'verifier', 'critic')]
        entries[2].update(name='worker_a1', kind='codex', worktree=str(self.worker), task_id='T01', attempt_id='T01-A1')
        self.manifest = dict(generated_by='herdr-role-pack', run_id='run', repo=str(self.repo),
                             state_backend='sqlite', backend_epoch=1, agents=entries)
        (self.run_dir / 'ACTIVATION.json').write_text(json.dumps(self.manifest), encoding='utf8')
        self.caller = 'delegator'

    def acknowledge(self, name):
        entry = next(a for a in self.manifest['agents'] if a['name'] == name)
        pane = self.pane(name)
        recipient = entry['signal_recipient']
        op = 'operation-' + name
        prompt_sha = hashlib.sha256(entry['prompt'].encode()).hexdigest()
        ledger.store_activation(self.run_dir, dict(operation_id=op, agent=name, status='SUBMITTED',
                                                   prompt_sha256=prompt_sha, epoch=1, observed=self.observed(name, pane, self.repo, entry['kind'])))
        ready = '\n'.join(('run=run', 'epoch=1', 'agent=' + name, 'pane=' + pane,
                           'role=' + entry['role'], 'operation=' + op, 'prompt_sha=' + prompt_sha))
        path = name + '.ready'
        (self.run_dir / path).write_text(ready, encoding='utf8')
        digest = hashlib.sha256((self.run_dir / path).read_bytes()).hexdigest()
        receipt = name + '.receipt'
        (self.run_dir / receipt).write_text(f'ROLE_READY run {op} {name} {pane} {recipient}\nevidence_sha={digest}\n', encoding='utf8')
        args = ledger.parser().parse_args(['--run-dir', str(self.run_dir), '--as-agent', name, '--epoch', '1',
                                          'role-confirm', '--operation-id', op, '--pane', pane,
                                          '--evidence', path, '--receipt', receipt, '--recipient', recipient, '--recipient-pane', self.pane(recipient)])
        return ledger.operation(self.run_dir, args)

    def live(self, name, *argv):
        args = ledger.parser().parse_args(['--run-dir', str(self.run_dir), '--as-agent', name, '--epoch', '1', *argv])
        with mock.patch.dict(os.environ, HERDR_PANE_ID=self.pane(name)), \
                mock.patch.object(ledger, 'herdr_json', return_value={'pane': {'pane_id': self.pane(name)}}):
            return ledger.operation(self.run_dir, args)

    def executing(self):
        self.test_plan_approval_and_execution_grant_remain_separate()

    def test_typed_dispatch_to_exact_candidate_acceptance(self):
        self.executing()
        for name in ('worker_a1', 'verifier', 'critic'):
            self.acknowledge(name)
        self.task()
        self.reserve()
        self.attach()
        self.report()
        self.integrate()
        self.cmd('attempt-release', '--attempt', 'T01-A1', '--generation', '1',
                 '--observation', 'mocked process-scope stopped', '--request-id', 'release1')
        self.cmd('candidate-freeze', '--task', 'T01', '--candidate', 'C01', '--checkout', str(self.repo), '--request-id', 'freeze1')
        (self.run_dir / 'CRITIQUE.md').write_text('Synthetic independent critic fixture', encoding='utf8')
        self.live('critic', 'critique-record', '--review', 'Review1', '--candidate', 'C01',
                  '--critic', 'critic', '--report', 'CRITIQUE.md', '--request-id', 'critique1')
        self.cmd('verify-start', '--verification', 'V01', '--candidate', 'C01', '--verifier', 'verifier', '--request-id', 'verify1')
        self.assertIn('PASS', self.live('verifier', 'verify-exec', '--verification', 'V01', '--request-id', 'check1', '--',
                      sys.executable, '-c', "assert open('code.txt').read().strip() == 'implemented worker patch'"))
        self.live('manager', 'decision-record', '--decision-id', 'D01', '--subject', 'C01', '--authority', 'MANAGER',
                  '--actor', 'manager', '--attestor', 'delegator', '--disposition', 'APPROVED', '--provenance',
                  'synthetic-manager-review', '--rationale', 'MANAGER.txt', '--request-id', 'decision1')
        self.assertIn('ACCEPTED', self.cmd('task-accept', '--task', 'T01', '--candidate', 'C01', '--decision-id', 'D01', '--request-id', 'accept1'))

    def test_cancel_preserves_claims_until_observed_release(self):
        self.executing()
        self.task()
        self.reserve()
        self.cmd('run-cancel', '--decision-id', 'Cancel1', '--actor', 'human', '--attestor', 'delegator',
                 '--authority-source', 'synthetic-human-cancel', '--reason', 'MANAGER.txt', '--request-id', 'cancel1')
        self.assertIn('ownership remains', self.cmd('cancel-finalize', '--decision-id', 'Cancel1', '--request-id', 'finish1', ok=False))
        self.cmd('attempt-release', '--attempt', 'T01-A1', '--generation', '1', '--observation', 'mocked absent agent', '--request-id', 'release1')
        self.assertIn('CANCELLED', self.cmd('cancel-finalize', '--decision-id', 'Cancel1', '--request-id', 'finish2'))

    def test_plan_approval_and_execution_grant_remain_separate(self):
        self.bootstrap()
        for name in ('manager', 'delegator'):
            self.acknowledge(name)
        self.live('manager', 'plan-approve', '--plan', 'PLAN.md', '--decision-id', 'Plan1',
                  '--authority', 'MANAGER', '--actor', 'manager', '--attestor', 'human',
                  '--rationale', 'MANAGER.txt', '--request-id', 'plan1')
        self.assertIn('state=PLANNING', self.cmd('status'))
        with self.assertRaisesRegex(ledger.LedgerError, 'not executing'):
            self.live('delegator', 'task-add', '--task', 'T01', '--title', 'Safe task',
                      '--outcome', 'Safe result', '--brief', 'TASK.md', '--request-id', 'early')
        (self.run_dir / 'GRANT.md').write_text('Synthetic human grant fixture', encoding='utf8')
        self.live('delegator', 'run-authorize', '--grant-ref', 'GRANT.md', '--decision-id', 'Grant1',
                  '--actor', 'human', '--attestor', 'delegator', '--request-id', 'grant1')
        self.assertIn('state=EXECUTING', self.cmd('status'))

    def test_worker_cannot_approve_plan_or_run_verification(self):
        self.bootstrap()
        self.acknowledge('worker_a1')
        for command in ('plan-approve', 'verify-exec', 'task-accept'):
            with self.subTest(command=command), self.assertRaisesRegex(ledger.LedgerError, 'ROLE_AUTHORITY_BLOCKED'):
                ledger.role_guard(self.run_dir, type('Args', (), dict(command=command, as_agent='worker_a1', epoch=1))())

    def test_prompt_submission_is_not_readiness(self):
        self.bootstrap()
        with self.assertRaisesRegex(ledger.LedgerError, 'ROLE_NOT_READY'):
            self.live('delegator', 'task-add', '--task', 'T01', '--title', 'Safe task',
                      '--outcome', 'Safe result', '--brief', 'TASK.md', '--request-id', 'early')

    def test_stale_epoch_and_changed_acknowledgment_block_mutation(self):
        self.bootstrap()
        self.acknowledge('manager')
        args = type('Args', (), dict(command='plan-approve', as_agent='manager', epoch=2))()
        with self.assertRaisesRegex(ledger.LedgerError, 'BACKEND_INTEGRITY_BLOCKED'):
            ledger.role_guard(self.run_dir, args)
        (self.run_dir / 'manager.ready').write_text('altered', encoding='utf8')
        args.epoch = 1
        with self.assertRaisesRegex(ledger.LedgerError, 'evidence changed'):
            ledger.role_guard(self.run_dir, args)

    def test_observation_failure_prevents_role_confirmation(self):
        self.bootstrap()
        with mock.patch.object(ledger, 'observe_herdr', side_effect=ledger.LedgerError('HERDR_IDENTITY_MISMATCH')):
            with self.assertRaisesRegex(ledger.LedgerError, 'HERDR_IDENTITY_MISMATCH'):
                self.acknowledge('manager')
        self.assertEqual(ledger.activation_receipts(self.run_dir)[0]['status'], 'SUBMITTED')

    def test_role_process_restart_requires_new_acknowledgment(self):
        self.bootstrap()
        self.acknowledge('manager')
        changed = dict(self.observed('manager', 'w3:p1', self.repo, 'claude'), pid=5678)
        with mock.patch.object(ledger, 'observe_herdr', return_value=changed):
            with self.assertRaisesRegex(ledger.LedgerError, 'role process changed'):
                self.live('manager', 'plan-approve', '--plan', 'PLAN.md', '--decision-id', 'Plan1',
                          '--authority', 'MANAGER', '--actor', 'manager', '--attestor', 'human',
                          '--rationale', 'MANAGER.txt', '--request-id', 'plan1')

    def test_sqlite_activation_records_submission_and_blocks_replay(self):
        self.bootstrap()
        spec = importlib.util.spec_from_file_location('activation_sqlite_test', ROOT / 'tools/activate_roles.py')
        activation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(activation)
        fake = subprocess.CompletedProcess([], 0, '{}', '')
        observed = dict(agent='manager', pane='w3:p1', terminal='term-test', pid=1234, model='', effort='')
        with mock.patch.dict(os.environ, HERDR_ENV='1'), mock.patch.object(activation.shutil, 'which', return_value='herdr'), \
                mock.patch.object(activation.subprocess, 'run', return_value=fake), \
                mock.patch.object(activation.herdr_ledger, 'herdr_json', return_value={'agent': {'pane_id': 'w3:p1'}}), \
                mock.patch.object(activation.herdr_ledger, 'observe_herdr', return_value=observed), \
                mock.patch.object(activation, '_run_prompt', return_value=fake) as prompts:
            self.assertEqual(activation.main(['--run-dir', str(self.run_dir), '--roles', 'manager']), 0)
            self.assertEqual(activation.main(['--run-dir', str(self.run_dir), '--roles', 'manager']), 2)
            self.assertEqual(prompts.call_count, 1)
        self.assertFalse((self.run_dir / 'SESSION.md').exists())
        self.assertEqual(activation.herdr_ledger.activation_receipts(self.run_dir)[0]['status'], 'OUTCOME_UNCERTAIN')

    def test_markdown_operation_ids_remain_compatible_and_sqlite_identity_changes(self):
        spec = importlib.util.spec_from_file_location('activation_identity_test', ROOT / 'tools/activate_roles.py')
        activation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(activation)
        entry = dict(name='manager', role='manager', prompt='setup', state_backend='markdown', backend_epoch=7)
        identity = dict(run_id='run', task_id='', attempt_id='', role='manager', agent='manager',
                        prompt_sha256=hashlib.sha256(b'setup').hexdigest())
        original = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]
        self.assertEqual(activation._operation(entry, 'run')[0], original)
        entry.update(state_backend='sqlite', _observed={'pid': 1234})
        before = activation._operation(entry, 'run')[0]
        entry['_observed']['pid'] = 5678
        self.assertNotEqual(activation._operation(entry, 'run')[0], before)
