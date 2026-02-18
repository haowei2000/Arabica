import { apiClient } from '@/services/api';
import { API_ENDPOINTS } from '@/constants/api';
import type { WorkspaceCreate, WorkspaceListResponse, Workspace, WorkspaceContextList, WorkspaceContextConfig } from '@/types/workspace';

export const workspaceService = {
  async listWorkspaces(params?: { page?: number; page_size?: number; status?: string; app_id?: string }) {
    return apiClient.get<WorkspaceListResponse>(API_ENDPOINTS.WORKSPACES.LIST, {
      params,
    });
  },

  async createWorkspace(data: WorkspaceCreate) {
    return apiClient.post<Workspace>(API_ENDPOINTS.WORKSPACES.CREATE, data);
  },

  async listWorkspaceContexts(workspaceId: string, params?: { page?: number; page_size?: number }) {
    return apiClient.get<WorkspaceContextList>(API_ENDPOINTS.WORKSPACES.CONTEXTS(workspaceId), {
      params,
    });
  },

  async copyContextsToWorkspace(workspaceId: string, body: { context_ids: string[]; path_prefix?: string }) {
    return apiClient.post(API_ENDPOINTS.WORKSPACES.CONTEXTS_COPY(workspaceId), body);
  },

  async removeWorkspaceContext(workspaceId: string, contextId: string) {
    return apiClient.delete(API_ENDPOINTS.WORKSPACES.CONTEXT_DELETE(workspaceId, contextId));
  },

  async deleteWorkspace(workspaceId: string) {
    return apiClient.delete(API_ENDPOINTS.WORKSPACES.DELETE(workspaceId));
  },

  async updateWorkspace(workspaceId: string, data: { name?: string; description?: string }) {
    return apiClient.patch<Workspace>(API_ENDPOINTS.WORKSPACES.UPDATE(workspaceId), data);
  },

  async reinitWorkspaceContext(workspaceId: string, config: WorkspaceContextConfig) {
    return apiClient.post(API_ENDPOINTS.WORKSPACES.CONTEXT_REINIT(workspaceId), config);
  },
};
