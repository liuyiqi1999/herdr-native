import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]


class PersistentPiTest(unittest.TestCase):
    def test_reconnect_keeps_backend_and_discards_partial_commands(self):
        with tempfile.TemporaryDirectory(prefix='herdr-rpc-', dir='/tmp') as directory:
            path = Path(directory) / 'rpc.sock'
            host = subprocess.Popen(['python3', str(ROOT / 'remote/pi-host.py'), '--socket', str(path),
                                     '--cwd', str(ROOT), '--binary', str(ROOT / 'remote/tests/fake-rpc.mjs')],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not path.exists():
                    self.assertIsNone(host.poll())
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.05)

                def query(method, **fields):
                    with socket.socket(socket.AF_UNIX) as connection:
                        connection.settimeout(3)
                        connection.connect(str(path))
                        request_id = str(uuid.uuid4())
                        connection.sendall((json.dumps(dict(id=request_id, type=method, **fields)) + '\n').encode())
                        with connection.makefile('rb') as reader:
                            result = json.loads(reader.readline())
                        self.assertEqual(result['id'], request_id)
                    time.sleep(0.1)
                    return result['data']

                first = query('pid')['pid']
                with socket.socket(socket.AF_UNIX) as partial:
                    partial.connect(str(path))
                    partial.sendall(b'{"id":"incomplete","type":"prompt","message":"do not execute')
                time.sleep(0.1)
                self.assertEqual(query('echo', text='中文\nnew client')['text'], '中文\nnew client')
                self.assertEqual(query('pid')['pid'], first, 'Disconnect must not restart the agent')
                with socket.socket(socket.AF_UNIX) as owner:
                    owner.connect(str(path))
                    time.sleep(0.1)
                    with socket.socket(socket.AF_UNIX) as second:
                        second.settimeout(3)
                        second.connect(str(path))
                        self.assertEqual(second.recv(1), b'', 'Second client must not steal input')
                print('PASS: backend survives reconnect; partial commands discarded; single-client ownership')
            finally:
                host.terminate()
                try:
                    stdout, stderr = host.communicate(timeout=6)
                except subprocess.TimeoutExpired:
                    host.kill()
                    stdout, stderr = host.communicate()
                self.assertEqual(host.returncode, 0, stderr.decode())
                self.assertFalse(path.exists())


if __name__ == '__main__':
    unittest.main()
