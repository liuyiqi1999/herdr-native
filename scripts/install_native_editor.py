#!/usr/bin/env python3
"""Enable native local input for ordinary Pi launches on a saved Herdr machine."""
import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import remote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'remote'))
from auto_launch import choose_profile, local_tools

INSTALL = r"""
import base64, hashlib, json, os, pathlib, sys, tempfile
request = json.load(sys.stdin)
root = pathlib.Path.home()
runtime_dir = root / '.cache/herdr-native/editor'
runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
runtime = runtime_dir / 'native-editor.ts'
extensions = root / '.pi/agent/extensions'
extensions.mkdir(parents=True, exist_ok=True)
entry = extensions / 'herdr-native-input.ts'
marker = '// Managed by Herdr Native: ordinary Pi local input\n'
if entry.exists() and not entry.read_text().startswith(marker):
    raise RuntimeError('Refusing to overwrite an unrelated extension: ' + str(entry))
wrapper = marker + 'import nativeEditor from ' + json.dumps(str(runtime)) + ';\n'
wrapper += '''export default function (pi: any) {
  // Explicit native launches already load the bridge. Other terminals keep normal Pi.
  if (!process.env.HERDR_PANE_ID || process.env.HERDR_PI_EDITOR_SOCKET) return;
  return nativeEditor(pi);
}
'''
def atomic(path, data):
    fd, temporary = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
source = base64.b64decode(request['source'])
atomic(runtime, source)
atomic(entry, wrapper.encode())
print(json.dumps({'extension': str(entry), 'runtime': str(runtime), 'sha256': hashlib.sha256(source).hexdigest()}))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--machine')
    args = parser.parse_args()
    local_tools()
    herdr = os.environ.get('HERDR_LOCAL_CLI') or shutil.which('herdr')
    profiles = json.loads(subprocess.check_output([herdr, 'machine', 'list', '--json'], text=True))
    profile = choose_profile(profiles, args.machine)
    result = subprocess.run(remote.ssh_command(profile['target'], ['python3', '-c', INSTALL]),
                            input=json.dumps({'source': base64.b64encode((ROOT / 'remote/native-editor.ts').read_bytes()).decode()}),
                            capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    print(result.stdout.strip())


if __name__ == '__main__':
    main()
