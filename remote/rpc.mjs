import { spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { StringDecoder } from 'node:string_decoder';
import { randomUUID } from 'node:crypto';

const SOCKET_PROXY = `import os,socket,sys,threading
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.connect(sys.argv[1])
def read():
 while True:
  data=s.recv(65536)
  if not data: os._exit(0)
  sys.stdout.buffer.write(data); sys.stdout.buffer.flush()
threading.Thread(target=read,daemon=True).start()
while True:
 data=os.read(0,65536)
 if not data: break
 s.sendall(data)
s.close()`;

export function quote(value) {
  if (value.includes('\0')) throw new Error('NUL is not allowed in process arguments');
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export function piCommand({ host, cwd, binary = 'pi', session, socket, local = false }) {
  if (!cwd.startsWith('/')) throw new Error('Remote cwd must be absolute');
  const args = ['--mode', 'rpc'];
  if (session) args.push('--session', session);
  if (local) return socket ? { command: 'python3', args: ['-c', SOCKET_PROXY, socket], options: {} } : { command: binary, args, options: { cwd } };
  if (!host || host.startsWith('-') || /\s/.test(host)) throw new Error('Invalid SSH host');
  const command = socket ? ['python3', '-c', SOCKET_PROXY, socket].map(quote).join(' ') : `cd ${quote(cwd)} && exec ${[binary, ...args].map(quote).join(' ')}`;
  return { command: 'ssh', args: ['-T', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3', host, command], options: {} };
}

/** Strict LF-framed JSON RPC. No key events or terminal bytes are sent to the backend. */
export class Rpc extends EventEmitter {
  constructor(command, args, options = {}) {
    super();
    this.sequence = 0;
    this.connection = randomUUID();
    this.pending = new Map();
    this.closed = false;
    this.buffer = '';
    this.decoder = new StringDecoder('utf8');
    this.stderr = '';
    this.process = spawn(command, args, { ...options, stdio: ['pipe', 'pipe', 'pipe'] });
    this.process.stdout.on('data', chunk => this.feed(chunk));
    this.process.stderr.on('data', chunk => { this.stderr = (this.stderr + chunk).slice(-8192); });
    this.process.on('error', error => this.fail(error));
    this.process.stdin.on('error', error => this.fail(error));
    this.process.on('exit', (code, signal) => this.fail(new Error(`Remote Pi exited (${code ?? signal}). ${this.stderr}`)));
  }
  feed(chunk) {
    this.buffer += this.decoder.write(chunk);
    if (this.buffer.length > 16 * 1024 * 1024) { this.fail(new Error('RPC record exceeds 16 MiB')); return; }
    let index;
    while ((index = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, index).replace(/\r$/, '');
      this.buffer = this.buffer.slice(index + 1);
      if (!line) continue;
      let record;
      try { record = JSON.parse(line); }
      catch { this.fail(new Error('Invalid JSON from remote Pi; check its RPC version and SSH startup output')); return; }
      if (record.type === 'response') {
        const pending = this.pending.get(record.id);
        if (!pending) continue;
        clearTimeout(pending.timer);
        this.pending.delete(record.id);
        if (record.success) pending.resolve(record.data);
        else pending.reject(Object.assign(new Error(record.error || 'Remote command rejected'), { rejected: true }));
      } else this.emit('event', record);
    }
  }
  send(type, fields = {}, timeout = 30000) {
    if (this.closed) return Promise.reject(new Error('Disconnected; reconnect explicitly. No automatic resubmission.'));
    if (this.pending.size >= 32) return Promise.reject(new Error('Too many outstanding RPC requests'));
    const id = `herdr-native-${this.connection}-${++this.sequence}`;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`No acknowledgment for ${type}; delivery is uncertain. Do not retry without checking.`));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.write({ ...fields, type, id });
    });
  }
  write(record) {
    if (this.closed) throw new Error('RPC connection closed');
    this.process.stdin.write(JSON.stringify(record) + '\n');
  }
  fail(error) {
    if (this.closed) return;
    this.closed = true;
    for (const pending of this.pending.values()) { clearTimeout(pending.timer); pending.reject(error); }
    this.pending.clear();
    this.emit('disconnect', error);
  }
  async close() {
    if (this.process.exitCode !== null) return;
    this.process.stdin.end();
    await new Promise(resolve => {
      const timer = setTimeout(() => this.process.kill('SIGTERM'), 1500);
      const force = setTimeout(() => this.process.kill('SIGKILL'), 3000);
      this.process.once('exit', () => { clearTimeout(timer); clearTimeout(force); resolve(); });
    });
  }
}

/** Admission is distinct from completion. A lost acknowledgment never triggers a retry. */
export class Submission {
  constructor(rpc) { this.rpc = rpc; this.pending = false; this.uncertain = false; this.draft = ''; }
  async submit(text, streaming = false, followUp = false) {
    if (this.pending || this.uncertain) throw new Error('Previous delivery is pending/uncertain; inspect it before clearing the draft');
    if (!text.trim() && !this.allowEmpty) return;
    this.pending = true;
    this.draft = text;
    try {
      const result = followUp
        ? await this.rpc.send('follow_up', { message: text })
        : await this.rpc.send('prompt', { message: text, ...(streaming ? { streamingBehavior: 'steer' } : {}) });
      this.draft = '';
      return result;
    } catch (error) {
      this.uncertain = !error.rejected;
      throw error;
    } finally { this.pending = false; }
  }
  discard() {
    if (this.pending) return false;
    this.uncertain = false;
    this.draft = '';
    return true;
  }
}
