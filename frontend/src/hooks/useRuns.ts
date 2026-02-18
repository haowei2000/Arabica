import { useQuery } from '@tanstack/react-query';
import { runService } from '@/services/runService';
import { eventService } from '@/services/eventService';

export const useRuns = (
  workspaceId: string,
  params?: { page?: number; page_size?: number; status?: string }
) => {
  return useQuery({
    queryKey: ['runs', workspaceId, params],
    queryFn: () => runService.listRuns(workspaceId, params),
    enabled: !!workspaceId,
  });
};

export const useRunEvents = (runId: string | null, enabled = true) => {
  return useQuery({
    queryKey: ['run-events', runId],
    queryFn: () => eventService.listByRun(runId!, { limit: 200 }),
    enabled: !!runId && enabled,
    staleTime: 0,
  });
};
