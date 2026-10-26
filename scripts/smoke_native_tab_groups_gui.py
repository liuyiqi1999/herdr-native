#!/usr/bin/env python3
"""Exercise Ghostty AppleScript Cmd+D -> actual native split, in a test app/session."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid
ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'dist/Herdr Native Tab Groups Test.app'
BINARY=APP/'Contents/MacOS/herdr'
SESSION='gui-groups-'+uuid.uuid4().hex[:6]

def script(body):
    text='using terms from application "/Applications/Herdr Native.app"\n tell application "'+str(APP)+'"\n'+body+'\n end tell\nend using terms from'
    p=subprocess.run(['/usr/bin/osascript','-e',text],capture_output=True,text=True,timeout=25)
    if p.returncode:raise RuntimeError(p.stderr)
    return p.stdout.strip()

with tempfile.TemporaryDirectory(prefix='hgg-',dir='/tmp') as tmp:
    config=Path(tmp)/'config.toml'
    config.write_text('onboarding = false\n[keys]\nsplit_vertical = "super+d"\nnew_tab = "super+t"\n'
                      '[ui]\nprompt_new_tab_name = false\nprompt_new_tab_command = false\n'
                      '[terminal]\ndefault_shell = "/bin/sh"\nshell_mode = "non_login"\n')
    env={k:v for k,v in os.environ.items() if not k.startswith('HERDR_')}
    env.update(HERDR_NATIVE_CONFIG_PATH=str(config),HERDR_NATIVE_SESSION=SESSION,HERDR_CONFIG_PATH=str(config),XDG_CONFIG_HOME=tmp+'/c',XDG_STATE_HOME=tmp+'/s')
    app=subprocess.Popen([str(APP/'Contents/MacOS/HerdrNative')],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def cli(*args):
        p=subprocess.run([str(BINARY),'--session',SESSION,*args],env=env,capture_output=True,text=True,timeout=10)
        if p.returncode:raise RuntimeError(p.stderr+p.stdout)
        return json.loads(p.stdout)['result']
    try:
        for attempt in range(40):
            time.sleep(.3)
            try:
                panes=cli('pane','list')['panes']
                if panes:break
            except RuntimeError:pass
        else:raise RuntimeError('Test GUI server did not start')
        first=panes[0];ws=first['workspace_id']
        cli('tab','rename',first['tab_id'],'RIGHT_TAB')
        cli('tab','create','--workspace',ws,'--label','LEFT_TAB','--no-focus')
        before={p['terminal_id'] for p in cli('pane','list')['panes']}
        time.sleep(1)
        assert script('return count of terminals')=='1'
        script('perform action "csi:100;9u" on focused terminal of selected tab of front window')
        for attempt in range(30):
            time.sleep(.3)
            if script('return count of terminals')=='2':break
        else:raise RuntimeError('Cmd+D did not create native split')
        time.sleep(2)
        assert before=={p['terminal_id'] for p in cli('pane','list')['panes']}
        script('perform action "csi:116;9u" on focused terminal of selected tab of front window')
        time.sleep(2)
        tabs = cli('tab','list')['tabs']
        assert len(tabs)==3
        moved_tab = next(t['tab_id'] for t in tabs if t['label'] not in ('LEFT_TAB', 'RIGHT_TAB'))
        source_surface = script('return id of focused terminal of selected tab of front window')
        matches = []
        for candidate in Path(tempfile.gettempdir()).glob('herdr-groups-*'):
            for surface_file in candidate.glob('surface-*'):
                if surface_file.read_text().strip() == source_surface:
                    matches.append((candidate, surface_file.name.removeprefix('surface-')))
        assert len(matches) == 1, matches
        group_root, source_group = matches[0]
        boot = next(p.name for p in group_root.iterdir() if p.is_dir())
        identities = {p['terminal_id'] for p in cli('pane','list')['panes']}
        subprocess.run(['/usr/bin/python3', str(ROOT/'scripts/native_tab_group.py'),
                        '--root', str(group_root), '--group', source_group,
                        '--boot', boot, '--tab', moved_tab, '--workspace', ws,
                        '--previous', first['tab_id'], '--target-group', 'main'],
                       env={**env, 'HERDR_LOCAL_CLI': str(BINARY), 'HERDR_SESSION': SESSION,
                            'HERDR_NATIVE_APP': str(APP)}, check=True, timeout=30)
        time.sleep(1)
        assert (group_root/boot/moved_tab).read_text() == 'main'
        assert script('return id of focused terminal of selected tab of front window') == (group_root/'surface-main').read_text().strip()
        assert script('return count of terminals') == '2'
        assert identities == {p['terminal_id'] for p in cli('pane','list')['panes']}
        # Exercise all directional chords through the real native split helper.
        for arrow in ('D', 'A', 'B', 'C'):
            count = int(script('return count of terminals'))
            script('perform action "csi:100;9u" on focused terminal of selected tab of front window\n'
                   'perform action "csi:1;9' + arrow + '" on focused terminal of selected tab of front window')
            for attempt in range(30):
                time.sleep(.3)
                if int(script('return count of terminals')) == count + 1:
                    break
            else:
                raise RuntimeError('Directional split failed: ' + arrow)
            time.sleep(.5)
        assert before <= {p['terminal_id'] for p in cli('pane','list')['panes']}
        # Capture only this disposable test window, not the user's desktop.
        swift='import CoreGraphics; let rows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] ?? []; for row in rows { if (row[kCGWindowOwnerPID as String] as? Int) == '+str(app.pid)+' && (row[kCGWindowLayer as String] as? Int) == 0 { print(row[kCGWindowNumber as String] as! Int); break } }'
        window=subprocess.check_output(['swift','-e',swift],text=True).strip()
        if window:
            subprocess.run(['screencapture','-x','-l',window,str(ROOT/'.local/tab-groups-gui.png')])
        print('PASS: native four-way Cmd+D arrow splits, default Cmd+D, Cmd+T, cross-group transfer/focus, preserved PTYs (physical mouse covered separately by AppKit and PTY tests)')
    finally:
        for log in Path(tmp).rglob('*.log'):
            (ROOT/('.local/gui-groups-'+log.name)).write_text(log.read_text())
        swift='import CoreGraphics; let rows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] ?? []; for row in rows { if (row[kCGWindowOwnerPID as String] as? Int) == '+str(app.pid)+' && (row[kCGWindowLayer as String] as? Int) == 0 { print(row[kCGWindowNumber as String] as! Int); break } }'
        window=subprocess.check_output(['swift','-e',swift],text=True).strip()
        if window:subprocess.run(['screencapture','-x','-l',window,str(ROOT/'.local/tab-groups-gui.png')])
        try:script('quit')
        except Exception:pass
        app.wait(timeout=10)
        subprocess.run([str(BINARY),'session','stop',SESSION],env=env,capture_output=True,timeout=10)
        subprocess.run([str(BINARY),'session','delete',SESSION],env=env,capture_output=True,timeout=10)
