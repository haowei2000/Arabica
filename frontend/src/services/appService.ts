import {apiClient} from './api';
import {API_ENDPOINTS} from '@/constants/api';
import type {AgentTemplate, App, AppCreate, AppUpdate,} from '@/types/app';
import type {PaginatedResponse} from '@/types/api';

export const appService = {
  /**
   * 获取可用的 agent 模板列表
   */
  async getTemplates(): Promise<AgentTemplate[]> {
    return apiClient.get(API_ENDPOINTS.APPS.TEMPLATES);
  },

  /**
   * 获取 App 列表
   */
  async getApps(params?: {
    page?: number;
    page_size?: number;
    enabled_only?: boolean;
  }): Promise<PaginatedResponse<App>> {
    return apiClient.get(API_ENDPOINTS.APPS.LIST, { params });
  },

  /**
   * 获取单个 App（使用 app_id）
   */
  async getApp(appId: string): Promise<App> {
      return apiClient.get(API_ENDPOINTS.APPS.GET(appId));
  },

  /**
   * 创建 App
   */
  async createApp(data: AppCreate): Promise<App> {
    return apiClient.post(API_ENDPOINTS.APPS.CREATE, data);
  },

  /**
   * 更新 App（使用 app_id）
   */
  async updateApp(appId: string, data: AppUpdate): Promise<App> {
      return apiClient.post(API_ENDPOINTS.APPS.UPDATE(appId), data);
  },

  /**
   * 删除 App（使用 app_id）
   */
  async deleteApp(appId: string): Promise<void> {
      return apiClient.post(API_ENDPOINTS.APPS.DELETE(appId));
  },

  /**
   * 查询 Apps（支持多种过滤条件）
   */
  async queryApps(params?: {
      template_id?: string;
      enabled?: boolean;
      page?: number;
      page_size?: number;
  }): Promise<PaginatedResponse<App>> {
      return apiClient.get(API_ENDPOINTS.APPS.QUERY, {params});
  },
};
