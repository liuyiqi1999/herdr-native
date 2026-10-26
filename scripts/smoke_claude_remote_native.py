#!/usr/bin/env python3
"""Real SSH + official remote Herdr + automatic Claude compositor; no model prompt."""
import argparse
import codecs
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import uuid
from build import profiles
from remote import host_value, ssh_command

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.tools/python'))
sys.path.insert(0, str(ROOT / 'remote'))
import pyte
from claude_package import bundle

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host', type=host_value, required=True)
args = parser.parse_args()
session = 'hn-claude-live-' + uuid.uuid4().hex[:8]
binary = Path(os.environ.get('HERDR_TEST_BIN', str(ROOT / 'herdr/target/release/herdr')))
child = server = backend = None
paused = False
pid = None

def ssh(source, payload=None):
    result = subprocess.run(ssh_command(args.host, ['python3', '-c', source]),
                            input=json.dumps(payload) if payload is not None else '', capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout) if result.stdout.strip() else None

cwd = ssh("import tempfile,json; print(json.dumps(tempfile.mkdtemp(prefix='hn-claude-live-')))")
print('Isolated session:', session, 'remote directory:', cwd, flush=True)
try:
    ssh('''import subprocess,json,sys,pathlib,time
r=json.load(sys.stdin)
with open(r['cwd']+'/server.log','wb') as log:
 subprocess.Popen([str(pathlib.Path.home()/'.local/bin/herdr'),'--session',r['session'],'server'],cwd=r['cwd'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
time.sleep(1)
''', {'cwd': cwd, 'session': session})
    backend = ssh((ROOT / 'remote/backend.py').read_text(), {
        'agent': 'claude', 'interactive': True, 'cwd': cwd, 'herdr_session': session,
        'claude_assets': bundle(ROOT / 'remote'), 'test_no_approve': True})
    print('Remote Claude started; attaching the desktop client', flush=True)
    pid = ssh('import json,sys; print(json.dumps(json.load(open(json.load(sys.stdin)))))', backend['socket'])['claude_pid']
    with tempfile.TemporaryDirectory(prefix='hn-claude-live-', dir='/tmp') as directory:
        temp = Path(directory)
        config = temp / 'herdr.toml'
        config.write_text(profiles()[1])
        catalog = temp / 'state/herdr/client'
        catalog.mkdir(parents=True)
        profile = uuid.uuid4().hex
        (catalog / 'endpoints.json').write_text(json.dumps({'version': 1, 'selected_profile': profile, 'ssh': [
            {'id': profile, 'label': 'REMOTE CLAUDE TEST', 'target': args.host, 'session': session, 'enabled': True}]}))
        env = {**os.environ, 'XDG_CONFIG_HOME': str(temp / 'config'), 'XDG_STATE_HOME': str(temp / 'state'),
               'HERDR_CONFIG_PATH': str(config), 'HERDR_REMOTE_LAUNCHER': os.environ.get('HERDR_TEST_LAUNCHER', str(ROOT / 'scripts/remote.py')),
               'HERDR_NATIVE_DRAFT_DIR': str(temp / 'drafts'), 'TERM': 'xterm-256color'}
        for name in ('HERDR_ENV', 'HERDR_SOCKET_PATH', 'HERDR_CLIENT_SOCKET_PATH', 'HERDR_SESSION', 'HERDR_PANE_ID'):
            env.pop(name, None)
        with (temp / 'server.log').open('wb') as log:
            server = subprocess.Popen([str(Path.home() / '.local/bin/herdr'), '--session', session, 'server'], env=env, stdout=log, stderr=log)
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 42, 150, 0, 0))
        child = subprocess.Popen([str(binary), '--session', session], env=env, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        class Screen(pyte.Screen):
            def report_device_status(self, *a, **kw):
                pass
        screen = Screen(150, 42)
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
        try:
            deadline = time.monotonic() + 30
            trusted = False
            captured = False
            while not list((temp / 'drafts').glob('*.json')):
                display = '\n'.join(screen.display)
                assert time.monotonic() < deadline, display
                if not trusted and 'trust this folder' in display:
                    if '❯ No, exit' in display: os.write(master, b'\x1b[B')
                    os.write(master, b'\r')
                    trusted = True
                    drain(2)
                elif 'Claude Code v' in display:
                    if not captured:
                        drain(1)
                        capture = ssh('''import json,sys,subprocess,pathlib
r=json.load(sys.stdin)
p=subprocess.run([str(pathlib.Path.home()/'.local/bin/herdr'),'--session',r['session'],'pane','read',r['pane'],'--source','detection','--format','ansi'],capture_output=True,text=True)
print(json.dumps({'out':p.stdout,'error':p.stderr}))
''', {'session': session, 'pane': backend['pane']})
                        (ROOT / '.local/claude-remote-capture.json').write_text(json.dumps(capture))
                        captured = True
                    os.write(master, b'x')
                drain(.8)
            print('Native editor is ready; checking paused remote input', flush=True)
            for _ in range(40): os.write(master, b'\x7f')
            drain(.5)
            ssh('import json,sys,os,signal; os.kill(json.load(sys.stdin),signal.SIGSTOP)', pid)
            paused = True
            os.write(master, '\x1b[200~远端Claude本地输入🙂\nsecond line\x1b[201~'.encode())
            deadline = time.monotonic() + 8
            while True:
                drain(.2)
                display = '\n'.join(screen.display)
                if '远端Claude本地输入' in display and 'second line' in display:
                    break
                assert time.monotonic() < deadline, display
            assert 'REMOTE CLAUDE TEST' in display and 'Claude Code v' in display, display
            ssh('import json,sys,os,signal; os.kill(json.load(sys.stdin),signal.SIGCONT)', pid)
            paused = False
            for _ in range(60): os.write(master, b'\x7f')
            os.write(master, b'/model')
            drain(.5)
            os.write(master, b'\r')
            drain(3)
            display = '\n'.join(screen.display)
            assert 'model' in display.lower() and ('Select model' in display or 'Select a model' in display), display
            print('PASS: actual SSH/remote workspace, unchanged server, automatic Claude inline editing, Unicode with remote Claude SIGSTOP, one Enter opens original model menu; no model prompt')
        finally:
            (ROOT / '.local/claude-remote-native-screen.txt').write_text('\n'.join(screen.display))
            if not paused:
                for _ in range(3):
                    os.write(master, b'\x03')
                    drain(.4)
            subprocess.run([str(binary), 'session', 'stop', session], env=env, capture_output=True, timeout=10)
            subprocess.run([str(binary), 'session', 'delete', session], env=env, capture_output=True, timeout=10)
            os.close(master)
finally:
    try:
        diagnostics = ssh('import json,sys,pathlib; p=pathlib.Path(json.load(sys.stdin))/"editor-diagnostics.jsonl"; print(json.dumps(p.read_text() if p.exists() else ""))', cwd)
        (ROOT / '.local/claude-remote-debug.jsonl').write_text(diagnostics)
    except Exception:
        pass
    if paused and pid:
        ssh('import json,sys,os,signal; os.kill(json.load(sys.stdin),signal.SIGCONT)', pid)
    for process in (child, server):
        if process:
            if process.poll() is None: process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
    ssh('''import json,sys,subprocess,pathlib,shutil,hashlib
r=json.load(sys.stdin)
subprocess.run([str(pathlib.Path.home()/'.local/bin/herdr'),'--session',r['session'],'server','stop'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
identity=[r['session'],r['cwd'],'claude',None,'claude-auto-v1']
key=hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
shutil.rmtree(str(pathlib.Path.home()/'.cache/herdr-native'/key),ignore_errors=True)
shutil.rmtree(r['cwd'],ignore_errors=True)
if r.get('socket'): shutil.rmtree(str(pathlib.Path(r['socket']).parent),ignore_errors=True)
''', {'session': session, 'cwd': cwd, 'socket': backend['socket'] if backend else None})
