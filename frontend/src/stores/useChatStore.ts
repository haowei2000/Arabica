import {create} from 'zustand';
import type {MessageRoleType, SimpleMessage} from '@/types/message';
import {messageService} from '@/services/messageService';

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

        // Fetch messages using message service with conversation_id filter
        const messagesResponse = await messageService.getMessages({
            conversation_id: conversationId,
            page: 1,
            page_size: 100,
        });

        // Convert API messages to SimpleMessage format
        const messages: SimpleMessage[] = [];
        for (const msg of messagesResponse.items) {
            // Each message contains a list of message content (user + assistant)
            if (Array.isArray(msg.message)) {
                for (const content of msg.message) {
                    messages.push({
                        id: `${msg.id}-${content.role}`,
                        role: content.role as MessageRoleType,
                        content: content.content || '',
                        timestamp: new Date(msg.created_at),
                    });
                }
            }
        }

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
