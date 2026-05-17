import { useQuery } from '@tanstack/react-query';
import { runService } from '@/services/runService';

const RUN_LIST_STALE_MS = 5_000;

export const useRuns = (
  workspaceId: string,
  params?: { page?: number; page_size?: number; status?: string }
) => {
  return useQuery({
    queryKey: ['runs', workspaceId, params],
    queryFn: () => runService.listRuns(workspaceId, params),
    enabled: !!workspaceId,
    staleTime: RUN_LIST_STALE_MS,
  });
};
