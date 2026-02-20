import { apiClient } from './api';
import { API_ENDPOINTS } from '@/constants/api';
import type { Event, EventListResponse, EventFilterParams } from '@/types/event';

export const eventService = {
  async getEvent(id: string): Promise<Event> {
    return apiClient.get(API_ENDPOINTS.EVENTS.GET(id));
  },

  async listByWorkspace(
    workspaceId: string,
    params?: {
      skip?: number;
      limit?: number;
      event_types?: string;
    }
  ): Promise<EventListResponse> {
    return apiClient.get(API_ENDPOINTS.EVENTS.LIST_BY_WORKSPACE(workspaceId), { params });
  },

  async listByRun(
    runId: string,
    params?: {
      skip?: number;
      limit?: number;
      event_types?: string;
    }
  ): Promise<EventListResponse> {
    return apiClient.get(API_ENDPOINTS.EVENTS.LIST_BY_RUN(runId), { params });
  },

  async listByUser(params?: {
    skip?: number;
    limit?: number;
    event_types?: string;
    workspace_id?: string;
  }): Promise<EventListResponse> {
    return apiClient.get(API_ENDPOINTS.EVENTS.LIST_BY_USER, { params });
  },
};
