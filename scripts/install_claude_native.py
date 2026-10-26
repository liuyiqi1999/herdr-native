#!/usr/bin/env python3
"""Install Herdr-only ordinary Claude launch integration, preserving shell config."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'remote'))
from claude_package import bundle
from remote import host_value, ssh_command

INSTALL = r"""
import base64, hashlib, io, json, os, pathlib, shlex, stat, sys, tempfile, time, zipfile
request = json.load(sys.stdin)
base = pathlib.Path.home() / '.cache/herdr-native/claude'
base.mkdir(parents=True, exist_ok=True, mode=0o700)
info = base.lstat()
if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
    raise RuntimeError('Expected private user-owned runtime directory: ' + str(base))
data = base64.b64decode(request['assets'])
version = base / hashlib.sha256(data).hexdigest()[:16]
if not version.exists():
    temporary = pathlib.Path(tempfile.mkdtemp(prefix='install-', dir=str(base)))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            path = pathlib.Path(entry.filename)
            if path.is_absolute() or '..' in path.parts:
                raise RuntimeError('Invalid asset path')
        archive.extractall(str(temporary))
    temporary.rename(version)
entry = base / 'entry.sh'
marker = '# Managed by Herdr Native: automatic Claude input\n'
if entry.exists() and not entry.read_text().startswith(marker):
    raise RuntimeError('Unrelated shell entry already exists: ' + str(entry))
body = marker + r'''function claude {
  if [ -n "${HERDR_PANE_ID-}" ] && [ "${HERDR_NATIVE_CLAUDE:-1}" != 0 ] && [ -t 0 ] && [ -t 1 ]; then
    local hn_claude_binary
    if [ -n "${ZSH_VERSION-}" ]; then
      hn_claude_binary="$(whence -p claude)"
    else
      hn_claude_binary="$(type -P claude)"
    fi
    if [ -z "$hn_claude_binary" ]; then
      printf '%s\n' 'Claude Code is not installed on this machine' >&2
      return 127
    fi
    __PYTHON__ __ADAPTER__ --binary "$hn_claude_binary" -- "$@"
  else
    command claude "$@"
  fi
}
'''
body = body.replace('__PYTHON__', shlex.quote(sys.executable)).replace('__ADAPTER__', shlex.quote(str(version / 'claude-auto.py')))
def atomic(path, content):
    fd, name = tempfile.mkstemp(dir=str(path.parent))
    with os.fdopen(fd, 'w') as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    os.replace(name, str(path))
atomic(entry, body)
line = '[ ! -f "$HOME/.cache/herdr-native/claude/entry.sh" ] || . "$HOME/.cache/herdr-native/claude/entry.sh"'
configs = [pathlib.Path.home() / '.zshrc', pathlib.Path.home() / '.bashrc']
changed = []
backups = []
for config in configs:
    if not config.exists() and config.name != ('.zshrc' if 'zsh' in os.environ.get('SHELL', '') else '.bashrc'):
        continue
    old = config.read_text() if config.exists() else ''
    if line in old:
        continue
    if config.exists():
        backup_dir = base / 'backups'
        backup_dir.mkdir(exist_ok=True, mode=0o700)
        fd, backup = tempfile.mkstemp(prefix=config.name + '-', dir=str(backup_dir))
        with os.fdopen(fd, 'w') as file:
            file.write(old)
        backups.append(backup)
    # Follow an existing dotfile symlink instead of replacing the user's symlink.
    target = config.resolve() if config.is_symlink() else config
    permissions = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
    atomic(target, old.rstrip('\n') + '\n\n# Herdr Native Claude input (only inside Herdr panes)\n' + line + '\n')
    target.chmod(permissions)
    changed.append(str(config))
print(json.dumps({'runtime': str(version), 'entry': str(entry), 'shell_configs_updated': changed, 'backups': backups}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--host', type=host_value)
    target.add_argument('--local', action='store_true')
    args = parser.parse_args()
    command = ssh_command(args.host, ['python3', '-c', INSTALL]) if args.host else [sys.executable, '-c', INSTALL]
    result = subprocess.run(command, input=json.dumps({'assets': bundle(ROOT / 'remote')}), text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    print(result.stdout.strip())


if __name__ == '__main__':
    main()
