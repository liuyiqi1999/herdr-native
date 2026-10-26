#!/usr/bin/env python3
"""Exercise direct shortcut bytes through a real Herdr TUI in an isolated test session."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import struct
import subprocess
import tempfile
import termios
import time
import uuid
from build import profiles

binary = shutil.which("herdr")
assert binary, "herdr must be installed"
session = "native-test-" + uuid.uuid4().hex[:8]
with tempfile.TemporaryDirectory(prefix="hnt-", dir="/tmp") as temporary:
    config = Path(temporary) / "config.toml"
    marker = Path(temporary) / "new-tab-runs"
    command = f"printf 'started\\n' >> '{marker}'"
    config.write_text(profiles()[1].replace('new_tab_command = ""',
                                           f"new_tab_command = {json.dumps(command)}"))
    env = dict(os.environ, HERDR_CONFIG_PATH=str(config), TERM="xterm-256color",
               XDG_CONFIG_HOME=str(Path(temporary) / "config"),
               XDG_STATE_HOME=str(Path(temporary) / "state"))
    for name in ("HERDR_SOCKET_PATH", "HERDR_CLIENT_SOCKET_PATH", "HERDR_SESSION", "HERDR_ENV", "HERDR_PANE_ID"):
        env.pop(name, None)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 42, 140, 0, 0))
    process = subprocess.Popen([binary, "--session", session], stdin=slave, stdout=slave,
                               stderr=slave, env=env, start_new_session=True)
    os.close(slave)

    output = bytearray()

    def drain(seconds=0.4):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if select.select([master], [], [], 0.05)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    return
                output.extend(data)
                if b"\x1b[6n" in data:
                    os.write(master, b"\x1b[1;1R")
                if b"\x1b[?u" in data:
                    os.write(master, b"\x1b[?0u")
                if b"\x1b[c" in data:
                    os.write(master, b"\x1b[?62;22c")

    def query(*args):
        result = subprocess.run([binary, "--session", session, *args], env=env,
                                capture_output=True, text=True, timeout=5)
        if result.returncode:
            raise RuntimeError(result.stderr + "\nTUI output: " + output.decode(errors="replace"))
        return json.loads(result.stdout)

    try:
        drain(3)
        initial = query("workspace", "list")
        print("Initial workspace state:", json.dumps(initial))
        before = query("tab", "list")
        os.write(master, b"\x1b[116;9u")  # Cmd+T, no prefix
        drain(0.5)
        os.write(master, b"\r")  # Confirm Herdr's new-tab naming dialog.
        drain(1)
        after = query("tab", "list")
        print("Tabs before:", json.dumps(before))
        print("Tabs after Cmd+T:", json.dumps(after))
        assert len(after["result"]["tabs"]) == len(before["result"]["tabs"]) + 1, "Cmd+T did not create a tab after confirmation"
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            drain(0.2)
        assert marker.exists(), "Startup command missing; TUI output: " + output.decode(errors="replace")
        assert marker.read_text() == "started\n", "New tab command did not run exactly once"
        before_split = query("pane", "list")
        os.write(master, b"\x1b[100;9u")  # Cmd+D
        drain(1)
        after_split = query("pane", "list")
        print("Panes after Cmd+D:", json.dumps(after_split))
        assert len(after_split["result"]["panes"]) == len(before_split["result"]["panes"]) + 1, "Cmd+D did not split the pane"
        os.write(master, b"\x1b[1;11D")  # Cmd+Option+Left
        drain(0.5)
        left = query("pane", "list")
        focused = lambda state: next(p["pane_id"] for p in state["result"]["panes"] if p["focused"])
        assert focused(left) != focused(after_split), "Cmd+Option+Left did not change focus"
        os.write(master, b"\x1b[119;9u")  # Cmd+W (there are two panes)
        drain(0.5)
        closed = query("pane", "list")
        assert len(closed["result"]["panes"]) == len(left["result"]["panes"]) - 1, "Cmd+W did not close the pane"
        assert marker.read_text() == "started\n", "Splitting or focusing a pane reran the new tab command"
        print("PASS: Cmd+T auto command, Cmd+D, Cmd+Option+Left, Cmd+W through a real TUI")
    finally:
        # This unique session and every pane in it belong exclusively to this test.
        os.close(master)
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()  # Only the disposable PTY test client.
            process.wait(timeout=5)
        subprocess.run([binary, "session", "stop", session],
                       env=env, capture_output=True, timeout=30)
        subprocess.run([binary, "session", "delete", session],
                       env=env, capture_output=True, timeout=10)
