import { API_BASE_URL, API_ENDPOINTS } from '@/constants/api';
import type { AgentPlanStepPayload, ToolCallPayload, ToolPendingPayload, ToolResultPayload } from '@/types/events';

export interface StreamOptions {
  workspaceId: string;
  appId?: string;
  message: string;
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
}

interface StreamEventPayload {
  event_type?: string;
  payload?: Record<string, any> | null;
  sequence?: number;
}

class StreamService {
  private controller: AbortController | null = null;
  private currentRunId: string | null = null;

  async sendStreamingMessage(options: StreamOptions): Promise<void> {
    const { workspaceId, appId, message, onChunk, onComplete, onError } = options;
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
          body: JSON.stringify({ message, app_id: appId }),
        }
      );

      if (!startResponse.ok) {
        throw new Error(`Failed to start run: ${startResponse.status}`);
      }

      const runInfo = await startResponse.json();
      this.currentRunId = runInfo.id;
      options.onRunStart(runInfo.id);

      // 2. Open SSE stream
      this.controller = new AbortController();
      const streamResponse = await fetch(
        `${API_BASE_URL}${API_ENDPOINTS.EVENTS.RUN_STREAM(runInfo.id)}`,
        {
          method: 'GET',
          headers: { Authorization: `Bearer ${token}` },
          signal: this.controller.signal,
        }
      );

      if (!streamResponse.ok || !streamResponse.body) {
        throw new Error(`Failed to stream events: ${streamResponse.status}`);
      }

      // 3. Read & dispatch
      const reader = streamResponse.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let eventName: string | null = null;
      let dataLines: string[] = [];
      let hasTokens = false;
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

        const eventType = payload?.event_type || eventName;
        const eventPayload = payload?.payload || {};
        const internalType = (payload as any)?.type as string | undefined;

        // ── internal / error ──────────────────────────────
        if (eventType === 'error' || internalType === 'error') {
          finished = true;
          onError(new Error((payload as any)?.message || 'Stream error'));
          return;
        }
        if (internalType === 'keepalive') return;

        // ── agent text events ─────────────────────────────
        if (eventType === 'agent.token') {
          const tokenValue = eventPayload?.token as string | undefined;
          if (tokenValue) {
            hasTokens = true;
            onChunk(tokenValue);
          }
          return;
        }

        if (eventType === 'agent.message') {
          const content = eventPayload?.content as string | undefined;
          if (content && !hasTokens) {
            onChunk(content);
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
            onError(new Error(eventPayload?.reason || 'Run failed'));
          } else if (nextState === 'cancelled') {
            finished = true;
            onComplete();
          }
          return;
        }

        if (eventType === 'run.failed') {
          finished = true;
          onError(new Error(eventPayload?.error || 'Run failed'));
        }

        if (eventType === 'run.cancelled' || eventType === 'run.completed') {
          finished = true;
          onComplete();
        }
      };

      // 4. SSE line parser loop
      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          this.currentRunId = null;
          if (!finished) onComplete();
          break;
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
      if (error instanceof Error && error.name !== 'AbortError') {
        onError(error);
      }
    } finally {
      this.controller = null;
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
