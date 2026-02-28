import { create } from 'zustand';
import type { Event } from '@/types/event';

interface RunEventsStore {
  /** Latest SSE-delivered event per run (runId → event), used for preview indicators */
  latestEvents: Record<string, Event>;
  /** All SSE-delivered events per run (runId → Event[]), ordered by sequence */
  liveEvents: Record<string, Event[]>;
  setLatestEvent: (runId: string, event: Event) => void;
  addLiveEvent: (runId: string, event: Event) => void;
  clearRunEvents: (runId: string) => void;
  reset: () => void;
}

export const useRunEventsStore = create<RunEventsStore>((set) => ({
  latestEvents: {},
  liveEvents: {},

  setLatestEvent: (runId, event) =>
    set((s) => ({ latestEvents: { ...s.latestEvents, [runId]: event } })),

  addLiveEvent: (runId, event) =>
    set((s) => {
      const existing = s.liveEvents[runId] ?? [];
      // Deduplicate by sequence — keep existing entry if already present
      if (existing.some((e) => e.sequence === event.sequence)) return s;
      const updated = [...existing, event].sort((a, b) => a.sequence - b.sequence);
      return { liveEvents: { ...s.liveEvents, [runId]: updated } };
    }),

  clearRunEvents: (runId) =>
    set((s) => {
      const { [runId]: _l, ...restLive } = s.liveEvents;
      const { [runId]: _lt, ...restLatest } = s.latestEvents;
      return { liveEvents: restLive, latestEvents: restLatest };
    }),

  reset: () => set({ latestEvents: {}, liveEvents: {} }),
}));
