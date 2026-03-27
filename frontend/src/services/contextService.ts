import { apiClient } from './api';
import { API_ENDPOINTS } from '@/constants/api';
import type { ContextListResponse, EntityContextType } from '@/types/context';

export const contextService = {
  async getEntityContext(
    entityType: EntityContextType,
    entityId: string,
    params?: { page?: number; page_size?: number },
  ): Promise<ContextListResponse> {
    const endpointMap: Record<EntityContextType, string> = {
      skill: API_ENDPOINTS.CONTEXT.FOR_SKILL(entityId),
      knowledge: API_ENDPOINTS.CONTEXT.FOR_KNOWLEDGE(entityId),
      document: API_ENDPOINTS.CONTEXT.FOR_DOCUMENT(entityId),
      tool: API_ENDPOINTS.CONTEXT.FOR_TOOL(entityId),
      memory: API_ENDPOINTS.CONTEXT.MEMORIES,
    };
    return apiClient.get(endpointMap[entityType], { params });
  },

  async getMemories(params?: { page?: number; page_size?: number }): Promise<ContextListResponse> {
    return apiClient.get(API_ENDPOINTS.CONTEXT.MEMORIES, { params });
  },
};
