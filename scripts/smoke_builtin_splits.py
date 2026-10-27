#!/usr/bin/env python3
"""Exercise native pane splits in one plain Ghostty surface, in an isolated session."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
APP = Path(os.environ.get('HERDR_TEST_APP', ROOT / 'dist/Herdr Native Plain Test.app'))
BINARY = APP / 'Contents/MacOS/herdr'
SESSION = 'builtin-test-' + uuid.uuid4().hex[:8]


def script(body):
    source = f'''using terms from application "/Applications/Herdr Native.app"
 tell application "{APP}"
{body}
 end tell
end using terms from'''
    return subprocess.check_output(['osascript', '-e', source], text=True, timeout=20).strip()


def wait_for(predicate, message):
    for _ in range(100):
        try:
            value = predicate()
            if value:
                return value
        except (subprocess.SubprocessError, json.JSONDecodeError):
            pass
        time.sleep(0.2)
    raise AssertionError(message)


with tempfile.TemporaryDirectory(prefix='hbuiltin-', dir='/tmp') as tmp:
    config = Path(tmp) / 'herdr.toml'
    config.write_text('onboarding = false\n[keys]\nsplit_vertical = "super+d"\n'
                      'split_horizontal = "super+shift+d"\nnew_tab = "super+t"\n'
                      '[ui]\nprompt_new_tab_name = false\nprompt_new_tab_command = false\n'
                      'new_tab_command = ""\n[terminal]\ndefault_shell = "/bin/sh"\n'
                      'shell_mode = "non_login"\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
    env.update(HERDR_NATIVE_CONFIG_PATH=str(config), HERDR_CONFIG_PATH=str(config),
               HERDR_NATIVE_SESSION=SESSION, XDG_CONFIG_HOME=tmp + '/c', XDG_STATE_HOME=tmp + '/s')
    app_env = dict(env, HERDR_ENV='1', HERDR_PANE_ID='stale-pane',
                   HERDR_SOCKET_PATH='/tmp/herdr-invalid.sock',
                   HERDR_NATIVE_GLASS='1', HERDR_NATIVE_GROUP_ROOT=tmp + '/obsolete')
    resources = APP / 'Contents/Resources'
    for obsolete in ['native-glass.conf', 'native_tab_group.py', 'ghostty-tab-drop.patch', 'ghostty-glass.patch']:
        assert not (resources / obsolete).exists(), obsolete
    with open(ROOT / '.local/plain-app.log', 'w') as log:
        app = subprocess.Popen([str(APP / 'Contents/MacOS/HerdrNative')], env=app_env, stdout=log, stderr=log)

        def cli(*args):
            result = subprocess.check_output([str(BINARY), '--session', SESSION, *args],
                                             env=env, text=True, stderr=subprocess.DEVNULL, timeout=15)
            return json.loads(result)['result']

        try:
            panes = wait_for(lambda: cli('pane', 'list')['panes'], 'Session did not start')
            originals = {p['terminal_id'] for p in panes}
            original_tab = panes[0]['tab_id']
            for action, count in [('csi:100;9u', 2), ('csi:100;10u', 3)]:
                script(f'perform action "{action}" on focused terminal of selected tab of front window')
                wait_for(lambda: len(cli('pane', 'list')['panes']) == count, 'Built-in split failed')
                assert script('return count of terminals') == '1', 'Custom host split created'
                current = cli('pane', 'list')['panes']
                assert {p['tab_id'] for p in current} == {original_tab}
                assert originals <= {p['terminal_id'] for p in current}
            print('PASS: Cmd+D and Cmd+Shift+D create built-in panes in one host surface', flush=True)
            script('perform action "csi:116;9u" on focused terminal of selected tab of front window')
            wait_for(lambda: len(cli('tab', 'list')['tabs']) == 2, 'New tab failed')
            assert not Path(tmp + '/obsolete').exists()
            print('PASS: new tab, original terminal identity, stale environment isolation; no glass resources', flush=True)
        finally:
            try:
                script('quit')
            except subprocess.SubprocessError:
                app.terminate()
            try:
                app.wait(timeout=15)
            except subprocess.TimeoutExpired:
                app.kill()
                app.wait()
            subprocess.run([str(BINARY), 'session', 'stop', SESSION], env=env, capture_output=True, timeout=15)
            subprocess.run([str(BINARY), 'session', 'delete', SESSION], env=env, capture_output=True, timeout=15)
