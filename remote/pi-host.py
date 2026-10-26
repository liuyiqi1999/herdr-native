#!/usr/bin/env python3
"""Keep one Pi RPC backend alive in a Herdr pane while local TUI clients disconnect.
The Unix socket must live in a private (0700), user-owned directory. One client at a time.
"""
import argparse
from collections import deque
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import subprocess

LIMIT = 16 * 1024 * 1024


def serve(path, cwd, binary, session=None):
    path = Path(path)
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
        raise ValueError('Socket directory must be owned by this user with mode 0700')
    if path.exists():
        raise ValueError('Socket already exists; refusing to replace an active backend')
    args = [binary, '--mode', 'rpc']
    if session:
        args += ['--session', session]
    backend = subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    selector = selectors.DefaultSelector()
    client = None
    incoming = bytearray()
    outgoing = bytearray()
    output_frames = bytearray()
    discard_output = False
    pending = deque()
    pending_bytes = 0
    stopping = False

    def stop(_sig, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def detach():
        nonlocal client
        if client is not None:
            selector.unregister(client)
            client.close()
            client = None
        incoming.clear()  # Never complete a partially sent command using the next client's bytes.
        outgoing.clear()

    try:
        listener.bind(str(path))
        os.chmod(path, 0o600)
        listener.listen(4)
        listener.setblocking(False)
        os.set_blocking(backend.stdout.fileno(), False)
        os.set_blocking(backend.stdin.fileno(), False)
        selector.register(listener, selectors.EVENT_READ, 'listener')
        selector.register(backend.stdout, selectors.EVENT_READ, 'output')
        print(f'Pi RPC backend listening on {path}', flush=True)
        while not stopping and backend.poll() is None:
            for key, mask in selector.select(0.2):
                if key.data == 'listener':
                    connection, _ = listener.accept()
                    if client is not None:
                        connection.close()  # Do not let another client steal the live editor.
                        continue
                    client = connection
                    client.setblocking(False)
                    selector.register(client, selectors.EVENT_READ, 'client')
                elif key.data == 'output':
                    chunk = os.read(backend.stdout.fileno(), 65536)
                    if not chunk:
                        stopping = True
                        break
                    if discard_output:
                        boundary = chunk.find(b'\n')
                        if boundary < 0: continue
                        chunk = chunk[boundary + 1:]
                        discard_output = False
                    output_frames.extend(chunk)
                    while b'\n' in output_frames:
                        boundary = output_frames.index(b'\n') + 1
                        record = output_frames[:boundary]
                        del output_frames[:boundary]
                        if client is not None:
                            outgoing.extend(record)
                            if len(outgoing) > LIMIT:
                                detach()  # Slow clients cannot block remote work indefinitely.
                            else:
                                selector.modify(client, selectors.EVENT_READ | selectors.EVENT_WRITE, 'client')
                    if len(output_frames) > LIMIT:
                        detach()
                        output_frames.clear()
                        discard_output = True
                elif key.data == 'input':
                    try:
                        count = os.write(backend.stdin.fileno(), pending[0])
                        pending_bytes -= count
                        pending[0] = pending[0][count:]
                        if not pending[0]: pending.popleft()
                        if not pending: selector.unregister(backend.stdin)
                    except BlockingIOError:
                        pass
                elif key.data == 'client' and client is key.fileobj:
                    try:
                        if mask & selectors.EVENT_READ:
                            chunk = client.recv(65536)
                            if not chunk:
                                detach()
                                continue
                            incoming.extend(chunk)
                            if len(incoming) > LIMIT:
                                detach()
                                continue
                            while b'\n' in incoming:
                                boundary = incoming.index(b'\n') + 1
                                record = bytes(incoming[:boundary])
                                del incoming[:boundary]
                                if pending_bytes + len(record) > LIMIT:
                                    detach()
                                    break
                                if not pending: selector.register(backend.stdin, selectors.EVENT_WRITE, 'input')
                                pending.append(record)
                                pending_bytes += len(record)
                        if client is not None and mask & selectors.EVENT_WRITE:
                            count = client.send(outgoing)
                            del outgoing[:count]
                            if not outgoing: selector.modify(client, selectors.EVENT_READ, 'client')
                    except (ConnectionError, OSError):
                        detach()
    finally:
        detach()
        listener.close()
        selector.close()
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        backend.stdin.close()
        try:
            backend.wait(timeout=3)
        except subprocess.TimeoutExpired:
            backend.terminate()
            try:
                backend.wait(timeout=3)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--cwd', required=True)
    parser.add_argument('--binary', default='pi')
    parser.add_argument('--session')
    args = parser.parse_args()
    serve(args.socket, args.cwd, args.binary, args.session)
