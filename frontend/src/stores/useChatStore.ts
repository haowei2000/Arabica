import { create } from 'zustand';
import type { MessageRoleType, SimpleMessage } from '@/types/message';
import type { ToolCallState, ToolPendingState, AgentPlanStepPayload, StreamError, ContextUsageState, OutcomeState, AgentQueryState } from '@/types/events';
import { runService } from '@/services/runService';

interface ChatState {
  currentRunId: string | null;
  messages: SimpleMessage[];
  streamingMessage: string;
  isStreaming: boolean;
  isLoadingConversation: boolean;

  // ── live event-stream state (cleared on stream end) ──────
  thinkingContent: string[];
  activeToolCalls: ToolCallState[];
  planSteps: AgentPlanStepPayload[];
  pendingApprovals: ToolPendingState[];
  pendingQueries: AgentQueryState[];
  contextUsages: ContextUsageState[];
  outcomes: OutcomeState[];

  // ── token usage for current streaming turn ───────────────
  streamingInputTokens: number;
  streamingOutputTokens: number;

  // ── reconnect & error state ─────────────────────────────
  lastEventTimestamp: number | null;
  reconnecting: boolean;
  reconnectAttempt: number;
  streamError: StreamError | null;

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
  appendThinkingContent: (content: string) => void;
  addToolCall: (call: ToolCallState) => void;
  updateToolCall: (toolId: string, updates: Partial<ToolCallState>) => void;
  clearToolCalls: () => void;
  addOrUpdatePlanStep: (step: AgentPlanStepPayload) => void;
  clearPlanSteps: () => void;
  addContextUsage: (usage: ContextUsageState) => void;
  clearContextUsages: () => void;
  addOutcome: (outcome: OutcomeState) => void;
  clearOutcomes: () => void;

  // ── approval actions ──────────────────────────────────────
  addPendingApproval: (approval: ToolPendingState) => void;
  removePendingApproval: (toolId: string) => void;
  clearPendingApprovals: () => void;

  // ── query actions (ask_for_user) ──────────────────────────
  addPendingQuery: (query: AgentQueryState) => void;
  removePendingQuery: (toolId: string) => void;
  clearPendingQueries: () => void;

  // ── token actions ─────────────────────────────────────
  setStreamingTokens: (inputTokens: number, outputTokens: number) => void;
  clearStreamingTokens: () => void;

  // ── reconnect & error actions ───────────────────────────
  setLastEventTimestamp: (ts: number | null) => void;
  setReconnecting: (reconnecting: boolean) => void;
  setReconnectAttempt: (attempt: number) => void;
  setStreamError: (error: StreamError | null) => void;
}

export const useChatStore = create<ChatState>((set) => ({
  currentRunId: null,
  messages: [],
  streamingMessage: '',
  isStreaming: false,
  isLoadingConversation: false,

  thinkingContent: [],
  activeToolCalls: [],
  planSteps: [],
  pendingApprovals: [],
  pendingQueries: [],
  contextUsages: [],
  outcomes: [],

  streamingInputTokens: 0,
  streamingOutputTokens: 0,

  lastEventTimestamp: null,
  reconnecting: false,
  reconnectAttempt: 0,
  streamError: null,

  // ── base actions ────────────────────────────────────────
  setCurrentRun: (id) => set({ currentRunId: id }),

  setMessages: (messages) => set({ messages }),

  addMessage: (message) =>
    set((state) => {
      console.log('addMessage:', message);
      return {
        messages: [...state.messages, message],
      };
    }),

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
        thinkingContent: [],
        activeToolCalls: [],
        planSteps: [],
        pendingApprovals: [],
        pendingQueries: [],
        contextUsages: [],
        outcomes: [],
        lastEventTimestamp: null,
        reconnecting: false,
        reconnectAttempt: 0,
        streamError: null,
      });
    } catch (error) {
      set({ isLoadingConversation: false });
    }
  },

  startNewRun: () =>
    set({
      currentRunId: null,
      messages: [],
      streamingMessage: '',
      thinkingContent: [],
      activeToolCalls: [],
      planSteps: [],
      pendingApprovals: [],
      pendingQueries: [],
      contextUsages: [],
      outcomes: [],
      streamingInputTokens: 0,
      streamingOutputTokens: 0,
      lastEventTimestamp: null,
      reconnecting: false,
      reconnectAttempt: 0,
      streamError: null,
    }),

  reset: () =>
    set({
      currentRunId: null,
      messages: [],
      streamingMessage: '',
      isStreaming: false,
      isLoadingConversation: false,
      thinkingContent: [],
      activeToolCalls: [],
      planSteps: [],
      pendingApprovals: [],
      pendingQueries: [],
      contextUsages: [],
      outcomes: [],
      lastEventTimestamp: null,
      reconnecting: false,
      reconnectAttempt: 0,
      streamError: null,
    }),

  // ── event-stream actions ──────────────────────────────────
  setThinkingContent: (content) => set({ thinkingContent: content ? [content] : [] }),

  appendThinkingContent: (content) =>
    set((state) => ({
      thinkingContent: [...state.thinkingContent, content],
    })),

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

  addContextUsage: (usage) =>
    set((state) => ({
      contextUsages: [...state.contextUsages, usage],
    })),

  clearContextUsages: () => set({ contextUsages: [] }),

  addOutcome: (outcome) =>
    set((state) => ({
      outcomes: [...state.outcomes, outcome],
    })),

  clearOutcomes: () => set({ outcomes: [] }),

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

  // ── query actions (ask_for_user) ──────────────────────────
  addPendingQuery: (query) =>
    set((state) => ({
      pendingQueries: [...state.pendingQueries, query],
    })),

  removePendingQuery: (toolId) =>
    set((state) => ({
      pendingQueries: state.pendingQueries.filter((q) => q.tool_id !== toolId),
    })),

  clearPendingQueries: () => set({ pendingQueries: [] }),

  // ── token actions ─────────────────────────────────────
  setStreamingTokens: (inputTokens, outputTokens) =>
    set({ streamingInputTokens: inputTokens, streamingOutputTokens: outputTokens }),
  clearStreamingTokens: () => set({ streamingInputTokens: 0, streamingOutputTokens: 0 }),

  // ── reconnect & error actions ───────────────────────────
  setLastEventTimestamp: (ts) => set({ lastEventTimestamp: ts }),
  setReconnecting: (reconnecting) => set({ reconnecting }),
  setReconnectAttempt: (attempt) => set({ reconnectAttempt: attempt }),
  setStreamError: (error) => set({ streamError: error }),
}));
