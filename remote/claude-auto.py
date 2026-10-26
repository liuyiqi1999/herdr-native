#!/usr/bin/env python3
"""Automatic Claude input bridge. The original Claude process owns all dialogs."""
import argparse
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
import shutil
import signal
import socket
import sys
import tempfile
import termios
import time
import tty

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'claude-vendor'))
import pyte


# Native mode hints follow user-defined status lines, which may wrap. Bound the
# search to the nearby footer rather than searching arbitrary terminal history.
MAX_FOOTER_ROWS = 8


def prompt_region(screen, allow_completion=False, display=None):
    """Require the live caret inside Claude's boxed composer and its idle footer."""
    # Claude's fullscreen renderer can hide the hardware caret while retaining
    # its composer coordinates. Require the surrounding UI, not DECTCEM alone.
    rows = screen.display if display is None else display
    y = screen.cursor.y
    def border(row):
        text = row.strip()
        return len(text) >= max(8, screen.columns // 2) and set(text) <= set('─━')
    top = next((i for i in range(y - 1, -1, -1) if border(rows[i])), None)
    bottom = next((i for i in range(y + 1, len(rows)) if border(rows[i])), None)
    if top is None or bottom is None or bottom - top > 15:
        return None
    if not rows[top + 1].startswith('❯') or screen.cursor.x < 2:
        return None
    footer = '\n'.join(rows[bottom + 1:bottom + 1 + MAX_FOOTER_ROWS])
    completing = allow_completion and rows[top + 1][1:].lstrip().startswith('/') and '❯ /' in footer
    idle_footer = any(token in footer for token in ('? for shortcuts', 'shift+tab to cycle', 'manual mode on'))
    if (not idle_footer and not completing) or 'esc to interrupt' in footer.lower():
        return None
    return top, bottom


class PromptSnapshot:
    """Cache presentation by PTY output generation, not wall-clock polling.

    Cursor reports can move the caret without changing cells; geometry can change
    on SIGWINCH. Both still invalidate the recognition result. Completion mode
    changes also re-evaluate the gate, without re-materializing the screen.
    """
    def __init__(self):
        self.content_key = None
        self.display = None
        self.region_key = None
        self.region = None

    def rows(self, screen, generation):
        key = (generation, screen.columns, screen.lines)
        if key != self.content_key:
            self.display = screen.display
            self.content_key = key
            self.region_key = None
        return self.display

    def detect(self, screen, generation, allow_completion=False):
        rows = self.rows(screen, generation)
        key = (self.content_key, screen.cursor.x, screen.cursor.y, allow_completion)
        if key != self.region_key:
            self.region = prompt_region(screen, allow_completion, rows)
            self.region_key = key
        return self.region


def interactive(arguments):
    # CLI commands and print/structured invocations must remain byte-for-byte native.
    commands = {'auth', 'agents', 'attach', 'auto-mode', 'doctor', 'gateway', 'import', 'install',
                'logs', 'mcp', 'plugin', 'plugins', 'purge', 'respawn', 'rm', 'setup-token',
                'stop', 'kill', 'ultrareview', 'update', 'upgrade', 'remote-control', 'daemon'}
    if arguments and arguments[0] in commands:
        return False
    return not any(arg in ('-p', '--print', '-h', '--help', '-v', '--version', '--bg', '--background')
                   or arg.startswith(('--input-format', '--output-format', '--sdk-url'))
                   or (arg.startswith('-') and not arg.startswith('--') and 'p' in arg[1:])
                   for arg in arguments)


def terminal_reply(data):
    return re.fullmatch(
        rb'(?:\x1b\[(?:\??[0-9;]+c|\?[0-9]+u|[0-9]+;[0-9]+R|[468];[0-9]+;[0-9]+t|\?[0-9]+;[0-9]+\$y|[IO])'
        rb'|\x1b\](?:10|11);rgb:[0-9a-fA-F/]+(?:\x07|\x1b\\))+', data) is not None


def write_all(fd, data):
    while data:
        count = os.write(fd, data)
        data = data[count:]


def run(binary, arguments, ready=None):
    executable = shutil.which(binary)
    if not executable:
        raise RuntimeError('Claude Code executable not found: ' + binary)
    if not interactive(arguments) or not sys.stdin.isatty() or not sys.stdout.isatty() or os.environ.get('HERDR_NATIVE_CLAUDE') == '0':
        os.execv(executable, [executable] + arguments)
    # Herdr probes the outer foreground process, not Claude's nested PTY.
    # Re-exec so Linux /proc/<pid>/environ exposes the identity hint too;
    # mutating os.environ in an already running Python process is insufficient.
    if os.environ.get('HERDR_AGENT') != 'claude':
        argv = [sys.executable, str(Path(__file__).resolve()), '--binary', executable]
        if ready:
            argv.extend(['--ready', str(ready)])
        os.execve(sys.executable, argv + ['--'] + arguments,
                  {**os.environ, 'HERDR_AGENT': 'claude'})
    with tempfile.TemporaryDirectory(prefix='hn-claude-', dir='/tmp') as directory:
        control_path = directory + '/control.sock'
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(control_path)
        os.chmod(control_path, 0o600)
        server.listen(1)
        editor = ' '.join(shlex.quote(part) for part in [sys.executable, str(ROOT / 'claude-editor.py'), 'edit'])
        pid, master = pty.fork()
        if pid == 0:
            server.close()
            os.environ.update(VISUAL=editor, EDITOR=editor, HERDR_CLAUDE_AUTO_CONTROL=control_path)
            os.execv(executable, [executable] + arguments)
        if ready:
            with open(ready, 'w') as ready_file:
                json.dump({'pid': os.getpid(), 'claude_pid': pid}, ready_file)
            os.chmod(ready, 0o600)
        terminal = sys.stdin.fileno()
        previous = termios.tcgetattr(terminal)
        cols, rows = os.get_terminal_size(sys.stdout.fileno())
        class Screen(pyte.Screen):
            def report_device_status(self, *args, **kwargs):
                pass
            def report_device_attributes(self, *args, **kwargs):
                pass
        screen = Screen(cols, rows)
        stream = pyte.Stream(screen)
        snapshot = PromptSnapshot()
        decoder = codecs.getincrementaldecoder('utf8')('replace')
        input_decoder = codecs.getincrementaldecoder('utf8')('replace')
        peer = None
        discovery_server = None
        discovery_peer = None
        discovery_buffer = b''
        discovery_directory = None
        discovery_generation = -1
        peer_buffer = b''
        frozen = False
        buffered = bytearray()
        region = None
        original_lines = None
        marked_size = None
        active_socket = None
        entering_at = None
        last_output = time.monotonic()
        last_input = last_output
        pending_action = None
        action_deadline = 0
        action_generation = 0
        generation = 0
        suspended = False
        left_prompt = False
        suspend_armed = False
        queued_input = ''
        stopping = False
        size_synced = False
        def resize(*_):
            nonlocal cols, rows, suspended, size_synced, region, original_lines, marked_size
            next_cols, next_rows = os.get_terminal_size(sys.stdout.fileno())
            if next_cols < 8 or next_rows < 3:
                return
            if size_synced and (cols, rows) == (next_cols, next_rows):
                return
            cols, rows = next_cols, next_rows
            size_synced = True
            screen.resize(rows, cols)
            fcntl.ioctl(master, termios.TIOCSWINSZ, fcntl.ioctl(terminal, termios.TIOCGWINSZ, b'\0' * 8))
            os.kill(pid, signal.SIGWINCH)
            # The next normal Claude repaint establishes fresh geometry.
            if frozen:
                if active_socket and rows >= 3:
                    top = min(region[0], rows - 3)
                    region = (top, top + 2)
                    display = snapshot.rows(screen, generation)
                    original_lines = [(i, display[i]) for i in region]
                    marked_size = (cols, rows)
                    paint(original_lines, active_socket)
                send({'type': 'resize'})
        def send(value):
            if peer:
                try:
                    peer.sendall((json.dumps(value) + '\n').encode())
                except OSError:
                    pass
        def release():
            nonlocal frozen, buffered, entering_at, active_socket
            active_socket = None
            if region and original_lines and marked_size == (cols, rows):
                paint(original_lines, None)
            if buffered:
                write_all(1, bytes(buffered))
            buffered = bytearray()
            frozen = False
            entering_at = None
        def paint(lines, address):
            if address and os.environ.get('HERDR_CLAUDE_DIAGNOSTICS'):
                with open(os.environ['HERDR_CLAUDE_DIAGNOSTICS'], 'a') as log:
                    log.write(json.dumps({'event': 'marked', 'socket': address, 'lines': [i for i, _ in lines], 'size': [cols, rows]}) + '\n')
            marker = ('https://herdr-native.invalid/editor/v1/' + base64.urlsafe_b64encode(address.encode()).decode().rstrip('=')) if address else ''
            data = '\x1b7'
            for y, line in lines:
                data += '\x1b[{};1H\x1b]8;;{}\x1b\\{}\x1b]8;;\x1b\\'.format(y + 1, marker, line)
            data += '\x1b8'
            write_all(1, data.encode())
        def close_discovery():
            nonlocal discovery_server, discovery_peer, discovery_directory, discovery_buffer
            if discovery_peer:
                discovery_peer.close()
                discovery_peer = None
            if discovery_server:
                discovery_server.close()
                discovery_server = None
            if discovery_directory:
                discovery_directory.cleanup()
                discovery_directory = None
            discovery_buffer = b''
        def begin_edit(current):
            nonlocal region, original_lines, marked_size, frozen, buffered, entering_at
            region = current
            display = snapshot.rows(screen, generation)
            original_lines = [(i, display[i]) for i in region]
            marked_size = (cols, rows)
            frozen = True
            buffered = bytearray()
            entering_at = time.monotonic()
            write_all(master, b'\x07')
        def stop(*_):
            nonlocal stopping
            stopping = True
        old_resize = signal.signal(signal.SIGWINCH, resize)
        old_term = signal.signal(signal.SIGTERM, stop)
        old_hup = signal.signal(signal.SIGHUP, stop)
        try:
            tty.setraw(terminal)
            resize()
            while not stopping:
                readable, _, _ = select.select([master, terminal, server] + ([peer] if peer else []) +
                                               ([discovery_server] if discovery_server else []) +
                                               ([discovery_peer] if discovery_peer else []), [], [], .05)
                if master in readable:
                    try:
                        data = os.read(master, 65536)
                    except OSError:
                        break
                    if not data:
                        break
                    stream.feed(decoder.decode(data))
                    generation += 1
                    last_output = time.monotonic()
                    if frozen:
                        buffered.extend(data)
                        if len(buffered) > 2 * 1024 * 1024:
                            send({'type': 'resize'})  # Cancel and restore; never hide unbounded output.
                            release()
                            suspended = True
                    else:
                        write_all(1, data)
                if terminal in readable:
                    data = os.read(terminal, 65536)
                    if not data:
                        break
                    last_input = time.monotonic()
                    if terminal_reply(data):
                        for report in re.finditer(rb'\x1b\[([0-9]+);([0-9]+)R', data):
                            screen.cursor_position(int(report[1]), int(report[2]))
                    # Terminal capability replies belong to Claude, even during handoff.
                    if frozen and not terminal_reply(data):
                        decoded = input_decoder.decode(data)
                        if peer:
                            send({'type': 'input', 'data': decoded})
                        else:
                            queued_input += decoded
                    else:
                        if pending_action and not terminal_reply(data):
                            pending_action = None
                            suspended = True
                        write_all(master, data)
                if discovery_server and discovery_server in readable:
                    incoming, _ = discovery_server.accept()
                    if discovery_peer:
                        incoming.close()
                    else:
                        discovery_peer = incoming
                        discovery_buffer = b''
                if discovery_peer and discovery_peer in readable:
                    data = discovery_peer.recv(65536)
                    discovery_buffer += data
                    if not data or len(discovery_buffer) > 1024 * 1024:
                        close_discovery()
                    elif b'\n' in discovery_buffer:
                        current = snapshot.detect(screen, generation)
                        try:
                            request = json.loads(discovery_buffer.split(b'\n', 1)[0])
                            valid = request.get('type') == 'get_state' and current is not None and not frozen
                            reply = {'id': request.get('id'), 'type': 'response', 'success': valid,
                                     'data': {'editorKind': 'claude-activate'}, 'error': 'Claude has left the input box'}
                            discovery_peer.sendall((json.dumps(reply) + '\n').encode())
                            if valid:
                                begin_edit(current)
                        except (ValueError, AttributeError, OSError):
                            pass
                        close_discovery()
                if server in readable:
                    incoming, _ = server.accept()
                    if peer:
                        incoming.close()
                    else:
                        peer = incoming
                        peer_buffer = b''
                if peer and peer in readable:
                    data = peer.recv(65536)
                    if not data:
                        peer.close()
                        peer = None
                        if frozen:
                            release()
                            suspended = True
                        continue
                    peer_buffer += data
                    if len(peer_buffer) > 1024 * 1024:
                        peer.close()
                        peer = None
                        release()
                        suspended = True
                        continue
                    while b'\n' in peer_buffer:
                        line, peer_buffer = peer_buffer.split(b'\n', 1)
                        try:
                            request = json.loads(line)
                        except ValueError:
                            continue
                        if request.get('type') == 'begin':
                            automatic = frozen and entering_at is not None
                            address = request.get('socket', '')
                            if not (address.startswith('/tmp/hn-editor-') and address.endswith('/editor.sock') and '..' not in address):
                                automatic = False
                            send({'automatic': automatic, 'input': queued_input if automatic else ''})
                            queued_input = ''
                            if automatic:
                                active_socket = address
                                paint(original_lines, address)
                                entering_at = None
                        elif request.get('type') == 'end':
                            if isinstance(request.get('input'), str):
                                queued_input += request['input']
                            action = request.get('action')
                            if frozen and action in ('submit', 'cancel', 'key'):
                                key = '\r' if action == 'submit' else request.get('key', '\x1b')
                                if isinstance(key, str) and len(key) <= 100:
                                    pending_action = key if action != 'cancel' else None
                                    action_generation = generation
                                    action_deadline = time.monotonic() + 5
                                suspended = action != 'submit' and key != ''
                                left_prompt = False
                                suspend_armed = False
                            send({'ended': True})
                            release()
                now = time.monotonic()
                current = snapshot.detect(screen, generation, allow_completion=bool(pending_action))
                if suspended and not pending_action and current and generation > action_generation:
                    suspend_armed = True
                if suspended and suspend_armed and current is None:
                    left_prompt = True
                if suspended and left_prompt and current:
                    suspended = False
                if entering_at and now - entering_at > 4:
                    release()
                    suspended = True
                    # No confirmed external editor means no auto-submission is possible.
                    if queued_input:
                        write_all(master, queued_input.encode())
                        queued_input = ''
                if pending_action and now > action_deadline:
                    pending_action = None
                    suspended = True
                if pending_action and not frozen and generation > action_generation and current and now - last_output > .15:
                    key = pending_action
                    pending_action = None
                    write_all(master, key.encode())
                    last_input = now
                if not frozen and (not current or suspended or pending_action):
                    close_discovery()
                if not frozen and not pending_action and not suspended and current and now - max(last_output, last_input) > .35:
                    if discovery_server is None:
                        discovery_directory = tempfile.TemporaryDirectory(prefix='hn-editor-', dir='/tmp')
                        discovery_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        discovery_server.bind(discovery_directory.name + '/editor.sock')
                        os.chmod(discovery_directory.name + '/editor.sock', 0o600)
                        discovery_server.listen(1)
                        discovery_generation = -1
                    if discovery_generation != generation:
                        display = snapshot.rows(screen, generation)
                        paint([(i, display[i]) for i in current], discovery_directory.name + '/editor.sock')
                        discovery_generation = generation
        finally:
            close_discovery()
            if peer:
                peer.close()
            server.close()
            signal.signal(signal.SIGWINCH, old_resize)
            signal.signal(signal.SIGTERM, old_term)
            signal.signal(signal.SIGHUP, old_hup)
            termios.tcsetattr(terminal, termios.TCSANOW, previous)
            os.close(master)
            try:
                os.kill(pid, signal.SIGHUP)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 3
            status = None
            while time.monotonic() < deadline:
                child, child_status = os.waitpid(pid, os.WNOHANG)
                if child:
                    status = child_status
                    break
                time.sleep(.05)
            if status is None:
                os.kill(pid, signal.SIGKILL)
                _, status = os.waitpid(pid, 0)
        return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', default='claude')
    parser.add_argument('--ready')
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    return run(args.binary, arguments, args.ready)


if __name__ == '__main__':
    raise SystemExit(main())
