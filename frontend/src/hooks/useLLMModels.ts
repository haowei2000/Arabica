import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { llmService } from '@/services/llmService';
import type { ChatModelCreate, ChatModelUpdate, EmbeddingModelCreate, EmbeddingModelUpdate } from '@/types/llm';

// ── Chat Model Hooks ──────────────────────────────────────────────────────────

export const useChatModels = (params?: {
  provider?: string;
  enabled?: boolean;
  page?: number;
  page_size?: number;
}) => {
  return useQuery({
    queryKey: ['llm', 'chat-models', params],
    queryFn: () => llmService.listChatModels(params),
  });
};

export const useCreateChatModel = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: ChatModelCreate) => llmService.createChatModel(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm', 'chat-models'] });
    },
  });
};

export const useUpdateChatModel = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ChatModelUpdate }) =>
      llmService.updateChatModel(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm', 'chat-models'] });
    },
  });
};

export const useDeleteChatModel = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => llmService.deleteChatModel(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm', 'chat-models'] });
    },
  });
};

// ── Embedding Model Hooks ─────────────────────────────────────────────────────

export const useEmbeddingModels = (params?: {
  provider?: string;
  enabled?: boolean;
  page?: number;
  page_size?: number;
}) => {
  return useQuery({
    queryKey: ['llm', 'embedding-models', params],
    queryFn: () => llmService.listEmbeddingModels(params),
  });
};

export const useCreateEmbeddingModel = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: EmbeddingModelCreate) => llmService.createEmbeddingModel(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm', 'embedding-models'] });
    },
  });
};

export const useUpdateEmbeddingModel = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: EmbeddingModelUpdate }) =>
      llmService.updateEmbeddingModel(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm', 'embedding-models'] });
    },
  });
};

export const useDeleteEmbeddingModel = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => llmService.deleteEmbeddingModel(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm', 'embedding-models'] });
    },
  });
};
