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
  } = useChatStore();

  const sendMessage = useCallback(
    async (content: string) => {
      if (!workspaceId) {
        console.error('No workspace selected');
        return;
      }

      const userMessage = {
        id: generateUUID(),
        role: MessageRole.USER,
        content,
        timestamp: new Date(),
      };
      addMessage(userMessage);

      setIsStreaming(true);
      clearStreamingMessage();

      await streamService.sendStreamingMessage({
        workspaceId,
        appId: appId || undefined,
        message: content,
        onRunStart: (runId) => {
          setCurrentRun(runId);
        },
        onChunk: (chunk) => {
          appendStreamingMessage(chunk);
        },
        onComplete: () => {
          const currentStreamingMessage = useChatStore.getState().streamingMessage;

          if (currentStreamingMessage) {
            const assistantMessage = {
              id: generateUUID(),
              role: MessageRole.ASSISTANT,
              content: currentStreamingMessage,
              timestamp: new Date(),
            };
            addMessage(assistantMessage);
          }

          clearStreamingMessage();
          setIsStreaming(false);
        },
        onError: (error) => {
          setIsStreaming(false);
          clearStreamingMessage();

          const errorMessage = {
            id: generateUUID(),
            role: MessageRole.ASSISTANT,
            content: `错误：${error.message}`,
            timestamp: new Date(),
          };
          addMessage(errorMessage);
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
    ]
  );

  const stopStreaming = useCallback(async () => {
    await streamService.abort(workspaceId);
    setIsStreaming(false);
    clearStreamingMessage();
  }, [workspaceId, setIsStreaming, clearStreamingMessage]);

  return {
    sendMessage,
    stopStreaming,
  };
};
