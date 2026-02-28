import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { API_BASE_URL, API_ENDPOINTS } from '@/constants/api';
import { useRunEventsStore } from '@/stores/useRunEventsStore';

const MAX_RETRIES = 10;

/**
 * Opens a persistent SSE connection to the workspace-level event stream.
 * Automatically invalidates React Query caches when relevant events arrive,
 * keeping the right panel (runs list, context tree) in sync without polling.
 *
 * Reconnects with exponential backoff on disconnect, resuming from the last
 * received sequence number so no events are missed.
 */
export function useWorkspaceStream(workspaceId: string | null) {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!workspaceId) return;

    let stopped = false;
    let abortController: AbortController | null = null;
    let retryTimeout: ReturnType<typeof setTimeout> | null = null;
    let retryCount = 0;
    let lastEventId = '$';

    async function connect() {
      if (stopped) return;

      abortController?.abort();
      abortController = new AbortController();

      try {
        const token = localStorage.getItem('access_token');
        let url = `${API_BASE_URL}${API_ENDPOINTS.EVENTS.WORKSPACE_STREAM(workspaceId)}`;
        if (lastEventId && lastEventId !== '$') {
          url += `?last_event_id=${encodeURIComponent(lastEventId)}`;
        }

        const resp = await fetch(url, {
          headers: { Authorization: `Bearer ${token}` },
          signal: abortController.signal,
        });

        if (!resp.ok || !resp.body) {
          throw new Error(`Workspace SSE error: ${resp.status}`);
        }

        // Connected — reset retry counter
        retryCount = 0;

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let eventName: string | null = null;
        let dataLines: string[] = [];

        function dispatch() {
          if (!dataLines.length) return;
          const raw = dataLines.join('\n');
          dataLines = [];
          eventName = null;

          let payload: Record<string, unknown>;
          try {
            payload = JSON.parse(raw);
          } catch {
            return;
          }

          // Track sequence for reconnect resume
          if (payload.sequence != null) {
            lastEventId = String(payload.sequence);
          }

          const type = (payload.event_type as string | undefined) || eventName;
          const internal = payload.type as string | undefined;

          // Skip keepalives and heartbeats
          if (internal === 'keepalive' || type === 'agent.heartbeat') return;

          // Store latest event per run for real-time preview in the runs panel,
          // and accumulate all live events so the timeline stays complete even
          // before PG persistence catches up.
          const runId = payload.run_id as string | undefined;
          if (runId && type) {
            const event: import('@/types/event').Event = {
              id: (payload.id as string) || '',
              workspace_id: workspaceId,
              run_id: runId,
              app_id: (payload.app_id as string) || '',
              user_id: (payload.user_id as string) || '',
              sequence: (payload.sequence as number) || 0,
              event_type: type,
              payload: (payload.payload as Record<string, unknown>) || {},
              created_at: (payload.created_at as string) || new Date().toISOString(),
            };
            const store = useRunEventsStore.getState();
            store.setLatestEvent(runId, event);
            store.addLiveEvent(runId, event);
            // Invalidate per-run events query so expanded run lists refresh
            queryClient.invalidateQueries({ queryKey: ['run-events', runId] });
          }

          switch (type) {
            // Run list changes
            case 'run.created':
            case 'run.state.change':
            case 'run.completed':
            case 'run.failed':
            case 'run.cancelled':
              queryClient.invalidateQueries({ queryKey: ['runs', workspaceId] });
              break;

            // Workspace metadata changes
            case 'workspace.updated':
            case 'workspace.member.join':
            case 'workspace.member.leave':
              queryClient.invalidateQueries({ queryKey: ['workspaces'] });
              break;

            // Context tree changes
            case 'context.put_outcome':
              queryClient.invalidateQueries({ queryKey: ['workspace-contexts', workspaceId] });
              break;
          }
        }

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';

          for (const line of lines) {
            if (line.startsWith('event:')) {
              eventName = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
              dataLines.push(line.slice(5).trim());
            } else if (line === '') {
              dispatch();
            }
          }
        }

        // Stream closed gracefully — reconnect
        if (!stopped) scheduleRetry();
      } catch (err: unknown) {
        if ((err as { name?: string })?.name === 'AbortError' || stopped) return;
        scheduleRetry();
      }
    }

    function scheduleRetry() {
      if (stopped || retryCount >= MAX_RETRIES) return;
      const delay = Math.min(1000 * Math.pow(2, retryCount), 30_000);
      retryCount++;
      retryTimeout = setTimeout(connect, delay);
    }

    connect();

    return () => {
      stopped = true;
      abortController?.abort();
      if (retryTimeout) clearTimeout(retryTimeout);
    };
  }, [workspaceId, queryClient]);
}
