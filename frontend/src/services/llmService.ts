import { apiClient } from './api';
import { API_ENDPOINTS } from '@/constants/api';
import type {
  ChatModel,
  ChatModelCreate,
  ChatModelUpdate,
  ChatModelListResponse,
  EmbeddingModel,
  EmbeddingModelCreate,
  EmbeddingModelUpdate,
  EmbeddingModelListResponse,
} from '@/types/llm';

export const llmService = {
  // Chat Models
  async listChatModels(params?: {
    provider?: string;
    enabled?: boolean;
    page?: number;
    page_size?: number;
  }): Promise<ChatModelListResponse> {
    return apiClient.get(API_ENDPOINTS.LLM_CHAT_MODELS.LIST, { params });
  },

  async createChatModel(data: ChatModelCreate): Promise<ChatModel> {
    return apiClient.post(API_ENDPOINTS.LLM_CHAT_MODELS.CREATE, data);
  },

  async getChatModel(id: string): Promise<ChatModel> {
    return apiClient.get(API_ENDPOINTS.LLM_CHAT_MODELS.GET(id));
  },

  async updateChatModel(id: string, data: ChatModelUpdate): Promise<ChatModel> {
    return apiClient.put(API_ENDPOINTS.LLM_CHAT_MODELS.UPDATE(id), data);
  },

  async deleteChatModel(id: string): Promise<void> {
    return apiClient.delete(API_ENDPOINTS.LLM_CHAT_MODELS.DELETE(id));
  },

  async searchChatModels(q: string, params?: { page?: number; page_size?: number }): Promise<ChatModelListResponse> {
    return apiClient.get(API_ENDPOINTS.LLM_CHAT_MODELS.SEARCH, { params: { q, ...params } });
  },

  // Embedding Models
  async listEmbeddingModels(params?: {
    provider?: string;
    enabled?: boolean;
    page?: number;
    page_size?: number;
  }): Promise<EmbeddingModelListResponse> {
    return apiClient.get(API_ENDPOINTS.LLM_EMBEDDING_MODELS.LIST, { params });
  },

  async createEmbeddingModel(data: EmbeddingModelCreate): Promise<EmbeddingModel> {
    return apiClient.post(API_ENDPOINTS.LLM_EMBEDDING_MODELS.CREATE, data);
  },

  async getEmbeddingModel(id: string): Promise<EmbeddingModel> {
    return apiClient.get(API_ENDPOINTS.LLM_EMBEDDING_MODELS.GET(id));
  },

  async updateEmbeddingModel(id: string, data: EmbeddingModelUpdate): Promise<EmbeddingModel> {
    return apiClient.put(API_ENDPOINTS.LLM_EMBEDDING_MODELS.UPDATE(id), data);
  },

  async deleteEmbeddingModel(id: string): Promise<void> {
    return apiClient.delete(API_ENDPOINTS.LLM_EMBEDDING_MODELS.DELETE(id));
  },

  async searchEmbeddingModels(q: string, params?: { page?: number; page_size?: number }): Promise<EmbeddingModelListResponse> {
    return apiClient.get(API_ENDPOINTS.LLM_EMBEDDING_MODELS.SEARCH, { params: { q, ...params } });
  },
};
