#!/usr/bin/env node
// Headless original Pi editor. Herdr composites its cells into the marked remote input region.
import { parseArgs } from 'node:util';
import { createInterface } from 'node:readline';
import { createHash } from 'node:crypto';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { mkdirSync, readFileSync, writeFileSync, renameSync } from 'node:fs';
import { loadPi } from './pi-package.mjs';
import { editorFrame } from './editor-frame.mjs';
import { Rpc, Submission, piCommand } from './rpc.mjs';
import { commandCompletion } from './pi-state.mjs';
import { ClipboardPaste, stageClipboardImage } from './clipboard.mjs';
import { remoteFiles, withFiles } from './files.mjs';
const { values } = parseArgs({ options: { host: { type: 'string' }, cwd: { type: 'string' }, socket: { type: 'string' }, local: { type: 'boolean' } } });
const { sdk, tui, colors, keys, commands, clipboard } = await loadPi();
colors.initTheme('dark', false);
let bindings = keys.KeybindingsManager.create();
tui.setKeybindings(bindings);
let width = 80, height = 24, scheduled = false, ready = false, closed = false, lastKey = '', status = '';
let remoteFocused = true, thinkingLevel = 'off', externalEditor = false, automaticEditor = false;
const earlyInputs = [];
const externalHint = 'Claude Code · Enter / Ctrl+G: save draft and return · Esc: cancel';
let controlQueue = Promise.resolve(), pendingControls = 0, handoffPending = false;
const output = value => process.stdout.write(JSON.stringify(value) + '\n');
const hostUi = { terminal: { rows: height, columns: width }, requestRender: schedule };
const editor = new sdk.CustomEditor(hostUi, colors.getEditorTheme(), bindings, { embedWorkingStatus: true });
editor.focused = true;
const command = piCommand({ ...values, binary: 'pi' });
const rpc = new Rpc(command.command, command.args, command.options);
const submission = new Submission(rpc);
const directory = process.env.HERDR_NATIVE_DRAFT_DIR || join(homedir(), 'Library/Application Support/Herdr Native/drafts');
mkdirSync(directory, { recursive: true, mode: 0o700 });
const draftFile = join(directory, createHash('sha256').update(JSON.stringify([values.host, values.socket])).digest('hex') + '.json');
let dirty = false;
function persist() {
  if (!dirty) return;
  writeFileSync(draftFile + '.tmp', JSON.stringify({ text: editor.getText(), uncertain: submission.uncertain, pending: submission.pending, submitted: submission.draft }), { mode: 0o600 });
  renameSync(draftFile + '.tmp', draftFile); dirty = false;
}
function schedule() {
  if (scheduled || closed) return;
  scheduled = true;
  setImmediate(() => {
    scheduled = false;
    editor.borderColor = editor.getText().startsWith('!') ? colors.theme.getBashModeBorderColor() : colors.theme.getThinkingBorderColor(thinkingLevel);
    const { lines, cursor } = editorFrame(editor.render(width), tui);
    if (externalEditor && !automaticEditor && !status) { lines.unshift(tui.truncateToWidth(externalHint, width)); if (cursor) cursor[1]++; }
    if (status) { lines.unshift(tui.truncateToWidth(status, width)); if (cursor) cursor[1]++; }
    output({ type: 'frame', width, lines, cursor, ready, preserve_top: !externalEditor && !status });
  });
}
async function submit(text, followUp = false) {
  if (!ready || submission.pending || submission.uncertain || rpc.closed || (automaticEditor && handoffPending)) {
    editor.setText(text); status = 'Connection unavailable or delivery uncertain; draft retained.'; dirty = true; schedule(); return;
  }
  editor.disableSubmit = true;
  try {
    const pending = submission.submit(text, false, followUp);
    dirty = true; persist();
    await pending;
    editor.addToHistory(text); status = '';
    if (automaticEditor) void shutdown();
  } catch (error) {
    editor.setText(text + (editor.getText() ? '\n' + editor.getText() : '')); status = error.message;
  } finally { editor.disableSubmit = false; dirty = true; persist(); schedule(); }
}
function control(key, deferInput = false) {
  if (automaticEditor) {
    if (handoffPending || submission.pending || submission.uncertain) return;
    handoffPending = true;
    return rpc.send('key', { key: key === '\x07' ? '\x1b' : key, message: editor.getText() })
      .then(() => shutdown())
      .catch(error => { status = error.message; dirty = true; schedule(); });
  }
  if (externalEditor) {
    if (tui.matchesKey(key, 'escape') || tui.matchesKey(key, 'ctrl+c')) return rpc.send('key', { key: '\x1b' }).catch(error => { status = error.message; schedule(); });
    if (key === '\x07') return submit(editor.getText());
    return;
  }
  pendingControls++;
  controlQueue = controlQueue.then(async () => {
    if (deferInput && remoteFocused) { lastKey = key; editor.handleInput(key); return; }
    if (!ready || submission.pending || submission.uncertain || rpc.closed) return;
    const draft = editor.getText();
    try {
      const result = await rpc.send('key', { key, message: draft });
      remoteFocused = result.focused !== false;
      thinkingLevel = result.thinkingLevel || thinkingLevel;
      if (editor.getText() === draft && typeof result.text === 'string') editor.setText(result.text);
      status = '';
    } catch (error) { status = error.message; }
  }).finally(() => { pendingControls--; dirty = true; persist(); schedule(); });
  return controlQueue;
}
editor.onSubmit = text => { void submit(text); };
for (const action of Object.keys(keys.KEYBINDINGS ?? {})) {
  if (action.startsWith('app.')) editor.onAction(action, () => { void control(lastKey); });
}
editor.onAction('app.clear', () => {
  if (editor.getText()) { if (submission.discard()) { editor.setText(''); status = ''; } }
  else void control(lastKey);
});
editor.onAction('app.message.followUp', () => {
  if (externalEditor) { void submit(editor.getText()); return; }
  if (editor.disableSubmit) return;
  const text = editor.getText(); editor.setText(''); void submit(text, true);
});
editor.onEscape = () => { void control(lastKey); };
editor.onCtrlD = () => { void control(lastKey); };
const clipboardPaste = new ClipboardPaste({
  read: () => clipboard.readClipboardImage(),
  stage: image => stageClipboardImage(image, values),
  insert: text => { editor.insertTextAtCursor(text); dirty = true; persist(); schedule(); },
  input: data => {
    lastKey = data;
    if (automaticEditor && (tui.matchesKey(data, 'tab') || tui.matchesKey(data, 'shift+tab') ||
        (!editor.getText() && (tui.matchesKey(data, 'up') || tui.matchesKey(data, 'down'))))) { void control(data); return; }
    if (externalEditor && tui.matchesKey(data, 'ctrl+g')) { void control('\x07'); return; }
    if (externalEditor && tui.matchesKey(data, 'ctrl+c')) { void control('\x03'); return; }
    if (pendingControls || !remoteFocused) void control(data, true);
    else editor.handleInput(data);
    dirty = true; persist(); schedule();
  },
  status: text => { status = text; schedule(); },
});
editor.onPasteImage = () => {
  const key = lastKey;
  void clipboardPaste.paste(() => control(key));
};
rpc.on('event', event => {
  if (event.type === 'editor_input') { if (ready) clipboardPaste.handleInput(event.data); else earlyInputs.push(event.data); }
  if (event.type === 'editor_resize') { if (ready) void control(''); else earlyInputs.push(null); }
  if (event.type === 'editor_state') { thinkingLevel = event.thinkingLevel || 'off'; schedule(); }
  if (event.type === 'editor_text' && !editor.getText() && !submission.pending) { editor.setText(event.text); dirty = true; schedule(); }
});
rpc.on('disconnect', error => { status = error.message; dirty = true; schedule(); });
const input = createInterface({ input: process.stdin });
input.on('line', line => {
  try {
    const event = JSON.parse(line);
    if (event.type === 'size') {
      width = Math.max(8, Math.min(500, event.width)); height = Math.max(5, Math.min(200, event.height));
      hostUi.terminal.columns = width; hostUi.terminal.rows = height;
    } else if (event.type === 'input' && ready) {
      clipboardPaste.handleInput(event.data);
    }
    schedule();
  } catch { output({ type: 'error', message: 'Invalid editor input' }); }
});
const timer = setInterval(() => { try { persist(); } catch (error) { status = 'Cannot save local draft: ' + error.message; schedule(); } }, 250);
async function shutdown() {
  if (closed) return; closed = true; clearInterval(timer); persist(); input.close(); await rpc.close();
}
input.once('close', () => { void shutdown(); });
process.once('SIGTERM', () => { void shutdown(); });
try {
  const state = await rpc.send('get_state');
  if (state.editorKind === 'claude-activate') {
    // The proxy changes Claude's input mode only after a native client arrives.
    // Its next explicit marker supplies the actual draft and editor connection.
    await shutdown();
  } else {
  automaticEditor = state.editorKind === 'claude-auto';
  externalEditor = automaticEditor || state.editorKind === 'claude-external';
  submission.allowEmpty = externalEditor;
  editor.setText(state.text || '');
  thinkingLevel = state.thinkingLevel || 'off';
  bindings = new keys.KeybindingsManager(state.keybindings || {});
  editor.keybindings = bindings; tui.setKeybindings(bindings);
  if (Number.isInteger(state.paddingX)) editor.setPaddingX(state.paddingX);
  if (Number.isInteger(state.autocompleteMaxVisible)) editor.setAutocompleteMaxVisible(state.autocompleteMaxVisible);
  for (const text of state.history || []) editor.addToHistory(text);
  try {
    const saved = JSON.parse(readFileSync(draftFile, 'utf8'));
    if (typeof saved.text === 'string') editor.setText(saved.text);
    if (saved.pending || saved.uncertain) { submission.uncertain = true; submission.draft = saved.submitted || ''; if (!saved.text) editor.setText(submission.draft); status = 'Previous delivery uncertain; inspect remote transcript before clearing.'; }
  } catch { /* First connection has no local draft. */ }
  let files = [];
  editor.setAutocompleteProvider(withFiles(commandCompletion(externalEditor ? [] : [...commands.BUILTIN_SLASH_COMMANDS, ...(state.commands || [])]), () => files));
  ready = true;
  if (state.initialInput) clipboardPaste.handleInput(state.initialInput);
  if (state.resizeRequested) void control('');
  for (const data of earlyInputs) { if (data === null) void control(''); else clipboardPaste.handleInput(data); }
  earlyInputs.length = 0;
  schedule();
  if (!automaticEditor) void remoteFiles(values).then(next => { files = next; }).catch(() => {});
  }
} catch (error) {
  status = error.message; ready = false; schedule();
  setTimeout(() => { void shutdown(); }, 2000).unref();
}
