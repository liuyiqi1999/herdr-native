#!/usr/bin/env python3
"""Package the isolated native/tab-groups build without altering the installed app."""
import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--test',action='store_true')
parser.add_argument('--glass', action='store_true', help='Bundle native Liquid Glass presentation (requires rebuilt engine and client)')
parser.add_argument('--herdr', type=Path, default=ROOT/'herdr/target/release/herdr')
parser.add_argument('--ghostty-app', type=Path, help='Rebuilt Ghostty.app with the native drop-target bridge')
args=parser.parse_args()
if args.output.exists():parser.error('Output already exists; choose a fresh path')
if args.glass and not args.ghostty_app:
    parser.error('--glass requires --ghostty-app with the native glass bridge')
if not args.herdr.is_file():
    parser.error(f'Herdr binary not found: {args.herdr}')
shutil.copytree('/Applications/Herdr Native.app',args.output,symlinks=True)
contents=args.output/'Contents'
# Upgrade copied installations too: losing native split focus must not dim
# the primary surface's shared sidebar.
config_path = contents/'Resources/ghostty.conf'
config_lines = [line for line in config_path.read_text().splitlines()
                if not line.strip().startswith('unfocused-split-opacity')]
config_path.write_text('\n'.join(config_lines) + '\nunfocused-split-opacity = 1\n')
if args.ghostty_app:
    engine_contents = args.ghostty_app / 'Contents'
    shutil.copy2(engine_contents / 'MacOS/ghostty', contents / 'MacOS/ghostty')
    # ditto overlays versioned framework symlinks correctly; copytree with
    # symlinks=True raises FileExistsError on an existing Sparkle.framework.
    subprocess.run(['ditto', str(engine_contents / 'Resources'), str(contents / 'Resources')], check=True)
    if (engine_contents / 'Frameworks').exists():
        subprocess.run(['ditto', str(engine_contents / 'Frameworks'), str(contents / 'Frameworks')], check=True)
binary=contents/'MacOS/herdr'
shutil.copy2(args.herdr,binary)
if args.glass:
    shutil.copy2(ROOT/'native/native-glass.conf', contents/'Resources/native-glass.conf')
shutil.copy2(ROOT/'scripts/native_tab_group.py',contents/'Resources/native_tab_group.py')
subprocess.run(['xcrun','swiftc',str(ROOT/'native/TabGroupsLauncher.swift'),'-O','-o',str(contents/'MacOS/HerdrNative')],check=True)
if args.test:
    path=contents/'Info.plist'
    info=plistlib.loads(path.read_bytes())
    info['CFBundleIdentifier']='dev.herdr.native.glass-test' if args.glass else 'dev.herdr.native.tab-groups-test'
    info['CFBundleName']='Herdr Native Glass Test' if args.glass else 'Herdr Native Tab Groups Test'
    path.write_bytes(plistlib.dumps(info))
source=ROOT/'herdr-worktrees/tab-groups'
patch=subprocess.check_output(['git','-C',str(source),'diff','--binary','HEAD'])
for name in subprocess.check_output(['git','-C',str(source),'ls-files','--others','--exclude-standard'],text=True).splitlines():
    p=subprocess.run(['git','diff','--no-index','--binary','--','/dev/null',name],cwd=source,capture_output=True)
    if p.returncode not in (0,1):raise RuntimeError(p.stderr.decode())
    patch+=p.stdout
(contents/'Resources/herdr-complete.patch').write_bytes(patch)
ghostty_patch = subprocess.check_output(['git', '-C', str(ROOT/'ghostty'), 'diff', '--binary', 'HEAD'])
for name in subprocess.check_output(['git', '-C', str(ROOT/'ghostty'), 'ls-files', '--others', '--exclude-standard', 'macos/Sources'], text=True).splitlines():
    p = subprocess.run(['git', 'diff', '--no-index', '--binary', '--', '/dev/null', name], cwd=ROOT/'ghostty', capture_output=True)
    if p.returncode not in (0, 1): raise RuntimeError(p.stderr.decode())
    ghostty_patch += p.stdout
(contents/'Resources/ghostty-tab-drop.patch').write_bytes(ghostty_patch)
provenance=json.loads((contents/'Resources/provenance.json').read_text())
provenance.update(herdr_binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                  herdr_complete_patch_sha256=hashlib.sha256(patch).hexdigest(),
                  native_feature=('four-way tab splits and cross-group tab drops' if args.ghostty_app else 'four-way tab splits; native drop bridge not bundled'),
                  native_drop_bridge=bool(args.ghostty_app),
                  native_liquid_glass=args.glass,
                  ghostty_binary_sha256=hashlib.sha256((contents/'MacOS/ghostty').read_bytes()).hexdigest(),
                  native_group_helper_sha256=hashlib.sha256((contents/'Resources/native_tab_group.py').read_bytes()).hexdigest())
(contents/'Resources/provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
subprocess.run(['codesign','--force','--deep','--sign','-',str(args.output)],check=True)
subprocess.run(['codesign','--verify','--deep','--strict',str(args.output)],check=True)
print(args.output)
