import { apiClient } from '@/services/api';
import { API_ENDPOINTS } from '@/constants/api';
import type { Trigger, TriggerCreate, TriggerUpdate, TriggerListResponse } from '@/types/trigger';

export const triggerService = {
  async listTriggers(params?: {
    page?: number;
    page_size?: number;
    event_type?: string;
    enabled?: boolean;
  }) {
    return apiClient.get<TriggerListResponse>(API_ENDPOINTS.TRIGGERS.LIST, { params });
  },

  async createTrigger(data: TriggerCreate) {
    return apiClient.post<Trigger>(API_ENDPOINTS.TRIGGERS.CREATE, data);
  },

  async getTrigger(id: string) {
    return apiClient.get<Trigger>(API_ENDPOINTS.TRIGGERS.GET(id));
  },

  async updateTrigger(id: string, data: TriggerUpdate) {
    return apiClient.put<Trigger>(API_ENDPOINTS.TRIGGERS.UPDATE(id), data);
  },

  async deleteTrigger(id: string) {
    return apiClient.delete(API_ENDPOINTS.TRIGGERS.DELETE(id));
  },
};
