#!/usr/bin/env python3
"""Exercise the real Glass app in an isolated session and capture only its window."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'dist/Herdr Native Glass Test.app'
BINARY = APP / 'Contents/MacOS/herdr'
SESSION = 'glass-test-' + uuid.uuid4().hex[:8]
ARTIFACTS = ROOT / '.local/glass-acceptance'
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def script(body):
    source = f'''using terms from application "/Applications/Herdr Native.app"
 tell application "{APP}"
{body}
 end tell
end using terms from'''
    result = subprocess.run(['osascript', '-e', source], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def wait_for(predicate, message):
    for _ in range(100):
        try:
            value = predicate()
            if value:
                return value
        except (RuntimeError, FileNotFoundError, json.JSONDecodeError):
            pass
        time.sleep(0.2)
    raise RuntimeError(message)


# Darwin Unix-domain socket paths must stay below 104 bytes.
with tempfile.TemporaryDirectory(prefix='hglass-', dir='/tmp') as tmp:
    config = Path(tmp) / 'herdr.toml'
    config.write_text('onboarding = false\n[keys]\nsplit_vertical = "super+d"\n'
                      'new_tab = "super+t"\ntoggle_sidebar = "super+b"\n[ui]\nprompt_new_tab_name = false\n'
                      'prompt_new_tab_command = false\nnew_tab_command = ""\n'
                      '[terminal]\ndefault_shell = "/bin/sh"\nshell_mode = "non_login"\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
    env.update(HERDR_NATIVE_CONFIG_PATH=str(config), HERDR_CONFIG_PATH=str(config),
               HERDR_NATIVE_SESSION=SESSION, XDG_CONFIG_HOME=tmp + '/config', XDG_STATE_HOME=tmp + '/state')
    before = set(Path(tempfile.gettempdir()).glob('herdr-groups-*'))
    with (ARTIFACTS / 'app.log').open('w') as log:
        # Reproduce launching the desktop app from an existing Herdr pane.
        # Only the app gets these stale markers; the test CLI keeps clean routing.
        app_env = dict(env, HERDR_ENV='1', HERDR_PANE_ID='stale-pane',
                       HERDR_PANE_RUNTIME_ID='stale-runtime', HERDR_TAB_ID='stale-tab',
                       HERDR_WORKSPACE_ID='stale-workspace', HERDR_SESSION='wrong-session',
                       HERDR_SOCKET_PATH='/tmp/herdr-glass-invalid.sock',
                       HERDR_CLIENT_SOCKET_PATH='/tmp/herdr-glass-invalid-client.sock')
        app = subprocess.Popen([str(APP / 'Contents/MacOS/HerdrNative')], env=app_env, stdout=log, stderr=log)

        def cli(*args):
            result = subprocess.run([str(BINARY), '--session', SESSION, *args], env=env,
                                    capture_output=True, text=True, timeout=15)
            if result.returncode:
                (ARTIFACTS / 'last-cli-error.log').write_text(result.stderr + result.stdout)
                raise RuntimeError(result.stderr + result.stdout)
            return json.loads(result.stdout)['result']

        def packets():
            roots = set(Path(tempfile.gettempdir()).glob('herdr-groups-*')) - before
            return [(path, json.loads(path.read_text())) for root in roots for path in root.glob('glass-*.json')]

        def capture(name):
            source = '''import CoreGraphics
let rows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] ?? []
for row in rows {
 if (row[kCGWindowOwnerPID as String] as? Int) == __OWNER_PID__ && (row[kCGWindowLayer as String] as? Int) == 0 {
  print(row[kCGWindowNumber as String] as! Int); break
 }
}'''.replace('__OWNER_PID__', str(app.pid))
            script('activate')
            window = wait_for(lambda: subprocess.check_output(['swift', '-e', source], text=True).strip(),
                              'No visible test window')
            subprocess.run(['screencapture', '-x', '-l', window, str(ARTIFACTS / name)], check=True)

        try:
            panes = wait_for(lambda: cli('pane', 'list')['panes'], 'Test session did not start')
            print('PASS: desktop launch ignores inherited nesting, pane IDs and socket routing', flush=True)
            cli('tab', 'rename', panes[0]['tab_id'], 'Native Glass')
            cli('tab', 'create', '--workspace', panes[0]['workspace_id'], '--label', 'Terminal', '--no-focus')
            initial = wait_for(lambda: packets(), 'No native glass geometry published')
            wait_for(lambda: any(r['kind'] == 'tab' and r['selected']
                                for _, p in packets() for r in p['regions']), 'No selected tab capsule')
            kinds = {r['kind'] for _, p in packets() for r in p['regions']}
            assert {'tabbar', 'tab', 'sidebar', 'terminal'} <= kinds, kinds
            wait_for(lambda: any(r['kind'] == 'tab' and r['height'] == 3
                                for _, p in packets() for r in p['regions']), 'Capsules did not get full height')
            capture('single.png')
            print('PASS: real client published tabs, sidebars and terminal glass regions', flush=True)
            script('perform action "csi:98;9u" on focused terminal of selected tab of front window')
            wait_for(lambda: sum(r['kind'] == 'sidebar' for _, p in packets() for r in p['regions']) < 2,
                     'Sidebar glass did not collapse')
            script('perform action "csi:98;9u" on focused terminal of selected tab of front window')
            wait_for(lambda: sum(r['kind'] == 'sidebar' for _, p in packets() for r in p['regions']) == 2,
                     'Sidebar glass did not expand')
            previous_cols = packets()[0][1]['cols']
            script('perform action "increase_font_size:1" on focused terminal of selected tab of front window')
            wait_for(lambda: packets()[0][1]['cols'] < previous_cols, 'Font resize did not update glass geometry')
            script('perform action "decrease_font_size:1" on focused terminal of selected tab of front window')
            wait_for(lambda: packets()[0][1]['cols'] == previous_cols, 'Font restore did not restore geometry')
            print('PASS: sidebar collapse/expand and font resize update native geometry', flush=True)
            original_terminals = {p['terminal_id'] for p in cli('pane', 'list')['panes']}
            script('perform action "csi:100;9u" on focused terminal of selected tab of front window')
            wait_for(lambda: script('return count of terminals') == '2', 'Native split did not appear')
            wait_for(lambda: len(packets()) == 2, 'Split has no independent glass layer')
            assert original_terminals == {p['terminal_id'] for p in cli('pane', 'list')['panes']}
            def cards_fill_split():
                for _, packet in packets():
                    bar = next(r for r in packet['regions'] if r['kind'] == 'tabbar')
                    card = next(r for r in packet['regions'] if r['kind'] == 'terminal')
                    if card['x'] != bar['x'] or card['width'] != bar['width']:
                        return False
                return True
            wait_for(cards_fill_split, 'Single terminal glass did not fill its native split')
            time.sleep(1)
            capture('split.png')
            (ARTIFACTS / 'geometry.json').write_text(json.dumps([p for _, p in packets()], indent=2))
            script('perform action "csi:116;9u" on focused terminal of selected tab of front window')
            wait_for(lambda: len(cli('tab', 'list')['tabs']) == 3, 'New tab shortcut failed')
            print('PASS: Cmd+D, Cmd+T, independent glass surfaces, original terminal identities preserved', flush=True)
        finally:
            try:
                capture('last-window.png')
            except Exception as error:
                print(f'Capture: {error}', flush=True)
            try:
                script('quit')
            except Exception:
                app.terminate()
            try:
                app.wait(timeout=15)
            except subprocess.TimeoutExpired:
                app.kill()
                app.wait()
            subprocess.run([str(BINARY), 'session', 'stop', SESSION], env=env, capture_output=True, timeout=15)
            subprocess.run([str(BINARY), 'session', 'delete', SESSION], env=env, capture_output=True, timeout=15)
