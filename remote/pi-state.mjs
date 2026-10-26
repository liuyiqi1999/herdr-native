// RPC 1.3 message_update contains deltas, not SDK cumulative message snapshots.
export function updateAssistant(message, event) {
  if (!message) return message;
  const update = event.assistantMessageEvent;
  if (!update) return message;
  const content = message.content ?? (message.content = []);
  const i = update.contentIndex;
  switch (update.type) {
    case 'text_start': content[i] = { type: 'text', text: '' }; break;
    case 'thinking_start': content[i] = { type: 'thinking', thinking: '' }; break;
    case 'text_delta':
      content[i] ??= { type: 'text', text: '' };
      content[i].text += update.delta;
      break;
    case 'thinking_delta':
      content[i] ??= { type: 'thinking', thinking: '' };
      content[i].thinking += update.delta;
      break;
    case 'text_end': content[i] = { type: 'text', text: update.content }; break;
    case 'thinking_end': content[i] = { type: 'thinking', thinking: update.content }; break;
    case 'toolcall_start': content[i] = { type: 'toolCall', id: update.id, name: update.toolName, arguments: {} }; break;
    case 'toolcall_end': content[i] = update.toolCall; break;
  }
  if (event.usage) message.usage = event.usage;
  return message;
}

export function commandCompletion(commands) {
  return {
    triggerCharacters: ['/'],
    async getSuggestions(lines, line, column) {
      const prefix = lines[line].slice(0, column);
      if (line !== 0 || !prefix.startsWith('/') || prefix.includes(' ')) return null;
      const items = commands.filter(c => ('/' + c.name).startsWith(prefix)).map(c => ({ value: '/' + c.name, label: '/' + c.name, description: c.description }));
      return items.length ? { items, prefix } : null;
    },
    applyCompletion(lines, line, column, item, prefix) {
      const next = [...lines];
      const start = column - prefix.length;
      next[line] = lines[line].slice(0, start) + item.value + ' ' + lines[line].slice(column);
      return { lines: next, cursorLine: line, cursorCol: start + item.value.length + 1 };
    },
  };
}
