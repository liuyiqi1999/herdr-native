#!/usr/bin/env python3
"""Exercise Claude's external-editor boundary and local rendering without model calls."""
import base64
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ClaudeEditorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.path = self.directory / 'draft.txt'
        self.path.write_text('original draft')
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, __import__('termios').TIOCSWINSZ, struct.pack('HHHH', 30, 100, 0, 0))
        self.process = subprocess.Popen([sys.executable, str(ROOT / 'remote/claude-editor.py'), 'edit', str(self.path)],
                                        stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        raw = b''
        deadline = time.monotonic() + 5
        match = None
        while time.monotonic() < deadline and not match:
            if select.select([self.master], [], [], .1)[0]:
                raw += os.read(self.master, 65536)
                match = re.search(rb'editor/v1/([A-Za-z0-9_-]+)', raw)
        self.assertIsNotNone(match, raw)
        encoded = match[1]
        self.address = base64.urlsafe_b64decode(encoded + b'=' * (-len(encoded) % 4)).decode()
        self.peer = None
        self.native = None
        self.draining = True
        def drain():
            while self.draining:
                try:
                    if select.select([self.master], [], [], .1)[0]:
                        if not os.read(self.master, 65536):
                            break
                except OSError:
                    break
        self.drain_thread = threading.Thread(target=drain, daemon=True)
        self.drain_thread.start()

    def connect(self):
        self.peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.peer.settimeout(5)
        self.peer.connect(self.address)
        self.reader = self.peer.makefile('rb')

    def query(self, kind, **fields):
        self.peer.sendall((json.dumps({'id': 'test', 'type': kind, **fields}) + '\n').encode())
        return json.loads(self.reader.readline())

    def tearDown(self):
        if self.native:
            self.native.terminate()
            self.native.communicate(timeout=5)
        if self.peer:
            self.reader.close()
            self.peer.close()
        if self.process.poll() is None:
            os.kill(self.process.pid, signal.SIGCONT)
            self.process.terminate()
        self.process.wait(timeout=5)
        self.draining = False
        self.drain_thread.join(timeout=1)
        os.close(self.master)
        self.temporary.cleanup()

    def test_save_and_empty_draft(self):
        self.connect()
        self.assertEqual(self.query('get_state')['data']['editorKind'], 'claude-external')
        self.assertTrue(self.query('prompt', message='')['success'])
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertEqual(self.path.read_text(), '')

    def test_cancel_preserves_file(self):
        self.connect()
        self.assertTrue(self.query('key', key='\x1b')['success'])
        self.assertEqual(self.process.wait(timeout=5), 1)
        self.assertEqual(self.path.read_text(), 'original draft')

    def test_reject_invalid_and_disconnect_then_reconnect(self):
        self.connect()
        self.assertFalse(self.query('follow_up', message='do not submit')['success'])
        self.assertFalse(self.query('prompt', message=123)['success'])
        self.reader.close()
        self.peer.close()
        self.peer = None
        time.sleep(.05)
        self.connect()
        self.assertEqual(self.query('get_state')['data']['text'], 'original draft')
        self.assertTrue(self.query('prompt', message='中文🙂\nsecond line')['success'])
        self.process.wait(timeout=5)
        self.assertEqual(self.path.read_text(), '中文🙂\nsecond line')

    def test_local_input_while_remote_paused(self):
        self.native = subprocess.Popen(['node', str(ROOT / 'remote/native-input.mjs'), '--local', '--cwd',
                                       str(self.directory), '--socket', self.address],
                                      env={**os.environ, 'HERDR_NATIVE_DRAFT_DIR': str(self.directory / 'drafts')},
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        buffer = b''
        def frame_containing(text):
            nonlocal buffer
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if b'\n' not in buffer:
                    if not select.select([self.native.stdout], [], [], .1)[0]:
                        continue
                    chunk = os.read(self.native.stdout.fileno(), 65536)
                    self.assertTrue(chunk, 'Native editor exited unexpectedly')
                    buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    frame = json.loads(line)
                    if frame.get('ready') and text in '\n'.join(frame['lines']):
                        return frame
            self.fail('Timed out waiting for local editor: ' + text)
        def key(data):
            self.native.stdin.write((json.dumps({'type': 'input', 'data': data}) + '\n').encode())
            self.native.stdin.flush()
        frame_containing('original draft')
        os.kill(self.process.pid, signal.SIGSTOP)
        key('\x1b[200~中文🙂\nsecond line\x1b[201~')
        frame_containing('second line')
        self.assertEqual(self.path.read_text(), 'original draft')
        os.kill(self.process.pid, signal.SIGCONT)
        key('\x1b[103;5u')
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertIn('中文🙂\nsecond line', self.path.read_text())


if __name__ == '__main__':
    unittest.main()
