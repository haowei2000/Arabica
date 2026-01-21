import { create } from 'zustand';
import type { SimpleMessage } from '@/types/message';
import type { ConversationDetail } from '@/types/conversation';
import { conversationService } from '@/services/conversationService';

interface ChatState {
  currentConversationId: string | null;
  messages: SimpleMessage[];
  streamingMessage: string;
  isStreaming: boolean;
  isLoadingConversation: boolean;

  setCurrentConversation: (id: string | null) => void;
  setMessages: (messages: SimpleMessage[]) => void;
  addMessage: (message: SimpleMessage) => void;
  updateStreamingMessage: (content: string) => void;
  appendStreamingMessage: (content: string) => void;
  clearStreamingMessage: () => void;
  setIsStreaming: (isStreaming: boolean) => void;
  loadConversation: (conversationId: string) => Promise<void>;
  startNewConversation: () => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  currentConversationId: null,
  messages: [],
  streamingMessage: '',
  isStreaming: false,
  isLoadingConversation: false,

  setCurrentConversation: (id) =>
    set({ currentConversationId: id }),

  setMessages: (messages) => set({ messages }),

  addMessage: (message) =>
    set((state) => ({
      messages: [...state.messages, message],
    })),

  updateStreamingMessage: (content) =>
    set({ streamingMessage: content }),

  appendStreamingMessage: (content) =>
    set((state) => ({
      streamingMessage: state.streamingMessage + content,
    })),

  clearStreamingMessage: () =>
    set({ streamingMessage: '' }),

  setIsStreaming: (isStreaming) =>
    set({ isStreaming }),

  loadConversation: async (conversationId: string) => {
    try {
      set({ isLoadingConversation: true });
      const conversationDetail = await conversationService.getConversation(conversationId) as ConversationDetail;

      // Convert conversation messages to SimpleMessage format
      const messages: SimpleMessage[] = conversationDetail.messages?.map((msg) => ({
        id: msg.id,
        role: (msg.role || 'user') as any,
        content: msg.content || msg.query || msg.answer || '',
        timestamp: new Date(msg.created_at),
      })) || [];

      set({
        currentConversationId: conversationId,
        messages,
        streamingMessage: '',
        isLoadingConversation: false,
      });
    } catch (error) {
      console.error('Failed to load conversation:', error);
      set({ isLoadingConversation: false });
    }
  },

  startNewConversation: () =>
    set({
      currentConversationId: null,
      messages: [],
      streamingMessage: '',
    }),

  reset: () =>
    set({
      currentConversationId: null,
      messages: [],
      streamingMessage: '',
      isStreaming: false,
      isLoadingConversation: false,
    }),
}));
