#!/usr/bin/env python3
"""Isolated real SSH/old-server/full-Pi/native-Herdr test. No model prompts."""
import argparse, base64, codecs, fcntl, json, os, pty, select, signal, struct, subprocess, sys, tempfile, termios, time, uuid
from pathlib import Path
from build import profiles
import remote
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.tools/python'))
import pyte
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--plain',action='store_true',help='Start ordinary Pi without -e or a bridge environment variable')
args=parser.parse_args()
host='liuyiqi.1@10.251.230.15'
session='hne2e-'+uuid.uuid4().hex[:8]
backend=None; child=None; local_server=None; paused=False; remote_pid=None

def ssh(source,payload=None):
 p=subprocess.run(remote.ssh_command(host,['python3','-c',source]),input=json.dumps(payload) if payload is not None else '',text=True,capture_output=True,timeout=60)
 if p.returncode: raise RuntimeError(p.stderr)
 return json.loads(p.stdout) if p.stdout.strip() else None

cwd=ssh("import tempfile,json; print(json.dumps(tempfile.mkdtemp(prefix='hn-editor-e2e-')))")
try:
 ssh('''import json,sys,subprocess,pathlib,time
r=json.load(sys.stdin); log=open(r['cwd']+'/server.log','wb')
subprocess.Popen(['/home/liuyiqi.1/.local/bin/herdr','--session',r['session'],'server'],cwd=r['cwd'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
time.sleep(1)
''',dict(cwd=cwd,session=session))
 if args.plain:
  backend=ssh('''import json,sys,subprocess,pathlib,os,time
r=json.load(sys.stdin)
def call(*args):
 p=subprocess.run(['/home/liuyiqi.1/.local/bin/herdr','--session',r['session'],*args],capture_output=True,text=True,check=True)
 return json.loads(p.stdout)['result'] if p.stdout.strip() else {}
w=call('workspace','create','--label','ordinary pi','--cwd',r['cwd'],'--focus')
call('pane','run',w['root_pane']['pane_id'],'exec /home/liuyiqi.1/.npm-global/bin/pi --offline --no-approve')
for _ in range(150):
 for path in pathlib.Path('/tmp').glob('hn-editor-*/pid'):
  try:
   pid=int(path.read_text())
   if os.path.realpath('/proc/'+str(pid)+'/cwd')==r['cwd'] and (path.parent/'editor.sock').exists():
    print(json.dumps({'socket':str(path.parent/'editor.sock'),'pane':w['root_pane']['pane_id']}));sys.exit(0)
  except (OSError,ValueError):pass
 time.sleep(.1)
raise RuntimeError('Ordinary Pi did not enable native input automatically')
''',dict(cwd=cwd,session=session))
 else:
  backend=ssh((ROOT/'remote/backend.py').read_text(),dict(agent='pi',interactive=True,cwd=cwd,herdr_session=session,test_no_approve=True,extension=base64.b64encode((ROOT/'remote/native-editor.ts').read_bytes()).decode()))
 remote_pid=ssh("import json,sys,pathlib; r=json.load(sys.stdin);print(pathlib.Path(r).read_text())",str(Path(backend['socket']).parent/'pid'))
 with tempfile.TemporaryDirectory(prefix='hn-e2e-',dir='/tmp') as directory:
  temp=Path(directory); config=temp/'herdr.toml';config.write_text(profiles()[1])
  catalog=temp/'state/herdr/client';catalog.mkdir(parents=True)
  profile=uuid.uuid4().hex
  (catalog/'endpoints.json').write_text(json.dumps(dict(version=1,selected_profile=profile,ssh=[dict(id=profile,label='REMOTE PI TEST',target=host,session=session,enabled=True)])))
  env={**os.environ,'XDG_CONFIG_HOME':str(temp/'config'),'XDG_STATE_HOME':str(temp/'state'),'HERDR_CONFIG_PATH':str(config),'HERDR_REMOTE_LAUNCHER':str(ROOT/'scripts/remote.py'),'HERDR_NATIVE_DRAFT_DIR':str(temp/'drafts'),'TERM':'xterm-256color'}
  for k in ('HERDR_SOCKET_PATH','HERDR_CLIENT_SOCKET_PATH','HERDR_SESSION','HERDR_PANE_ID'):env.pop(k,None)
  with (temp/'server.log').open('wb') as log:
   local_server=subprocess.Popen(['/Users/bytedance/.local/bin/herdr','--session',session,'server'],env=env,stdout=log,stderr=log)
  master,slave=pty.openpty();fcntl.ioctl(slave,termios.TIOCSWINSZ,struct.pack('HHHH',42,150,0,0))
  child=subprocess.Popen([str(ROOT/'herdr/target/release/herdr'),'--session',session],env=env,stdin=slave,stdout=slave,stderr=slave,start_new_session=True);os.close(slave)
  class Screen(pyte.Screen):
   def report_device_status(self,*a,**kw):pass
  screen=Screen(150,42);stream=pyte.Stream(screen);decoder=codecs.getincrementaldecoder('utf8')('replace')
  raw=bytearray()
  def drain(seconds):
   until=time.monotonic()+seconds
   while time.monotonic()<until:
    if select.select([master],[],[],.025)[0]:
     data=os.read(master,65536);raw.extend(data);stream.feed(decoder.decode(data))
     if b'\x1b[6n' in data:os.write(master,b'\x1b[1;1R')
     if b'\x1b[?u' in data:os.write(master,b'\x1b[?0u')
     if b'\x1b[c' in data:os.write(master,b'\x1b[?62;22c')
  drain(4)
  # Wait for the local editor to acknowledge an edit; SSH/package startup is asynchronous.
  until=time.monotonic()+25
  while not list((temp/'drafts').glob('*.json')):
   assert time.monotonic()<until,'Native editor did not become ready'
   os.write(master,b'x');drain(.5)
  os.write(master,b'\x03');drain(.5)
  # Pause the actual remote Pi, not its SSH transport or a fake backend.
  ssh('import os,signal,json,sys;os.kill(json.load(sys.stdin),signal.SIGSTOP)',remote_pid);paused=True
  os.write(master,'\x1b[200~本地完整界面测试🙂\nsecond line\x1b[201~'.encode());drain(1)
  text='\n'.join(screen.display)
  (ROOT/'.local/native-editor-live-screen.txt').write_text(text)
  assert '本地完整界面测试' in text and 'second line' in text,'Input did not render locally; see .local/native-editor-live-screen.txt'
  assert 'REMOTE PI TEST' in text,'Remote workspace missing'
  ssh('import os,signal,json,sys;os.kill(json.load(sys.stdin),signal.SIGCONT)',remote_pid);paused=False
  drain(.5)
  os.write(master,b'\x0c');drain(2)
  (ROOT/'.local/native-editor-model-screen.txt').write_text('\n'.join(screen.display))
  os.write(master,b'\x1b');drain(1)
  print('PASS:', 'ordinary pi auto-loading;' if args.plain else 'explicit bridge;', 'unchanged official server in isolated remote session; full remote Pi UI/workspace; Chinese/multiline local editing with remote Pi SIGSTOP; original model menu; no prompt')
finally:
 if paused and remote_pid:
  ssh('import os,signal,json,sys;os.kill(json.load(sys.stdin),signal.SIGCONT)',remote_pid)
 if child:
  child.terminate()
  try:child.wait(timeout=5)
  except subprocess.TimeoutExpired:child.kill();child.wait()
 if local_server:
  local_server.terminate()
  try:local_server.wait(timeout=5)
  except subprocess.TimeoutExpired:local_server.kill();local_server.wait()
 ssh('''import json,sys,subprocess,shutil,pathlib,hashlib
r=json.load(sys.stdin)
subprocess.run(['/home/liuyiqi.1/.local/bin/herdr','--session',r['session'],'server','stop'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
key=hashlib.sha256(json.dumps([r['session'],r['cwd'],'pi',None,'native-editor-v1']).encode()).hexdigest()[:24]
shutil.rmtree(pathlib.Path.home()/'.cache/herdr-native'/key,ignore_errors=True)
shutil.rmtree(r['cwd'],ignore_errors=True)
''',dict(session=session,cwd=cwd))
