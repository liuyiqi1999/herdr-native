#!/usr/bin/env python3
"""Original Pi TUI + native editor bridge; never submits a model prompt."""
import base64, fcntl, json, os, pty, re, select, signal, struct, subprocess, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='native-editor-smoke-') as directory:
    master, slave = pty.openpty()
    fcntl.ioctl(slave, 0x80087467, struct.pack('HHHH', 35, 110, 0, 0))
    pi = subprocess.Popen(['pi', '--offline', '--no-approve', '--no-session', '--no-extensions', '--no-skills', '--no-prompt-templates', '--no-context-files', '-e', str(ROOT/'remote/native-editor.ts')], cwd=directory, env={**os.environ, 'TERM':'xterm-256color'}, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
    os.close(slave)
    raw = bytearray()
    def drain(seconds):
        end = time.monotonic()+seconds
        while time.monotonic()<end:
            if select.select([master],[],[],.05)[0]:
                data=os.read(master,65536); raw.extend(data)
                if b'\x1b[6n' in data: os.write(master,b'\x1b[1;1R')
                if b'\x1b[?u' in data: os.write(master,b'\x1b[?0u')
                if b'\x1b[c' in data: os.write(master,b'\x1b[?62;22c')
    native=None
    try:
        drain(8)
        match = re.search(rb'https://herdr-native.invalid/editor/v1/([A-Za-z0-9_-]+)',raw)
        assert match, raw[-9000:].decode(errors='replace')
        encoded=match[1]; socket_path=base64.urlsafe_b64decode(encoded+b'='*(-len(encoded)%4)).decode()
        native=subprocess.Popen(['node',str(ROOT/'remote/native-input.mjs'),'--local','--cwd',directory,'--socket',socket_path],env={**os.environ,'HERDR_NATIVE_DRAFT_DIR':directory+'/drafts'},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        native.stdin.write(json.dumps({'type':'size','width':110,'height':35})+'\n');native.stdin.flush()
        frame=json.loads(native.stdout.readline())
        while not frame['ready']:
            frame=json.loads(native.stdout.readline())
        os.kill(pi.pid,signal.SIGSTOP)
        native.stdin.write(json.dumps({'type':'input','data':'\x1b[200~本地编辑🙂\nsecond line\x1b[201~'})+'\n');native.stdin.flush()
        frame=json.loads(native.stdout.readline())
        assert '本地编辑' in '\n'.join(frame['lines']),frame
        assert 'second line' in '\n'.join(frame['lines']),frame
        os.kill(pi.pid,signal.SIGCONT)
        # Flush local draft to the remote ORIGINAL model selector, without submitting it.
        native.stdin.write(json.dumps({'type':'input','data':'\x0c'})+'\n');native.stdin.flush()
        drain(2)
        print('PASS: original Pi marks focused input; original local editor types while entire remote Pi is paused; no prompt submitted')
    finally:
        os.kill(pi.pid,signal.SIGCONT)
        if native:
            native.terminate()
            try: native.wait(timeout=5)
            except subprocess.TimeoutExpired: native.kill();native.wait()
        os.killpg(pi.pid,signal.SIGTERM)
        try: pi.wait(timeout=5)
        except subprocess.TimeoutExpired: os.killpg(pi.pid,signal.SIGKILL);pi.wait()
        os.close(master)
