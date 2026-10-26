#!/usr/bin/env python3
"""Test the actual Cmd+D transport/config against a disposable Herdr session."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import termios
import time
import uuid

binary = '/Applications/Herdr Native.app/Contents/MacOS/herdr'
script = Path(__file__).with_name('move_tab_right.py')
session = 'move-tab-test-' + uuid.uuid4().hex[:8]
with tempfile.TemporaryDirectory(prefix='hm-', dir='/tmp') as tmp:
    config = Path(tmp) / 'config.toml'
    config.write_text('onboarding = false\n[keys]\nsplit_vertical = "prefix+v"\n'
                      '[[keys.command]]\nkey = "super+d"\ntype = "shell"\n'
                      'command = ' + json.dumps('/usr/bin/python3 ' + str(script)) + '\n'
                      '[terminal]\ndefault_shell = "/bin/sh"\nshell_mode = "non_login"\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
    env.update(HERDR_CONFIG_PATH=str(config), XDG_CONFIG_HOME=tmp+'/config', XDG_STATE_HOME=tmp+'/state', TERM='xterm-256color')
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 42, 140, 0, 0))
    client = subprocess.Popen([binary, '--session', session], env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    def drain(seconds=0.6):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if select.select([master], [], [], 0.05)[0]:
                data = os.read(master, 65536)
                if b'\x1b[6n' in data: os.write(master, b'\x1b[1;1R')
                if b'\x1b[?u' in data: os.write(master, b'\x1b[?0u')
                if b'\x1b[c' in data: os.write(master, b'\x1b[?62;22c')
    def cli(*args):
        result = subprocess.run([binary, '--session', session, *args], env=env, text=True, capture_output=True, timeout=10)
        if result.returncode:
            raise RuntimeError(result.stderr + result.stdout)
        return json.loads(result.stdout)['result']
    def press():
        drain()
        os.write(master, b'\x1b[100;9u')
        drain(2)
    try:
        drain(2)
        panes = cli('pane', 'list')['panes']
        workspace = panes[0]['workspace_id']
        original = {p['terminal_id'] for p in panes}
        press()
        panes = cli('pane', 'list')['panes']
        assert len(panes) == 2, panes
        assert original <= {p['terminal_id'] for p in panes}
        # A new tab, moved by the real keyboard shortcut, retains its terminal.
        cli('tab', 'create', '--workspace', workspace)
        drain()
        before = cli('pane', 'list')['panes']
        assert len(cli('tab', 'list')['tabs']) == 2
        press()
        after = cli('pane', 'list')['panes']
        assert len(cli('tab', 'list')['tabs']) == 1
        assert {p['terminal_id'] for p in before} == {p['terminal_id'] for p in after}
        # Move a tab with multiple panes, preserving all terminal identities.
        cli('tab', 'create', '--workspace', workspace)
        drain()
        current = next(p for p in cli('pane', 'list')['panes'] if p['focused'])
        cli('pane', 'split', current['pane_id'], '--direction', 'down')
        drain()
        before = cli('pane', 'list')['panes']
        press()
        after = cli('pane', 'list')['panes']
        assert len(cli('tab', 'list')['tabs']) == 1
        assert {p['terminal_id'] for p in before} == {p['terminal_id'] for p in after}
        print('PASS: Cmd+D single-tab split, existing-tab move, multi-pane move; terminal identities preserved')
    finally:
        subprocess.run([binary, 'session', 'stop', session], env=env, capture_output=True, timeout=10)
        subprocess.run([binary, 'session', 'delete', session], env=env, capture_output=True, timeout=10)
        os.close(master)
        if client.poll() is None: client.terminate()
        client.wait(timeout=5)
