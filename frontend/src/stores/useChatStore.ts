import {create} from 'zustand';
import type { MessageRoleType, SimpleMessage } from '@/types/message';
import { runService } from '@/services/runService';

interface ChatState {
  currentRunId: string | null;
  messages: SimpleMessage[];
  streamingMessage: string;
  isStreaming: boolean;
  isLoadingConversation: boolean;

  setCurrentRun: (id: string | null) => void;
  setMessages: (messages: SimpleMessage[]) => void;
  addMessage: (message: SimpleMessage) => void;
  updateStreamingMessage: (content: string) => void;
  appendStreamingMessage: (content: string) => void;
  clearStreamingMessage: () => void;
  setIsStreaming: (isStreaming: boolean) => void;
  loadRun: (runId: string) => Promise<void>;
  startNewRun: () => void;
  reset: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  currentRunId: null,
  messages: [],
  streamingMessage: '',
  isStreaming: false,
  isLoadingConversation: false,

  setCurrentRun: (id) => set({ currentRunId: id }),

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

  loadRun: async (runId: string) => {
    try {
      set({ isLoadingConversation: true });

      const runState = await runService.getRunState(runId);
      const messages: SimpleMessage[] = runState.messages.map((msg, index) => ({
        id: `${runId}-${index}`,
        role: msg.role as MessageRoleType,
        content: msg.content || '',
        timestamp: new Date(msg.timestamp),
      }));

      set({
        currentRunId: runId,
        messages,
        streamingMessage: '',
        isLoadingConversation: false,
      });
    } catch (error) {
      console.error('Failed to load conversation:', error);
      set({ isLoadingConversation: false });
    }
  },

  startNewRun: () =>
    set({
      currentRunId: null,
      messages: [],
      streamingMessage: '',
    }),

  reset: () =>
    set({
      currentRunId: null,
      messages: [],
      streamingMessage: '',
      isStreaming: false,
      isLoadingConversation: false,
    }),
}));
