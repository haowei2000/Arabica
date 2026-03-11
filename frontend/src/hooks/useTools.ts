import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toolService } from '@/services/toolService';
import type { UserToolCreate, UserToolUpdate, ToolTemplate, ToolExportData, InnerToolListResponse, MCPServerConfig, MCPImportRequest, ToolBundleListResponse } from '@/types/tool';

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
  tags?: string;
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

export const useUpdateTool = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UserToolUpdate }) =>
      toolService.updateTool(id, data),
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

export const useToolAsTemplate = (id: string | null) => {
  return useQuery<ToolTemplate>({
    queryKey: ['tool-template', id],
    queryFn: () => toolService.getToolAsTemplate(id!),
    enabled: !!id,
    staleTime: 60_000,
  });
};

export const useInnerTools = () => {
  return useQuery<InnerToolListResponse>({
    queryKey: ['tools', 'registry', 'inner-tools'],
    queryFn: () => toolService.getInnerTools(),
    staleTime: 5 * 60 * 1000,
  });
};

export const useTestTool = () => {
  return useMutation({
    mutationFn: ({ id, parameters }: { id: string; parameters: Record<string, unknown> }) =>
      toolService.testTool(id, parameters),
  });
};

export const useExportTool = () => {
  return useMutation({
    mutationFn: (id: string) => toolService.exportTool(id),
  });
};

export const useImportTool = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: ToolExportData) => toolService.importTool(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tools'] });
    },
  });
};

export const useProbeMcp = () =>
  useMutation({
    mutationFn: (config: MCPServerConfig) => toolService.probeMcp(config),
  });

export const useImportFromMcp = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: MCPImportRequest) => toolService.importFromMcp(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tools'] });
      queryClient.invalidateQueries({ queryKey: ['tool-bundles'] });
    },
  });
};

export const useToolBundles = (params?: { include_public?: boolean }) => {
  return useQuery<ToolBundleListResponse>({
    queryKey: ['tool-bundles', params],
    queryFn: () => toolService.getToolBundles(params),
    staleTime: 30_000,
  });
};
