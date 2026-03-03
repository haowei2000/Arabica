import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { runService } from '@/services/runService';
import { useRunEventsStore } from '@/stores/useRunEventsStore';
import type { Event } from '@/types/event';

/**
 * Merges PG-persisted events with live SSE-streamed events for a run.
 *
 * - PG events: fetched once from `GET /runs/{runId}/events` (React Query cached)
 * - Live events: accumulated in `useRunEventsStore.liveEvents` from the workspace SSE stream
 * - Result: deduplicated by sequence number, sorted ascending
 */
export function useRunEvents(runId: string, enabled: boolean = true) {
  const { data, isLoading } = useQuery({
    queryKey: ['run-events', runId],
    queryFn: () => runService.getRunEvents(runId, { from_sequence: 0, limit: 500 }),
    enabled: !!runId && enabled,
    // PG events are immutable once a run finishes; stale time keeps unnecessary refetches low
    staleTime: 30_000,
  });

  const liveEvents = useRunEventsStore((s) => s.liveEvents[runId]);

  const merged = useMemo<Event[]>(() => {
    const pgEvents: Event[] = data?.items ?? [];
    const streamEvents: Event[] = liveEvents ?? [];

    // Merge into a map keyed by sequence to deduplicate
    const bySeq = new Map<number, Event>();
    for (const e of pgEvents) bySeq.set(e.sequence, e);
    // Live events override PG (they may have richer data from the SSE payload)
    for (const e of streamEvents) bySeq.set(e.sequence, e);

    return Array.from(bySeq.values()).sort((a, b) => a.sequence - b.sequence);
  }, [data?.items, liveEvents]);

  return { events: merged, isLoading };
}
