import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'remote'))
from claude_package import bundle
from install_claude_native import INSTALL

spec = importlib.util.spec_from_file_location('claude_auto', ROOT / 'remote/claude-auto.py')
auto = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auto)


class AutomaticClaudeTests(unittest.TestCase):
    def test_partial_wide_character_redraw_preserves_title_and_prompt_polling(self):
        screen = auto.pyte.Screen(80, 12)
        stream = auto.pyte.Stream(screen)
        stream.feed('\x1b]0;◐ V3链路特征时序开关\x07中文')
        self.assertTrue(screen.display[0].startswith('中文'))
        # Overwriting only a wide glyph's first cell leaves an orphan stub.
        stream.feed('\rX')
        self.assertTrue(screen.display[0].startswith('X 文'))
        self.assertIsNone(auto.prompt_region(screen))
        self.assertEqual(screen.title, '◐ V3链路特征时序开关')
        stream.feed('\x1b]0;✳ 更新后的标题\x07')
        self.assertEqual(screen.title, '✳ 更新后的标题')

    def test_capability_replies_are_distinct_from_kitty_keystrokes(self):
        for value in (b'\x1b[1;1R', b'\x1b[?62;22c', b'\x1b[?0u', b'\x1b[6;16;8t',
                      b'\x1b[I', b'\x1b[?0u\x1b[6;16;8t'):
            self.assertTrue(auto.terminal_reply(value))
        for value in (b'\x1b[103;5u', b'\x1b[27u', b'\x1b[A', b'hello'):
            self.assertFalse(auto.terminal_reply(value))

    def test_interactive_wrapper_reexecs_with_process_identity(self):
        with patch.object(auto.shutil, 'which', return_value='/bin/claude'), \
             patch.object(auto.sys.stdin, 'isatty', return_value=True), \
             patch.object(auto.sys.stdout, 'isatty', return_value=True), \
             patch.dict(auto.os.environ, {'HERDR_AGENT': 'pi', 'HERDR_NATIVE_CLAUDE': '1'}), \
             patch.object(auto.os, 'execve', side_effect=RuntimeError('reexec')) as execute:
            with self.assertRaisesRegex(RuntimeError, 'reexec'):
                auto.run('claude', ['--resume', 'session-id'], '/tmp/ready.json')
        binary, argv, env = execute.call_args.args
        self.assertEqual(binary, sys.executable)
        self.assertEqual(argv, [sys.executable, str(ROOT / 'remote/claude-auto.py'),
                               '--binary', '/bin/claude', '--ready', '/tmp/ready.json',
                               '--', '--resume', 'session-id'])
        self.assertEqual(env['HERDR_AGENT'], 'claude')

    def test_noninteractive_run_does_not_publish_identity(self):
        with patch.object(auto.shutil, 'which', return_value='/bin/claude'), \
             patch.object(auto.os, 'execv', side_effect=RuntimeError('passthrough')) as execute, \
             patch.object(auto.os, 'execve') as reexecute:
            with self.assertRaisesRegex(RuntimeError, 'passthrough'):
                auto.run('claude', ['--version'])
        execute.assert_called_once_with('/bin/claude', ['/bin/claude', '--version'])
        reexecute.assert_not_called()

    def test_noninteractive_invocations_are_never_intercepted(self):
        for arguments in (['--version'], ['--print', 'hello'], ['-p', 'hello'], ['mcp', 'list'],
                          ['auth', 'status'], ['--output-format=stream-json'], ['--bg']):
            self.assertFalse(auto.interactive(arguments), arguments)
        self.assertTrue(auto.interactive(['--continue']))
        self.assertTrue(auto.interactive(['--model', 'opus']))

    def test_installer_is_idempotent_and_preserves_symlink_and_existing_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            original = '# keep user config\nexport SOMETHING=unchanged\n'
            real = home / 'real-zshrc'
            real.write_text(original)
            (home / '.zshrc').symlink_to(real)
            (home / '.bashrc').write_text(original)
            payload = json.dumps({'assets': bundle(ROOT / 'remote')})
            def install():
                with patch.object(Path, 'home', return_value=home), patch.object(sys, 'stdin', io.StringIO(payload)), patch.object(sys, 'stdout', io.StringIO()) as output:
                    exec(compile(INSTALL, 'installer', 'exec'), {})
                    return json.loads(output.getvalue())
            first = install()
            second = install()
            self.assertEqual(first['runtime'], second['runtime'])
            self.assertEqual(second['shell_configs_updated'], [])
            self.assertTrue((home / '.zshrc').is_symlink())
            self.assertTrue(real.read_text().startswith(original))
            self.assertEqual(len(first['backups']), 2)
            for backup in first['backups']:
                self.assertEqual(Path(backup).read_text(), original)
            for shell in ('bash', 'zsh'):
                subprocess.run([shell, '-n', first['entry']], check=True)
            self.assertTrue((Path(first['runtime']) / 'claude-vendor/pyte/__init__.py').exists())

    def test_prompt_gate_requires_caret_inside_composer_not_a_menu_or_history(self):
        screen = auto.pyte.Screen(80, 12)
        stream = auto.pyte.Stream(screen)
        stream.feed('\x1b[7;1H' + '─' * 80 + '\x1b[8;1H❯ hello\x1b[9;1H' + '─' * 80 + '\x1b[10;1H? for shortcuts\x1b[8;3H')
        self.assertEqual(auto.prompt_region(screen), (6, 8))
        stream.feed('\x1b[4;3H')
        self.assertIsNone(auto.prompt_region(screen), 'A dialog caret must not reuse the composer in the background')
        stream.feed('\x1b[8;3H\x1b[10;1H\x1b[2Kesc to interrupt\x1b[8;3H')
        self.assertIsNone(auto.prompt_region(screen), 'Working state must remain native')


if __name__ == '__main__':
    unittest.main()
