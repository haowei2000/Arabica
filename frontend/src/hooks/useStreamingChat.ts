import { useCallback, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useChatStore } from '@/stores/useChatStore';
import { streamService } from '@/services/streamService';
import { MessageRole } from '@/types/message';
import { ErrorCategory } from '@/types/events';
import type { StreamError } from '@/types/events';
import { generateUUID } from '@/utils/uuid';

function categorizeError(error: Error): StreamError {
  const msg = error.message.toLowerCase();

  if (msg.includes('failed to fetch') || msg.includes('network') || msg.includes('connection lost') || msg.includes('reconnect')) {
    return { category: ErrorCategory.NETWORK, message: 'Network connection lost. Please check your connection.', retryable: true };
  }
  if (msg.includes('timeout') || msg.includes('timed out') || msg.includes('stuck_run_timeout')) {
    return { category: ErrorCategory.TIMEOUT, message: 'The operation timed out. The server may be under heavy load.', retryable: true };
  }
  if (msg.includes('run failed') || msg.includes('failed')) {
    return { category: ErrorCategory.RUN_FAILED, message: error.message, retryable: false };
  }
  return { category: ErrorCategory.UNKNOWN, message: error.message, retryable: true };
}

export const useStreamingChat = (workspaceId: string, appId?: string | null) => {
  const lastUserMessageRef = useRef<string>('');
  const queryClient = useQueryClient();

  const {
    addMessage,
    appendStreamingMessage,
    clearStreamingMessage,
    setIsStreaming,
    setCurrentRun,
    setThinkingContent,
    addToolCall,
    updateToolCall,
    clearToolCalls,
    addOrUpdatePlanStep,
    clearPlanSteps,
    addPendingApproval,
    clearPendingApprovals,
    removePendingApproval,
    addPendingQuery,
    removePendingQuery,
    clearPendingQueries,
    appendThinkingContent,
    addContextUsage,
    clearContextUsages,
    addOutcome,
    clearOutcomes,
    setStreamingTokens,
    clearStreamingTokens,
    setStreamError,
    streamError,
    currentRunId,
  } = useChatStore();

  /** Snapshot current streaming state into a persisted assistant message. */
  const saveStreamingStateAsMessage = useCallback(() => {
    const state = useChatStore.getState();
    const hasContent = state.streamingMessage || state.thinkingContent.length > 0 ||
      state.activeToolCalls.length > 0 || state.planSteps.length > 0 ||
      state.contextUsages.length > 0 || state.outcomes.length > 0;

    if (hasContent) {
      addMessage({
        id: generateUUID(),
        role: MessageRole.ASSISTANT,
        content: state.streamingMessage || '',
        timestamp: new Date(),
        thinkingContent: state.thinkingContent.length > 0 ? [...state.thinkingContent] : undefined,
        toolCalls: state.activeToolCalls.length > 0 ? [...state.activeToolCalls] : undefined,
        planSteps: state.planSteps.length > 0 ? [...state.planSteps] : undefined,
        contextUsages: state.contextUsages.length > 0 ? [...state.contextUsages] : undefined,
        outcomes: state.outcomes.length > 0 ? [...state.outcomes] : undefined,
        inputTokens: state.streamingInputTokens || undefined,
        outputTokens: state.streamingOutputTokens || undefined,
      });
    }
  }, [addMessage]);

  /** Clear all live streaming state after it has been saved. */
  const clearStreamingState = useCallback(() => {
    clearStreamingMessage();
    setThinkingContent(null);
    clearToolCalls();
    clearPlanSteps();
    clearPendingApprovals();
    clearPendingQueries();
    clearContextUsages();
    clearOutcomes();
    clearStreamingTokens();
  }, [clearStreamingMessage, setThinkingContent, clearToolCalls, clearPlanSteps, clearPendingApprovals, clearPendingQueries, clearContextUsages, clearOutcomes, clearStreamingTokens]);

  const sendMessage = useCallback(
    async (content: string, forcedTools?: string[]) => {
      if (!workspaceId) {
        return;
      }

      lastUserMessageRef.current = content;

      // Optimistic user message
      addMessage({
        id: generateUUID(),
        role: MessageRole.USER,
        content,
        timestamp: new Date(),
      });

      // Reset all streaming state
      setIsStreaming(true);
      clearStreamingState();
      setStreamError(null);

      await streamService.sendStreamingMessage({
        workspaceId,
        appId: appId || undefined,
        message: content,
        forcedTools,

        // ── run lifecycle ───────────────────────────────
        onRunStart: (runId) => setCurrentRun(runId),

        // ── text stream ─────────────────────────────────
        onChunk: (chunk) => appendStreamingMessage(chunk),

        // ── thinking ────────────────────────────────────
        onThinking: (thinkingText) => appendThinkingContent(thinkingText),

        // ── tool lifecycle ──────────────────────────────
        onToolCall: (event) => {
          addToolCall({
            tool_id: event.tool_id,
            tool_name: event.tool_name,
            arguments: event.arguments,
            status: 'pending',
          });
        },
        onToolResult: (event) => {
          updateToolCall(event.tool_id, {
            status: event.success ? 'completed' : 'error',
            result: event.result,
            error_message: event.error_message,
            execution_time_ms: event.execution_time_ms,
          });
        },

        // ── plan steps ──────────────────────────────────
        onPlanStep: (step) => addOrUpdatePlanStep(step),

        // ── HITL approval gate ──────────────────────────
        onToolPending: (event) => {
          addPendingApproval({
            tool_id: event.tool_id,
            tool_name: event.tool_name,
            arguments: event.arguments,
            reason: event.reason,
          });
        },

        // ── agent query (ask_for_user) ──────────────────
        onAgentQuery: (event) => {
          addPendingQuery({
            tool_id: event.tool_id,
            tool_name: event.tool_name,
            question: event.question,
          });
        },

        // ── run status ─────────────────────────────────
        onStatus: () => {
          queryClient.invalidateQueries({ queryKey: ['runs', workspaceId] });
        },

        // ── context events ────────────────────────────
        onUsingContext: (event) => addContextUsage(event),
        onPutOutcome: (event) => addOutcome(event),

        // ── token usage ───────────────────────────────
        onAgentMessage: ({ inputTokens, outputTokens }) =>
          setStreamingTokens(inputTokens, outputTokens),

        // ── stream finished ─────────────────────────────
        onComplete: () => {
          saveStreamingStateAsMessage();
          clearStreamingState();
          setIsStreaming(false);
        },

        // ── stream errored ──────────────────────────────
        onError: (error) => {
          // Persist whatever intermediate events we already have
          saveStreamingStateAsMessage();
          clearStreamingState();
          setIsStreaming(false);
          setStreamError(categorizeError(error));
        },
      });
    },
    [
      workspaceId,
      appId,
      appendStreamingMessage,
      setIsStreaming,
      setCurrentRun,
      addToolCall,
      updateToolCall,
      addOrUpdatePlanStep,
      addPendingApproval,
      addPendingQuery,
      appendThinkingContent,
      addContextUsage,
      addOutcome,
      setStreamingTokens,
      setStreamError,
      saveStreamingStateAsMessage,
      clearStreamingState,
      queryClient,
    ]
  );

  const stopStreaming = useCallback(async () => {
    await streamService.abort(workspaceId);
    saveStreamingStateAsMessage();
    clearStreamingState();
    setIsStreaming(false);
  }, [workspaceId, setIsStreaming, saveStreamingStateAsMessage, clearStreamingState]);

  /** Approve or deny a pending tool; POSTs to the resume endpoint. */
  const approveToolCall = useCallback(
    async (toolId: string, approved: boolean, toolResult?: string) => {
      if (!currentRunId) return;
      removePendingApproval(toolId);
      await streamService.resumeRun(workspaceId, currentRunId, {
        approval: approved,
        tool_result: toolResult,
      });
    },
    [workspaceId, currentRunId, removePendingApproval]
  );

  /** Submit the user's answer to an agent.query (ask_for_user) prompt. */
  const respondToQuery = useCallback(
    async (toolId: string, answer: string) => {
      if (!currentRunId) return;
      removePendingQuery(toolId);
      await streamService.submitFeedback(workspaceId, currentRunId, answer);
    },
    [workspaceId, currentRunId, removePendingQuery]
  );

  const retryLastMessage = useCallback(() => {
    if (lastUserMessageRef.current) {
      sendMessage(lastUserMessageRef.current);
    }
  }, [sendMessage]);

  return { sendMessage, stopStreaming, approveToolCall, respondToQuery, streamError, retryLastMessage };
};
