import { apiClient } from '@/services/api';
import { API_ENDPOINTS } from '@/constants/api';
import type { WorkspaceCreate, WorkspaceListResponse, Workspace } from '@/types/workspace';

export const workspaceService = {
  async listWorkspaces(params?: { page?: number; page_size?: number; status?: string }) {
    return apiClient.get<WorkspaceListResponse>(API_ENDPOINTS.WORKSPACES.LIST, {
      params,
    });
  },

  async createWorkspace(data: WorkspaceCreate) {
    return apiClient.post<Workspace>(API_ENDPOINTS.WORKSPACES.CREATE, data);
  },
};
