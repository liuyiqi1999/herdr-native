import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backend = load('backend', ROOT / 'remote/backend.py')
auto = load('auto', ROOT / 'remote/auto_launch.py')


class AutomaticConnectionTests(unittest.TestCase):
    def test_machine_and_project_selection_never_uses_local_paths(self):
        profiles = [dict(id='a', enabled=True, selected=False), dict(id='b', enabled=True, selected=True)]
        self.assertEqual(auto.choose_profile(profiles)['id'], 'b')
        self.assertEqual(auto.choose_profile(profiles, 'a')['id'], 'a')
        with self.assertRaises(RuntimeError): auto.choose_profile(profiles, 'deleted')
        panes = [dict(focused=True, cwd='/remote/project', foreground_cwd='/remote/worktree')]
        self.assertEqual(auto.choose_cwd(panes), '/remote/worktree')
        self.assertEqual(auto.choose_cwd(panes, '/selected/project'), '/selected/project')
        with self.assertRaises(RuntimeError): auto.choose_cwd([], None)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name)
        self.panes = []
        self.calls = []
        self.created = 0
        self.fail_create = False
        self.fail_run = False
        self.socket_paths = []
        self.patches = [patch.object(backend.Path, 'home', return_value=self.home),
                        patch.object(backend, 'executable', side_effect=lambda name: '/fake/' + name),
                        patch.object(backend.subprocess, 'run', side_effect=self.run_command)]
        for item in self.patches: item.start()
        self.request = {'agent': 'pi', 'cwd': str(self.home), 'herdr_session': 'saved-session', 'helper': ''}

    def tearDown(self):
        for item in reversed(self.patches): item.stop()
        # Only private directories created by this test's mocked backend are removed.
        for path in self.socket_paths: shutil.rmtree(Path(path).parent, ignore_errors=True)
        for path in self.home.rglob('backend.json'):
            record = json.loads(path.read_text())
            if record.get('socket'): shutil.rmtree(Path(record['socket']).parent, ignore_errors=True)
        self.temporary.cleanup()

    def run_command(self, argv, **kwargs):
        self.calls.append(argv)
        if argv == ['/fake/claude', '--version']:
            return subprocess.CompletedProcess(argv, 0, '2.1.299 (Claude Code)\n', '')
        if argv == ['/fake/pi', '--version']:
            return subprocess.CompletedProcess(argv, 0, '1.3.0\n', '')
        self.assertEqual(argv[:3], ['/fake/herdr', '--session', 'saved-session'])
        command = argv[3:]
        if command == ['pane', 'list']:
            result = {'panes': list(self.panes)}
        elif command[:2] == ['workspace', 'create']:
            self.created += 1
            if self.fail_create: raise subprocess.TimeoutExpired(argv, 15)
            pane = {'pane_id': 'opaque-pane-' + str(self.created), 'terminal_id': 'terminal-' + str(self.created)}
            self.panes.append(pane)
            result = {'root_pane': pane, 'workspace': {'workspace_id': 'opaque-workspace-' + str(self.created)}}
        elif command[:2] == ['workspace', 'focus']:
            result = {}
        elif command[:2] == ['pane', 'run']:
            if self.fail_run: raise subprocess.TimeoutExpired(argv, 15)
            state = next(self.home.rglob('backend.json'))
            socket_path = json.loads(state.read_text())['socket']
            Path(socket_path).touch()
            self.socket_paths.append(socket_path)
            return subprocess.CompletedProcess(argv, 0, '', '')
        else:
            self.fail('Unexpected remote mutation: ' + str(argv))
        return subprocess.CompletedProcess(argv, 0, json.dumps({'result': result}), '')

    def test_claude_runs_original_ui_and_reopens_same_remote_workspace(self):
        self.request.update(agent='claude', interactive=True, extension='')
        first = backend.ensure(self.request)
        second = backend.ensure(self.request)
        self.assertTrue(second['reused'])
        self.assertEqual(first['pane'], second['pane'])
        launch = next(c[-1] for c in self.calls if c[3:5] == ['pane', 'run'])
        self.assertIn('claude-editor.py launch --ready', launch)
        self.assertIn('--binary /fake/claude', launch)
        self.assertNotIn('app-server', launch)
        self.assertNotIn('--print', launch)
        self.assertEqual(self.created, 1)
        self.assertIn(['/fake/herdr', '--session', 'saved-session', 'workspace', 'focus', first['workspace']], self.calls)

    def test_claude_automatic_backend_bundles_python_dependencies(self):
        package = load('claude_package', ROOT / 'remote/claude_package.py')
        self.request.update(agent='claude', interactive=True, claude_assets=package.bundle(ROOT / 'remote'))
        first = backend.ensure(self.request)
        second = backend.ensure(self.request)
        launch = next(c[-1] for c in self.calls if c[3:5] == ['pane', 'run'])
        self.assertIn('claude-auto.py --ready', launch)
        self.assertTrue(second['reused'])
        self.assertEqual(first['pane'], second['pane'])
        self.assertTrue(list(self.home.rglob('pyte/__init__.py')))

    def test_claude_rejects_noninteractive_backend(self):
        self.request['agent'] = 'claude'
        with self.assertRaises(ValueError):
            backend.ensure(self.request)
        self.assertFalse(self.calls)

    def test_repeated_open_reuses_backend_and_saved_session(self):
        first = backend.ensure(self.request)
        second = backend.ensure(self.request)
        self.assertFalse(first['reused'])
        self.assertTrue(second['reused'])
        self.assertEqual(first['pane'], second['pane'])
        self.assertEqual(self.created, 1)
        self.assertEqual(sum(c[3:5] == ['pane', 'run'] for c in self.calls), 1)

    def test_lost_create_ack_never_automatically_repeats_mutation(self):
        self.fail_create = True
        with self.assertRaises(subprocess.TimeoutExpired): backend.ensure(self.request)
        self.fail_create = False
        with self.assertRaisesRegex(RuntimeError, 'uncertain'): backend.ensure(self.request)
        self.assertEqual(self.created, 1)

    def test_lost_run_ack_retains_known_pane_instead_of_creating_another(self):
        self.fail_run = True
        with self.assertRaises(subprocess.TimeoutExpired): backend.ensure(self.request)
        self.fail_run = False
        with self.assertRaisesRegex(RuntimeError, 'incomplete'): backend.ensure(self.request)
        self.assertEqual(self.created, 1)

    def test_codex_version_cannot_inject_an_npm_package_spec(self):
        with self.assertRaises(RuntimeError):
            auto.codex_client('codex-cli 0.155.0;bad-command')
        self.assertEqual(self.calls, [])

    def test_matching_installed_codex_is_used_without_installation(self):
        binary = self.home / 'codex'
        binary.write_text('fixture')
        with patch.object(auto.shutil, 'which', return_value=str(binary)), patch.object(auto.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0, 'codex-cli 0.155.0\n', '')) as command:
            self.assertEqual(auto.codex_client('codex-cli 0.155.0'), str(binary))
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[0], [str(binary), '--version'])

    def test_dead_pane_is_not_confused_with_reused_pane_id(self):
        first = backend.ensure(self.request)
        self.panes[0]['terminal_id'] = 'replacement-terminal'
        second = backend.ensure(self.request)
        self.assertNotEqual(first['pane'], second['pane'])
        self.assertEqual(self.created, 2)


if __name__ == '__main__':
    unittest.main()
