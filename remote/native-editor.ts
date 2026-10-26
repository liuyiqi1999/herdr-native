/* Native Herdr input bridge. The original remote InteractiveMode owns the entire UI.
 * Only its focused editor is marked for local rendering; RPC mode is unchanged.
 * Installed explicitly with -e by the native launcher, not as a global extension.
 */
import { createServer, type Socket } from 'node:net';
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { CustomEditor, type ExtensionAPI, type ExtensionContext } from '@earendil-works/pi-coding-agent';

export default function nativeEditor(pi: ExtensionAPI) {
  let context: ExtensionContext;
  let editor: CustomEditor | undefined;
  let peer: Socket | undefined;
  let directory: string | undefined;
  let server: ReturnType<typeof createServer> | undefined;
  let applying = false;
  let keybindingsConfig: unknown = {};
  const replies = new Map<string, unknown>();
  const write = (value: unknown) => {
    if (!peer || peer.destroyed) return;
    if (peer.writableLength > 2 * 1024 * 1024) { peer.destroy(); return; }
    peer.write(JSON.stringify(value) + '\n');
  };
  const state = () => ({
    text: editor?.getText() ?? '', cwd: context.cwd,
    model: context.model, thinkingLevel: pi.getThinkingLevel(),
    commands: pi.getCommands(), theme: context.ui.theme.name,
    isStreaming: !context.isIdle(), sessionId: context.sessionManager.getSessionId(),
    focused: editor?.focused ?? false,
    paddingX: editor?.getPaddingX(), autocompleteMaxVisible: editor?.getAutocompleteMaxVisible(),
    keybindings: keybindingsConfig,
    history: context.sessionManager.getBranch().filter((entry: any) => entry.type === 'message' && entry.message.role === 'user')
      .slice(-100).map((entry: any) => typeof entry.message.content === 'string' ? entry.message.content : entry.message.content.filter((part: any) => part.type === 'text').map((part: any) => part.text).join('\n')),
  });
  const close = () => {
    peer?.destroy(); peer = undefined;
    server?.close(); server = undefined;
    if (directory) rmSync(directory, { recursive: true, force: true });
    directory = undefined;
  };
  pi.on('session_start', async (_event, ctx) => {
    if (ctx.mode !== 'tui') return;
    close(); context = ctx;
    const requested = process.env.HERDR_PI_EDITOR_SOCKET;
    if (requested && !/^\/tmp\/hn-editor-[A-Za-z0-9_-]+\/editor\.sock$/.test(requested)) throw new Error('Invalid native editor socket');
    directory = requested ? dirname(requested) : mkdtempSync(join(tmpdir(), 'hn-editor-'));
    chmodSync(directory, 0o700);
    const socketPath = requested || join(directory, 'editor.sock');
    writeFileSync(join(directory, 'pid'), String(process.pid), { mode: 0o600 });
    const marker = 'https://herdr-native.invalid/editor/v1/' + Buffer.from(socketPath).toString('base64url');
    ctx.ui.setEditorComponent((tui, theme, keybindings) => {
      class NativeEditor extends CustomEditor {
        render(width: number): string[] {
          const lines = super.render(width);
          return this.focused ? lines.map(line => `\x1b]8;;${marker}\x1b\\${line}\x1b]8;;\x1b\\`) : lines;
        }
        handleInput(data: string) {
          const before = this.getText();
          super.handleInput(data);
          if (!applying && this.getText() !== before) write({ type: 'editor_text', text: this.getText() });
        }
        setText(text: string) {
          super.setText(text);
          if (!applying) write({ type: 'editor_text', text });
        }
      }
      keybindingsConfig = keybindings.getEffectiveConfig();
      editor = new NativeEditor(tui, theme, keybindings, { embedWorkingStatus: true });
      return editor;
    });
    server = createServer(client => {
      if (peer && !peer.destroyed) { client.destroy(); return; }
      peer = client;
      client.setEncoding('utf8');
      let buffer = '';
      client.on('error', () => {});
      client.on('close', () => { if (peer === client) peer = undefined; });
      client.on('data', chunk => {
        buffer += chunk;
        if (buffer.length > 1024 * 1024) { client.destroy(); return; }
        let end;
        while ((end = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, end); buffer = buffer.slice(end + 1);
          let request: any;
          try { request = JSON.parse(line); } catch { client.destroy(); return; }
          if (typeof request.id !== 'string' || request.id.length > 200) { client.destroy(); return; }
          if (replies.has(request.id)) { write(replies.get(request.id)); continue; }
          let reply: any;
          try {
            if (request.type === 'get_state') {
              reply = { id: request.id, type: 'response', success: true, data: state() };
            } else {
              if (!editor || (request.type !== 'key' && (!editor.focused || editor.disableSubmit))) throw new Error('The remote editor is not accepting input; draft retained.');
              if (typeof request.message !== 'string' || request.message.length > 512 * 1024) throw new Error('Invalid editor message');
              if (!['prompt', 'follow_up', 'key'].includes(request.type)) throw new Error('Unsupported editor command');
              applying = true;
              try {
                if (editor.focused) editor.setText(request.message);
                if (request.type === 'key') {
                  if (typeof request.key !== 'string' || request.key.length > 512 * 1024) throw new Error('Invalid control key');
                  process.stdin.emit('data', Buffer.from(request.key));
                } else {
                  process.stdin.emit('data', Buffer.from(request.type === 'follow_up' ? '\x1b\r' : '\r'));
                }
              } finally { applying = false; }
              reply = { id: request.id, type: 'response', success: true, data: { text: editor.getText(), focused: editor.focused, thinkingLevel: pi.getThinkingLevel() } };
            }
          } catch (error) {
            reply = { id: request.id, type: 'response', success: false, error: String(error) };
          }
          replies.set(request.id, reply);
          if (replies.size > 512) replies.delete(replies.keys().next().value!);
          write(reply);
        }
      });
    });
    await new Promise<void>((resolve, reject) => {
      server!.once('error', reject);
      server!.listen(socketPath, () => { chmodSync(socketPath, 0o600); resolve(); });
    });
  });
  for (const event of ['agent_start', 'agent_settled', 'model_select', 'session_info_changed'] as const) {
    pi.on(event, (_event, ctx) => {
      context = ctx;
      if (editor) write({ type: 'editor_state', ...state() });
    });
  }
  pi.on('session_shutdown', close);
}
