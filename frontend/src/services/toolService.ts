import { apiClient } from './api';
import { API_ENDPOINTS } from '@/constants/api';
import type {
  ToolTemplate,
  ToolTemplateListResponse,
  UserTool,
  UserToolCreate,
  UserToolListResponse,
} from '@/types/tool';

export const toolService = {
  async getTemplates(params?: {
    execution_mode?: string;
    source?: string;
  }): Promise<ToolTemplateListResponse> {
    return apiClient.get(API_ENDPOINTS.TOOLS.TEMPLATES, { params });
  },

  async getTemplate(id: string): Promise<ToolTemplate> {
    return apiClient.get(API_ENDPOINTS.TOOLS.TEMPLATE(id));
  },

  async getTools(params?: {
    workspace_id?: string;
    enabled_only?: boolean;
    include_public?: boolean;
    tool_type?: string;
    tags?: string;
  }): Promise<UserToolListResponse> {
    return apiClient.get(API_ENDPOINTS.TOOLS.LIST, { params });
  },

  async createTool(data: UserToolCreate): Promise<UserTool> {
    return apiClient.post(API_ENDPOINTS.TOOLS.CREATE, data);
  },

  async deleteTool(id: string): Promise<void> {
    return apiClient.delete(API_ENDPOINTS.TOOLS.DELETE(id));
  },

  async toggleTool(id: string, enabled: boolean): Promise<UserTool> {
    return apiClient.post(API_ENDPOINTS.TOOLS.TOGGLE(id), null, {
      params: { enabled },
    });
  },
};
