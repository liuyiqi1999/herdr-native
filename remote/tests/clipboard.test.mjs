import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, stat, unlink } from 'node:fs/promises';
import { ClipboardPaste, stageClipboardImage, MAX_IMAGE_BYTES } from '../clipboard.mjs';
import { loadPi } from '../pi-package.mjs';

const image = { mimeType: 'image/png', bytes: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64') };
test('staging preserves image bytes in a private file', async () => {
  const path = await stageClipboardImage(image, { local: true });
  try {
    assert.deepEqual(await readFile(path), image.bytes);
    assert.equal((await stat(path)).mode & 0o777, 0o600);
  } finally { await unlink(path); }
});
test('reject invalid targets, unsupported types and oversized payloads before upload', async () => {
  await assert.rejects(stageClipboardImage(image, { host: '-bad' }), /SSH host/);
  await assert.rejects(stageClipboardImage({ ...image, mimeType: 'text/plain' }, { local: true }), /Unsupported/);
  await assert.rejects(stageClipboardImage({ ...image, bytes: Buffer.alloc(MAX_IMAGE_BYTES + 1) }, { local: true }), /10 MiB/);
});
test('original Pi editor submits the uploaded path even when Enter arrives during paste', async () => {
  const { sdk, colors, keys, tui } = await loadPi();
  colors.initTheme('dark', false);
  tui.setKeybindings(keys.KeybindingsManager.create());
  const editor = new sdk.CustomEditor({ requestRender() {}, terminal: { rows: 40, columns: 100 } }, colors.getEditorTheme(), keys.KeybindingsManager.create());
  let resolveUpload, sent;
  editor.setText('describe');
  editor.onSubmit = text => { sent = text; };
  const paste = new ClipboardPaste({ read: async () => image, stage: () => new Promise(resolve => { resolveUpload = resolve; }), insert: text => editor.insertTextAtCursor(text), input: key => editor.handleInput(key), status() {} });
  let pending;
  editor.onPasteImage = () => { pending = paste.paste(() => assert.fail('must not forward image paste to remote clipboard')); };
  paste.handleInput('\x16');
  assert.ok(pending, 'Ctrl+V must invoke the local image handler');
  paste.handleInput('\r');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(sent, undefined);
  resolveUpload('/tmp/pi-clipboard-test.png');
  await pending;
  assert.equal(sent.trim(), 'describe /tmp/pi-clipboard-test.png');
});
test('upload failure keeps text and does not submit queued Enter', async () => {
  const inputs = [], statuses = [];
  const paste = new ClipboardPaste({ read: async () => image, stage: async () => { throw new Error('SSH failed'); }, insert: () => assert.fail(), input: key => inputs.push(key), status: value => statuses.push(value) });
  const pending = paste.paste(() => assert.fail());
  paste.handleInput('x'); paste.handleInput('\r');
  await pending;
  assert.deepEqual(inputs, ['x']);
  assert.equal(statuses.at(-1), 'SSH failed');
});
test('non-image clipboard preserves the previous fallback', async () => {
  let calls = 0;
  const paste = new ClipboardPaste({ read: async () => null, stage: () => assert.fail(), insert: () => assert.fail(), input() {}, status() {} });
  await paste.paste(async () => { calls++; });
  assert.equal(calls, 1);
});
