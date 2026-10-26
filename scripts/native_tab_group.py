#!/usr/bin/env python3
"""Create a native split with its own Herdr client and independent tab strip.

No pane.move, terminal input, process restart, or backend replacement is involved.
Ownership files are scoped to one app launch and server boot; client detach returns
its tabs to the main group. Requires Ghostty's native scripting dictionary.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import uuid

SCRIPT = r'''
using terms from application "/Applications/Herdr Native.app"
    on run argv
        set appPath to item 1 of argv
        set cmd to item 2 of argv
        tell application appPath
            set sourceID to item 4 of argv
            if sourceID is "" then
                set target to focused terminal of selected tab of front window
            else
                set target to terminal id sourceID
            end if
            set splitDirection to item 3 of argv
            if splitDirection is "left" then
                set newTerminal to split target direction left with configuration {command:cmd}
            else if splitDirection is "up" then
                set newTerminal to split target direction up with configuration {command:cmd}
            else if splitDirection is "down" then
                set newTerminal to split target direction down with configuration {command:cmd}
            else
                set newTerminal to split target direction right with configuration {command:cmd}
            end if
            return (id of target) & linefeed & (id of newTerminal)
        end tell
    end run
end using terms from
'''


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    temp.write_text(text)
    temp.replace(path)


def split_group(args, call, launch, focus=None):
    root = Path(args.root)
    owners = root / args.boot
    source = owners / args.tab
    old_owner = source.read_text() if source.exists() else None
    current_owner = old_owner or 'main'
    if current_owner != args.group:
        raise RuntimeError('Tab already moved; ignoring repeated shortcut')
    # Validate explicit identities, not another client's current focus.
    tabs = call('tab', 'list', '--workspace', args.workspace)['tabs']
    if not any(t['tab_id'] == args.tab for t in tabs):
        raise RuntimeError('Source tab has closed')
    target = getattr(args, 'target_group', None)
    if target:
        if target == args.group:
            raise RuntimeError('Source and destination groups are identical')
        if not (root / ('surface-' + target)).is_file():
            raise RuntimeError('Destination group is no longer available')
    created = None
    previous = args.previous
    if not previous:
        created = call('tab', 'create', '--workspace', args.workspace, '--no-focus')['tab']['tab_id']
        atomic_write(owners / created, args.group)
        previous = created
    group = target or uuid.uuid4().hex
    focus_paths = [root / ('focus-' + args.group), root / ('focus-' + group)]
    old_focus = [p.read_text() if p.exists() else None for p in focus_paths]
    try:
        atomic_write(focus_paths[0], previous)
        atomic_write(focus_paths[1], args.tab)
        atomic_write(source, group)
        if not target:
            launch(group)
        elif focus is not None:
            focus(group)
    except Exception:
        if old_owner is None:
            source.unlink(missing_ok=True)
        else:
            atomic_write(source, old_owner)
        for path, old in zip(focus_paths, old_focus):
            if old is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, old)
        # Keep the newly created shell rather than risk closing a user-occupied tab.
        raise
    return group


def main():
    parser = argparse.ArgumentParser()
    for name in ('root', 'group', 'boot', 'tab', 'workspace', 'previous'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--direction', choices=('right', 'down', 'left', 'up'), default='right')
    parser.add_argument('--target-group')
    args = parser.parse_args()
    for value in (args.group, args.boot, args.tab, args.workspace, args.target_group or 'main'):
        if not value or not all(c.isascii() and (c.isalnum() or c in '-_:') for c in value):
            raise ValueError('Invalid identity')
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    binary = os.environ['HERDR_LOCAL_CLI']
    app = os.environ.get('HERDR_NATIVE_APP', '/Applications/Herdr Native.app')
    def call(*command):
        response = subprocess.run([binary, *command], capture_output=True, text=True, timeout=15)
        if response.returncode:
            raise RuntimeError(response.stderr or response.stdout)
        return json.loads(response.stdout)['result']
    def launch(group):
        # These are values passed through argv to AppleScript, not interpolated code.
        env = {
            'HERDR_NATIVE_GROUP_ROOT': str(root),
            'HERDR_NATIVE_GROUP_ID': group,
            'HERDR_NATIVE_GROUP_LAUNCHER': str(Path(__file__).resolve()),
            'HERDR_NATIVE_APP': app,
            'HERDR_CONFIG_PATH': os.environ['HERDR_CONFIG_PATH'],
        }
        command = shlex.join(['/usr/bin/env', *[k + '=' + v for k, v in env.items()],
                              binary, '--session', os.environ.get('HERDR_SESSION', 'native')])
        source_path = root / ('surface-' + args.group)
        source_id = source_path.read_text().strip() if source_path.exists() else ''
        result = subprocess.run(['/usr/bin/osascript', '-e', SCRIPT, app, command, args.direction, source_id],
                                text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
        source_surface, new_surface = result.stdout.strip().splitlines()
        atomic_write(root / ('surface-' + args.group), source_surface)
        atomic_write(root / ('surface-' + group), new_surface)
    def focus(group):
        surface = (root / ('surface-' + group)).read_text().strip()
        script = '''using terms from application "/Applications/Herdr Native.app"
            on run argv
                tell application (item 1 of argv)
                    focus terminal id (item 2 of argv)
                end tell
            end run
        end using terms from'''
        result = subprocess.run(['/usr/bin/osascript', '-e', script, app, surface],
                                text=True, capture_output=True, timeout=20)
        if result.returncode:
            raise RuntimeError(result.stderr.strip())
    with (root / '.split.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        split_group(args, call, launch, focus)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        root = os.environ.get('HERDR_NATIVE_GROUP_ROOT')
        if root:
            atomic_write(Path(root) / ('error-' + os.environ.get('HERDR_NATIVE_GROUP_ID', 'main')), str(error))
        raise
