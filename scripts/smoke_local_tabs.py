#!/usr/bin/env python3
"""Exercise local tab choice and OSC title projection in an isolated real PTY."""
import argparse
import codecs
import fcntl
import json
import os
from pathlib import Path
import pty
import select
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

class Screen(pyte.Screen):
    def report_device_status(self, *args, **kwargs):
        pass

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--claude-adapter', action='store_true', help='Test real nested-PTY adapter identity without agent reports')
args = parser.parse_args()
binary = Path(os.environ.get('HERDR_TEST_BIN', str(ROOT / '.local/herdr-local-tabs')))
session = 'local-tabs-test-' + uuid.uuid4().hex[:8]
with tempfile.TemporaryDirectory(prefix='hlt-', dir='/tmp') as directory:
    temp = Path(directory)
    config = temp / 'herdr.toml'
    config.write_text(profiles()[1] + '\n[terminal]\ndefault_shell = "/bin/sh"\nshell_mode = "non_login"\n')
    for agent, title in [('pi', 'π - Local pi title - repo'), ('claude', '✳ Local Claude title')]:
        script = temp / agent
        script.write_text('#!/bin/sh\n' +
            f'"{binary}" pane report-agent "$HERDR_PANE_ID" --source custom:tab-test --agent {agent} --state idle >/dev/null\n' +
            f"printf '\\033]0;{title}\\007'\n" +
            'while IFS= read -r line; do printf "\\033]0;%s\\007" "$line"; done\n')
        script.chmod(0o755)
        if agent == 'claude' and args.claude_adapter:
            import shlex
            child = temp / 'nested-child'
            child.write_text('#!/bin/sh\n' +
                             f"printf '\\033]0;{title}\\007'\n" +
                             'while IFS= read -r line; do printf "\\033]0;%s\\007" "$line"; done\n')
            child.chmod(0o755)
            script.write_text('#!/bin/sh\nexec ' + shlex.join([
                sys.executable, str(ROOT / 'remote/claude-auto.py'), '--binary', str(child), '--']) + '\n')
    env = {**os.environ, 'PATH': str(temp) + ':' + os.environ['PATH'],
           'XDG_CONFIG_HOME': str(temp / 'config'), 'XDG_STATE_HOME': str(temp / 'state'),
           'HERDR_CONFIG_PATH': str(config), 'TERM': 'xterm-256color'}
    for name in ('HERDR_ENV', 'HERDR_SOCKET_PATH', 'HERDR_CLIENT_SOCKET_PATH', 'HERDR_SESSION', 'HERDR_PANE_ID', 'HERDR_WORKSPACE_ID'):
        env.pop(name, None)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 42, 140, 0, 0))
    client = subprocess.Popen([str(binary), '--session', session], env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    screen = Screen(140, 42)
    stream = pyte.Stream(screen)
    decoder = codecs.getincrementaldecoder('utf8')('replace')

    def drain(seconds=0.3):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.05)[0]:
                data = os.read(master, 65536)
                stream.feed(decoder.decode(data))
                if b'\x1b[6n' in data: os.write(master, b'\x1b[1;1R')
                if b'\x1b[?u' in data: os.write(master, b'\x1b[?0u')
                if b'\x1b[c' in data: os.write(master, b'\x1b[?62;22c')

    def until(text):
        for _ in range(40):
            drain()
            if text in '\n'.join(screen.display): return
        raise AssertionError('Missing rendered text: ' + text)

    def cli(*args):
        result = subprocess.run([str(binary), '--session', session, *args], env=env, capture_output=True, text=True, check=True, timeout=10)
        return json.loads(result.stdout)['result'] if result.stdout.lstrip().startswith('{') else None

    try:
        until('spaces')
        for agent in ('pi', 'claude'):
            os.write(master, b'\x1b[116;9u')
            until('new tab')
            os.write(master, b'\r')
            until('start command:')
            if agent == 'claude':
                os.write(master, b'\x1b[B')
                drain()
            os.write(master, b'\r')
            until('Local pi title' if agent == 'pi' else 'Local Claude title')
            assert any(('Local pi title' if agent == 'pi' else 'Local Claude title') in row for row in screen.display[:4]), screen.display[:4]
            pane = next(p for p in cli('pane', 'list')['panes'] if p['focused'])
            if agent == 'claude' and args.claude_adapter:
                assert pane.get('agent') == 'claude', pane
                agents = cli('agent', 'list')['agents']
                assert any(a['pane_id'] == pane['pane_id'] and a.get('agent') == 'claude' for a in agents), agents
            updated = 'π - Updated pi title - repo' if agent == 'pi' else '✳ Updated Claude title'
            cli('pane', 'send-text', pane['pane_id'], updated)
            cli('pane', 'send-keys', pane['pane_id'], 'enter')
            until('Updated pi title' if agent == 'pi' else 'Updated Claude title')
            assert any(('Updated pi title' if agent == 'pi' else 'Updated Claude title') in row for row in screen.display[:4]), screen.display[:4]
        before = len(cli('pane', 'list')['panes'])
        os.write(master, b'\x1b[116;9u')
        until('new tab')
        os.write(master, b'\r')
        until('start command:')
        os.write(master, b'\x1b')
        drain(0.5)
        assert len(cli('pane', 'list')['panes']) == before
        print('PASS: real PTY pi/claude selection, local OSC titles and updates, cancel without tab creation')
    finally:
        (ROOT / '.local/local-tabs-screen.txt').write_text('\n'.join(screen.display))
        subprocess.run([str(binary), 'session', 'stop', session], env=env, capture_output=True, timeout=10)
        subprocess.run([str(binary), 'session', 'delete', session], env=env, capture_output=True, timeout=10)
        os.close(master)
        if client.poll() is None: client.terminate()
        try: client.wait(timeout=3)
        except subprocess.TimeoutExpired: client.kill(); client.wait()
