import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toolService } from '@/services/toolService';
import type { MCPServerConfig, MCPImportRequest, ToolBundleListResponse, InnerToolListResponse } from '@/types/tool';

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

/** Returns all tools mapped to InnerToolInfo shape (for tool pickers in other pages). */
export const useInnerTools = (): { data: InnerToolListResponse | undefined; isLoading: boolean } => {
  const { data, isLoading } = useToolList({ include_public: true });
  const mapped = useMemo<InnerToolListResponse | undefined>(() => {
    if (!data) return undefined;
    return {
      inner_tools: data.tools.map(t => ({
        name: t.name,
        display_name: t.display_name,
        description: t.description,
        category: t.category ?? '',
        tags: t.tags ?? [],
        timeout: t.timeout ?? 30,
        input_schema: t.input_schema ?? {},
        output_schema: (t.output_schema ?? {}) as Record<string, unknown>,
      })),
      total: data.total,
    };
  }, [data]);
  return { data: mapped, isLoading };
};

export const useTestTool = () => {
  return useMutation({
    mutationFn: ({ id, parameters }: { id: string; parameters: Record<string, unknown> }) =>
      toolService.testTool(id, parameters),
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
