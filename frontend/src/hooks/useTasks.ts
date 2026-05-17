import { useQuery } from '@tanstack/react-query';
import { taskService } from '@/services/taskService';

const TASK_LIST_STALE_MS = 10_000;

export const useTasks = (
  workspaceId: string,
  params?: { run_id?: string; status?: string; limit?: number; offset?: number },
  options?: { refetchInterval?: number }
) => {
  return useQuery({
    queryKey: ['tasks', workspaceId, params],
    queryFn: () => taskService.listTasks(workspaceId, params),
    enabled: !!workspaceId,
    refetchInterval: options?.refetchInterval,
    staleTime: TASK_LIST_STALE_MS,
  });
};
