import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { workspaceService } from '@/services/workspaceService';
import type { WorkspaceCreate } from '@/types/workspace';

export const useWorkspaces = (params?: {
  page?: number;
  page_size?: number;
  status?: string;
}) => {
  return useQuery({
    queryKey: ['workspaces', params],
    queryFn: () => workspaceService.listWorkspaces(params),
  });
};

export const useCreateWorkspace = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: WorkspaceCreate) => workspaceService.createWorkspace(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
    },
  });
};
