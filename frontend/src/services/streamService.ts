import { API_BASE_URL, API_ENDPOINTS } from '@/constants/api';

export interface StreamOptions {
  appId: string;
  query: string;
  conversationId?: string;
  conversationName?: string;
  onChunk: (content: string) => void;
  onComplete: (conversationId?: string) => void;
  onError: (error: Error) => void;
  onStatus?: (status: string) => void;
}

interface StreamEvent {
  event: 'metadata' | 'status' | 'chunk' | 'success' | 'error';
  data: any;
}

interface MetadataEvent {
  conversation_id: string;
  message_id: string;
}

class StreamService {
  private controller: AbortController | null = null;
  private currentTaskId: string | null = null;

  /**
   * 发送流式消息
   */
  async sendStreamingMessage(options: StreamOptions): Promise<void> {
    const {
      appId,
      query,
      conversationId,
      conversationName,
      onChunk,
      onComplete,
      onError,
    } = options;

    this.controller = new AbortController();
    const token = localStorage.getItem('access_token');

    try {
      const response = await fetch(
        `${API_BASE_URL}${API_ENDPOINTS.CHAT.STREAM(appId)}`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            query,
            conversation_id: conversationId,
            conversation_name: conversationName,
            from_source: 'web',
          }),
          signal: this.controller.signal,
        }
      );

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();

      if (!reader) {
        throw new Error('No reader available');
      }

      let buffer = '';
      let returnedConversationId: string | undefined;

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          this.currentTaskId = null; // Clear task ID on completion
          onComplete(returnedConversationId);
          break;
        }

        // 解码数据块
        buffer += decoder.decode(value, { stream: true });

        // 按行分割
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // 保留最后一个不完整的行

        for (const line of lines) {
          const trimmedLine = line.trim();

          if (!trimmedLine) continue;

          // SSE 格式: "data: {...}"
          if (trimmedLine.startsWith('data: ')) {
            const data = trimmedLine.slice(6).trim();

            if (data === '[DONE]') {
              this.currentTaskId = null; // Clear task ID
              onComplete(returnedConversationId);
              return;
            }

            try {
              const parsed: StreamEvent = JSON.parse(data);
              console.log('📥 SSE Event:', parsed.event, parsed.data);

              switch (parsed.event) {
                case 'metadata':
                  // 提取 conversation_id 和 message_id (task_id)
                  const metadata = parsed.data as MetadataEvent;
                  if (metadata.conversation_id && !conversationId) {
                    returnedConversationId = metadata.conversation_id;
                    console.log('✅ Conversation ID:', returnedConversationId);
                  }
                  if (metadata.message_id) {
                    this.currentTaskId = metadata.message_id;
                    console.log('✅ Task ID (message_id):', this.currentTaskId);
                  }
                  break;

                case 'status':
                  // 状态消息（可选处理）
                  console.log('📊 Status:', parsed.data);
                  if (options.onStatus) {
                    options.onStatus(parsed.data);
                  }
                  break;

                case 'chunk':
                  // 处理 chunk 数据
                  const chunkData = parsed.data as string;

                  // 提取 token（格式: "Token: xxx"）
                  if (chunkData.startsWith('Token: ')) {
                    const token = chunkData.slice(7); // 移除 "Token: " 前缀
                    if (token) {
                      console.log('🔤 Token:', token);
                      onChunk(token);
                    }
                  }
                  // 或者是完整消息（格式: "Full message: xxx"）
                  else if (chunkData.startsWith('Full message: ')) {
                    console.log('📝 Full message received');
                    // 忽略完整消息，因为我们已经通过 token 累积了完整内容
                    // 或者可以用来验证
                  }
                  // 或者是其他格式的 chunk
                  else if (chunkData && !chunkData.startsWith('Input: ')) {
                    console.log('💬 Other chunk:', chunkData);
                    onChunk(chunkData);
                  }
                  break;

                case 'success':
                  // 成功完成
                  console.log('✅ Stream completed successfully');
                  this.currentTaskId = null; // Clear task ID on success
                  onComplete(returnedConversationId);
                  return;

                case 'error':
                  // 错误事件
                  console.error('❌ Stream error:', parsed.data);
                  throw new Error(typeof parsed.data === 'string' ? parsed.data : 'Stream error');

                case 'cancelled':
                  // 任务被取消
                  console.log('🛑 Task cancelled:', parsed.data);
                  this.currentTaskId = null; // Clear task ID
                  onComplete(returnedConversationId);
                  return;

                default:
                  console.warn('⚠️  Unknown event type:', parsed.event);
                  break;
              }
            } catch (parseError) {
              console.error('Failed to parse SSE data:', parseError, data);
              // 继续处理下一行，不中断整个流
            }
          }
        }
      }
    } catch (error) {
      if (error instanceof Error && error.name !== 'AbortError') {
        onError(error);
      }
    } finally {
      this.controller = null;
    }
  }

  /**
   * 中止当前流式请求并取消后端任务
   */
  async abort(): Promise<void> {
    // 1. Send cancel request to backend if we have a task ID
    if (this.currentTaskId) {
      try {
        const token = localStorage.getItem('access_token');
        const response = await fetch(
          `${API_BASE_URL}/api/chat/${this.currentTaskId}/cancel`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${token}`,
            },
          }
        );

        if (response.ok) {
          const result = await response.json();
          console.log('✅ Task cancelled on backend:', result);
        } else {
          console.warn('⚠️ Failed to cancel task on backend:', response.status);
        }
      } catch (error) {
        console.error('❌ Error cancelling task on backend:', error);
      } finally {
        this.currentTaskId = null;
      }
    }

    // 2. Abort the fetch request
    if (this.controller) {
      this.controller.abort();
      this.controller = null;
    }
  }
}

// 导出单例
export const streamService = new StreamService();
