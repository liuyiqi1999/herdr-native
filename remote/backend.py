"""Sent over SSH by the local launcher. Only existing Herdr public APIs are used."""
import base64
import fcntl
import hashlib
import io
import zipfile
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time


def executable(name):
    candidate = shutil.which(name)
    if candidate:
        return candidate
    for folder in ('.local/bin', '.cargo/bin', '.npm-global/bin'):
        candidate = Path.home() / folder / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise RuntimeError(f'{name} is not installed or not on the remote PATH')


def private_directory(path):
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError(f'Expected a private user-owned directory: {path}')


def save(path, record):
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(record, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def ensure(request):
    agent = request['agent']
    if agent not in ('pi', 'codex', 'claude'):
        raise ValueError('Unsupported agent')
    if agent == 'claude' and not request.get('interactive'):
        raise ValueError('Claude Code requires the original interactive UI')
    cwd = request['cwd']
    if not os.path.isabs(cwd) or not Path(cwd).is_dir():
        raise ValueError('Remote project directory does not exist')
    cwd = os.path.realpath(cwd)
    herdr = executable('herdr')
    session = request.get('herdr_session', 'default')

    def call(*args):
        result = subprocess.run([herdr, '--session', session, *args], text=True, capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'Herdr request failed')
        return json.loads(result.stdout)['result'] if result.stdout.strip() else {}

    # These reads fail if the existing server is unavailable. Never launch or replace it.
    call('pane', 'list')
    base = Path.home() / '.cache/herdr-native'
    private_directory(base)
    identity = [session, cwd, agent, request.get('session')]
    interactive = agent in ('pi', 'claude') and request.get('interactive', False)
    if interactive: identity.append('claude-auto-v1' if request.get('claude_assets') else 'native-editor-v1')
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
    if request.get('fresh'):
        key += '-' + os.urandom(4).hex()
    directory = base / key
    private_directory(directory)
    # Keep socket paths short on macOS as well as Linux.
    state = directory / 'backend.json'
    with (directory / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        panes = call('pane', 'list')['panes']
        if state.exists():
            record = json.loads(state.read_text())
            live = next((p for p in panes if p['pane_id'] == record.get('pane') and
                         p.get('terminal_id') == record.get('terminal_id')), None)
            if live:
                if record.get('socket') and Path(record['socket']).exists():
                    if interactive: call('workspace', 'focus', record['workspace'])
                    return {**record, 'reused': True}
                raise RuntimeError(f"Backend startup is incomplete in {record['pane']}; inspect that pane before retrying. No duplicate was started.")
            if record.get('phase') == 'creating':
                raise RuntimeError('Previous workspace creation is uncertain; inspect remote Herdr before removing this pending record: ' + str(state))
        binary = executable(request.get('remote_bin') or agent)
        version = subprocess.run([binary, '--version'], capture_output=True, text=True, timeout=10)
        if version.returncode:
            raise RuntimeError(f'{agent} failed its version check')
        if agent == 'codex':
            help_result = subprocess.run([binary, 'app-server', '--help'], capture_output=True, text=True, timeout=10)
            if 'unix://' not in help_result.stdout:
                raise RuntimeError('Remote Codex does not support Unix app-server sockets; update Codex, not Herdr')
        elif agent == 'pi' and version.stdout.strip() != '1.3.0':
            raise RuntimeError('This Pi adapter requires remote Pi 1.3.0; detected ' + version.stdout.strip())
        socket_dir = tempfile.mkdtemp(prefix='hn-editor-' if interactive else 'hn-' + agent + '-', dir='/tmp')
        os.chmod(socket_dir, 0o700)
        socket_path = socket_dir + ('/editor.sock' if interactive else '/rpc.sock')
        # Journal before mutation: a lost API acknowledgment must never cause automatic duplication.
        save(state, {'phase': 'creating', 'socket': socket_path})
        workspace = call('workspace', 'create', '--label', (Path(cwd).name + ' · ' + agent) if interactive else agent + ' backend', '--cwd', cwd, '--focus' if interactive else '--no-focus')
        pane = workspace['root_pane']
        record = {'phase': 'starting', 'pane': pane['pane_id'], 'terminal_id': pane['terminal_id'],
                  'workspace': workspace['workspace']['workspace_id'], 'socket': socket_path,
                  'cwd': cwd, 'agent': agent, 'version': version.stdout.strip(), 'herdr_session': session}
        save(state, record)
        if interactive and agent == 'claude' and request.get('claude_assets'):
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(request['claude_assets']))) as archive:
                for entry in archive.infolist():
                    parts = Path(entry.filename).parts
                    if not parts or Path(entry.filename).is_absolute() or '..' in parts:
                        raise ValueError('Invalid Claude asset path')
                archive.extractall(str(directory))
            argv = ['exec', sys.executable, str(directory / 'claude-auto.py'), '--ready', socket_path, '--binary', binary]
            if request.get('test_no_approve'):
                argv[1:1] = ['env', 'DISABLE_AUTOUPDATER=1', 'HERDR_CLAUDE_DIAGNOSTICS=' + cwd + '/editor-diagnostics.jsonl']
                argv += ['--', '--safe-mode', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}']
        elif interactive and agent == 'claude':
            helper = directory / 'claude-editor.py'
            helper.write_bytes(base64.b64decode(request['extension']))
            helper.chmod(0o600)
            argv = ['exec', sys.executable, str(helper), 'launch', '--ready', socket_path, '--binary', binary]
        elif interactive:
            extension = directory / 'native-editor.ts'
            extension.write_bytes(base64.b64decode(request['extension']))
            extension.chmod(0o600)
            argv = ['exec', 'env', 'HERDR_PI_EDITOR_SOCKET=' + socket_path, binary, '-e', str(extension)]
            if request.get('session'): argv += ['--session', request['session']]
            if request.get('test_no_approve'): argv += ['--no-approve', '--offline']
        elif agent == 'pi':
            helper = directory / 'pi-host.py'
            helper.write_bytes(base64.b64decode(request['helper']))
            helper.chmod(0o600)
            argv = ['exec', sys.executable, str(helper), '--socket', socket_path, '--cwd', cwd, '--binary', binary]
            if request.get('session'): argv += ['--session', request['session']]
        else:
            argv = ['exec', binary, 'app-server', '--listen', 'unix://' + socket_path]
        call('pane', 'run', pane['pane_id'], ' '.join(shlex.quote(arg) for arg in argv))
        for _ in range(150):
            if Path(socket_path).exists():
                record['phase'] = 'ready'
                save(state, record)
                return {**record, 'reused': False}
            time.sleep(0.1)
        raise RuntimeError('Backend socket not ready; inspect remote pane ' + pane['pane_id'] + '. No automatic retry.')


if __name__ == '__main__':
    try:
        print(json.dumps(ensure(json.load(sys.stdin))))
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
