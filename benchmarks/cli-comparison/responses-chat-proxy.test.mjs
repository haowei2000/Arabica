import test from 'node:test';
import assert from 'node:assert/strict';
import {
  chatToResponses,
  createToolTranslation,
  responsesToChat,
  responseEvents,
} from './responses-chat-proxy.mjs';

test('preserves reasoning, calls, and results across stateless continuation', () => {
  const chat = responsesToChat({
    model: 'glm-5.3-flash',
    input: [
      { role: 'user', content: 'edit the file' },
      { type: 'reasoning', summary: [{ type: 'summary_text', text: 'inspect first' }] },
      { type: 'function_call', call_id: 'call_1', name: 'read_file', arguments: '{"path":"a.js"}' },
      { type: 'function_call_output', call_id: 'call_1', output: 'old' },
    ],
    tools: [{ type: 'function', name: 'read_file', description: 'read',
      parameters: { type: 'object', properties: { path: { type: 'string' } } }, strict: true }],
    tool_choice: { type: 'function', name: 'read_file' },
    reasoning: { effort: 'high' },
  });
  assert.deepEqual(chat.messages[1], { role: 'assistant', content: null,
    reasoning_content: 'inspect first', tool_calls: [{ id: 'call_1', type: 'function',
      function: { name: 'read_file', arguments: '{"path":"a.js"}' } }] });
  assert.deepEqual(chat.messages[2], { role: 'tool', tool_call_id: 'call_1', content: 'old' });
  assert.equal(chat.tools[0].function.name, 'read_file');
  assert.deepEqual(chat.tool_choice, { type: 'function', function: { name: 'read_file' } });
  assert.deepEqual(chat.thinking, { type: 'enabled' });
});

test('maps Responses developer instructions to GLM-supported system messages', () => {
  const chat = responsesToChat({ input: [{ role: 'developer', content: 'Follow policy.' }] });
  assert.deepEqual(chat.messages, [{ role: 'system', content: 'Follow policy.' }]);
});

test('converts chat reasoning, text, tool calls, usage, and SSE completion', () => {
  const response = chatToResponses({ model: 'glm-5.3-flash', choices: [{
    finish_reason: 'tool_calls',
    message: { reasoning_content: 'need file', content: 'checking', tool_calls: [{
      id: 'call_7', type: 'function', function: { name: 'read_file', arguments: '{"path":"a.js"}' },
    }] },
  }], usage: { prompt_tokens: 20, completion_tokens: 10, total_tokens: 30,
    prompt_tokens_details: { cached_tokens: 4 },
    completion_tokens_details: { reasoning_tokens: 6 } } }, 'glm-5.3-flash');
  assert.deepEqual(response.output.map(item => item.type), ['reasoning', 'message', 'function_call']);
  assert.equal(response.output[2].call_id, 'call_7');
  assert.equal(response.usage.input_tokens_details.cached_tokens, 4);
  assert.equal(response.usage.output_tokens_details.reasoning_tokens, 6);
  const events = responseEvents(response);
  assert.equal(events.at(-1).type, 'response.completed');
  assert.ok(events.some(event => event.type === 'response.function_call_arguments.done'));
});

test('rejects custom and multimodal items instead of silently losing data', () => {
  assert.throws(() => responsesToChat({ input: [], tools: [{ type: 'custom', name: 'patch' }] }),
    /unsupported Responses tool type/);
  assert.throws(() => responsesToChat({ input: [{ role: 'user', content: [{ type: 'input_image' }] }] }),
    /unsupported non-text content type/);
});

test('round-trips namespace tools through unique Chat function names', () => {
  const request = {
    input: [{
      type: 'function_call', call_id: 'call_ns', namespace: 'multi_agent_v1',
      name: 'spawn_agent', arguments: '{"message":"inspect"}',
    }],
    tools: [{
      type: 'namespace', name: 'multi_agent_v1', description: 'Agent tools', tools: [{
        type: 'function', name: 'spawn_agent', description: 'Spawn',
        parameters: { type: 'object', properties: { message: { type: 'string' } } },
      }],
    }],
  };
  const translation = createToolTranslation(request.tools);
  const chat = responsesToChat(request, translation);
  const encodedName = chat.tools[0].function.name;
  assert.match(encodedName, /^ns_[a-f0-9]{10}_spawn_agent$/);
  assert.equal(chat.messages[0].tool_calls[0].function.name, encodedName);

  const response = chatToResponses({ choices: [{ message: { tool_calls: [{
    id: 'call_new', type: 'function', function: { name: encodedName, arguments: '{}' },
  }] } }] }, 'glm-5.3-flash', translation);
  assert.equal(response.output[0].namespace, 'multi_agent_v1');
  assert.equal(response.output[0].name, 'spawn_agent');
});

test('advertises hosted web search but fails explicitly if GLM selects it', () => {
  const translation = createToolTranslation([{ type: 'web_search' }]);
  assert.equal(translation.chatTools[0].function.name, 'hosted_web_search');
  assert.throws(() => chatToResponses({ choices: [{ message: { tool_calls: [{
    id: 'call_web', type: 'function', function: {
      name: 'hosted_web_search', arguments: '{"query":"current release"}',
    },
  }] } }] }, 'glm-5.3-flash', translation), /cannot execute/);
});
