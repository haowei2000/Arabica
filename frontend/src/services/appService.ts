import { apiClient } from './api';
import { API_ENDPOINTS } from '@/constants/api';
import type {
  App,
  AppCreate,
  AppUpdate,
  AgentTemplate,
} from '@/types/app';
import type { PaginatedResponse } from '@/types/api';

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
   * 获取单个 App（使用 app_code）
   */
  async getApp(appCode: string): Promise<App> {
    return apiClient.get(API_ENDPOINTS.APPS.GET(appCode));
  },

  /**
   * 创建 App
   */
  async createApp(data: AppCreate): Promise<App> {
    return apiClient.post(API_ENDPOINTS.APPS.CREATE, data);
  },

  /**
   * 更新 App（使用 app_code）
   */
  async updateApp(appCode: string, data: AppUpdate): Promise<App> {
    return apiClient.put(API_ENDPOINTS.APPS.UPDATE(appCode), data);
  },

  /**
   * 删除 App（使用 app_code）
   */
  async deleteApp(appCode: string): Promise<void> {
    return apiClient.delete(API_ENDPOINTS.APPS.DELETE(appCode));
  },

  /**
   * 获取使用特定模板的所有 Apps
   */
  async getAppsByTemplate(
    templateId: string,
    params?: {
      page?: number;
      page_size?: number;
    }
  ): Promise<PaginatedResponse<App>> {
    return apiClient.get(API_ENDPOINTS.APPS.BY_TEMPLATE(templateId), {
      params,
    });
  },
};
