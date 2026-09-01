#!/usr/bin/env node
import { createServer } from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const host = process.env.GLM_PROXY_HOST ?? '127.0.0.1';
const port = Number.parseInt(process.env.GLM_PROXY_PORT ?? '4119', 10);
const upstream = (process.env.GLM_CHAT_BASE_URL
  ?? 'https://open.bigmodel.cn/api/coding/paas/v4').replace(/\/$/, '');
const apiKey = process.env.GLM_API_KEY;
const clientToken = process.env.GLM_PROXY_CLIENT_TOKEN ?? 'local-proxy';

export function responsesToChat(request, toolTranslation = createToolTranslation(request.tools ?? [])) {
  const messages = [];
  if (request.instructions) messages.push({ role: 'system', content: request.instructions });
  for (const item of typeof request.input === 'string'
    ? [{ role: 'user', content: request.input }]
    : (request.input ?? [])) {
    const type = item.type;
    if (!type || type === 'message') {
      const role = item.role === 'developer' ? 'system' : (item.role ?? 'user');
      messages.push({ role, content: textContent(item.content) });
    } else if (type === 'reasoning') {
      const reasoning = (item.summary ?? []).map(part => part.text ?? '').join('\n');
      if (reasoning) messages.push({ role: 'assistant', content: null, reasoning_content: reasoning });
    } else if (type === 'function_call') {
      const responseKey = responseToolKey(item.namespace, item.name);
      const translatedTool = toolTranslation.byResponseKey.get(responseKey);
      if (item.namespace && !translatedTool) {
        throw new Error(`unknown Responses namespace tool: ${item.namespace}.${item.name}`);
      }
      const call = {
        id: item.call_id,
        type: 'function',
        function: { name: translatedTool?.chatName ?? item.name, arguments: item.arguments ?? '{}' },
      };
      const last = messages.at(-1);
      if (last?.role === 'assistant' && last.content == null && !last.tool_call_id) {
        (last.tool_calls ??= []).push(call);
      } else {
        messages.push({ role: 'assistant', content: null, tool_calls: [call] });
      }
    } else if (type === 'function_call_output') {
      messages.push({ role: 'tool', tool_call_id: item.call_id, content: textContent(item.output) });
    }
  }
  const tools = toolTranslation.chatTools;
  let toolChoice = request.tool_choice;
  if (toolChoice && typeof toolChoice === 'object' && toolChoice.type === 'function') {
    const translatedTool = toolTranslation.byResponseKey.get(
      responseToolKey(toolChoice.namespace, toolChoice.name),
    );
    toolChoice = { type: 'function', function: { name: translatedTool?.chatName ?? toolChoice.name } };
  }
  return {
    model: request.model,
    messages,
    ...(tools.length ? { tools, tool_choice: toolChoice ?? 'auto' } : {}),
    ...(request.max_output_tokens ? { max_tokens: request.max_output_tokens } : {}),
    ...(request.reasoning ? { thinking: { type: 'enabled' } } : {}),
    stream: false,
  };
}

export function chatToResponses(chat, requestedModel, toolTranslation = emptyToolTranslation()) {
  const choice = chat.choices?.[0];
  if (!choice) throw new Error('Chat Completions response has no choices');
  const message = choice.message ?? {};
  const output = [];
  if (message.reasoning_content) output.push({
    id: `rs_${randomUUID()}`,
    type: 'reasoning',
    summary: [{ type: 'summary_text', text: message.reasoning_content }],
  });
  if (message.content) output.push({
    id: `msg_${randomUUID()}`,
    type: 'message',
    status: 'completed',
    role: 'assistant',
    content: [{ type: 'output_text', text: message.content, annotations: [] }],
  });
  for (const call of message.tool_calls ?? []) {
    const translatedTool = toolTranslation.byChatName.get(call.function?.name);
    if (translatedTool?.kind === 'hosted_web_search') {
      throw new Error('GLM selected provider-hosted web_search, which Chat Completions cannot execute');
    }
    output.push({
      id: `fc_${randomUUID()}`,
      type: 'function_call',
      status: 'completed',
      call_id: call.id,
      ...(translatedTool?.namespace ? { namespace: translatedTool.namespace } : {}),
      name: translatedTool?.name ?? call.function?.name,
      arguments: call.function?.arguments ?? '{}',
    });
  }
  const usage = chat.usage ?? {};
  return {
    id: `resp_${randomUUID()}`,
    object: 'response',
    created_at: Math.floor(Date.now() / 1000),
    status: 'completed',
    model: chat.model ?? requestedModel,
    output,
    usage: {
      input_tokens: usage.prompt_tokens ?? 0,
      input_tokens_details: {
        cached_tokens: usage.prompt_tokens_details?.cached_tokens ?? 0,
      },
      output_tokens: usage.completion_tokens ?? 0,
      output_tokens_details: {
        reasoning_tokens: usage.completion_tokens_details?.reasoning_tokens ?? 0,
      },
      total_tokens: usage.total_tokens
        ?? (usage.prompt_tokens ?? 0) + (usage.completion_tokens ?? 0),
    },
  };
}

export function createToolTranslation(responseTools) {
  const chatTools = [];
  const byChatName = new Map();
  const byResponseKey = new Map();
  const addFunction = (tool, namespace = undefined) => {
    const chatName = namespace ? encodedNamespaceToolName(namespace, tool.name) : tool.name;
    if (byChatName.has(chatName)) throw new Error(`duplicate translated tool name: ${chatName}`);
    const translated = { kind: 'function', chatName, namespace, name: tool.name };
    byChatName.set(chatName, translated);
    byResponseKey.set(responseToolKey(namespace, tool.name), translated);
    chatTools.push({ type: 'function', function: {
      name: chatName,
      description: namespace
        ? `[Responses namespace ${namespace}] ${tool.description ?? ''}`.trim()
        : (tool.description ?? ''),
      parameters: tool.parameters ?? { type: 'object', properties: {} },
      ...(tool.strict == null ? {} : { strict: tool.strict }),
    } });
  };

  for (const tool of responseTools) {
    if (tool.type === 'function') {
      addFunction(tool);
    } else if (tool.type === 'namespace') {
      for (const child of tool.tools ?? []) {
        if (child.type !== 'function') {
          throw new Error(`unsupported Responses namespace child type: ${child.type}`);
        }
        addFunction(child, tool.name);
      }
    } else if (tool.type === 'web_search') {
      const chatName = encodedHostedToolName('web_search');
      const translated = { kind: 'hosted_web_search', chatName, name: 'web_search' };
      byChatName.set(chatName, translated);
      byResponseKey.set(responseToolKey(undefined, 'web_search'), translated);
      chatTools.push({ type: 'function', function: {
        name: chatName,
        description: 'Provider-hosted web search. Use only when current external information is required.',
        parameters: {
          type: 'object',
          properties: { query: { type: 'string', description: 'Search query' } },
          required: ['query'],
          additionalProperties: false,
        },
      } });
    } else {
      throw new Error(`unsupported Responses tool type: ${tool.type}`);
    }
  }
  return { chatTools, byChatName, byResponseKey };
}

function emptyToolTranslation() {
  return { chatTools: [], byChatName: new Map(), byResponseKey: new Map() };
}

function responseToolKey(namespace, name) {
  return `${namespace ?? ''}\0${name ?? ''}`;
}

function encodedNamespaceToolName(namespace, name) {
  const digest = createHash('sha256').update(namespace).digest('hex').slice(0, 10);
  const suffix = String(name).replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 48);
  return `ns_${digest}_${suffix}`;
}

function encodedHostedToolName(name) {
  return `hosted_${name}`;
}

export function responseEvents(response) {
  const pending = { ...response, status: 'in_progress', output: [] };
  const events = [{ type: 'response.created', response: pending }];
  response.output.forEach((item, output_index) => {
    events.push({ type: 'response.output_item.added', output_index, item: pendingItem(item) });
    if (item.type === 'message') {
      const text = item.content?.[0]?.text ?? '';
      events.push({ type: 'response.output_text.delta', item_id: item.id,
        output_index, content_index: 0, delta: text });
      events.push({ type: 'response.output_text.done', item_id: item.id,
        output_index, content_index: 0, text });
    } else if (item.type === 'function_call') {
      events.push({ type: 'response.function_call_arguments.delta', item_id: item.id,
        output_index, delta: item.arguments });
      events.push({ type: 'response.function_call_arguments.done', item_id: item.id,
        output_index, arguments: item.arguments });
    }
    events.push({ type: 'response.output_item.done', output_index, item });
  });
  events.push({ type: 'response.completed', response });
  return events.map((event, sequence_number) => ({ ...event, sequence_number }));
}

function pendingItem(item) {
  if (item.type === 'message') return { ...item, status: 'in_progress', content: [] };
  if (item.type === 'function_call') return { ...item, status: 'in_progress', arguments: '' };
  return item;
}

function textContent(content) {
  if (content == null) return '';
  if (typeof content === 'string') return content;
  if (!Array.isArray(content)) return String(content);
  return content.map(part => {
    if (typeof part === 'string') return part;
    if (typeof part?.text === 'string') return part.text;
    throw new Error(`unsupported non-text content type: ${part?.type ?? typeof part}`);
  }).join('');
}

async function handle(request, response) {
  if (request.headers.authorization !== `Bearer ${clientToken}`) {
    return json(response, 401, { error: { message: 'Unauthorized', type: 'authentication_error' } });
  }
  if (request.method === 'GET' && request.url?.endsWith('/health')) {
    return json(response, 200, { status: 'ready', upstream });
  }
  if (request.method === 'GET' && request.url?.endsWith('/models')) {
    return forwardModels(response);
  }
  if (request.method === 'POST' && request.url?.endsWith('/chat/completions')) {
    try {
      return await forwardChatCompletions(request, response);
    } catch (error) {
      return json(response, 502, { error: { message: error.message, type: 'upstream_error' } });
    }
  }
  if (request.method !== 'POST' || !request.url?.endsWith('/responses')) {
    return json(response, 404, { error: { message: 'Not Found', type: 'invalid_request_error' } });
  }
  const started = Date.now();
  try {
    const responsesRequest = JSON.parse(await readBody(request));
    process.stdout.write(`${JSON.stringify({ type: 'request.received', stream: Boolean(responsesRequest.stream),
      model: responsesRequest.model, tools: (responsesRequest.tools ?? []).map(tool => ({
        type: tool.type, name: tool.name,
        children: (tool.tools ?? []).map(child => ({ type: child.type, name: child.name })),
      })) })}\n`);
    const toolTranslation = createToolTranslation(responsesRequest.tools ?? []);
    const chatRequest = responsesToChat(responsesRequest, toolTranslation);
    const upstreamResponse = await fetch(`${upstream}/chat/completions`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(chatRequest),
      signal: AbortSignal.timeout(300000),
    });
    const raw = await upstreamResponse.text();
    if (!upstreamResponse.ok) {
      response.writeHead(upstreamResponse.status, { 'Content-Type': 'application/json' });
      response.end(raw);
      return;
    }
    const translated = chatToResponses(JSON.parse(raw), responsesRequest.model, toolTranslation);
    if (responsesRequest.stream) {
      response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
      response.end(responseEvents(translated)
        .map(event => `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`).join(''));
    } else {
      json(response, 200, translated);
    }
    process.stdout.write(`${JSON.stringify({ type: 'request.completed', stream: Boolean(responsesRequest.stream),
      model: responsesRequest.model, upstream_model: translated.model,
      input_tokens: translated.usage.input_tokens, output_tokens: translated.usage.output_tokens,
      reasoning_tokens: translated.usage.output_tokens_details.reasoning_tokens,
      output_types: translated.output.map(item => item.type), duration_ms: Date.now() - started })}\n`);
  } catch (error) {
    json(response, 400, { error: { message: error.message, type: 'invalid_request_error' } });
  }
}

async function forwardChatCompletions(request, response) {
  const started = Date.now();
  const rawRequest = await readBody(request);
  const parsedRequest = JSON.parse(rawRequest);
  process.stdout.write(`${JSON.stringify({ type: 'chat.request.received',
    stream: Boolean(parsedRequest.stream), model: parsedRequest.model,
    tool_count: (parsedRequest.tools ?? []).length })}\n`);
  const upstreamResponse = await fetch(`${upstream}/chat/completions`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
    body: rawRequest,
    signal: AbortSignal.timeout(300000),
  });
  const rawResponse = await upstreamResponse.text();
  response.writeHead(upstreamResponse.status, {
    'Content-Type': upstreamResponse.headers.get('content-type') ?? 'application/json',
    'Cache-Control': 'no-cache',
  });
  response.end(rawResponse);
  if (!parsedRequest.stream && upstreamResponse.ok) {
    const parsedResponse = JSON.parse(rawResponse);
    process.stdout.write(`${JSON.stringify({ type: 'chat.request.completed',
      model: parsedRequest.model, upstream_model: parsedResponse.model,
      input_tokens: parsedResponse.usage?.prompt_tokens ?? 0,
      output_tokens: parsedResponse.usage?.completion_tokens ?? 0,
      reasoning_tokens: parsedResponse.usage?.completion_tokens_details?.reasoning_tokens ?? 0,
      duration_ms: Date.now() - started })}\n`);
  }
}

async function forwardModels(response) {
  const upstreamResponse = await fetch(`${upstream}/models`, { headers: { Authorization: `Bearer ${apiKey}` } });
  response.writeHead(upstreamResponse.status, { 'Content-Type': 'application/json' });
  response.end(await upstreamResponse.text());
}

function json(response, status, body) {
  response.writeHead(status, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify(body));
}

async function readBody(request) {
  let body = '';
  for await (const chunk of request) body += chunk;
  return body;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  if (!apiKey) throw new Error('GLM_API_KEY is required');
  createServer(handle).listen(port, host, () => {
    process.stdout.write(`${JSON.stringify({ type: 'proxy.ready', host, port, upstream })}\n`);
  });
}
