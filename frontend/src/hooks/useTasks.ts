import { useQuery } from '@tanstack/react-query';
import { taskService } from '@/services/taskService';

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
  });
};
