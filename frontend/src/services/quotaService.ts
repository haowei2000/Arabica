import { API_ENDPOINTS } from '@/constants/api';
import { apiClient } from '@/services/api';
import type { Quota } from '@/types/quota';

export const quotaService = {
  async getMyQuota(): Promise<Quota> {
    return apiClient.get<Quota>(API_ENDPOINTS.QUOTA.ME);
  },
};
