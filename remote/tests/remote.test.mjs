import test from 'node:test';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { Rpc, Submission, piCommand } from '../rpc.mjs';
import { loadPi } from '../pi-package.mjs';
import { updateAssistant, commandCompletion } from '../pi-state.mjs';
import { withFiles } from '../files.mjs';
const fixture = fileURLToPath(new URL('./fake-rpc.mjs', import.meta.url));

function backend(t) {
  const rpc = new Rpc(process.execPath, [fixture]);
  t.after(() => rpc.close());
  return rpc;
}

test('SSH arguments preserve remote paths and reject option injection', () => {
  const launch = piCommand({ host: 'devbox', cwd: "/tmp/a b'c", binary: '/opt/pi path/pi' });
  assert.equal(launch.command, 'ssh');
  assert.match(launch.args.at(-1), /cd '\/tmp\/a b'\\''c'/);
  assert.throws(() => piCommand({ host: '-oProxyCommand=bad', cwd: '/tmp' }));
});

test('RPC preserves Unicode separators, CJK, multiline, and correlated replies', async t => {
  const rpc = backend(t);
  const texts = ['中文🙂\nsecond', 'before\u2028after\u2029end'];
  const responses = await Promise.all(texts.map(text => rpc.send('echo', { text })));
  assert.deepEqual(responses.map(r => r.text), texts);
  const records = [];
  rpc.on('event', e => records.push(e));
  const bytes = Buffer.from(JSON.stringify({ type: 'note', text: '中文🙂' }) + '\n');
  for (const byte of bytes) rpc.feed(Buffer.from([byte]));
  assert.equal(records[0].text, '中文🙂');
});

test('only one pending submission; lost acknowledgment never retries', async t => {
  const rpc = backend(t);
  const submit = new Submission(rpc);
  const promise = submit.submit('hello\nworld');
  await assert.rejects(submit.submit('duplicate'), /pending/);
  assert.equal((await promise).received, 'hello\nworld');
  assert.equal(submit.draft, '');
  const uncertain = new Submission({ send: () => Promise.reject(new Error('disconnected')) });
  await assert.rejects(uncertain.submit('keep me'));
  assert.equal(uncertain.draft, 'keep me');
  assert.equal(uncertain.uncertain, true);
  await assert.rejects(uncertain.submit('again'), /uncertain/);
  assert.equal(uncertain.discard(), true);
});

test('explicit rejection remains distinguishable from uncertain delivery', async t => {
  const rpc = backend(t);
  await assert.rejects(rpc.send('reject'), e => e.rejected === true);
  await assert.rejects(rpc.send('hang', {}, 50), /uncertain/);
  assert.equal(rpc.pending.size, 0);
});

test('Pi original editor: local Unicode, newline, movement, deletion, one submit', async () => {
  const { sdk, colors, keys, tui } = await loadPi();
  colors.initTheme('dark', false);
  tui.setKeybindings(keys.KeybindingsManager.create());
  let requests = 0;
  const submit = new Submission({ send: async (_method, data) => { requests++; return data; } });
  const editor = new sdk.CustomEditor({ requestRender() {}, terminal: { rows: 40, columns: 100 } }, colors.getEditorTheme(), keys.KeybindingsManager.create());
  let result;
  editor.onSubmit = text => { result = submit.submit(text); };
  editor.handleInput('\x1b[200~你好🙂\nsecond\x1b[201~');
  editor.handleInput('\x1b[D');
  editor.handleInput('\x7f');
  assert.equal(requests, 0, 'editing must never send to backend');
  assert.equal(editor.getText(), '你好🙂\nsecod');
  assert.ok(editor.render(80).length >= 3);
  editor.handleInput('\r');
  assert.equal(requests, 1);
  const sent = await result;
  assert.equal(sent.message, '你好🙂\nsecod');
});

test('RPC delta rendering and cached command/file completion do not require a backend', async () => {
  const message = { role: 'assistant', content: [] };
  updateAssistant(message, { assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: '你' } });
  updateAssistant(message, { assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: '好' } });
  assert.equal(message.content[0].text, '你好');
  updateAssistant(message, { assistantMessageEvent: { type: 'text_end', contentIndex: 0, content: 'authoritative' } });
  assert.equal(message.content[0].text, 'authoritative');
  const provider = withFiles(commandCompletion([{ name: 'model' }]), () => ['src/main.ts', 'space name.ts']);
  assert.equal((await provider.getSuggestions(['/mo'], 0, 3, {})).items[0].value, '/model');
  assert.equal((await provider.getSuggestions(['read @main'], 0, 10, {})).items[0].value, '@src/main.ts');
  const suggestion = await provider.getSuggestions(['@space'], 0, 6, {});
  assert.equal(provider.applyCompletion(['@space'], 0, 6, suggestion.items[0], suggestion.prefix).lines[0], '@"space name.ts" ');
});
