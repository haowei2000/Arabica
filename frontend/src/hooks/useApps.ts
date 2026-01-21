import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { appService } from '@/services/appService';
import type { AppCreate, AppUpdate } from '@/types/app';

/**
 * 获取 App 列表
 */
export const useApps = (params?: {
  page?: number;
  page_size?: number;
  enabled_only?: boolean;
}) => {
  return useQuery({
    queryKey: ['apps', params],
    queryFn: () => appService.getApps(params),
  });
};

/**
 * 获取单个 App
 */
export const useApp = (appCode: string) => {
  return useQuery({
    queryKey: ['app', appCode],
    queryFn: () => appService.getApp(appCode),
    enabled: !!appCode,
  });
};

/**
 * 获取 Agent 模板列表
 */
export const useTemplates = () => {
  return useQuery({
    queryKey: ['templates'],
    queryFn: () => appService.getTemplates(),
  });
};

/**
 * 创建 App
 */
export const useCreateApp = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: AppCreate) => appService.createApp(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apps'] });
    },
  });
};

/**
 * 更新 App
 */
export const useUpdateApp = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ appCode, data }: { appCode: string; data: AppUpdate }) =>
      appService.updateApp(appCode, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apps'] });
    },
  });
};

/**
 * 删除 App
 */
export const useDeleteApp = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (appCode: string) => appService.deleteApp(appCode),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apps'] });
    },
  });
};
