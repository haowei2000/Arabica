import { API_BASE_URL, API_ENDPOINTS } from '@/constants/api';

export interface StreamOptions {
  workspaceId: string;
  appId?: string;
  message: string;
  onRunStart: (runId: string) => void;
  onChunk: (content: string) => void;
  onComplete: () => void;
  onError: (error: Error) => void;
  onStatus?: (status: string) => void;
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
      const startResponse = await fetch(
        `${API_BASE_URL}${API_ENDPOINTS.WORKSPACES.RUNS(workspaceId)}`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            message,
            app_id: appId,
          }),
        }
      );

      if (!startResponse.ok) {
        throw new Error(`Failed to start run: ${startResponse.status}`);
      }

      const runInfo = await startResponse.json();
      this.currentRunId = runInfo.id;
      options.onRunStart(runInfo.id);

      this.controller = new AbortController();
      const streamResponse = await fetch(
        `${API_BASE_URL}${API_ENDPOINTS.EVENTS.RUN_STREAM(runInfo.id)}`,
        {
          method: 'GET',
          headers: {
            Authorization: `Bearer ${token}`,
          },
          signal: this.controller.signal,
        }
      );

      if (!streamResponse.ok || !streamResponse.body) {
        throw new Error(`Failed to stream events: ${streamResponse.status}`);
      }

      const reader = streamResponse.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let eventName: string | null = null;
      let dataLines: string[] = [];
      let hasTokens = false;
      let finished = false;

      const handleEvent = () => {
        if (!dataLines.length) {
          return;
        }

        const rawData = dataLines.join('\n');
        dataLines = [];

        let payload: StreamEventPayload | null = null;
        try {
          payload = JSON.parse(rawData);
        } catch (error) {
          console.warn('Failed to parse SSE data:', rawData);
          return;
        }

        const eventType = payload?.event_type || eventName;
        const eventPayload = payload?.payload || {};
        const internalType = (payload as any)?.type as string | undefined;

        if (eventType === 'error' || internalType === 'error') {
          finished = true;
          onError(new Error((payload as any)?.message || 'Stream error'));
          return;
        }

        if (internalType === 'keepalive') {
          return;
        }

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

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          this.currentRunId = null;
          if (!finished) {
            onComplete();
          }
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith(':')) {
            continue;
          }

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
          `${API_BASE_URL}${API_ENDPOINTS.WORKSPACES.RUN_CANCEL(
            workspaceId,
            this.currentRunId
          )}`,
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
}

export const streamService = new StreamService();
