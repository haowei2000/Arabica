import { apiClient } from './api';
import { API_ENDPOINTS } from '@/constants/api';
import type {
  Conversation,
  ConversationCreate,
  ConversationUpdate,
} from '@/types/conversation';
import type { PaginatedResponse } from '@/types/api';

export const conversationService = {
  /**
   * 获取对话列表
   */
  async getConversations(params?: {
    app_id?: string;
    status?: string;
    page?: number;
    page_size?: number;
  }): Promise<PaginatedResponse<Conversation>> {
    return apiClient.get(API_ENDPOINTS.CONVERSATIONS.LIST, { params });
  },

  /**
   * 获取单个对话详情
   */
  async getConversation(id: string): Promise<Conversation> {
    return apiClient.get(API_ENDPOINTS.CONVERSATIONS.GET(id));
  },

  /**
   * 创建新对话
   */
  async createConversation(
    data: ConversationCreate
  ): Promise<Conversation> {
    return apiClient.post(API_ENDPOINTS.CONVERSATIONS.CREATE, data);
  },

  /**
   * 更新对话
   */
  async updateConversation(
    id: string,
    data: ConversationUpdate
  ): Promise<Conversation> {
    return apiClient.put(API_ENDPOINTS.CONVERSATIONS.UPDATE(id), data);
  },

  /**
   * 删除对话（软删除）
   */
  async deleteConversation(id: string): Promise<void> {
    return apiClient.delete(API_ENDPOINTS.CONVERSATIONS.DELETE(id));
  },

  /**
   * 搜索对话
   */
  async searchConversations(params: {
    q: string;
    app_id?: string;
    page?: number;
    page_size?: number;
  }): Promise<PaginatedResponse<Conversation>> {
    return apiClient.get(API_ENDPOINTS.CONVERSATIONS.SEARCH, { params });
  },
};
