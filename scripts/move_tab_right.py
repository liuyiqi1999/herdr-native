#!/usr/bin/env python3
"""Move the invoking tab's live panes into its preceding tab; never restart PTYs."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def move_tab_right(call, workspace, tab, pane):
    tabs = call('tab', 'list', '--workspace', workspace)['tabs']
    index = next(i for i, item in enumerate(tabs) if item['tab_id'] == tab)
    if len(tabs) == 1:
        call('pane', 'split', pane, '--direction', 'right', '--focus')
        return
    # At the first tab, use the next tab rather than wrap to an unrelated last tab.
    target = tabs[index - 1 if index else 1]['tab_id']
    panes = call('pane', 'list', '--workspace', workspace)['panes']
    source = [p for p in panes if p['tab_id'] == tab]
    if not source or not any(p['pane_id'] == pane for p in source):
        raise RuntimeError('Source tab changed; retry the shortcut')
    destination = [p for p in panes if p['tab_id'] == target]
    if not destination:
        raise RuntimeError('Destination tab disappeared')
    anchor = next((p for p in destination if p.get('focused')), destination[-1])['pane_id']
    # Multiple source panes form a column on the right. All PTYs remain alive.
    for i, item in enumerate(source):
        result = call('pane', 'move', item['pane_id'], '--tab', target,
                      '--target-pane', anchor, '--split', 'right' if i == 0 else 'down',
                      '--focus' if i == len(source) - 1 else '--no-focus')
        anchor = result['move_result']['pane']['pane_id']


def main():
    binary = os.environ.get('HERDR_BIN_PATH', '/Applications/Herdr Native.app/Contents/MacOS/herdr')
    workspace = os.environ['HERDR_ACTIVE_WORKSPACE_ID']
    tab = os.environ['HERDR_ACTIVE_TAB_ID']
    pane = os.environ['HERDR_ACTIVE_PANE_ID']
    def call(*args):
        result = subprocess.run([binary, *args], capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return json.loads(result.stdout)['result']
    key = hashlib.sha256(os.environ.get('HERDR_SOCKET_PATH', 'native').encode()).hexdigest()[:16]
    with open(Path(tempfile.gettempdir()) / ('herdr-move-tab-' + key + '.lock'), 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        move_tab_right(call, workspace, tab, pane)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        log = Path.home() / 'Library/Logs/Herdr Native/move-tab-right.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open('a') as stream:
            stream.write(str(error) + '\n')
        raise
