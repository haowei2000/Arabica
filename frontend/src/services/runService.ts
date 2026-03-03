import { apiClient } from '@/services/api';
import { API_ENDPOINTS } from '@/constants/api';
import type { Run, RunListResponse, RunStartRequest, RunState } from '@/types/run';
import type { EventListResponse } from '@/types/event';

export const runService = {
  async listRuns(
    workspaceId: string,
    params?: { page?: number; page_size?: number; status?: string }
  ) {
    return apiClient.get<RunListResponse>(API_ENDPOINTS.WORKSPACES.RUNS(workspaceId), {
      params,
    });
  },

  async createRun(workspaceId: string, data: RunStartRequest) {
    return apiClient.post<Run>(API_ENDPOINTS.WORKSPACES.RUNS(workspaceId), data);
  },

  async cancelRun(workspaceId: string, runId: string, reason?: string) {
    return apiClient.post<Run>(
      API_ENDPOINTS.WORKSPACES.RUN_CANCEL(workspaceId, runId),
      undefined,
      {
        params: reason ? { reason } : undefined,
      }
    );
  },

  async listUserRuns(params?: { page?: number; page_size?: number; status?: string }) {
    return apiClient.get<RunListResponse>(API_ENDPOINTS.RUNS.USER_LIST, { params });
  },

  async getRunState(runId: string) {
    return apiClient.get<RunState>(API_ENDPOINTS.EVENTS.RUN_STATE(runId));
  },

  async getRunEvents(runId: string, params?: { from_sequence?: number; limit?: number }) {
    return apiClient.get<EventListResponse>(API_ENDPOINTS.EVENTS.RUN_EVENTS(runId), { params });
  },
};
