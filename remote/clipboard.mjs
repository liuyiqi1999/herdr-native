import { execFile } from 'node:child_process';
import { quote } from './rpc.mjs';

export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const extensions = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif' };
// Send bytes over stdin, never through argv or a shell interpolation. mkstemp uses mode 0600.
const STAGE = `import os,sys,tempfile
payload=sys.stdin.buffer.read(${MAX_IMAGE_BYTES + 1})
if not payload or len(payload)>${MAX_IMAGE_BYTES}: raise ValueError('Invalid image size')
fd,path=tempfile.mkstemp(prefix='pi-clipboard-',suffix='.'+sys.argv[1])
try:
 with os.fdopen(fd,'wb') as out: out.write(payload)
except BaseException:
 os.unlink(path)
 raise
print(path)`;

export async function stageClipboardImage(image, { host, local = false }) {
  const extension = extensions[image?.mimeType];
  if (!extension) throw new Error('Unsupported clipboard image type');
  const bytes = Buffer.from(image.bytes);
  if (!bytes.length || bytes.length > MAX_IMAGE_BYTES) throw new Error('Clipboard image must be between 1 byte and 10 MiB');
  if (!local && (!host || host.startsWith('-') || /\s/.test(host))) throw new Error('Invalid SSH host');
  const remote = ['python3', '-c', STAGE, extension];
  const args = local ? remote.slice(1) : ['-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', host, remote.map(quote).join(' ')];
  const stdout = await new Promise((resolve, reject) => {
    const child = execFile(local ? 'python3' : 'ssh', args, { timeout: 30000, maxBuffer: 64 * 1024 }, (error, stdout) => {
      if (error) reject(new Error(`Image upload failed: ${error.message}`));
      else resolve(stdout);
    });
    child.stdin.on('error', () => {}); // execFile reports process/transport failure.
    child.stdin.end(bytes);
  });
  const path = stdout.trim();
  if (!/^\/[^\r\n\x00-\x1f]*\/pi-clipboard-[\w-]+\.(png|jpg|webp|gif)$/.test(path)) throw new Error('Invalid uploaded image path');
  return path;
}

/** Queue input while the clipboard/upload is pending, including an immediate Enter. */
export class ClipboardPaste {
  constructor({ read, stage, insert, input, status }) {
    Object.assign(this, { read, stage, insert, input, status });
    this.busy = false;
    this.queue = [];
  }
  handleInput(data) {
    if (this.busy) this.queue.push(data);
    else this.input(data);
  }
  async paste(fallback) {
    if (this.busy) return;
    this.busy = true;
    this.status('Reading local clipboard…');
    try {
      const image = await this.read();
      if (image) {
        this.status('Uploading image…');
        const path = await this.stage(image);
        this.insert(` ${path} `);
        this.status('Image attached');
      } else {
        await fallback();
        this.status('');
      }
    } catch (error) {
      this.status(error.message);
      // Do not replay Enter after a failed attachment. Keep editing keys, but require a new submit.
      this.queue = this.queue.filter(key => !['\r', '\n', '\x1b\r'].includes(key));
    } finally {
      this.busy = false;
      while (!this.busy && this.queue.length) this.input(this.queue.shift());
    }
  }
}
