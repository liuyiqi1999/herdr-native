"""Automatic connection flow launched by Herdr's native client menu."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import re
import shlex
import socket
import uuid
import subprocess
import sys


def choose_profile(profiles, requested=None):
    profiles = [p for p in profiles if p.get('enabled')]
    if requested:
        found = next((p for p in profiles if p['id'] == requested), None)
        if not found:
            raise RuntimeError('The selected machine was removed or disabled. Choose it again in Herdr.')
        return found
    selected = next((p for p in profiles if p.get('selected')), None)
    if selected:
        return selected
    if len(profiles) == 1:
        return profiles[0]
    if not profiles:
        raise RuntimeError('No saved SSH machines. Add a machine in Herdr first.')
    if not sys.stdin.isatty():
        raise RuntimeError('Select a saved remote machine in the Herdr sidebar first.')
    print('选择远端机器：')
    for index, profile in enumerate(profiles, 1):
        print(f"  {index}. {profile['label']}")
    value = input('编号（回车取消）：').strip()
    if not value:
        raise RuntimeError('已取消；远端未改动。')
    try:
        index = int(value)
        if not 1 <= index <= len(profiles): raise ValueError()
        return profiles[index - 1]
    except ValueError:
        raise RuntimeError('Invalid machine selection')


def choose_cwd(panes, explicit=None):
    if explicit:
        if not explicit.startswith('/'):
            raise RuntimeError('Remote project path must be absolute')
        return explicit
    focused = next((p for p in panes if p.get('focused')), None)
    if focused:
        path = focused.get('foreground_cwd') or focused.get('cwd')
        if path and path.startswith('/'):
            return path
    paths = list(dict.fromkeys(p.get('foreground_cwd') or p.get('cwd') for p in panes))
    paths = [p for p in paths if p and p.startswith('/')]
    if len(paths) == 1:
        return paths[0]
    raise RuntimeError('Select a project pane on the remote machine, then choose New remote agent again.')


def local_api(method, params):
    """Use the official JSON socket for exact pane focus (CLI focus is directional)."""
    path = os.environ.get('HERDR_SOCKET_PATH')
    if not path:
        raise RuntimeError('The local Herdr API socket is unavailable')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(15)
        connection.connect(path)
        request_id = 'native-' + uuid.uuid4().hex
        connection.sendall((json.dumps({'id': request_id, 'method': method, 'params': params}) + '\n').encode())
        with connection.makefile('rb') as reader:
            response = json.loads(reader.readline(1024 * 1024))
        if response.get('id') != request_id or 'error' in response:
            raise RuntimeError(response.get('error', {}).get('message', 'Invalid local API response'))
        return response['result']


def state_directory():
    return Path(os.environ.get('HERDR_NATIVE_STATE_DIR', str(Path.home() / 'Library/Application Support/Herdr Native')))


def codex_client(remote_version):
    match = re.fullmatch(r'codex-cli (\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)', remote_version)
    if not match:
        raise RuntimeError('Unrecognized remote Codex version: ' + remote_version)
    version = match.group(1)
    def matches(binary):
        if not binary or not Path(binary).is_file(): return False
        result = subprocess.run([str(binary), '--version'], capture_output=True, text=True, timeout=30)
        return result.returncode == 0 and result.stdout.strip() == remote_version
    installed = os.environ.get('HERDR_CODEX_BIN') or shutil.which('codex')
    if matches(installed): return str(installed)
    prefix = state_directory() / 'codex' / version
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    binary = prefix / 'node_modules/.bin/codex'
    with (prefix / 'install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not matches(binary):
            if not shutil.which('npm'):
                raise RuntimeError(f'Local Codex {version} is needed; npm is not installed. The remote backend was retained.')
            print(f'正在准备本地 Codex {version}（保留现有全局安装）…', flush=True)
            result = subprocess.run(['npm', 'install', '--prefix', str(prefix), '--ignore-scripts', '--no-audit',
                                     '--no-fund', '@openai/codex@' + version], capture_output=True, text=True, timeout=180)
            if result.returncode or not matches(binary):
                raise RuntimeError(f'Could not prepare local Codex {version}. Check npm registry connectivity; the remote backend was retained.')
    return str(binary)


def open_view(argv, connector):
    """Run outside the TUI. Create/reuse a Local view through the public API, never terminal input injection."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent', choices=('pi', 'codex', 'claude'), required=True)
    parser.add_argument('--machine')
    parser.add_argument('--cwd')
    args = parser.parse_args(argv)
    if args.agent in ('pi', 'claude') and connector is not None:
        return open_original_pi(args, connector)
    local_tools()
    herdr = os.environ['HERDR_LOCAL_CLI']
    launcher = str(Path(sys.argv[0]).resolve())

    def cli(*parts):
        result = subprocess.run([herdr, *parts], capture_output=True, text=True, timeout=30)
        if result.returncode: raise RuntimeError(result.stderr.strip() or 'Local Herdr request failed')
        return json.loads(result.stdout) if result.stdout.strip() else {}

    profile = choose_profile(cli('machine', 'list', '--json'), args.machine)
    panes = [] if args.cwd else cli('--machine', profile['id'], 'pane', 'list')['result']['panes']
    cwd = choose_cwd(panes, args.cwd)
    identity = [profile['id'], profile['target'], profile['session'], cwd, args.agent, os.environ.get('HERDR_SOCKET_PATH')]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    directory = state_directory() / 'views'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    state = directory / (key + '.json')
    with (directory / (key + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if state.exists():
            owner = json.loads(state.read_text())
            if owner.get('phase') == 'creating':
                raise RuntimeError('Local pane creation is uncertain; inspect Herdr before retrying. Record: ' + str(state))
            live = cli('pane', 'list')['result']['panes']
            if any(p['pane_id'] == owner['pane_id'] and p['terminal_id'] == owner['terminal_id'] for p in live):
                local_api('pane.focus', {'pane_id': owner['pane_id']})
                return 0
        state.write_text(json.dumps({'phase': 'creating'}))
        environment = ['--env', 'HERDR_LOCAL_CLI=' + herdr, '--env', 'PATH=' + os.environ['PATH']]
        if os.environ.get('HERDR_NATIVE_STATE_DIR'):
            environment += ['--env', 'HERDR_NATIVE_STATE_DIR=' + os.environ['HERDR_NATIVE_STATE_DIR']]
        created = cli('workspace', 'create', '--label', f"{profile['label']} · {args.agent}", '--focus', *environment)['result']
        pane = created['root_pane']
        state.write_text(json.dumps({'phase': 'created', 'pane_id': pane['pane_id'], 'terminal_id': pane['terminal_id']}))
        live = cli('pane', 'list')['result']['panes']
        if not any(p['pane_id'] == pane['pane_id'] and p['terminal_id'] == pane['terminal_id'] for p in live):
            raise RuntimeError('The new local pane changed before launch; no command was sent.')
        command = shlex.join([sys.executable, launcher, 'auto', '--agent', args.agent,
                              '--machine', profile['id'], '--cwd', cwd])
        cli('pane', 'run', pane['pane_id'], command)
        return 0


def open_original_pi(args, connector):
    """The original agent TUI lives remotely; only explicit editor regions render locally."""
    local_tools()
    herdr = os.environ['HERDR_LOCAL_CLI']
    def cli(*parts):
        result = subprocess.run([herdr, *parts], capture_output=True, text=True, timeout=30)
        if result.returncode: raise RuntimeError(result.stderr.strip() or 'Herdr request failed')
        return json.loads(result.stdout)
    profile = choose_profile(cli('machine', 'list', '--json'), args.machine)
    panes = [] if args.cwd else cli('--machine', profile['id'], 'pane', 'list')['result']['panes']
    cwd = choose_cwd(panes, args.cwd)
    extension = 'claude-editor.py' if args.agent == 'claude' else 'native-editor.ts'
    request = {'agent': args.agent, 'interactive': True, 'cwd': cwd, 'herdr_session': profile['session'],
               'extension': base64.b64encode((connector.ROOT / 'remote' / extension).read_bytes()).decode()}
    if args.agent == 'claude':
        from claude_package import bundle
        request['claude_assets'] = bundle(connector.ROOT / 'remote')
    command = connector.ssh_command(connector.host_value(profile['target']),
                                   ['python3', '-c', (connector.ROOT / 'remote/backend.py').read_text()])
    command[1:1] = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
    result = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, timeout=75)
    if result.returncode: raise RuntimeError(result.stderr.strip() or 'Remote agent launch failed')
    return 0


def local_tools():
    # Finder and long-lived Herdr servers may not inherit the user's Node installation.
    additions = [Path.home() / '.npm-global/bin', Path.home() / '.local/bin']
    versions = Path.home() / '.nvm/versions/node'
    if versions.is_dir():
        def version(path):
            try: return tuple(map(int, path.name.lstrip('v').split('.')))
            except ValueError: return (0,)
        additions += [p / 'bin' for p in sorted(versions.iterdir(), key=version, reverse=True)]
    os.environ['PATH'] = os.environ.get('PATH', '') + os.pathsep + os.pathsep.join(str(p) for p in additions if p.is_dir())


def run(argv, connector):
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent', choices=('pi', 'codex'), required=True)
    parser.add_argument('--machine')
    parser.add_argument('--cwd')
    args = parser.parse_args(argv)
    root = connector.ROOT
    local_tools()
    herdr = os.environ.get('HERDR_LOCAL_CLI') or shutil.which('herdr')
    if not herdr:
        raise RuntimeError('Local Herdr executable is missing')

    def cli(*parts):
        result = subprocess.run([herdr, *parts], capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'Herdr request failed')
        return json.loads(result.stdout) if result.stdout.strip() else {}

    profile = choose_profile(cli('machine', 'list', '--json'), args.machine)
    panes = cli('--machine', profile['id'], 'pane', 'list')['result']['panes']
    cwd = choose_cwd(panes, args.cwd)
    print(f"{profile['label']} · {args.agent} · {cwd}\n正在检查并连接远端后端…", flush=True)
    # Check local client support before creating anything remotely.
    if args.agent == 'pi':
        check = subprocess.run(['node', '--input-type=module', '-e',
                                f"import {{ loadPi }} from {json.dumps((root / 'remote/pi-package.mjs').as_uri())}; await loadPi();"],
                               capture_output=True, text=True, timeout=20)
        if check.returncode: raise RuntimeError(check.stderr.strip())
    elif not shutil.which('codex') and not shutil.which('npm'):
        raise RuntimeError('Codex or npm is required locally. No remote backend was created.')

    cache = state_directory() / 'connections'
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = hashlib.sha256(json.dumps([profile['target'], profile['session'], cwd, args.agent,
                                    os.environ.get('HERDR_SOCKET_PATH')]).encode()).hexdigest()
    with (cache / (key + '.lock')).open('a+') as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lease.seek(0)
            try:
                owner = json.load(lease)
                local_panes = cli('pane', 'list')['result']['panes']
                if any(p['pane_id'] == owner['pane_id'] and p['terminal_id'] == owner['terminal_id'] for p in local_panes):
                    local_api('pane.focus', {'pane_id': owner['pane_id']})
                    print('该连接已打开，已切换到已有面板。')
                    return 0
            except (ValueError, KeyError):
                pass
            raise RuntimeError('This backend already has a local client. Close that connection before reconnecting.')
        current = next((p for p in cli('pane', 'list')['result']['panes'] if p['pane_id'] == os.environ.get('HERDR_PANE_ID')), {})
        lease.seek(0)
        lease.truncate()
        json.dump({k: current.get(k) for k in ('pane_id', 'terminal_id')}, lease)
        lease.flush()
        request = {'agent': args.agent, 'cwd': cwd, 'herdr_session': profile['session'],
                   'helper': base64.b64encode((root / 'remote/pi-host.py').read_bytes()).decode()}
        command = connector.ssh_command(connector.host_value(profile['target']),
                                        ['python3', '-c', (root / 'remote/backend.py').read_text()])
        command[1:1] = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
        try:
            result = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, timeout=75)
        except subprocess.TimeoutExpired:
            raise RuntimeError('连接超时；启动结果可能不确定。保留了远端记录，不会自动重复启动。')
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or 'Remote connection failed')
        backend = json.loads(result.stdout)
        remote_socket = connector.socket_value(backend['socket'])
        print(('已复用' if backend['reused'] else '已启动') + f"远端 {args.agent} 后端，正在打开本地编辑器…", flush=True)
        if args.agent == 'codex':
            return connector.codex(argparse.Namespace(socket=remote_socket, host=profile['target'], start=False, cwd=backend['cwd'], binary=codex_client(backend['version'])))
        return subprocess.call(['node', str(root / 'remote/pi-ui.mjs'), '--host', profile['target'],
                                '--cwd', backend['cwd'], '--socket', remote_socket])
