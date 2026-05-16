import { API_BASE_URL, API_ENDPOINTS } from '@/constants/api';
import type { AgentPlanStepPayload, AgentQueryPayload, ToolCallPayload, ToolPendingPayload, ToolResultPayload, UsingContextPayload, PutOutcomePayload } from '@/types/events';
import { useChatStore } from '@/stores/useChatStore';
import type { ChatFileAttachment } from '@/types/chatFile';

export interface StreamOptions {
  workspaceId: string;
  appId?: string;
  message: string;
  attachments?: ChatFileAttachment[];
  forcedTools?: string[];
  onRunStart: (runId: string) => void;
  onChunk: (content: string) => void;
  onComplete: () => void;
  onError: (error: Error) => void;
  onStatus?: (status: string) => void;
  /** Reasoning trace emitted by a thinking model. */
  onThinking?: (content: string) => void;
  /** A tool is about to be executed. */
  onToolCall?: (event: ToolCallPayload) => void;
  /** A tool finished – success or error distinguished by `success` flag. */
  onToolResult?: (event: ToolResultPayload) => void;
  /** A plan-step status update. */
  onPlanStep?: (event: AgentPlanStepPayload) => void;
  /** A tool requires human approval before it can execute. */
  onToolPending?: (event: ToolPendingPayload) => void;
  /** The agent is asking the user a question (ask_for_user tool). */
  onAgentQuery?: (event: AgentQueryPayload) => void;
  /** The agent is retrieving context (knowledge, memory, etc.). */
  onUsingContext?: (event: UsingContextPayload) => void;
  /** The agent produced an outcome (file, artifact, result). */
  onPutOutcome?: (event: PutOutcomePayload) => void;
  /** Token usage reported with an agent.message (input + output counts). */
  onAgentMessage?: (tokens: { inputTokens: number; outputTokens: number }) => void;
}

interface StreamEventPayload {
  event_type?: string;
  payload?: Record<string, unknown> | null;
  sequence?: number;
  type?: string;
  message?: string;
  input_tokens?: number;
  output_tokens?: number;
}

const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_BASE_DELAY_MS = 1000;
const RECONNECT_MAX_DELAY_MS = 30000;

class StreamService {
  private controller: AbortController | null = null;
  private currentRunId: string | null = null;
  private lastEventId: string | null = null;

  async sendStreamingMessage(options: StreamOptions): Promise<void> {
    const { workspaceId, appId, message, attachments, forcedTools, onError } = options;
    const token = localStorage.getItem('access_token');

    try {
      // 1. Create the run
      const startResponse = await fetch(
        `${API_BASE_URL}${API_ENDPOINTS.WORKSPACES.RUNS(workspaceId)}`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            workspace_id: workspaceId,
            app_id: appId,
            payload: {
              message: message,
              ...(attachments && attachments.length > 0 ? { attachments } : {}),
              ...(forcedTools && forcedTools.length > 0 ? { forced_tools: forcedTools } : {}),
            },
          }),
        }
      );

      if (!startResponse.ok) {
        let detail = `Failed to start run: ${startResponse.status}`;
        try {
          const body = await startResponse.json();
          if (typeof body?.detail === 'string') {
            detail = body.detail;
          }
        } catch {
          // Keep the status-based fallback when the server returns no JSON body.
        }
        throw new Error(detail);
      }

      const runInfo = await startResponse.json();
      this.currentRunId = runInfo.id;
      this.lastEventId = null;
      options.onRunStart(runInfo.id);

      // 2. Open SSE stream with reconnect
      const result = await this.readSSEStreamWithReconnect(runInfo.id, options);
      if (result === 'completed') {
        // stream ended normally via terminal event — already called onComplete/onError
      }
    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') {
        onError(error);
      }
    } finally {
      this.controller = null;
      this.currentRunId = null;
    }
  }

  /**
   * Open the SSE stream, read events, and reconnect on abnormal disconnects.
   * Returns 'completed' when a terminal event was received, 'exhausted' when
   * all reconnect attempts are used up.
   */
  private async readSSEStreamWithReconnect(
    runId: string,
    options: StreamOptions,
  ): Promise<'completed' | 'exhausted'> {
    let reconnectAttempts = 0;
    // Persists across reconnects so agent.message is not re-applied after tokens already received
    const tokenState = { hasTokens: false };

    while (true) {
      const lastId = this.lastEventId ?? '$';
      const result = await this.readSSEStream(runId, lastId, options, tokenState);

      if (result === 'terminal') {
        return 'completed';
      }

      // Stream ended abnormally (reader done without terminal event)
      reconnectAttempts++;
      if (reconnectAttempts > MAX_RECONNECT_ATTEMPTS) {
        options.onError(new Error('Connection lost after multiple reconnect attempts'));
        return 'exhausted';
      }

      // Exponential backoff
      const delay = Math.min(
        RECONNECT_BASE_DELAY_MS * Math.pow(2, reconnectAttempts - 1),
        RECONNECT_MAX_DELAY_MS,
      );

      const store = useChatStore.getState();
      store.setReconnecting(true);
      store.setReconnectAttempt(reconnectAttempts);

      console.warn(`SSE disconnected, reconnecting in ${delay}ms (attempt ${reconnectAttempts}/${MAX_RECONNECT_ATTEMPTS})`);
      await new Promise((resolve) => setTimeout(resolve, delay));

      // Check if we were aborted during the delay
      if (this.controller?.signal.aborted) {
        return 'completed';
      }
    }
  }

  /**
   * Read a single SSE stream connection. Returns 'terminal' if a terminal event
   * was received, or 'disconnected' if the stream ended without a terminal event.
   */
  private async readSSEStream(
    runId: string,
    lastId: string,
    options: StreamOptions,
    tokenState: { hasTokens: boolean },
  ): Promise<'terminal' | 'disconnected'> {
    const { onChunk, onComplete, onError } = options;
    const token = localStorage.getItem('access_token');

    this.controller = new AbortController();
    const store = useChatStore.getState();

    try {
      let streamUrl = `${API_BASE_URL}${API_ENDPOINTS.EVENTS.RUN_STREAM(runId)}`;
      if (lastId && lastId !== '$') {
        const separator = streamUrl.includes('?') ? '&' : '?';
        streamUrl += `${separator}last_event_id=${encodeURIComponent(lastId)}`;
      }

      const streamResponse = await fetch(streamUrl, {
        method: 'GET',
        headers: { Authorization: `Bearer ${token}` },
        signal: this.controller.signal,
      });

      if (!streamResponse.ok || !streamResponse.body) {
        throw new Error(`Failed to stream events: ${streamResponse.status}`);
      }

      // Successfully connected — clear reconnecting state
      store.setReconnecting(false);
      store.setReconnectAttempt(0);

      const reader = streamResponse.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let eventName: string | null = null;
      let dataLines: string[] = [];
      let finished = false;

      const handleEvent = () => {
        if (!dataLines.length) return;

        const rawData = dataLines.join('\n');
        dataLines = [];

        let payload: StreamEventPayload | null = null;
        try {
          payload = JSON.parse(rawData);
        } catch {
          console.warn('Failed to parse SSE data:', rawData);
          return;
        }

        // Track last event sequence for reconnect
        if (payload?.sequence != null) {
          this.lastEventId = String(payload.sequence);
        }

        // Update last event timestamp in store
        useChatStore.getState().setLastEventTimestamp(Date.now());

        const eventType = payload?.event_type || eventName;
        const eventPayload = payload?.payload || {};
        const internalType = payload?.type;

        // ── internal / error ──────────────────────────────
        if (eventType === 'error' || internalType === 'error') {
          finished = true;
          onError(new Error(payload?.message || 'Stream error'));
          return;
        }
        if (internalType === 'keepalive') return;

        // ── agent heartbeat (timestamp update only) ───────
        if (eventType === 'agent.heartbeat') {
          return;
        }

        // ── agent text events ─────────────────────────────
        if (eventType === 'agent.token') {
          const tokenValue = eventPayload?.token as string | undefined;
          if (tokenValue) {
            tokenState.hasTokens = true;
            onChunk(tokenValue);
          }
          return;
        }

        if (eventType === 'agent.message') {
          const content = eventPayload?.content as string | undefined;
          if (content && !tokenState.hasTokens) {
            onChunk(content);
          }
          return;
        }

        // ── to.executor: token usage summary for the completed run turn ──
        if (eventType === 'to.executor') {
          const inputTokens = payload?.input_tokens;
          const outputTokens = payload?.output_tokens;
          if ((inputTokens ?? 0) > 0 || (outputTokens ?? 0) > 0) {
            options.onAgentMessage?.({ inputTokens: inputTokens ?? 0, outputTokens: outputTokens ?? 0 });
          }
          return;
        }

        // ── thinking ──────────────────────────────────────
        if (eventType === 'agent.thinking') {
          const content = eventPayload?.content as string | undefined;
          if (content) {
            options.onThinking?.(content);
          }
          return;
        }

        // ── tool lifecycle ────────────────────────────────
        if (eventType === 'tool.call') {
          options.onToolCall?.({
            tool_name: eventPayload?.tool_name as string,
            tool_id: eventPayload?.tool_id as string,
            arguments: (eventPayload?.arguments ?? {}) as Record<string, unknown>,
          });
          return;
        }

        if (eventType === 'tool.result' || eventType === 'tool.error') {
          options.onToolResult?.({
            tool_name: eventPayload?.tool_name as string,
            tool_id: eventPayload?.tool_id as string,
            result: eventPayload?.result,
            success: eventType === 'tool.result',
            error_message: (eventPayload?.error_message ?? null) as string | null,
            execution_time_ms: (eventPayload?.execution_time_ms ?? null) as number | null,
          });
          return;
        }

        // ── tool pending (HITL approval gate) ───────────────
        if (eventType === 'tool.pending') {
          options.onToolPending?.({
            tool_name: eventPayload?.tool_name as string,
            tool_id: eventPayload?.tool_id as string,
            reason: (eventPayload?.reason ?? 'requires_approval') as string,
            requires_approval: (eventPayload?.requires_approval ?? true) as boolean,
            arguments: (eventPayload?.arguments ?? {}) as Record<string, unknown>,
          });
          return;
        }

        // ── agent query (ask_for_user) ────────────────────
        if (eventType === 'agent.query') {
          options.onAgentQuery?.({
            question: eventPayload?.question as string,
            tool_name: eventPayload?.tool_name as string,
            tool_id: eventPayload?.tool_id as string,
          });
          return;
        }

        // ── plan steps ────────────────────────────────────
        if (eventType === 'agent.plan.step') {
          options.onPlanStep?.({
            step_number: eventPayload?.step_number as number,
            step_description: eventPayload?.step_description as string,
            status: eventPayload?.status as AgentPlanStepPayload['status'],
            output: (eventPayload?.output ?? null) as string | null,
          });
          return;
        }

        // ── context events ─────────────────────────────────
        if (eventType === 'context.using') {
          options.onUsingContext?.({
            context_type: eventPayload?.context_type as string,
            context_name: eventPayload?.context_name as string,
            query: eventPayload?.query as string | undefined,
            results_count: eventPayload?.results_count as number | undefined,
            details: eventPayload?.details as Record<string, unknown> | undefined,
            tool_names: eventPayload?.tool_names as string[] | undefined,
            tool_count: eventPayload?.tool_count as number | undefined,
          });
          return;
        }

        if (eventType === 'context.put_outcome') {
          options.onPutOutcome?.({
            outcome_type: eventPayload?.outcome_type as string,
            outcome_name: eventPayload?.outcome_name as string,
            summary: eventPayload?.summary as string | undefined,
            details: eventPayload?.details as Record<string, unknown> | undefined,
          });
          return;
        }

        // ── run lifecycle ─────────────────────────────────
        if (eventType === 'run.state.change') {
          const nextState = eventPayload?.new_state as string | undefined;
          if (nextState && options.onStatus) {
            options.onStatus(nextState);
          }
          if (nextState === 'finished') {
            finished = true;
            onComplete();
          } else if (nextState === 'failed') {
            finished = true;
            const reason = typeof eventPayload?.reason === 'string' ? eventPayload.reason : 'Run failed';
            onError(new Error(reason));
          } else if (nextState === 'cancelled') {
            finished = true;
            onComplete();
          }
          return;
        }

        if (eventType === 'run.failed') {
          finished = true;
          const reason = typeof eventPayload?.error === 'string' ? eventPayload.error : 'Run failed';
          onError(new Error(reason));
        }

        if (eventType === 'run.cancelled' || eventType === 'run.completed') {
          finished = true;
          onComplete();
        }
      };

      // SSE line parser loop
      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          if (finished) return 'terminal';
          return 'disconnected';
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith(':')) continue; // SSE comment

          if (!line.trim()) {
            handleEvent();
            eventName = null;
            continue;
          }

          if (line.startsWith('event:')) {
            eventName = line.slice(6).trim();
            continue;
          }

          if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim());
          }
        }
      }
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        return 'terminal';
      }
      // Network error — try to reconnect
      console.warn('SSE stream error:', error);
      return 'disconnected';
    }
  }

  async abort(workspaceId: string): Promise<void> {
    if (this.currentRunId) {
      try {
        const token = localStorage.getItem('access_token');
        await fetch(
          `${API_BASE_URL}${API_ENDPOINTS.WORKSPACES.RUN_CANCEL(workspaceId, this.currentRunId)}`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${token}`,
            },
          }
        );
      } catch (error) {
        console.warn('Failed to cancel run:', error);
      } finally {
        this.currentRunId = null;
      }
    }

    if (this.controller) {
      this.controller.abort();
      this.controller = null;
    }
  }

  /** POST the user's answer to an agent.query back to the backend. */
  async submitFeedback(
    workspaceId: string,
    runId: string,
    feedback: string,
  ): Promise<void> {
    const token = localStorage.getItem('access_token');
    const res = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.WORKSPACES.RUN_FEEDBACK(workspaceId, runId)}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ feedback }),
      }
    );
    if (!res.ok) {
      throw new Error(`Feedback submission failed: ${res.status}`);
    }
  }

  /** POST the user's approve / deny decision back to the backend. */
  async resumeRun(
    workspaceId: string,
    runId: string,
    decision: { approval: boolean; tool_result?: string; user_input?: string }
  ): Promise<void> {
    const token = localStorage.getItem('access_token');
    const res = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.WORKSPACES.RUN_RESUME(workspaceId, runId)}`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(decision),
      }
    );
    if (!res.ok) {
      throw new Error(`Resume failed: ${res.status}`);
    }
  }
}

export const streamService = new StreamService();
