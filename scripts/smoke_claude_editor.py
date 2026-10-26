#!/usr/bin/env python3
"""Real Claude TUI -> external editor -> local Unicode input -> Claude draft, no model call."""
import argparse
import io
import zipfile
import base64
import codecs
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.tools/python'))
import pyte
from remote import host_value, ssh_command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', type=host_value)
    parser.add_argument('--automatic', action='store_true')
    parser.add_argument('--plain', action='store_true', help='Launch ordinary claude through the installed shell integration')
    args = parser.parse_args()
    remote_directory = None
    with tempfile.TemporaryDirectory(prefix='hn-claude-test-') as directory:
        if args.host:
            bundle = io.BytesIO()
            with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as archive:
                for name in ('claude-editor.py', 'claude-auto.py'):
                    archive.write(ROOT / 'remote' / name, name)
                for path in (ROOT / 'remote/claude-vendor').rglob('*'):
                    if path.is_file() and '__pycache__' not in str(path):
                        archive.write(path, str(path.relative_to(ROOT / 'remote')))
            source = "import base64,tempfile,pathlib,sys,zipfile,io; p=pathlib.Path(tempfile.mkdtemp(prefix='hn-claude-test-')); zipfile.ZipFile(io.BytesIO(base64.b64decode(sys.stdin.read()))).extractall(str(p)); print(p)"
            remote_directory = subprocess.check_output(ssh_command(args.host, ['python3', '-c', source]), input=base64.b64encode(bundle.getvalue()).decode(), text=True, timeout=20).strip()
            helper = 'claude-auto.py' if args.automatic else 'claude-editor.py'
            argv = ['ssh', '-tt', '-o', 'BatchMode=yes', args.host, 'cd ' + shlex.quote(remote_directory) + ' && exec env DISABLE_AUTOUPDATER=1 ' + shlex.join([
                'python3', remote_directory + '/' + helper] + ([] if args.automatic else ['launch']) + ['--', '--safe-mode', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}'])]
        else:
            helper = 'claude-auto.py' if args.automatic else 'claude-editor.py'
            argv = [sys.executable, str(ROOT / 'remote' / helper)] + ([] if args.automatic else ['launch']) + ['--', '--safe-mode', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}']
        if args.plain:
            ordinary = 'claude --safe-mode --strict-mcp-config --mcp-config ' + shlex.quote('{"mcpServers":{}}')
            if args.host:
                argv = ['ssh', '-tt', '-o', 'BatchMode=yes', args.host,
                        'cd ' + shlex.quote(remote_directory) + ' && exec env DISABLE_AUTOUPDATER=1 HERDR_PANE_ID=hn-claude-test zsh -ic ' + shlex.quote(ordinary)]
            else:
                argv = ['zsh', '-ic', ordinary]
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 35, 120, 0, 0))
        process = subprocess.Popen(argv, cwd=directory, env={**os.environ, 'DISABLE_AUTOUPDATER': '1', 'TERM': 'xterm-256color', **({'HERDR_PANE_ID': 'hn-claude-test'} if args.plain else {})}, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        screen = pyte.Screen(120, 35)
        stream = pyte.Stream(screen)
        decoder = codecs.getincrementaldecoder('utf8')('replace')
        raw = bytearray()
        native = None
        def drain(seconds):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                if select.select([master], [], [], .05)[0]:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    raw.extend(data)
                    stream.feed(decoder.decode(data))
                    if b'\x1b[6n' in data:
                        os.write(master, b'\x1b[1;1R')
                    if b'\x1b[?u' in data:
                        os.write(master, b'\x1b[?0u')
        try:
            drain(5)
            display = '\n'.join(screen.display)
            if 'trust this folder' in display:
                # Only the disposable empty directory owned by this test is trusted.
                if '❯ No, exit' in display:
                    os.write(master, b'\x1b[B')
                os.write(master, b'\r')
                drain(4)
            if process.poll() is not None:
                raise AssertionError(raw.decode(errors='replace'))
            if not args.automatic:
                os.write(master, b'\x07')
            drain(2)
            match = re.search(rb'https://herdr-native.invalid/editor/v1/([A-Za-z0-9_-]+)', raw)
            assert match, 'cursor=' + repr((screen.cursor.x, screen.cursor.y, screen.cursor.hidden)) + '\n' + '\n'.join(screen.display)
            encoded = match[1]
            address = base64.urlsafe_b64decode(encoded + b'=' * (-len(encoded) % 4)).decode()
            if args.automatic:
                # Typing before a native client is ready must stay in Claude's draft.
                os.write(master, '早期输入🙂'.encode())
                drain(.2)
            def start_native(address):
                command = ['node', str(ROOT / 'remote/native-input.mjs'), '--cwd', remote_directory or directory, '--socket', address]
                command += ['--host', args.host] if args.host else ['--local']
                return subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        env={**os.environ, 'HERDR_NATIVE_DRAFT_DIR': directory + '/drafts'})
            native = start_native(address)
            buffer = b''
            def wait_frame(text):
                nonlocal buffer, native, address
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    drain(.05)
                    if select.select([native.stdout], [], [], .05)[0]:
                        chunk = os.read(native.stdout.fileno(), 65536)
                        if not chunk and args.automatic:
                            markers = re.findall(rb'editor/v1/([A-Za-z0-9_-]+)', raw)
                            if markers:
                                encoded = markers[-1]
                                next_address = base64.urlsafe_b64decode(encoded + b'=' * (-len(encoded) % 4)).decode()
                                if next_address != address:
                                    native.communicate(timeout=3)
                                    address = next_address
                                    native = start_native(address)
                                    buffer = b''
                            continue
                        assert chunk, 'Editor stopped'
                        buffer += chunk
                    while b'\n' in buffer:
                        line, buffer = buffer.split(b'\n', 1)
                        frame = json.loads(line)
                        if frame.get('ready') and text in '\n'.join(frame['lines']):
                            return
                raise AssertionError('Local frame missing: ' + text + '\n' + '\n'.join(screen.display))
            def key(data):
                native.stdin.write((json.dumps({'type': 'input', 'data': data}) + '\n').encode())
                native.stdin.flush()
            wait_frame('早期输入' if args.automatic else 'Claude Code')
            key('\x1b[200~本地草稿验证🙂\nsecond line\x1b[201~')
            wait_frame('second line')
            if args.automatic:
                native.terminate()
                native.communicate(timeout=5)
                drain(10.5)
                native = start_native(address)
                buffer = b''
                wait_frame('second line')
                for _ in range(40):
                    key('\x7f')
                key('/model')
                wait_frame('/model')
                key('\r')
                drain(3)
                display = '\n'.join(screen.display)
                (ROOT / '.local/claude-auto-menu.txt').write_text(display)
                assert 'model' in display.lower() and ('Enter to confirm' in display or 'Select model' in display or 'Select a model' in display), display
                before = len(re.findall(rb'editor/v1/[A-Za-z0-9_-]+', raw))
                os.write(master, b'\x1b')
                drain(3)
                assert len(re.findall(rb'editor/v1/[A-Za-z0-9_-]+', raw)) > before, 'Automatic editor did not return after menu close'
                print('PASS:', args.host or 'local', 'automatic inline bridge without Ctrl+G; Unicode input; one Enter opens original /model; automatic return after Escape; no model prompt')
            else:
                key('\x07')
                drain(3)
                display = '\n'.join(screen.display)
                assert '本地草稿验证' in display and 'second line' in display, display
                assert 'Claude Code · local editing' not in display, 'External editor did not return to Claude'
                print('PASS:', args.host or 'local', 'original Claude Ctrl+G, local Chinese/multiline editing, saved draft returns without submission')
        finally:
            # Let Claude close its renderer normally before using process cleanup.
            for _ in range(3):
                if process.poll() is not None:
                    break
                try:
                    os.write(master, b'\x03')
                    drain(.4)
                except OSError:
                    break
            if native:
                native.terminate()
                try:
                    native.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    native.kill()
                    native.communicate()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            os.close(master)
            if remote_directory:
                subprocess.run(ssh_command(args.host, ['python3', '-c', 'import shutil,sys; shutil.rmtree(sys.argv[1])', remote_directory]), timeout=20, check=True)


if __name__ == '__main__':
    main()
