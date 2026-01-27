import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {appService} from '@/services/appService';
import type {AppCreate, AppUpdate} from '@/types/app';

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
export const useApp = (appId: string) => {
  return useQuery({
      queryKey: ['app', appId],
      queryFn: () => appService.getApp(appId),
      enabled: !!appId,
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
      mutationFn: ({appId, data}: { appId: string; data: AppUpdate }) =>
          appService.updateApp(appId, data),
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
      mutationFn: (appId: string) => appService.deleteApp(appId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apps'] });
    },
  });
};

/**
 * 查询 Apps（支持多种过滤条件）
 */
export const useQueryApps = (params?: {
    template_id?: string;
    enabled?: boolean;
    page?: number;
    page_size?: number;
}) => {
    return useQuery({
        queryKey: ['apps', 'query', params],
        queryFn: () => appService.queryApps(params),
    });
};
