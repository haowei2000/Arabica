import { useQuery } from '@tanstack/react-query';
import { workspaceService } from '@/services/workspaceService';
import { runService } from '@/services/runService';
import { eventService } from '@/services/eventService';

// Hook for fetching user's workspaces
export const useWorkspaces = (params?: {
  page?: number;
  page_size?: number;
  status?: string;
}) => {
  return useQuery({
    queryKey: ['workspaces', 'list', params],
    queryFn: () => workspaceService.listWorkspaces(params),
  });
};

// Hook for fetching runs in a workspace
export const useWorkspaceRuns = (workspaceId: string | null, enabled: boolean = true) => {
  return useQuery({
    queryKey: ['workspaces', workspaceId, 'runs'],
    queryFn: () => runService.listRuns(workspaceId!),
    enabled: !!workspaceId && enabled,
  });
};

// Hook for fetching events in a run
export const useRunEvents = (runId: string | null, enabled: boolean = true) => {
  return useQuery({
    queryKey: ['runs', runId, 'events'],
    queryFn: () => eventService.listByRun(runId!, { limit: 100 }),
    enabled: !!runId && enabled,
  });
};

// Hook for fetching events in a workspace
export const useWorkspaceEvents = (workspaceId: string | null, enabled: boolean = true) => {
  return useQuery({
    queryKey: ['workspaces', workspaceId, 'events'],
    queryFn: () => eventService.listByWorkspace(workspaceId!, { limit: 100 }),
    enabled: !!workspaceId && enabled,
  });
};
