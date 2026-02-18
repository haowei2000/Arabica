import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { workspaceService } from '@/services/workspaceService';
import { apiClient } from '@/services/api';
import { API_ENDPOINTS } from '@/constants/api';
import type { WorkspaceCreate, WorkspaceContextConfig } from '@/types/workspace';

export const useWorkspaces = (params?: {
  page?: number;
  page_size?: number;
  status?: string;
  app_id?: string;
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

export const useDeleteWorkspace = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (workspaceId: string) => workspaceService.deleteWorkspace(workspaceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
    },
  });
};

export const useWorkspaceContexts = (workspaceId: string, params?: { page?: number; page_size?: number }) => {
  return useQuery({
    queryKey: ['workspace-contexts', workspaceId, params],
    queryFn: () => workspaceService.listWorkspaceContexts(workspaceId, params),
    enabled: !!workspaceId,
  });
};

export const useCopyContexts = (workspaceId: string) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (body: { context_ids: string[]; path_prefix?: string }) =>
      workspaceService.copyContextsToWorkspace(workspaceId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspace-contexts', workspaceId] });
    },
  });
};

export const useRemoveWorkspaceContext = (workspaceId: string) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (contextId: string) =>
      workspaceService.removeWorkspaceContext(workspaceId, contextId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspace-contexts', workspaceId] });
    },
  });
};

export const useUpdateWorkspace = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ workspaceId, data }: { workspaceId: string; data: { name?: string; description?: string } }) =>
      workspaceService.updateWorkspace(workspaceId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspaces'] });
    },
  });
};

export const useReinitWorkspaceContext = (workspaceId: string) => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (config: WorkspaceContextConfig) =>
      workspaceService.reinitWorkspaceContext(workspaceId, config),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['workspace-contexts', workspaceId] });
    },
  });
};

export const useUserContexts = (params?: { context_type?: string; page?: number; page_size?: number }) => {
  return useQuery({
    queryKey: ['user-contexts', params],
    queryFn: () => apiClient.get(API_ENDPOINTS.CONTEXT.LIST, { params }),
  });
};
