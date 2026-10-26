#!/usr/bin/env python3
"""PTY smoke test: original Pi editor renders locally with a delayed fake backend (no model calls)."""
import codecs
import fcntl
import json
import tempfile
import os
from pathlib import Path
import pty
import select
import signal
import struct
import sys
import termios
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.tools/python'))
import pyte

test_state = tempfile.TemporaryDirectory(prefix='herdr-input-test-')
pid_file = Path(test_state.name) / 'backend.pid'
request_log = Path(test_state.name) / 'requests.jsonl'
backend_paused = False
backend_pid = None
ui_path = os.environ.get('HERDR_TEST_PI_UI', str(ROOT / 'remote/pi-ui.mjs'))
pid, fd = pty.fork()
if pid == 0:
    os.environ['TERM'] = 'xterm-256color'
    os.environ['HERDR_TEST_RPC_PID_FILE'] = str(pid_file)
    os.environ['HERDR_TEST_RPC_LOG_FILE'] = str(request_log)
    os.chdir(ROOT)
    os.execvp('node', ['node', ui_path, '--local', '--cwd', str(ROOT),
                       '--remote-bin', str(ROOT / 'remote/tests/fake-rpc.mjs')])
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 35, 110, 0, 0))
screen = pyte.Screen(110, 35)
stream = pyte.Stream(screen)
decoder = codecs.getincrementaldecoder('utf8')('replace')
output = bytearray()
def drain(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if select.select([fd], [], [], 0.05)[0]:
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            output.extend(data)
            stream.feed(decoder.decode(data))
            if b'\x1b[6n' in data:
                os.write(fd, b'\x1b[1;1R')
            if b'\x1b[?u' in data:
                os.write(fd, b'\x1b[?0u')
            if b'\x1b[c' in data:
                os.write(fd, b'\x1b[?62;22c')
try:
    drain(3)
    assert pid_file.exists() and request_log.exists(), 'Test backend did not initialize'
    before = request_log.read_text()
    backend_pid = int(pid_file.read_text())
    os.kill(backend_pid, signal.SIGSTOP)
    backend_paused = True
    os.write(fd, '\x1b[200~本地编辑🙂\nsecond line\x1b[201~'.encode())
    drain(0.5)
    text = '\n'.join(screen.display)
    if '本地编辑' not in text or 'second line' not in text:
        raise AssertionError('Pi input not rendered:\n' + text + '\nRaw tail:\n' + output[-1800:].decode(errors='replace'))
    evidence = ROOT / '.local/pi-remote-screen.txt'
    evidence.parent.mkdir(exist_ok=True)
    evidence.write_text(text + '\n')
    os.kill(backend_pid, signal.SIGCONT)
    backend_paused = False
    drain(0.5)
    assert request_log.read_text() == before, 'Editing sent a request to the backend'
    os.write(fd, b'\r')
    drain(1)
    assert 'TypeError' not in output.decode(errors='replace')
    assert 'Backend response' in '\n'.join(screen.display), 'RPC response did not render'
    assert 'Fixture tool output' in '\n'.join(screen.display), 'RPC tool result did not render'
    prompts = [r for r in map(json.loads, request_log.read_text().splitlines()) if r['type'] in ('prompt', 'follow_up')]
    assert len(prompts) == 1 and prompts[0]['message'] == '本地编辑🙂\nsecond line'
    print('PASS: installed Pi UI renders Chinese/emoji/multiline with backend paused; zero editing requests, exactly one complete prompt on Enter; no model requests')
finally:
    if backend_paused and backend_pid:
        os.kill(backend_pid, signal.SIGCONT)
    try:
        os.kill(pid, signal.SIGTERM)
        drain(2)
        done, _ = os.waitpid(pid, os.WNOHANG)
        if not done:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    except ProcessLookupError:
        pass
    os.close(fd)
    test_state.cleanup()
