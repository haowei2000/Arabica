import { create } from 'zustand';
import type { SimpleMessage } from '@/types/message';

interface ChatState {
  currentConversationId: string | null;
  messages: SimpleMessage[];
  streamingMessage: string;
  isStreaming: boolean;

  setCurrentConversation: (id: string | null) => void;
  setMessages: (messages: SimpleMessage[]) => void;
  addMessage: (message: SimpleMessage) => void;
  updateStreamingMessage: (content: string) => void;
  appendStreamingMessage: (content: string) => void;
  clearStreamingMessage: () => void;
  setIsStreaming: (isStreaming: boolean) => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  currentConversationId: null,
  messages: [],
  streamingMessage: '',
  isStreaming: false,

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

  reset: () =>
    set({
      currentConversationId: null,
      messages: [],
      streamingMessage: '',
      isStreaming: false,
    }),
}));
