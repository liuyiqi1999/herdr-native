import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { quote } from './rpc.mjs';
const execute = promisify(execFile);
const SCAN = `import os,json,sys
root=sys.argv[1]; paths=[]
for base, dirs, files in os.walk(root, followlinks=False):
 dirs[:]=sorted(d for d in dirs if d not in {'.git','node_modules','target','.venv','vendor','.cache'})
 for name in sorted(files):
  path=os.path.relpath(os.path.join(base,name),root)
  if not any(c in path for c in '\\n\\r\\x1b'): paths.append(path)
  if len(paths)>=10000: break
 if len(paths)>=10000: break
print(json.dumps(paths))`;

// Names only, one read-only request; never send the user's draft or run it as a shell command.
export async function remoteFiles({ host, cwd, local = false }) {
  const args = ['python3', '-c', SCAN, cwd];
  const command = local ? 'python3' : 'ssh';
  const argv = local ? args.slice(1) : ['-T', '-o', 'ConnectTimeout=10', host, args.map(quote).join(' ')];
  const { stdout } = await execute(command, argv, { timeout: 15000, maxBuffer: 4 * 1024 * 1024 });
  const paths = JSON.parse(stdout);
  if (!Array.isArray(paths) || !paths.every(p => typeof p === 'string')) throw new Error('Invalid remote file index');
  return paths;
}

export function withFiles(provider, getPaths) {
  return {
    triggerCharacters: ['/', '@'],
    async getSuggestions(lines, line, column, options) {
      const before = lines[line].slice(0, column);
      const match = before.match(/(?:^|\s)(@[^\s]*)$/);
      if (!match) return provider.getSuggestions(lines, line, column, options);
      const prefix = match[1], query = prefix.slice(1).toLowerCase();
      const items = getPaths().filter(p => p.toLowerCase().includes(query)).slice(0, 30)
        .map(p => ({ value: '@' + (p.includes(' ') ? JSON.stringify(p) : p), label: p }));
      return items.length ? { prefix, items } : null;
    },
    applyCompletion: provider.applyCompletion,
  };
}
