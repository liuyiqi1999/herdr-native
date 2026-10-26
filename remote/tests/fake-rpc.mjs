#!/usr/bin/env node
import { StringDecoder } from 'node:string_decoder';
import { writeFileSync, appendFileSync } from 'node:fs';
// Test-only instrumentation; this backend never runs real model requests.
if (process.env.HERDR_TEST_RPC_PID_FILE) writeFileSync(process.env.HERDR_TEST_RPC_PID_FILE, String(process.pid));
const decode = new StringDecoder('utf8');
let buffer = '';
const model = { id: 'test-model', name: 'Test', provider: 'test', api: 'test', contextWindow: 32000, maxTokens: 1024, reasoning: false, input: ['text'], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 } };
const output = object => process.stdout.write(JSON.stringify(object) + '\n');
process.stdin.on('data', chunk => {
  buffer += decode.write(chunk);
  let i;
  while ((i = buffer.indexOf('\n')) >= 0) {
    const record = JSON.parse(buffer.slice(0, i)); buffer = buffer.slice(i + 1);
    if (process.env.HERDR_TEST_RPC_LOG_FILE) appendFileSync(process.env.HERDR_TEST_RPC_LOG_FILE, JSON.stringify(record) + '\n');
    if (record.type === 'extension_ui_response') continue;
    let data;
    switch (record.type) {
      case 'get_state': data = { model, isStreaming: false, thinkingLevel: 'off', sessionId: 'test', pendingMessageCount: 0 }; break;
      case 'get_messages': data = { messages: [] }; break;
      case 'get_commands': data = { commands: [{ name: 'test', description: 'Test command' }] }; break;
      case 'get_available_models': data = { models: [model] }; break;
      case 'pid': data = { pid: process.pid }; break;
      case 'echo': data = { text: record.text }; break;
      case 'reject': output({ id: record.id, type: 'response', success: false, error: 'rejected' }); continue;
      case 'hang': continue;
      case 'prompt': case 'follow_up':
        setTimeout(() => {
          output({ id: record.id, type: 'response', success: true, data: { disposition: 'queued', received: record.message } });
          output({ type: 'agent_start' });
          output({ type: 'message_start', message: { role: 'user', content: record.message, timestamp: Date.now() } });
          const usage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
          const message = { role: 'assistant', content: [], api: 'test', provider: 'test', model: 'test-model', usage, timestamp: Date.now(), stopReason: 'stop' };
          output({ type: 'message_start', message });
          output({ type: 'message_update', usage, assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: 'Backend response' } });
          output({ type: 'message_end', message: { ...message, content: [{ type: 'text', text: 'Backend response' }] } });
          output({ type: 'tool_execution_start', toolCallId: 'fixture-tool', toolName: 'bash', args: { command: 'fixture only; never executed' } });
          output({ type: 'tool_execution_end', toolCallId: 'fixture-tool', toolName: 'bash', result: { content: [{ type: 'text', text: 'Fixture tool output' }] }, isError: false, durationMs: 1 });
          output({ type: 'agent_settled', aborted: false });
        }, 250);
        continue;
      default: data = {};
    }
    output({ id: record.id, type: 'response', success: true, data });
  }
});
