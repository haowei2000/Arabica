import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { conversationService } from '../services/conversationService';
import type { PaginationParams } from '../types/api';

export const useConversations = (appId: string | null, params?: PaginationParams) => {
  return useQuery({
    queryKey: ['conversations', appId, params],
    queryFn: () => {
      if (!appId) throw new Error('App ID is required');
      return conversationService.getConversations({
        app_id: appId,
        status: 'normal',
        ...params
      });
    },
    enabled: !!appId,
    staleTime: 30000, // Consider data fresh for 30 seconds
  });
};

export const useConversation = (conversationId: string | null) => {
  return useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => {
      if (!conversationId) throw new Error('Conversation ID is required');
      return conversationService.getConversation(conversationId);
    },
    enabled: !!conversationId,
  });
};

export const useCreateConversation = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: conversationService.createConversation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversations'] });
    },
  });
};

export const useUpdateConversation = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: any }) =>
      conversationService.updateConversation(id, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['conversations'] });
      queryClient.invalidateQueries({ queryKey: ['conversation', variables.id] });
    },
  });
};

export const useDeleteConversation = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: conversationService.deleteConversation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['conversations'] });
    },
  });
};
