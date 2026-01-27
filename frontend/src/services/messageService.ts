import {apiClient} from './api';
import {API_ENDPOINTS} from '@/constants/api';
import type {Message, MessageCreate, MessageUpdate,} from '@/types/message';
import type {PaginatedResponse} from '@/types/api';

export const messageService = {
  /**
   * 获取消息列表
   */
  async getMessages(params?: {
    conversation_id?: string;
    app_id?: string;
    status?: string;
    page?: number;
    page_size?: number;
  }): Promise<PaginatedResponse<Message>> {
    return apiClient.get(API_ENDPOINTS.MESSAGES.LIST, { params });
  },

  /**
   * 获取单个消息
   */
  async getMessage(id: string): Promise<Message> {
    return apiClient.get(API_ENDPOINTS.MESSAGES.GET(id));
  },

  /**
   * 创建消息
   */
  async createMessage(data: MessageCreate): Promise<Message> {
    return apiClient.post(API_ENDPOINTS.MESSAGES.CREATE, data);
  },

  /**
   * 更新消息
   */
  async updateMessage(
    id: string,
    data: MessageUpdate
  ): Promise<Message> {
      return apiClient.post(API_ENDPOINTS.MESSAGES.UPDATE(id), data);
  },

};
