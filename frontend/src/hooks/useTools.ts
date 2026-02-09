import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toolService } from '@/services/toolService';
import type { UserToolCreate } from '@/types/tool';

export const useTemplates = (params?: {
  execution_mode?: string;
  source?: string;
}) => {
  return useQuery({
    queryKey: ['tool-templates', params],
    queryFn: () => toolService.getTemplates(params),
  });
};

export const useToolList = (params?: {
  workspace_id?: string;
  enabled_only?: boolean;
  include_public?: boolean;
  tool_type?: string;
}) => {
  return useQuery({
    queryKey: ['tools', 'list', params],
    queryFn: () => toolService.getTools(params),
  });
};

export const useCreateTool = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: UserToolCreate) => toolService.createTool(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tools'] });
    },
  });
};

export const useDeleteTool = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => toolService.deleteTool(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tools'] });
    },
  });
};

export const useToggleTool = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      toolService.toggleTool(id, enabled),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tools'] });
    },
  });
};
