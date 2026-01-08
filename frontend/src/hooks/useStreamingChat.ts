import { useCallback, useRef } from 'react';
import { useChatStore } from '@/stores/useChatStore';
import { streamService } from '@/services/streamService';
import { MessageRole } from '@/types/message';
import { generateUUID } from '@/utils/uuid';

export const useStreamingChat = (appId: string) => {
  const {
    currentConversationId,
    addMessage,
    appendStreamingMessage,
    clearStreamingMessage,
    setIsStreaming,
    setCurrentConversation,
  } = useChatStore();

  const conversationIdRef = useRef<string | null>(currentConversationId);

  const sendMessage = useCallback(
    async (content: string, conversationName?: string) => {
      if (!appId) {
        console.error('No app selected');
        return;
      }

      // 添加用户消息
      const userMessage = {
        id: generateUUID(),
        role: MessageRole.USER,
        content,
        timestamp: new Date(),
      };
      addMessage(userMessage);

      // 开始流式响应
      setIsStreaming(true);
      clearStreamingMessage();

      await streamService.sendStreamingMessage({
        appId,
        query: content,
        conversationId: conversationIdRef.current || undefined,
        conversationName,
        onChunk: (chunk) => {
          console.log('📨 Received chunk, appending:', chunk);
          appendStreamingMessage(chunk);
        },
        onComplete: (newConversationId) => {
          console.log('✅ Stream complete, conversation ID:', newConversationId);

          // 如果是新建对话，更新 conversationId
          if (newConversationId && !conversationIdRef.current) {
            conversationIdRef.current = newConversationId;
            setCurrentConversation(newConversationId);
          }

          // 获取当前的流式消息内容
          const currentStreamingMessage = useChatStore.getState().streamingMessage;
          console.log('💾 Saving assistant message:', currentStreamingMessage);

          // 保存完整的 AI 消息
          if (currentStreamingMessage) {
            const assistantMessage = {
              id: generateUUID(),
              role: MessageRole.ASSISTANT,
              content: currentStreamingMessage,
              timestamp: new Date(),
            };
            addMessage(assistantMessage);
          } else {
            console.warn('⚠️  No streaming message to save!');
          }

          clearStreamingMessage();
          setIsStreaming(false);
        },
        onError: (error) => {
          console.error('Streaming error:', error);
          setIsStreaming(false);
          clearStreamingMessage();

          // 添加错误消息
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
      appId,
      addMessage,
      appendStreamingMessage,
      clearStreamingMessage,
      setIsStreaming,
      setCurrentConversation,
    ]
  );

  const stopStreaming = useCallback(async () => {
    // Abort the stream and cancel the backend task
    await streamService.abort();
    setIsStreaming(false);

    // Clear any partial streaming message
    clearStreamingMessage();

    console.log('🛑 Streaming stopped and task cancelled');
  }, [setIsStreaming, clearStreamingMessage]);

  return {
    sendMessage,
    stopStreaming,
  };
};
