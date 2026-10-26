#!/usr/bin/env python3
"""Claude Code's supported $VISUAL bridge; never submits a model prompt."""
import argparse
import base64
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import signal
import socket
import sys
import tempfile
import termios
import tty
import time

MARKER = 'https://herdr-native.invalid/editor/v1/'


def note(event, **fields):
    path = os.environ.get('HERDR_CLAUDE_DIAGNOSTICS')
    if path:
        with open(path, 'a') as log:
            log.write(json.dumps({'event': event, **fields}) + '\n')


def launch(binary, arguments, ready=None):
    executable = shutil.which(binary)
    if not executable:
        raise RuntimeError('Claude Code executable not found: ' + binary)
    editor = ' '.join(shlex.quote(arg) for arg in [sys.executable, str(Path(__file__).resolve()), 'edit'])
    os.environ.update(VISUAL=editor, EDITOR=editor)
    if ready:
        Path(ready).touch(mode=0o600)
    os.execv(executable, [executable, *arguments])


def edit(path):
    # Claude owns this file and reads it after the external editor exits.
    text = path.read_text()
    with tempfile.TemporaryDirectory(prefix='hn-editor-', dir='/tmp') as directory:
        address = directory + '/editor.sock'
        marker = MARKER + base64.urlsafe_b64encode(address.encode()).decode().rstrip('=')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(address)
            os.chmod(address, 0o600)
            server.listen(1)
            control = None
            automatic = False
            initial_input = ''
            control_buffer = b''
            ending = {'type': 'end', 'action': 'cancel'}
            started = time.monotonic()
            if os.environ.get('HERDR_CLAUDE_AUTO_CONTROL'):
                control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                control.settimeout(4)
                control.connect(os.environ['HERDR_CLAUDE_AUTO_CONTROL'])
                control.sendall((json.dumps({'type': 'begin', 'socket': address}) + '\n').encode())
                while b'\n' not in control_buffer:
                    control_buffer += control.recv(65536)
                line, control_buffer = control_buffer.split(b'\n', 1)
                response = json.loads(line)
                automatic = response.get('automatic', False)
                note('begin', automatic=automatic)
                initial_input = response.get('input', '')
                control.settimeout(None)
            terminal = sys.stdin.fileno()
            previous = termios.tcgetattr(terminal)
            def paint(*_):
                if automatic:
                    return
                cols, rows = shutil.get_terminal_size()
                lines = ['Claude Code · local editing', 'Enter / Ctrl+G: return draft to Claude · Esc: cancel']
                lines += [' ' * max(1, cols - 1)] * max(3, rows - len(lines) - 1)
                lines[0] = '\x1b]8;;' + marker + '\x1b\\' + lines[0] + '\x1b]8;;\x1b\\'
                lines[-1] = '\x1b]8;;' + marker + '\x1b\\' + lines[-1] + '\x1b]8;;\x1b\\'
                sys.stdout.write('\x1b[H\x1b[2J' + '\r\n'.join(lines))
                sys.stdout.flush()
            old_resize = signal.signal(signal.SIGWINCH, paint)
            peer = None
            had_client = False
            pending_resize = False
            buffer = b''
            try:
                tty.setraw(terminal)
                if not automatic:
                    sys.stdout.write('\x1b[?1049h')
                paint()
                while True:
                    readable, _, _ = select.select([server, terminal] + ([peer] if peer else []) + ([control] if control else []), [], [], .2)
                    if automatic and not peer and not had_client and time.monotonic() - started > 10:
                        note('no_client_timeout')
                        ending = {'type': 'end', 'action': 'key', 'key': '', 'input': initial_input}
                        return 0  # A plain Herdr client without the native renderer stays usable.
                    if control and control in readable:
                        data = control.recv(65536)
                        if not data:
                            note('parent_closed')
                            return 1
                        control_buffer += data
                        while b'\n' in control_buffer:
                            line, control_buffer = control_buffer.split(b'\n', 1)
                            event = json.loads(line)
                            if event.get('type') == 'resize':
                                note('resize', connected=peer is not None)
                                if peer:
                                    peer.sendall((json.dumps({'type': 'editor_resize'}) + '\n').encode())
                                elif had_client:
                                    pending_resize = True
                                else:
                                    ending = {'type': 'end', 'action': 'key', 'key': '', 'input': initial_input}
                                    return 0
                            elif event.get('type') == 'input':
                                if not peer and event.get('data') in ('\x03', '\x1b'):
                                    return 1
                                if peer:
                                    peer.sendall((json.dumps({'type': 'editor_input', 'data': event['data']}) + '\n').encode())
                                else:
                                    initial_input += event['data']
                    if terminal in readable:
                        data = os.read(terminal, 4096)
                        if not data or b'\x03' in data or b'\x1b' == data:
                            return 1
                    if server in readable:
                        client, _ = server.accept()
                        if peer:
                            client.close()
                        else:
                            peer = client
                            had_client = True
                            note('client_connected')
                            buffer = b''
                    if peer and peer in readable:
                        data = peer.recv(65536)
                        if not data:
                            peer.close()
                            peer = None
                            continue
                        buffer += data
                        if len(buffer) > 1024 * 1024:
                            peer.close()
                            peer = None
                            continue
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n', 1)
                            done = None
                            request = None
                            try:
                                request = json.loads(line)
                                note('request', kind=request.get('type'))
                                reply = {'id': request.get('id'), 'type': 'response', 'success': True}
                                if request.get('type') == 'get_state':
                                    reply['data'] = {'text': text, 'editorKind': 'claude-auto' if automatic else 'claude-external', 'focused': True, 'initialInput': initial_input, 'resizeRequested': pending_resize}
                                    pending_resize = False
                                    initial_input = ''
                                elif request.get('type') == 'prompt' or (automatic and request.get('type') == 'key' and isinstance(request.get('key'), str) and len(request['key']) <= 100):
                                    message = request.get('message')
                                    if not isinstance(message, str) or len(message.encode()) > 512 * 1024:
                                        raise ValueError('Invalid draft')
                                    with path.open('w') as file:
                                        file.write(message)
                                        file.flush()
                                        os.fsync(file.fileno())
                                    ending = {'type': 'end', 'action': 'submit' if request['type'] == 'prompt' else 'key', 'key': request.get('key', '')}
                                    reply['data'] = {'saved': True}
                                    done = 0
                                elif request.get('type') == 'key' and request.get('key') in ('\x1b', '\x03'):
                                    reply['data'] = {'focused': False}
                                    done = 1
                                else:
                                    raise ValueError('Return to Claude Code to use this command')
                            except (ValueError, TypeError, AttributeError, OSError) as error:
                                reply = {'id': request.get('id') if isinstance(request, dict) else None,
                                         'type': 'response', 'success': False, 'error': str(error)}
                            try:
                                peer.sendall((json.dumps(reply) + '\n').encode())
                            except OSError:
                                peer.close()
                                peer = None
                            if done is not None:
                                return done
                            if peer is None:
                                break
            finally:
                note('end', action=ending)
                if control:
                    try:
                        control.settimeout(2)
                        control.sendall((json.dumps(ending) + '\n').encode())
                        while True:
                            while b'\n' not in control_buffer:
                                data = control.recv(65536)
                                if not data:
                                    raise OSError('Parent disconnected')
                                control_buffer += data
                            line, control_buffer = control_buffer.split(b'\n', 1)
                            if json.loads(line).get('ended'):
                                break
                    except (OSError, ValueError):
                        pass
                    control.close()
                if peer:
                    peer.close()
                signal.signal(signal.SIGWINCH, old_resize)
                termios.tcsetattr(terminal, termios.TCSADRAIN, previous)
                if not automatic:
                    sys.stdout.write('\x1b[?1049l')
                    sys.stdout.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    editor = commands.add_parser('edit')
    editor.add_argument('file', type=Path)
    run = commands.add_parser('launch')
    run.add_argument('--ready')
    run.add_argument('--binary', default='claude')
    run.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == 'edit':
        return edit(args.file)
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    launch(args.binary, arguments, args.ready)


if __name__ == '__main__':
    raise SystemExit(main())
