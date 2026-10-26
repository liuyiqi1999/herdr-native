#!/usr/bin/env python3
"""Verify Claude editor markers are composited in a real local Herdr pane."""
import argparse
import codecs
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import shlex
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
        pass

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--automatic', action='store_true')
args = parser.parse_args()
binary = Path(os.environ.get('HERDR_TEST_BIN', str(ROOT / 'herdr/target/release/herdr')))
session = 'hn-claude-' + uuid.uuid4().hex[:8]
with tempfile.TemporaryDirectory(prefix='hn-claude-pane-', dir='/tmp') as directory:
    temp = Path(directory)
    config = temp / 'herdr.toml'
    config.write_text(profiles()[1])
    draft = temp / 'draft.txt'
    draft.write_text('')
    env = {**os.environ, 'DISABLE_AUTOUPDATER': '1', 'XDG_CONFIG_HOME': str(temp / 'config'), 'XDG_STATE_HOME': str(temp / 'state'),
           'HERDR_CONFIG_PATH': str(config), 'HERDR_REMOTE_LAUNCHER': os.environ.get('HERDR_TEST_LAUNCHER', str(ROOT / 'scripts/remote.py')),
           'HERDR_NATIVE_DRAFT_DIR': str(temp / 'drafts'), 'TERM': 'xterm-256color'}
    for name in ('HERDR_ENV', 'HERDR_SOCKET_PATH', 'HERDR_CLIENT_SOCKET_PATH', 'HERDR_SESSION', 'HERDR_PANE_ID'):
        env.pop(name, None)
    with (temp / 'server.log').open('wb') as log:
        server = subprocess.Popen([str(binary), '--session', session, 'server'], env=env, stdout=log, stderr=log)
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
            if select.select([master], [], [], .05)[0]:
                data = os.read(master, 65536)
                stream.feed(decoder.decode(data))
                if b'\x1b[6n' in data: os.write(master, b'\x1b[1;1R')
                if b'\x1b[?u' in data: os.write(master, b'\x1b[?0u')
                if b'\x1b[c' in data: os.write(master, b'\x1b[?62;22c')
    def cli(*args):
        return subprocess.check_output([str(binary), '--session', session, *args], env=env, text=True, timeout=10)
    try:
        drain(3)
        created = json.loads(cli('workspace', 'create', '--label', 'Claude editor test', '--cwd', directory, '--focus'))['result']
        pane = created['root_pane']
        drain(1)
        command = ([sys.executable, str(ROOT / 'remote/claude-auto.py'), '--ready', str(temp / 'ready.json'), '--', '--safe-mode', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}'] if args.automatic
                   else [sys.executable, str(ROOT / 'remote/claude-editor.py'), 'edit', str(draft)])
        cli('pane', 'run', pane['pane_id'], shlex.join(command))
        if args.automatic:
            deadline = time.monotonic() + 25
            trusted = False
            while not list((temp / 'drafts').glob('*.json')):
                display = '\n'.join(screen.display)
                assert time.monotonic() < deadline, display
                if not trusted and 'trust this folder' in display:
                    if '❯ No, exit' in display:
                        os.write(master, b'\x1b[B')
                    os.write(master, b'\r')
                    trusted = True
                    drain(2)
                elif 'Claude Code v' in display:
                    os.write(master, b'x')
                drain(.7)
            for _ in range(30):
                os.write(master, b'\x7f')
            drain(.5)
            pid = json.loads((temp / 'ready.json').read_text())['claude_pid']
            os.kill(pid, signal.SIGSTOP)
            try:
                os.write(master, '\x1b[200~Herdr本地输入🙂\nsecond line\x1b[201~'.encode())
                drain(1)
                display = '\n'.join(screen.display)
                assert 'Herdr本地输入' in display and 'second line' in display, display
                assert 'Claude Code v' in display, 'Original Claude header was replaced'
            finally:
                os.kill(pid, signal.SIGCONT)
            fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 130, 0, 0))
            screen.resize(40, 130)
            os.kill(client.pid, signal.SIGWINCH)
            drain(3)
            assert 'Herdr本地输入' in '\n'.join(screen.display), 'Resize lost the draft'
            for _ in range(50):
                os.write(master, b'\x7f')
            os.write(master, b'/model')
            drain(.5)
            os.write(master, b'\r')
            drain(3)
            display = '\n'.join(screen.display)
            assert 'model' in display.lower() and ('Select model' in display or 'Select a model' in display), display
            print('PASS: Herdr automatic inline Claude input, original header preserved, Chinese/multiline with Claude SIGSTOP, one Enter opens original model menu')
        else:
            deadline = time.monotonic() + 15
            while 'save draft and return' not in '\n'.join(screen.display):
                assert time.monotonic() < deadline, '\n'.join(screen.display)
                drain(.2)
            os.write(master, '\x1b[200~Herdr本地输入🙂\nsecond line\x1b[201~'.encode())
            drain(1)
            assert 'Herdr本地输入' in '\n'.join(screen.display), '\n'.join(screen.display)
            assert draft.read_text() == '', 'Draft saved before confirmation'
            os.write(master, b'\x07')
            drain(1)
            assert draft.read_text() == 'Herdr本地输入🙂\nsecond line', draft.read_text()
            print('PASS: real Herdr Local pane, explicit Claude marker, local Chinese/multiline rendering, save returns to terminal')
    finally:
        if args.automatic:
            for _ in range(3):
                try:
                    os.write(master, b'\x03')
                    drain(.4)
                except OSError:
                    break
        (ROOT / '.local/claude-herdr-screen.txt').write_text('\n'.join(screen.display))
        subprocess.run([str(binary), 'session', 'stop', session], env=env, capture_output=True, timeout=10)
        subprocess.run([str(binary), 'session', 'delete', session], env=env, capture_output=True, timeout=10)
        os.close(master)
        for process in (client, server):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
