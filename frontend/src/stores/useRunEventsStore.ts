import { create } from 'zustand';
import type { Event } from '@/types/event';

interface RunEventsStore {
  /** Latest SSE-delivered event per run (runId → event) */
  latestEvents: Record<string, Event>;
  setLatestEvent: (runId: string, event: Event) => void;
  reset: () => void;
}

export const useRunEventsStore = create<RunEventsStore>((set) => ({
  latestEvents: {},
  setLatestEvent: (runId, event) =>
    set((s) => ({ latestEvents: { ...s.latestEvents, [runId]: event } })),
  reset: () => set({ latestEvents: {} }),
}));
