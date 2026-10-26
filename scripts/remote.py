#!/usr/bin/env python3
"""Run local agent TUIs against remote backends, inside any local Herdr pane."""
import argparse
import base64
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

# Installed application resources are sealed by codesign; never cache imports there.
sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'Resources/remote').is_dir():
    ROOT = ROOT / 'Resources'


def host_value(value):
    if not value or value.startswith('-') or any(c.isspace() for c in value):
        raise argparse.ArgumentTypeError('Use an SSH config alias or user@host, without whitespace')
    return value


def socket_value(value):
    if not value.startswith('/') or any(c in value for c in ':\n\r'):
        raise argparse.ArgumentTypeError('The remote socket must be an absolute path without colon/newline')
    return value


def ssh_command(host, args):
    return ['ssh', '-T', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', host, shlex.join(args)]


# This runs only with --start. The persistent process belongs to the existing remote Herdr server.
BOOTSTRAP = r'''
import base64, json, os, pathlib, shlex, shutil, subprocess, sys, tempfile, time
agent, cwd, binary, helper, session = sys.argv[1:]
herdr = shutil.which('herdr')
if not herdr:
    herdr = next((str(p) for p in [pathlib.Path.home()/'.local/bin/herdr', pathlib.Path.home()/'.cargo/bin/herdr'] if p.is_file() and os.access(p, os.X_OK)), None)
if not herdr: raise SystemExit('Remote herdr executable not found in PATH, ~/.local/bin or ~/.cargo/bin')
if not os.path.isabs(cwd):
    raise SystemExit('remote cwd must be absolute')
def call(*args):
    result = subprocess.run([herdr, *args], text=True, capture_output=True, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return json.loads(result.stdout)
directory = tempfile.mkdtemp(prefix='herdr-native-' + agent + '-', dir='/tmp')
os.chmod(directory, 0o700)
workspace = call('workspace', 'create', '--label', agent + ' backend', '--cwd', cwd, '--no-focus')['result']
pane = workspace['root_pane']['pane_id']
socket = directory + '/server.sock'
if agent == 'pi':
    helper_path = pathlib.Path(directory) / 'pi-host.py'
    helper_path.write_bytes(base64.b64decode(helper))
    os.chmod(helper_path, 0o600)
    argv = ['exec', 'python3', str(helper_path), '--socket', socket, '--cwd', cwd, '--binary', binary]
    if session: argv += ['--session', session]
else:
    argv = ['exec', binary, 'app-server', '--listen', 'unix://' + socket]
command = ' '.join(shlex.quote(arg) for arg in argv)
try:
    call('pane', 'run', pane, command)
    for _ in range(100):
        if pathlib.Path(socket).exists():
            print(json.dumps({'socket': socket, 'pane': pane, 'workspace': workspace['workspace']['workspace_id']}))
            break
        time.sleep(0.1)
    else:
        raise RuntimeError('app-server socket did not appear; inspect Herdr pane ' + pane)
except Exception:
    print('Created backend pane: ' + pane + '; inspect it before retrying --start.', file=sys.stderr)
    raise
'''


def start_backend(args):
    helper = base64.b64encode((ROOT / 'remote/pi-host.py').read_bytes()).decode() if args.agent == 'pi' else ''
    try:
        result = subprocess.run(ssh_command(args.host, ['python3', '-c', BOOTSTRAP, args.agent, args.cwd,
                                                      args.remote_bin, helper, getattr(args, 'session', None) or '']),
                                text=True, capture_output=True, timeout=45)
    except subprocess.TimeoutExpired:
        raise SystemExit('Remote startup timed out. A backend may already exist: inspect remote Herdr before retrying --start.')
    if result.returncode:
        raise SystemExit(result.stderr)
    info = json.loads(result.stdout)
    remote_socket = socket_value(info['socket'])
    reconnect = ['herdr-remote', args.agent, args.host, '--socket', remote_socket]
    if args.agent == 'pi': reconnect += ['--cwd', args.cwd]
    print(f"Remote Herdr pane: {info['pane']}\nReconnect with: {shlex.join(reconnect)}", flush=True)
    return remote_socket


def codex(args):
    binary = getattr(args, 'binary', None) or os.environ.get('HERDR_CODEX_BIN') or 'codex'
    if not shutil.which(binary):
        raise SystemExit('Install local Codex with --remote support first')
    help_text = subprocess.check_output([binary, '--help'], text=True)
    if '--remote ' not in help_text:
        raise SystemExit('This Codex version has no --remote support; update it first')
    remote_socket = args.socket
    if args.start:
        remote_socket = start_backend(args)
    with tempfile.TemporaryDirectory(prefix='herdr-codex-', dir='/tmp') as temporary:
        local_socket = Path(temporary) / 'server.sock'
        tunnel = subprocess.Popen(['ssh', '-N', '-T', '-o', 'ExitOnForwardFailure=yes',
                                   '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
                                   '-L', f'{local_socket}:{remote_socket}', args.host])
        try:
            deadline = time.monotonic() + 30
            while not local_socket.exists():
                if tunnel.poll() is not None:
                    raise SystemExit('SSH forwarding failed')
                if time.monotonic() >= deadline:
                    raise SystemExit('SSH forwarding did not become ready within 30 seconds')
                time.sleep(0.1)
            client = [binary, '-c', 'check_for_update_on_startup=false', '--remote', f'unix://{local_socket}']
            if getattr(args, 'cwd', None):
                client += ['--cd', args.cwd]
            return subprocess.call(client)
        finally:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'claude':
        # Local Claude uses the same supported external-editor integration.
        os.execv(sys.executable, [sys.executable, str(ROOT / 'remote/claude-auto.py'), '--', *sys.argv[2:]])
    if len(sys.argv) > 1 and sys.argv[1] == 'editor':
        sys.path.insert(0, str(ROOT / 'remote'))
        from auto_launch import local_tools
        local_tools()
        os.execvp('node', ['node', str(ROOT / 'remote/native-input.mjs'), *sys.argv[2:]])
    if len(sys.argv) > 1 and sys.argv[1] in ('auto', 'open'):
        sys.path.insert(0, str(ROOT / 'remote'))
        from auto_launch import run, open_view
        try:
            if sys.argv[1] == 'open':
                return open_view(sys.argv[2:], sys.modules[__name__])
            return run(sys.argv[2:], sys.modules[__name__])
        except KeyboardInterrupt:
            print('\n已取消连接。远端已启动的后端会保留。', file=sys.stderr)
            return 130
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
            print(f'连接未完成：{error}', file=sys.stderr)
            if sys.argv[1] == 'open' and not os.environ.get('HERDR_NATIVE_NO_ALERT'):
                subprocess.run(['osascript', '-e', 'on run argv\ndisplay alert "无法打开远端 agent" message (item 1 of argv) as critical\nend run', str(error)], timeout=300)
            return 1
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='agent', required=True)
    c = commands.add_parser('codex', help='Original Codex TUI + remote app-server')
    c.add_argument('host', type=host_value)
    group = c.add_mutually_exclusive_group(required=True)
    group.add_argument('--socket', type=socket_value, help='Existing remote app-server Unix socket')
    group.add_argument('--start', action='store_true', help='Create a backend pane using the existing remote Herdr server')
    c.add_argument('--cwd', help='Absolute remote project directory (required for --start)')
    c.add_argument('--remote-bin', default='codex')
    p = commands.add_parser('pi', help='Pi editor/message components + remote Pi RPC')
    p.add_argument('host', type=host_value)
    p.add_argument('--cwd', required=True, help='Absolute remote project directory')
    p.add_argument('--remote-bin', default='pi')
    p.add_argument('--session', help='Remote Pi session file/id to resume')
    pg = p.add_mutually_exclusive_group()
    pg.add_argument('--start', action='store_true', help='Create a persistent Pi RPC backend in remote Herdr')
    pg.add_argument('--socket', type=socket_value, help='Reconnect to an existing Pi RPC backend')
    args = parser.parse_args()
    if getattr(args, 'start', False) and (not args.cwd or not args.cwd.startswith('/')):
        parser.error('--start requires --cwd with an absolute remote path')
    if args.agent == 'codex':
        return codex(args)
    if not args.cwd.startswith('/'):
        parser.error('--cwd must be an absolute remote path')
    if args.session and args.socket:
        parser.error('--session selects a new backend; it cannot replace a running --socket session')
    remote_socket = start_backend(args) if args.start else args.socket
    command = ['node', str(ROOT / 'remote/pi-ui.mjs'), '--host', args.host, '--cwd', args.cwd,
               '--remote-bin', args.remote_bin]
    if remote_socket:
        command += ['--socket', remote_socket]
    elif args.session:
        command += ['--session', args.session]
    return subprocess.call(command)


if __name__ == '__main__':
    raise SystemExit(main())
