#!/usr/bin/env python3
"""Opt-in live SSH test; creates and removes only its own temporary project/backends. No prompts."""
import argparse
import fcntl
import os
import pty
import select
import signal
import struct
import tempfile
import termios
import time
import base64
import codecs
import json
from pathlib import Path
import shutil
import subprocess
import sys
import remote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'remote'))
sys.path.insert(0, str(ROOT / '.tools/python'))
import pyte
from auto_launch import choose_profile, codex_client


def codex_tui(backend, profile):
    binary = codex_client(backend['version'])
    with tempfile.TemporaryDirectory(prefix='hn-codex-', dir='/tmp') as temporary:
        socket_path = Path(temporary) / 'rpc.sock'
        tunnel = subprocess.Popen(['ssh', '-N', '-T', '-o', 'ExitOnForwardFailure=yes',
                                   '-L', f"{socket_path}:{backend['socket']}", profile['target']],
                                  stderr=subprocess.PIPE)
        child = None
        try:
            deadline = time.monotonic() + 15
            while not socket_path.exists():
                assert tunnel.poll() is None and time.monotonic() < deadline
                time.sleep(0.05)
            child, fd = pty.fork()
            if child == 0:
                os.environ['TERM'] = 'xterm-256color'
                os.execvp(binary, [binary, '-c', 'check_for_update_on_startup=false', '--remote', 'unix://' + str(socket_path), '--cd', backend['cwd']])
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 36, 110, 0, 0))
            screen = pyte.Screen(110, 36)
            stream = pyte.Stream(screen)
            decoder = codecs.getincrementaldecoder('utf8')('replace')
            def drain(seconds):
                until = time.monotonic() + seconds
                while time.monotonic() < until:
                    if select.select([fd], [], [], 0.05)[0]:
                        data = os.read(fd, 65536)
                        stream.feed(decoder.decode(data))
                        if b'\x1b[6n' in data: os.write(fd, b'\x1b[1;1R')
                        if b'\x1b[?u' in data: os.write(fd, b'\x1b[?0u')
                        if b'\x1b[c' in data: os.write(fd, b'\x1b[?62;22c')
            drain(5)
            ready_since = None
            for _ in range(30):
                text = '\n'.join(screen.display)
                handled = True
                if ('Do you trust the contents of this directory?' in text
                        and backend['cwd'] in text and backend['cwd'].startswith('/tmp/herdr-native-smoke-')):
                    os.write(fd, b'\r')  # Only this test's own empty disposable directory.
                elif '› 1. Update now' in text:
                    os.write(fd, b'\x1b[B')
                elif '› 2. Skip' in text:
                    os.write(fd, b'\r')
                elif '› 1. Review hooks' in text:
                    os.write(fd, b'\x1b[B\x1b[B')
                elif '› 3. Continue without trusting' in text:
                    os.write(fd, b'\r')  # Only confirm the explicitly selected no-trust option.
                elif 'Continue without trusting' in text:
                    os.write(fd, b'\x1b')
                elif 'OpenAI Codex' in text or '? for shortcuts' in text:
                    handled = False
                    if ready_since is None: ready_since = time.monotonic()
                    if time.monotonic() - ready_since > 3: break
                if handled: ready_since = None
                drain(1)
            os.write(fd, b'remote-local-edit-only')
            drain(1)
            text = '\n'.join(screen.display)
            (ROOT / '.local/codex-live-remote-screen.txt').write_text(text)
            assert 'remote-local-edit-only' in text, 'Codex remote editor did not become ready; inspect .local/codex-live-remote-screen.txt'
            print('PASS: original local Codex TUI connected to real SSH app-server and rendered typing; no prompt')
        finally:
            if child:
                try: os.killpg(child, signal.SIGTERM)
                except ProcessLookupError: pass
                os.close(fd)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if os.waitpid(child, os.WNOHANG)[0]: break
                    time.sleep(0.05)
                else:
                    os.killpg(child, signal.SIGKILL)
                    os.waitpid(child, 0)
            tunnel.terminate()
            try: tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill(); tunnel.wait()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--machine')
args = parser.parse_args()
herdr = shutil.which('herdr')


def cli(*args):
    result = subprocess.run([herdr, *args], capture_output=True, text=True, timeout=30)
    if result.returncode: raise RuntimeError(result.stderr)
    return json.loads(result.stdout) if result.stdout.strip() else {}


profile = choose_profile(cli('machine', 'list', '--json'), args.machine)


def ssh(source, payload=None):
    result = subprocess.run(remote.ssh_command(profile['target'], ['python3', '-c', source]),
                            input=json.dumps(payload) if payload is not None else '',
                            capture_output=True, text=True, timeout=75)
    if result.returncode: raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


before = cli('--machine', profile['id'], 'pane', 'list')['result']['panes']
cwd = ssh("import tempfile,json; print(json.dumps(tempfile.mkdtemp(prefix='herdr-native-smoke-'))) ")
backends = []
try:
    for agent in ('pi', 'codex'):
        request = {'agent': agent, 'cwd': cwd, 'herdr_session': profile['session'],
                   'helper': base64.b64encode((ROOT / 'remote/pi-host.py').read_bytes()).decode()}
        first = ssh((ROOT / 'remote/backend.py').read_text(), request)
        backends.append(first)
        second = ssh((ROOT / 'remote/backend.py').read_text(), request)
        assert first['pane'] == second['pane'] and second['reused'] and not first['reused']
        if agent == 'pi':
            source = f"""import {{ Rpc, piCommand }} from {json.dumps((ROOT / 'remote/rpc.mjs').as_uri())};
const command = piCommand({json.dumps(dict(host=profile['target'], cwd=cwd, socket=first['socket']))});
const rpc = new Rpc(command.command, command.args, command.options);
try {{
 await rpc.send('get_state'); await rpc.send('get_messages'); await rpc.send('get_commands');
 console.log('PASS: real remote Pi state/history/commands, no prompt');
}} finally {{ await rpc.close(); }}
"""
            subprocess.run(['node', '--input-type=module', '-e', source], check=True, timeout=25)
        else:
            codex_tui(first, profile)
        print(f'PASS: {agent} auto-start and reuse on saved remote session; {first["version"]}', flush=True)
finally:
    # Discover only panes in the unique project this test created, even after a lost start acknowledgment.
    after = cli('--machine', profile['id'], 'pane', 'list')['result']['panes']
    for workspace in {p['workspace_id'] for p in after if p.get('cwd') == cwd}:
        cli('--machine', profile['id'], 'workspace', 'close', workspace)
    remaining = cli('--machine', profile['id'], 'pane', 'list')['result']['panes']
    for old in before:
        current = next(p for p in remaining if p['pane_id'] == old['pane_id'])
        assert current['terminal_id'] == old['terminal_id']
        assert current['focused'] == old['focused']
    ssh(r"""import json,sys,pathlib,shutil,hashlib,re,tempfile,os,stat
r=json.load(sys.stdin)
config=pathlib.Path.home()/'.codex/config.toml'
if config.exists():
 original=config.read_text()
 header='[projects.'+json.dumps(r['cwd'])+']'
 match=re.search(r'(?m)^'+re.escape(header)+r'\n([^\[]*)',original)
 if match and match.group(1).strip()=='trust_level = "trusted"':
  updated=original[:match.start()]+original[match.end():]
  fd,tmp=tempfile.mkstemp(dir=str(config.parent))
  with os.fdopen(fd,'w') as file: file.write(updated)
  os.chmod(tmp,stat.S_IMODE(config.stat().st_mode))
  if config.read_text()==original: os.replace(tmp,str(config))
  else: os.unlink(tmp)
for agent in ('pi','codex'):
 key=hashlib.sha256(json.dumps([r['session'],r['cwd'],agent,None]).encode()).hexdigest()[:24]
 directory=pathlib.Path.home()/'.cache/herdr-native'/key
 state=directory/'backend.json'
 if state.exists():
  record=json.loads(state.read_text()); socket_dir=pathlib.Path(record['socket']).parent
  if str(socket_dir).startswith('/tmp/hn-'): shutil.rmtree(socket_dir,ignore_errors=True)
 shutil.rmtree(directory,ignore_errors=True)
if r['cwd'].startswith('/tmp/herdr-native-smoke-'): shutil.rmtree(r['cwd'])
print(json.dumps(True))
""", {'session': profile['session'], 'cwd': cwd})
    print('PASS: existing remote panes and focus preserved; disposable backends removed')
