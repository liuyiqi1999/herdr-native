#!/usr/bin/env python3
"""Two independent real PTY clients, one server, disjoint native tab groups."""
import codecs
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.tools/python'))
import pyte
from native_tab_group import split_group
from types import SimpleNamespace

binary = os.environ['HERDR_TEST_BIN']
session = 'groups-' + uuid.uuid4().hex[:6]
class Screen(pyte.Screen):
    def report_device_status(self, *args, **kwargs): pass

with tempfile.TemporaryDirectory(prefix='hg-', dir='/tmp') as tmp:
    root = Path(tmp)
    config = root / 'herdr.toml'
    config.write_text('onboarding = false\n[keys]\ntoggle_sidebar = "super+b"\nnew_tab = "super+t"\nsplit_vertical = "super+d"\n'
                      'previous_tab = "super+left"\nnext_tab = "super+right"\n'
                      '[ui]\nprompt_new_tab_name = false\nprompt_new_tab_command = false\n'
                      '[terminal]\ndefault_shell = "/bin/sh"\nshell_mode = "non_login"\n')
    env = {k:v for k,v in os.environ.items() if not k.startswith('HERDR_')}
    env.update(HERDR_CONFIG_PATH=str(config), XDG_CONFIG_HOME=tmp+'/c', XDG_STATE_HOME=tmp+'/s', TERM='xterm-256color', HERDR_NATIVE_GROUP_ROOT=tmp+'/groups')
    helper = root / 'capture_split.py'
    captures = root / 'split-requests.jsonl'
    helper.write_text('import json,sys\nwith open(' + repr(str(captures)) + ', "a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n')
    env['HERDR_NATIVE_GROUP_LAUNCHER'] = str(helper)
    clients = []
    def launch(group):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 35, 100, 0, 0))
        child = subprocess.Popen([binary, '--session', session], env={**env, 'HERDR_NATIVE_GROUP_ID':group}, stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        screen = Screen(100, 35)
        item = [master, child, screen, pyte.Stream(screen), codecs.getincrementaldecoder('utf8')('replace')]
        clients.append(item)
        return item
    def drain(seconds=0.5):
        deadline = time.monotonic()+seconds
        while time.monotonic()<deadline:
            for item in list(clients):
                fd, child, _, stream, decoder = item
                if child.poll() is not None: continue
                if select.select([fd], [], [], 0.01)[0]:
                    data = os.read(fd, 65536)
                    stream.feed(decoder.decode(data))
                    if b'\x1b[6n' in data: os.write(fd,b'\x1b[1;1R')
                    if b'\x1b[?u' in data: os.write(fd,b'\x1b[?0u')
                    if b'\x1b[c' in data: os.write(fd,b'\x1b[?62;22c')
    def cli(*args):
        p = subprocess.run([binary,'--session',session,*args],env=env,capture_output=True,text=True,timeout=10)
        if p.returncode: raise RuntimeError(p.stderr+p.stdout)
        return json.loads(p.stdout)['result'] if p.stdout.lstrip().startswith('{') else p.stdout
    def top(client): return '\n'.join(client[2].display[:3])
    def panes(): return cli('pane','list')['panes']
    try:
        left = launch('main'); drain(6)
        a = panes()[0]; ws = a['workspace_id']
        cli('tab','rename',a['tab_id'],'LEFT_ONLY')
        b = cli('tab','create','--workspace',ws,'--label','RIGHT_ONLY','--no-focus')
        tab_b = b['tab']['tab_id']; drain()
        # The client creates its server-boot-scoped ownership directory.
        boot = next(p.name for p in Path(env['HERDR_NATIVE_GROUP_ROOT']).iterdir() if p.is_dir())
        # Cmd+D consumes its direction with or without Command still held.
        # Cmd+arrows without a pending chord retain workspace tab navigation.
        directions = [('D', 'left'), ('C', 'right'), ('A', 'up'), ('B', 'down')]
        cases = [(('\x1b[' + modifier + arrow).encode(), direction)
                 for modifier in ('', '1;9') for arrow, direction in directions]
        cases.append((b'', 'right'))
        for suffix, direction in cases:
            os.write(left[0], b'\x1b[100;9u' + suffix)
            drain(0.8)
            request = json.loads(captures.read_text().splitlines()[-1])
            assert request[request.index('--direction') + 1] == direction, request
        assert len(captures.read_text().splitlines()) == len(cases)
        os.write(left[0], b'\x1b[100;9u\x1b'); drain(0.8)
        assert len(captures.read_text().splitlines()) == len(cases)
        # Ctrl+D is now terminal EOF, not a split shortcut. A temporary reader
        # receives EOF and exits; the shell/client themselves must remain alive.
        os.write(left[0], b'/usr/bin/python3 -c \'import sys; print("CTRL_D_" + ("EOF" if sys.stdin.read(1) == "" else "OTHER"))\'\r')
        drain(.5)
        os.write(left[0], b'\x04'); drain(.8)
        assert 'CTRL_D_EOF' in json.dumps(cli('pane','read',a['pane_id']))
        assert len(captures.read_text().splitlines()) == len(cases)
        before = {p['terminal_id'] for p in panes()}
        args=SimpleNamespace(root=env['HERDR_NATIVE_GROUP_ROOT'],group='main',boot=boot,tab=tab_b,workspace=ws,previous=a['tab_id'])
        right_holder=[]
        gid=split_group(args,cli,lambda gid:right_holder.append(launch(gid)))
        right=right_holder[0]; drain(3)
        assert 'LEFT_ONLY' in top(left) and 'RIGHT_ONLY' not in top(left), top(left)
        assert 'RIGHT_ONLY' in top(right) and 'LEFT_ONLY' not in top(right), top(right)
        assert right[2].display[0].index('RIGHT_ONLY') < 4, top(right)
        assert left[2].display[0].index('LEFT_ONLY') >= 18, top(left)
        # The primary group owns the only sidebar. Cmd+B in another native
        # surface cannot resurrect a compact rail or change the primary one.
        os.write(right[0], b'\x1b[98;9u'); drain(.5)
        assert right[2].display[0].index('RIGHT_ONLY') < 4, top(right)
        assert left[2].display[0].index('LEFT_ONLY') >= 18, top(left)
        assert before == {p['terminal_id'] for p in panes()}
        # A new tab belongs to the client that issued Cmd+T.
        os.write(right[0],b'\x1b[116;9u'); drain(2)
        tabs=cli('tab','list')['tabs']
        new=next(t for t in tabs if t['tab_id'] not in (a['tab_id'],tab_b))
        cli('tab','rename',new['tab_id'],'RIGHT_NEW'); drain()
        assert 'RIGHT_NEW' in top(right), top(right)
        assert 'RIGHT_NEW' not in top(left), top(left)
        new_pane = next(p['pane_id'] for p in panes() if p['tab_id'] == new['tab_id'])
        # Top-tab clicks must switch even if a small movement crosses a cell
        # boundary while the pointer remains over that same tab.
        for jitter in (0, 1):
            os.write(right[0], b'\x1b[1;9D'); drain(.5)
            column = right[2].display[0].index('RIGHT_NEW') + 2
            os.write(right[0], f'\x1b[<0;{column};1M'.encode()); drain(.1)
            if jitter:
                os.write(right[0], f'\x1b[<32;{column+jitter};1M'.encode()); drain(.1)
            os.write(right[0], f'\x1b[<0;{column+jitter};1m'.encode()); drain(.7)
            marker = 'CLICK_SELECTED_NEW_' + str(jitter)
            os.write(right[0], ('echo ' + marker + '\r').encode()); drain(.5)
            assert marker in json.dumps(cli('pane','read',new_pane)), ('top tab click failed', jitter)
        # Next/previous tab operates only inside the right group.
        os.write(right[0],b'\x1b[1;9D'); drain()
        os.write(right[0],b'echo RIGHT_MARKER\r'); drain()
        text=cli('pane','read',b['root_pane']['pane_id'])
        assert 'RIGHT_MARKER' in json.dumps(text),text
        assert 'LEFT_ONLY' in top(left),top(left)
        # Transfer an existing tab to the other group without spawning a surface.
        (Path(env['HERDR_NATIVE_GROUP_ROOT']) / 'surface-main').write_text('main-surface')
        # Drive the actual TUI tab-drag path. The native host's hit-test is
        # separately covered by SurfaceViewAppKitTests; here reproduce its
        # timestamped UUID mailbox before delivering the release event.
        column = right[2].display[0].index('RIGHT_NEW') + 3
        os.write(right[0], f'\x1b[<0;{column};1M'.encode()); drain(.2)
        os.write(right[0], f'\x1b[<32;{column + 2};1M'.encode()); drain(.2)
        (Path(env['HERDR_NATIVE_GROUP_ROOT']) / 'drop-surface').write_text(f'{time.time()}\nmain-surface')
        os.write(right[0], f'\x1b[<0;{column + 2};1m'.encode()); drain(.8)
        request = json.loads(captures.read_text().splitlines()[-1])
        assert request[request.index('--target-group') + 1] == 'main', request
        assert request[request.index('--tab') + 1] == new['tab_id'], request
        move = SimpleNamespace(root=env['HERDR_NATIVE_GROUP_ROOT'], group=gid, boot=boot,
                               tab=new['tab_id'], workspace=ws, previous=tab_b, target_group='main')
        identities = {p['terminal_id'] for p in panes()}
        split_group(move, cli, lambda _: (_ for _ in ()).throw(AssertionError('unexpected launch')))
        drain(2)
        assert 'RIGHT_NEW' in top(left) and 'RIGHT_NEW' not in top(right)
        assert identities == {p['terminal_id'] for p in panes()}
        # Detach right; no server/process shutdown. Ownership returns to main.
        os.write(right[0],b'\x02q'); drain(2)
        assert right[1].poll() is not None
        assert 'RIGHT_ONLY' in top(left) and 'RIGHT_NEW' in top(left),top(left)
        assert before <= {p['terminal_id'] for p in panes()}
        # Closing the final tab in a group closes only that native client.
        again=[]
        split_group(args,cli,lambda gid:again.append(launch(gid)))
        drain(3)
        cli('tab','close',tab_b); drain(2)
        assert again[0][1].poll() is not None, 'Empty group must detach'
        assert left[1].poll() is None, 'Left client must remain attached'
        # Native defaults the command picker locally, without an unknown backend config key.
        config.write_text(config.read_text().replace('prompt_new_tab_command = false\n',''))
        cli('server','reload-config'); drain(1)
        count=len(cli('tab','list')['tabs'])
        os.write(left[0],b'\x1b[116;9u'); drain(1)
        assert 'start command:' in '\n'.join(left[2].display)
        assert len(cli('tab','list')['tabs'])==count
        os.write(left[0],b'\x1b'); drain()
        print('PASS: plain/jitter top-tab clicks, directional chords, TUI drag/drop routing, independent strips/focus/new tabs, live terminal identity, detach/reclaim, empty-group close, client-only prompt default')
    finally:
        for log in root.rglob('*.log'):
            (Path(__file__).resolve().parents[1]/('.local/tab-groups-' + log.name)).write_text(log.read_text())
        for i,item in enumerate(clients):
            (Path(__file__).resolve().parents[1]/f'.local/tab-groups-screen-{i}.txt').write_text('\n'.join(item[2].display))
        subprocess.run([binary,'session','stop',session],env=env,capture_output=True,timeout=10)
        subprocess.run([binary,'session','delete',session],env=env,capture_output=True,timeout=10)
        for fd,child,*_ in clients:
            os.close(fd)
            if child.poll() is None: child.terminate()
            child.wait(timeout=5)
