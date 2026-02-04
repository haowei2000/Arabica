import { create } from 'zustand';
import type { MessageRoleType, SimpleMessage } from '@/types/message';
import type { ToolCallState, ToolPendingState, AgentPlanStepPayload } from '@/types/events';
import { runService } from '@/services/runService';

interface ChatState {
  currentRunId: string | null;
  messages: SimpleMessage[];
  streamingMessage: string;
  isStreaming: boolean;
  isLoadingConversation: boolean;

  // ── live event-stream state (cleared on stream end) ──────
  thinkingContent: string | null;
  activeToolCalls: ToolCallState[];
  planSteps: AgentPlanStepPayload[];
  pendingApprovals: ToolPendingState[];

  // ── actions ───────────────────────────────────────────────
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

  // ── event-stream actions ──────────────────────────────────
  setThinkingContent: (content: string | null) => void;
  addToolCall: (call: ToolCallState) => void;
  updateToolCall: (toolId: string, updates: Partial<ToolCallState>) => void;
  clearToolCalls: () => void;
  addOrUpdatePlanStep: (step: AgentPlanStepPayload) => void;
  clearPlanSteps: () => void;

  // ── approval actions ──────────────────────────────────────
  addPendingApproval: (approval: ToolPendingState) => void;
  removePendingApproval: (toolId: string) => void;
  clearPendingApprovals: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  currentRunId: null,
  messages: [],
  streamingMessage: '',
  isStreaming: false,
  isLoadingConversation: false,

  thinkingContent: null,
  activeToolCalls: [],
  planSteps: [],
  pendingApprovals: [],

  // ── base actions ────────────────────────────────────────
  setCurrentRun: (id) => set({ currentRunId: id }),

  setMessages: (messages) => set({ messages }),

  addMessage: (message) =>
    set((state) => ({
      messages: [...state.messages, message],
    })),

  updateStreamingMessage: (content) => set({ streamingMessage: content }),

  appendStreamingMessage: (content) =>
    set((state) => ({
      streamingMessage: state.streamingMessage + content,
    })),

  clearStreamingMessage: () => set({ streamingMessage: '' }),

  setIsStreaming: (isStreaming) => set({ isStreaming }),

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
        thinkingContent: null,
        activeToolCalls: [],
        planSteps: [],
        pendingApprovals: [],
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
      thinkingContent: null,
      activeToolCalls: [],
      planSteps: [],
      pendingApprovals: [],
    }),

  reset: () =>
    set({
      currentRunId: null,
      messages: [],
      streamingMessage: '',
      isStreaming: false,
      isLoadingConversation: false,
      thinkingContent: null,
      activeToolCalls: [],
      planSteps: [],
      pendingApprovals: [],
    }),

  // ── event-stream actions ──────────────────────────────────
  setThinkingContent: (content) => set({ thinkingContent: content }),

  addToolCall: (call) =>
    set((state) => ({
      activeToolCalls: [...state.activeToolCalls, call],
    })),

  updateToolCall: (toolId, updates) =>
    set((state) => ({
      activeToolCalls: state.activeToolCalls.map((tc) =>
        tc.tool_id === toolId ? { ...tc, ...updates } : tc
      ),
    })),

  clearToolCalls: () => set({ activeToolCalls: [] }),

  addOrUpdatePlanStep: (step) =>
    set((state) => {
      const idx = state.planSteps.findIndex((s) => s.step_number === step.step_number);
      if (idx >= 0) {
        const updated = [...state.planSteps];
        updated[idx] = step;
        return { planSteps: updated };
      }
      return { planSteps: [...state.planSteps, step] };
    }),

  clearPlanSteps: () => set({ planSteps: [] }),

  // ── approval actions ──────────────────────────────────────
  addPendingApproval: (approval) =>
    set((state) => ({
      pendingApprovals: [...state.pendingApprovals, approval],
    })),

  removePendingApproval: (toolId) =>
    set((state) => ({
      pendingApprovals: state.pendingApprovals.filter((p) => p.tool_id !== toolId),
    })),

  clearPendingApprovals: () => set({ pendingApprovals: [] }),
}));
