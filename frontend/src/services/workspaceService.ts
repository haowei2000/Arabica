import { apiClient } from '@/services/api';
import { API_ENDPOINTS } from '@/constants/api';
import type { WorkspaceCreate, WorkspaceListResponse, Workspace, WorkspaceContextList } from '@/types/workspace';

export const workspaceService = {
  async listWorkspaces(params?: { page?: number; page_size?: number; status?: string }) {
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
};
