import { apiClient } from './api';
import { API_ENDPOINTS } from '@/constants/api';
import type {
  UserTool,
  UserToolListResponse,
  UserToolTestResponse,
  MCPServerConfig,
  MCPProbeResponse,
  MCPImportRequest,
  MCPImportResponse,
  ToolBundleListResponse,
} from '@/types/tool';

export const toolService = {
  async getTools(params?: {
    workspace_id?: string;
    enabled_only?: boolean;
    include_public?: boolean;
    tool_type?: string;
    tags?: string;
  }): Promise<UserToolListResponse> {
    return apiClient.get(API_ENDPOINTS.TOOLS.LIST, { params });
  },

  async deleteTool(id: string): Promise<void> {
    return apiClient.delete(API_ENDPOINTS.TOOLS.DELETE(id));
  },

  async toggleTool(id: string, enabled: boolean): Promise<UserTool> {
    return apiClient.post(API_ENDPOINTS.TOOLS.TOGGLE(id), null, {
      params: { enabled },
    });
  },

  async testTool(id: string, parameters: Record<string, unknown>): Promise<UserToolTestResponse> {
    return apiClient.post(API_ENDPOINTS.TOOLS.TEST(id), { parameters });
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
