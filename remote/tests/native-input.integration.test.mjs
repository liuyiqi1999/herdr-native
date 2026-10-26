import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:net';
import { spawn } from 'node:child_process';
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

async function until(predicate, message) {
  const end = Date.now() + 8000;
  while (Date.now() < end) {
    if (await predicate()) return;
    await delay(10);
  }
  throw new Error(message());
}

async function start(t, state = {}, dropPrompt = false) {
  const dir = await mkdtemp('/tmp/hn-input-test-');
  const socket = join(dir, 'editor.sock'), drafts = join(dir, 'drafts');
  const requests = [], frames = [], connections = new Set();
  let stderr = '', child;
  const server = createServer(connection => {
    connections.add(connection);
    connection.setEncoding('utf8');
    connection.on('error', () => {});
    connection.on('close', () => connections.delete(connection));
    let buffer = '';
    connection.on('data', data => {
      buffer += data;
      let index;
      while ((index = buffer.indexOf('\n')) >= 0) {
        const request = JSON.parse(buffer.slice(0, index)); buffer = buffer.slice(index + 1);
        requests.push(request);
        if (request.type === 'prompt' && dropPrompt) { connection.destroy(); return; }
        connection.write(JSON.stringify({ type: 'response', id: request.id, success: true,
          data: request.type === 'get_state' ? { text: '', history: [], commands: [], ...state } : { disposition: 'queued' } }) + '\n');
      }
    });
  });
  t.after(async () => {
    if (child && child.exitCode === null) {
      child.stdin.end();
      const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
      await new Promise(resolve => child.once('exit', resolve));
      clearTimeout(timer);
    }
    for (const connection of connections) connection.destroy();
    await new Promise(resolve => server.close(resolve));
    await rm(dir, { recursive: true, force: true });
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(socket, resolve); });
  child = spawn(process.execPath, [fileURLToPath(new URL('../native-input.mjs', import.meta.url)),
    '--local', '--cwd', dir, '--socket', socket], {
    env: { ...process.env, HERDR_NATIVE_DRAFT_DIR: drafts }, stdio: ['pipe', 'pipe', 'pipe'],
  });
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', data => { stderr += data; });
  child.stdout.setEncoding('utf8');
  let buffer = '';
  child.stdout.on('data', data => {
    buffer += data;
    let index;
    while ((index = buffer.indexOf('\n')) >= 0) {
      frames.push(JSON.parse(buffer.slice(0, index))); buffer = buffer.slice(index + 1);
    }
  });
  const send = data => child.stdin.write(JSON.stringify({ type: 'input', data }) + '\n');
  child.stdin.write(JSON.stringify({ type: 'size', width: 100, height: 30 }) + '\n');
  await until(() => frames.some(frame => frame.ready), () => 'Editor not ready: ' + stderr);
  const snapshot = async () => {
    const files = await readdir(drafts).catch(() => []);
    const file = files.find(name => name.endsWith('.json'));
    return file ? JSON.parse(await readFile(join(drafts, file), 'utf8')) : null;
  };
  return { child, send, requests, frames, snapshot };
}

for (const editorKind of [undefined, 'claude-external', 'claude-auto']) {
  test(`real bridge submits Unicode exactly once (${editorKind ?? 'pi'})`, async t => {
    const bridge = await start(t, { editorKind });
    bridge.send('\x1b[200~你好🙂\nsecond\x1b[201~');
    bridge.send('\x1b[D');
    bridge.send('\x7f');
    await until(() => bridge.frames.some(frame => frame.lines?.some(line => line.includes('secod'))), () => 'Missing locally edited frame');
    assert.deepEqual(bridge.requests.map(request => request.type), ['get_state']);
    bridge.send(editorKind === 'claude-external' ? '\x07' : '\r');
    await until(() => bridge.requests.some(request => request.type === 'prompt'), () => 'No submit');
    await until(async () => { const saved = await bridge.snapshot(); return saved && !saved.pending && !saved.uncertain && saved.submitted === ''; }, () => 'Acknowledged draft not persisted');
    assert.deepEqual(bridge.requests.filter(request => request.type === 'prompt').map(request => request.message), ['你好🙂\nsecod']);
    if (editorKind === 'claude-auto') await until(() => bridge.child.exitCode !== null, () => 'Automatic editor did not exit');
  });
}

test('real bridge retains uncertain submission after lost ACK and does not retry', async t => {
  const bridge = await start(t, {}, true);
  bridge.send('保留这条消息');
  bridge.send('\r');
  await until(async () => (await bridge.snapshot())?.uncertain === true, () => 'Lost acknowledgment not persisted');
  const saved = await bridge.snapshot();
  assert.equal(saved.submitted, '保留这条消息');
  assert.equal(saved.pending, false);
  bridge.send('\r');
  await delay(150);
  assert.equal(bridge.requests.filter(request => request.type === 'prompt').length, 1);
});
