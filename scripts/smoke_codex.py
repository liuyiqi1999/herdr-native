#!/usr/bin/env python3
"""Exercise installed Codex's original TUI over its Unix app-server transport. Never submits a prompt."""
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
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.tools/python'))
import pyte

with tempfile.TemporaryDirectory(prefix='herdr-codex-smoke-', dir='/tmp') as directory:
    socket = Path(directory) / 'server.sock'
    with open(Path(directory) / 'server.log', 'wb') as log:
        server = subprocess.Popen(['codex', '--disable', 'hooks', '--disable', 'in_app_updates', '-c', f'projects.{json.dumps(directory)}.trust_level="trusted"',
                                   'app-server', '--listen', f'unix://{socket}'], cwd=directory, stdout=log, stderr=log)
        pid = None
        try:
            deadline = time.monotonic() + 15
            while not socket.exists():
                if server.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Codex app-server failed to create its socket')
                time.sleep(0.1)
            pid, fd = pty.fork()
            if pid == 0:
                os.chdir(directory)
                os.environ['TERM'] = 'xterm-256color'
                os.execvp('codex', ['codex', '--disable', 'hooks', '--disable', 'in_app_updates', '--remote', f'unix://{socket}'])
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', 35, 110, 0, 0))
            screen = pyte.Screen(110, 35)
            stream = pyte.Stream(screen)
            decoder = codecs.getincrementaldecoder('utf8')('replace')
            def drain(seconds):
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    if select.select([fd], [], [], 0.05)[0]:
                        try:
                            data = os.read(fd, 65536)
                        except OSError:
                            return
                        stream.feed(decoder.decode(data))
                        if b'\x1b[6n' in data: os.write(fd, b'\x1b[1;1R')
                        if b'\x1b[?u' in data: os.write(fd, b'\x1b[?0u')
                        if b'\x1b[c' in data: os.write(fd, b'\x1b[?62;22c')
            drain(5)
            for _ in range(10):
                current = '\n'.join(screen.display)
                if 'Continue without trusting' in current:
                    os.write(fd, b'\x1b')  # The visible dialog documents Esc as skip.
                    drain(2)
                elif 'context' in current or '? for shortcuts' in current:
                    break
                else:
                    drain(1)
            os.kill(server.pid, signal.SIGSTOP)  # Prove the editor does not wait for the backend.
            os.write(fd, 'local-input-with-paused-backend'.encode())
            drain(1)
            text = '\n'.join(screen.display)
            evidence = ROOT / '.local/codex-remote-screen.txt'
            evidence.parent.mkdir(exist_ok=True)
            evidence.write_text(text + '\n')
            assert 'local-input-with-paused-backend' in text, 'Input was not rendered; see ' + str(evidence)
            print('PASS: original Codex TUI renders typing while app-server is paused; no prompt submitted')
        finally:
            if server.poll() is None:
                os.kill(server.pid, signal.SIGCONT)
            if pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    os.close(fd)
                    os.waitpid(pid, 0)
                except ProcessLookupError:
                    pass
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
