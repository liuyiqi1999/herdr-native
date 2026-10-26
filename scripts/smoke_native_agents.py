#!/usr/bin/env python3
"""Real TUI menu/shortcut test, with an older local server and a harmless fake connector."""
import codecs
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import uuid
from build import profiles

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.tools/python'))
import pyte

class TerminalScreen(pyte.Screen):
    def report_device_status(self, *args, **kwargs):
        pass  # PTY capability replies are written explicitly by drain().

binary = Path(os.environ.get('HERDR_TEST_BIN', str(ROOT / 'herdr/target/release/herdr')))
remote_resources = Path(os.environ.get('HERDR_TEST_REMOTE_RESOURCES', str(ROOT / 'remote')))
server_binary = shutil.which('herdr')
session = 'native-agent-test-' + uuid.uuid4().hex[:8]
with tempfile.TemporaryDirectory(prefix='hna-', dir='/tmp') as directory:
    temp = Path(directory)
    config = temp / 'new.toml'
    old_config = temp / 'old.toml'
    config.write_text(profiles()[1])
    old_config.write_text(profiles()[1].split('[[keys.command]]')[0])
    launcher = temp / 'herdr-remote'
    capture = temp / 'calls.jsonl'
    launcher.write_text(f'''#!{sys.executable}
import sys, json, os, traceback
sys.dont_write_bytecode = True
sys.path.insert(0, {str(remote_resources)!r})
if sys.argv[1] == 'open':
 import auto_launch
 auto_launch.choose_profile = lambda *args: dict(id='fixture', label='Fixture', target='unused', session='default')
 try:
  auto_launch.open_view(sys.argv[2:] + ['--cwd', '/remote/project'], None)
 except Exception:
  with open({str(temp / 'error.log')!r}, 'w') as file: traceback.print_exc(file=file)
  raise
else:
 with open({str(capture)!r}, 'a') as file:
  file.write(json.dumps(dict(args=sys.argv[1:], pane=os.environ.get('HERDR_PANE_ID'))) + '\\n')
 print('NATIVE_REMOTE_AGENT_TEST_READY', flush=True)
''')
    launcher.chmod(0o755)
    env = {**os.environ, 'XDG_CONFIG_HOME': str(temp / 'config'), 'XDG_STATE_HOME': str(temp / 'state'),
           'HERDR_CONFIG_PATH': str(config), 'HERDR_REMOTE_LAUNCHER': str(launcher), 'HERDR_NATIVE_STATE_DIR': str(temp / 'native'), 'TERM': 'xterm-256color'}
    for name in ('HERDR_SOCKET_PATH', 'HERDR_CLIENT_SOCKET_PATH', 'HERDR_SESSION', 'HERDR_PANE_ID', 'HERDR_WORKSPACE_ID'):
        env.pop(name, None)
    with (temp / 'server.log').open('wb') as log:
        server = subprocess.Popen([server_binary, '--session', session, 'server'], env={**env, 'HERDR_CONFIG_PATH': str(old_config)}, stdout=log, stderr=log)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 42, 140, 0, 0))
    client = subprocess.Popen([str(binary), '--session', session], env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    screen = TerminalScreen(140, 42)
    stream = pyte.Stream(screen)
    decoder = codecs.getincrementaldecoder('utf8')('replace')

    def drain(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                data = os.read(master, 65536)
                stream.feed(decoder.decode(data))
                if b'\x1b[6n' in data: os.write(master, b'\x1b[1;1R')
                if b'\x1b[?u' in data: os.write(master, b'\x1b[?0u')
                if b'\x1b[c' in data: os.write(master, b'\x1b[?62;22c')

    try:
        for _ in range(30):
            drain(1)
            if 'spaces' in '\n'.join(screen.display): break
        assert 'spaces' in '\n'.join(screen.display), 'Herdr client did not become ready'
        for agent in ('pi', 'codex'):
            os.write(master, b'\x1b[97;10u')  # Cmd+Shift+A
            drain(0.4)
            assert 'new remote pi' in '\n'.join(screen.display), 'Native menu was not opened'
            if agent == 'codex':
                os.write(master, b'\x1b[B')
                drain(0.2)
            os.write(master, b'\r')
            for _ in range(15):
                drain(0.4)
                if capture.exists():
                    current = [json.loads(line) for line in capture.read_text().splitlines()]
                    if current and current[-1]['args'][2] == agent: break
            calls = [json.loads(line) for line in capture.read_text().splitlines()]
            assert calls[-1]['args'] == ['auto', '--agent', agent, '--machine', 'fixture', '--cwd', '/remote/project'], calls
        assert len(calls) == 2 and calls[0]['pane'] != calls[1]['pane']
        os.write(master, b'\x1b[97;10u')
        drain(0.4)
        os.write(master, b'\r')  # Reopen pi: focus the existing view, do not type another launch command.
        drain(2)
        assert len(capture.read_text().splitlines()) == 2
        assert not (temp / 'error.log').exists(), (temp / 'error.log').read_text() if (temp / 'error.log').exists() else ''
        result = subprocess.run([str(binary), '--session', session, 'pane', 'list'], env=env, capture_output=True, text=True, check=True)
        focused = next(p for p in json.loads(result.stdout)['result']['panes'] if p['focused'])
        assert focused['pane_id'] == calls[0]['pane'], 'Reopen did not focus the existing pi view'
        assert 'NATIVE_REMOTE_AGENT_TEST_READY' in '\n'.join(screen.display)
        print('PASS: Cmd+Shift+A → pi/Codex menu → new local pane → automatic connector; unchanged older server')
    finally:
        (ROOT / '.local').mkdir(exist_ok=True)
        if (temp / 'error.log').exists():
            print((temp / 'error.log').read_text())
        (ROOT / '.local/native-agent-server.log').write_text((temp / 'server.log').read_text())
        (ROOT / '.local/native-agent-menu-screen.txt').write_text('\n'.join(screen.display))
        subprocess.run([str(binary), 'session', 'stop', session], env=env, capture_output=True, timeout=10)
        subprocess.run([str(binary), 'session', 'delete', session], env=env, capture_output=True, timeout=10)
        os.close(master)
        for process in (client, server):
            if process.poll() is None: process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
