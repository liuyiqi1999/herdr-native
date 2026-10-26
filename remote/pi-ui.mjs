#!/usr/bin/env node
import { parseArgs } from 'node:util';
import { tmpdir } from 'node:os';
import { posix } from 'node:path';
import { Rpc, Submission, piCommand } from './rpc.mjs';
import { loadPi } from './pi-package.mjs';
import { commandCompletion, updateAssistant } from './pi-state.mjs';
import { remoteFiles, withFiles } from './files.mjs';

const { values } = parseArgs({ options: {
  host: { type: 'string' }, cwd: { type: 'string' }, 'remote-bin': { type: 'string', default: 'pi' },
  session: { type: 'string' }, socket: { type: 'string' }, local: { type: 'boolean', default: false },
} });
if (!values.cwd || (!values.local && !values.host)) throw new Error('Usage: pi-ui.mjs --host SSH_ALIAS --cwd /remote/project [--remote-bin /path/pi] [--session ID]');
const { sdk, tui, colors, keys, renderer, viewport } = await loadPi();
// Herdr recognizes this process title as Pi, including when the agent runs remotely.
process.title = 'herdr:pi';
const command = piCommand({ host: values.host, cwd: values.cwd, binary: values['remote-bin'], session: values.session, socket: values.socket, local: values.local });
const rpc = new Rpc(command.command, command.args, command.options);
const submission = new Submission(rpc);
colors.initTheme('dark', false);
const keybindings = keys.KeybindingsManager.create();
tui.setKeybindings(keybindings);
const ui = renderer.createInteractiveTui({ tuiMode: 'fullscreen', showHardwareCursor: true, logDirectory: tmpdir() });
const document = new tui.Container();
const status = new tui.Text('Connecting…', 0, 0);
const footer = new tui.Text('', 0, 0);
const pending = new tui.Text('', 0, 0);
const editor = new sdk.CustomEditor(ui, colors.getEditorTheme(), keybindings, { embedWorkingStatus: true });
const view = viewport.createChatViewport({ document, status, pendingMessages: pending, editor, footer });
for (const component of [document, pending, status, editor, footer]) ui.addChild(component);
ui.setLayoutRoot(view.root);
ui.setFocus(editor);
let state = {}, closing = false, ready = false, activeAssistant, assistantView;
let extensionDialog, dialogDraft = '', dialogTimer;
let hideThinking = false, expanded = false;
let lastTerminalTitle;
const tools = new Map();
const assistantViews = [];
const extensionStatuses = new Map();
const report = text => { status.setText(text); ui.requestRender(); };
const textOf = content => typeof content === 'string' ? content : (content ?? []).filter(c => c.type === 'text').map(c => c.text).join('\n');
const builtins = [
  { name: 'new', description: 'New remote session' }, { name: 'model', description: 'List models or /model provider/id' },
  { name: 'thinking', description: '/thinking level' }, { name: 'compact', description: 'Compact remote context' },
  { name: 'name', description: 'Rename remote session' }, { name: 'session', description: 'Remote session details' },
  { name: 'refresh', description: 'Refresh remote history and commands' }, { name: 'quit', description: 'Close this connection' },
];
let remoteCommands = [];
let fileIndex = [];
async function refreshFiles() {
  try { fileIndex = await remoteFiles({ host: values.host, cwd: values.cwd, local: values.local }); }
  catch { /* Remote Python/file enumeration is optional; typing and RPC remain available. */ }
}

function renderFooter() {
  // Match interactive Pi's title, allowing Herdr to project the session name into its tab.
  const cleanTitle = value => String(value ?? '').replace(/[\x00-\x1f\x7f-\x9f]/g, ' ').trim();
  const name = cleanTitle(state.sessionName);
  const cwd = cleanTitle(posix.basename(values.cwd)) || '/';
  const title = name ? `π - ${name} - ${cwd}` : `π - ${cwd}`;
  if (title !== lastTerminalTitle) {
    ui.terminal.setTitle(title);
    lastTerminalTitle = title;
  }
  footer.setText(`${values.host ?? 'local RPC'}:${values.cwd}\n${state.model?.id ?? 'no model'} · ${state.thinkingLevel ?? 'off'}${state.sessionName ? ' · ' + state.sessionName : ''}`);
  ui.requestRender();
}
function addMessage(message, initial = false) {
  if (message.role === 'user') {
    const text = textOf(message.content);
    document.addChild(new sdk.UserMessageComponent(text));
    if (initial) editor.addToHistory(text);
  } else if (message.role === 'assistant') {
    const component = new sdk.AssistantMessageComponent(message, hideThinking);
    assistantViews.push(component);
    document.addChild(component);
    if (!initial) { activeAssistant = structuredClone(message); assistantView = component; }
  } else if (message.role === 'toolResult') {
    if (!tools.has(message.toolCallId)) {
      document.addChild(new tui.Text(`${message.toolName ?? 'tool'}\n${textOf(message.content)}`, 1, 1));
    }
  }
}
async function refresh() {
  const [next, history, commands] = await Promise.all([rpc.send('get_state'), rpc.send('get_messages'), rpc.send('get_commands')]);
  state = next;
  document.clear();
  activeAssistant = undefined;
  assistantView = undefined;
  tools.clear();
  assistantViews.length = 0;
  document.addChild(new tui.Text(`pi · ${values.host ?? 'local RPC'}`, 1, 1));
  for (const message of history.messages) addMessage(message, true);
  remoteCommands = commands.commands ?? [];
  editor.setAutocompleteProvider(withFiles(commandCompletion([...builtins, ...remoteCommands]), () => fileIndex));
  renderFooter();
  report(state.isStreaming ? 'Working…' : '');
}
async function shutdown() {
  if (closing) return;
  closing = true;
  clearTimeout(dialogTimer);
  ui.stop();
  await rpc.close();
}
async function control(type, fields = {}) {
  try { await rpc.send(type, fields); state = await rpc.send('get_state'); renderFooter(); }
  catch (error) { report(error.message); }
}
function restoreDialogDraft() {
  clearTimeout(dialogTimer);
  extensionDialog = undefined;
  editor.setText(dialogDraft);
  dialogDraft = '';
  report('');
}
function extensionUI(event) {
  if (['select', 'confirm', 'input', 'editor'].includes(event.method)) {
    if (extensionDialog) { report('Another remote dialog is pending; resolve it first.'); return; }
    extensionDialog = event;
    dialogDraft = editor.getText();
    editor.setText(event.prefill ?? '');
    const choices = event.options?.map((option, i) => `${i + 1}. ${option}`).join('\n');
    report([event.title, event.message, choices, event.method === 'confirm' ? 'Type yes or no; Esc cancels.' : 'Enter replies; Esc cancels.'].filter(Boolean).join('\n'));
    if (event.timeout) dialogTimer = setTimeout(restoreDialogDraft, event.timeout);
  } else if (event.method === 'notify') report(event.message);
  else if (event.method === 'setStatus') {
    if (event.statusText) extensionStatuses.set(event.statusKey, event.statusText);
    else extensionStatuses.delete(event.statusKey);
    report([...extensionStatuses.values()].join(' · '));
  } else if (event.method === 'set_editor_text') {
    if (!editor.getText()) editor.setText(event.text);
    else report('Remote extension offered editor text; current local draft retained.');
  } else if (event.method === 'setWidget') report((event.widgetLines ?? []).join('\n'));
}

rpc.on('event', event => {
  if (event.type === 'extension_ui_request') { extensionUI(event); return; }
  switch (event.type) {
    case 'agent_start': state.isStreaming = true; report('Working…'); break;
    case 'agent_settled': state.isStreaming = false; report(''); void rpc.send('get_state').then(next => { state = next; renderFooter(); }).catch(error => report(error.message)); break;
    case 'message_start': if (event.message.role !== 'toolResult') addMessage(event.message); break;
    case 'message_update':
      if (activeAssistant) { updateAssistant(activeAssistant, event); assistantView.updateContent(activeAssistant, true); }
      break;
    case 'message_end':
      if (event.message.role === 'assistant') {
        if (assistantView) assistantView.updateContent(event.message, false);
        else addMessage(event.message);
        activeAssistant = undefined;
        assistantView = undefined;
      }
      break;
    case 'tool_execution_start': {
      const component = new sdk.ToolExecutionComponent(event.toolName, event.toolCallId, event.args, undefined, undefined, ui, values.cwd);
      component.markExecutionStarted();
      component.setArgsComplete();
      component.setExpanded(expanded);
      tools.set(event.toolCallId, component);
      document.addChild(component);
      break;
    }
    case 'tool_execution_update': tools.get(event.toolCallId)?.updateResult(event.partialResult, true); break;
    case 'tool_execution_end': tools.get(event.toolCallId)?.updateResult({ ...event.result, isError: event.isError, durationMs: event.durationMs }, false); break;
    case 'queue_update': pending.setText([...event.steering, ...event.followUp].map(s => `↳ ${typeof s === 'string' ? s : s.text ?? ''}`).join('\n')); break;
    case 'thinking_level_changed': state.thinkingLevel = event.level; renderFooter(); break;
    case 'session_info_changed': state.sessionName = event.name; renderFooter(); break;
    case 'compaction_start': report('Compacting…'); break;
    case 'compaction_end': report(event.errorMessage ?? ''); break;
    case 'extension_error': report(event.error); break;
    case 'auto_retry_start': report(`Retry ${event.attempt}/${event.maxAttempts}: ${event.errorMessage}`); break;
  }
  ui.requestRender();
});
rpc.on('disconnect', error => {
  if (closing) return;
  ready = false;
  report(`${error.message}\nDisconnected. Local text remains editable; no automatic resend. Ctrl+D exits when empty.`);
});

async function submit(text, followUp = false) {
  if (extensionDialog) {
    const request = extensionDialog;
    let response;
    if (request.method === 'confirm') {
      if (!/^(yes|no)$/i.test(text.trim())) { editor.setText(text); report('Type yes or no; Esc cancels.'); return; }
      response = { confirmed: /^yes$/i.test(text.trim()) };
    } else if (request.method === 'select') {
      const selected = request.options[Number(text.trim()) - 1] ?? request.options.find(s => s === text);
      if (selected === undefined) { editor.setText(text); report('Enter an option number or exact option text.'); return; }
      response = { value: selected };
    } else response = { value: text };
    rpc.write({ type: 'extension_ui_response', id: request.id, ...response });
    restoreDialogDraft();
    return;
  }
  if (!ready || submission.pending || submission.uncertain) {
    editor.setText(submission.draft || text);
    report('Not ready, or previous delivery is uncertain. Draft retained; inspect remote state before Ctrl+C clears it.');
    return;
  }
  try {
    const [name, ...words] = text.trim().split(/\s+/);
    const rest = words.join(' ');
    if (name === '/quit') { await shutdown(); return; }
    if (name === '/new') {
      const result = await rpc.send('new_session');
      if (!result.cancelled) await refresh();
      return;
    }
    if (name === '/refresh') { await refresh(); void refreshFiles(); return; }
    if (name === '/session') { report(JSON.stringify(state, null, 2)); return; }
    if (name === '/name') { await control('set_session_name', { name: rest }); return; }
    if (name === '/thinking') { await control(rest ? 'set_thinking_level' : 'cycle_thinking_level', rest ? { level: rest } : {}); return; }
    if (name === '/compact') { await rpc.send('compact', { customInstructions: rest || undefined }, 300000); await refresh(); return; }
    if (name === '/model') {
      const models = (await rpc.send('get_available_models')).models;
      const model = models.find(m => `${m.provider}/${m.id}` === rest);
      if (!model) report(models.map(m => `/model ${m.provider}/${m.id}`).join('\n'));
      else await control('set_model', { provider: model.provider, modelId: model.id });
      return;
    }
    if (text.startsWith('/') && !remoteCommands.some(c => name === '/' + c.name)) {
      editor.setText(text);
      report('This local Pi command is not supported in RPC mode. Type / for available remote commands.');
      return;
    }
    if (text.startsWith('!')) {
      const result = await rpc.send('bash', { command: text.slice(text.startsWith('!!') ? 2 : 1), excludeFromContext: text.startsWith('!!') }, 300000);
      document.addChild(new tui.Text(result.output, 1, 1));
      editor.addToHistory(text);
      ui.requestRender();
      return;
    }
    editor.disableSubmit = true;
    await submission.submit(text, state.isStreaming, followUp);
    editor.addToHistory(text);
  } catch (error) {
    // Preserve any newly typed next-turn draft as well as the failed submission.
    const next = editor.getText();
    editor.setText(text + (next ? '\n' + next : ''));
    report(error.message);
  } finally { editor.disableSubmit = false; ui.requestRender(); }
}
editor.onSubmit = text => { void submit(text); };
editor.onAction('app.message.followUp', () => {
  if (editor.disableSubmit) return;
  const text = editor.getText(); editor.setText(''); void submit(text, true);
});
editor.onAction('app.clear', () => {
  if (editor.getText()) { if (submission.discard()) { editor.setText(''); report(''); } }
  else void shutdown();
});
editor.onCtrlD = () => { void shutdown(); };
editor.onEscape = () => {
  if (extensionDialog) { rpc.write({ type: 'extension_ui_response', id: extensionDialog.id, cancelled: true }); restoreDialogDraft(); return; }
  if (!ready) return;
  void (async () => {
    const queued = await rpc.send('clear_queue');
    await rpc.send('abort');
    const text = [...queued.steering, ...queued.followUp, editor.getText()].filter(Boolean).join('\n');
    editor.setText(text);
  })().catch(error => report(error.message));
};
editor.onAction('app.thinking.cycle', () => { void control('cycle_thinking_level'); });
editor.onAction('app.model.cycleForward', () => { void control('cycle_model'); });
editor.onAction('app.tools.expand', () => { expanded = !expanded; for (const tool of tools.values()) tool.setExpanded(expanded); ui.requestRender(); });
editor.onAction('app.thinking.toggle', () => { hideThinking = !hideThinking; for (const component of assistantViews) component.setHideThinkingBlock(hideThinking); ui.requestRender(); });
process.once('SIGTERM', () => { void shutdown(); });
process.once('SIGINT', () => { void shutdown(); });
ui.start();
try { await refresh(); ready = true; void refreshFiles(); }
catch (error) { report(error.message); }
