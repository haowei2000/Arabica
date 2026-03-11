import { apiClient } from './api';
import { API_ENDPOINTS } from '@/constants/api';
import type {
  ToolTemplate,
  ToolTemplateListResponse,
  ToolExportData,
  UserTool,
  UserToolCreate,
  UserToolUpdate,
  UserToolListResponse,
  UserToolTestResponse,
  InnerToolListResponse,
  MCPServerConfig,
  MCPProbeResponse,
  MCPImportRequest,
  MCPImportResponse,
  ToolBundleListResponse,
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

  async updateTool(id: string, data: UserToolUpdate): Promise<UserTool> {
    return apiClient.patch(API_ENDPOINTS.TOOLS.UPDATE(id), data);
  },

  async deleteTool(id: string): Promise<void> {
    return apiClient.delete(API_ENDPOINTS.TOOLS.DELETE(id));
  },

  async toggleTool(id: string, enabled: boolean): Promise<UserTool> {
    return apiClient.post(API_ENDPOINTS.TOOLS.TOGGLE(id), null, {
      params: { enabled },
    });
  },

  async getToolAsTemplate(id: string): Promise<ToolTemplate> {
    return apiClient.get(API_ENDPOINTS.TOOLS.TOOL_TEMPLATE(id));
  },

  async testTool(id: string, parameters: Record<string, unknown>): Promise<UserToolTestResponse> {
    return apiClient.post(API_ENDPOINTS.TOOLS.TEST(id), { parameters });
  },

  async getInnerTools(): Promise<InnerToolListResponse> {
    return apiClient.get(API_ENDPOINTS.TOOLS.INNER_TOOLS);
  },

  async exportTool(id: string): Promise<ToolExportData> {
    return apiClient.get(API_ENDPOINTS.TOOLS.EXPORT(id));
  },

  async importTool(data: ToolExportData): Promise<UserTool> {
    return apiClient.post(API_ENDPOINTS.TOOLS.IMPORT, data);
  },

  async probeMcp(data: MCPServerConfig): Promise<MCPProbeResponse> {
    return apiClient.post(API_ENDPOINTS.TOOLS.PROBE_MCP, data);
  },

  async importFromMcp(data: MCPImportRequest): Promise<MCPImportResponse> {
    return apiClient.post(API_ENDPOINTS.TOOLS.IMPORT_FROM_MCP, data);
  },

  async getToolBundles(params?: { include_public?: boolean; tags?: string }): Promise<ToolBundleListResponse> {
    return apiClient.get(API_ENDPOINTS.TOOL_BUNDLES.LIST, { params });
  },
};
