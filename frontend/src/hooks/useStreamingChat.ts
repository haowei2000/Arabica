import { useCallback } from 'react';
import { useChatStore } from '@/stores/useChatStore';
import { streamService } from '@/services/streamService';
import { MessageRole } from '@/types/message';
import { generateUUID } from '@/utils/uuid';

export const useStreamingChat = (workspaceId: string, appId?: string | null) => {
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
    currentRunId,
  } = useChatStore();

  const sendMessage = useCallback(
    async (content: string) => {
      if (!workspaceId) {
        console.error('No workspace selected');
        return;
      }

      // Optimistic user message
      addMessage({
        id: generateUUID(),
        role: MessageRole.USER,
        content,
        timestamp: new Date(),
      });

      // Reset all streaming state
      setIsStreaming(true);
      clearStreamingMessage();
      setThinkingContent(null);
      clearToolCalls();
      clearPlanSteps();
      clearPendingApprovals();

      await streamService.sendStreamingMessage({
        workspaceId,
        appId: appId || undefined,
        message: content,

        // ── run lifecycle ───────────────────────────────
        onRunStart: (runId) => setCurrentRun(runId),

        // ── text stream ─────────────────────────────────
        onChunk: (chunk) => appendStreamingMessage(chunk),

        // ── thinking ────────────────────────────────────
        onThinking: (thinkingText) => setThinkingContent(thinkingText),

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

        // ── stream finished ─────────────────────────────
        onComplete: () => {
          // Snapshot current event-stream state so it persists on the
          // completed assistant message (visible after streaming ends).
          const state = useChatStore.getState();

          if (state.streamingMessage) {
            addMessage({
              id: generateUUID(),
              role: MessageRole.ASSISTANT,
              content: state.streamingMessage,
              timestamp: new Date(),
              thinkingContent: state.thinkingContent || undefined,
              toolCalls: state.activeToolCalls.length ? [...state.activeToolCalls] : undefined,
              planSteps: state.planSteps.length ? [...state.planSteps] : undefined,
            });
          }

          clearStreamingMessage();
          setThinkingContent(null);
          clearToolCalls();
          clearPlanSteps();
          clearPendingApprovals();
          setIsStreaming(false);
        },

        // ── stream errored ──────────────────────────────
        onError: (error) => {
          setIsStreaming(false);
          clearStreamingMessage();
          setThinkingContent(null);
          clearToolCalls();
          clearPlanSteps();
          clearPendingApprovals();

          addMessage({
            id: generateUUID(),
            role: MessageRole.ASSISTANT,
            content: `错误：${error.message}`,
            timestamp: new Date(),
          });
        },
      });
    },
    [
      workspaceId,
      appId,
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
    ]
  );

  const stopStreaming = useCallback(async () => {
    await streamService.abort(workspaceId);
    setIsStreaming(false);
    clearStreamingMessage();
    setThinkingContent(null);
    clearToolCalls();
    clearPlanSteps();
    clearPendingApprovals();
  }, [workspaceId, setIsStreaming, clearStreamingMessage, setThinkingContent, clearToolCalls, clearPlanSteps, clearPendingApprovals]);

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

  return { sendMessage, stopStreaming, approveToolCall };
};
