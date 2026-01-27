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

interface StartTaskResponse {
  task_id: string;
  conversation_id: string;
  message_id: string;
}

interface StreamEvent {
  event: 'metadata' | 'status' | 'chunk' | 'success' | 'error' | 'cancelled';
  data: any;
}

class StreamService {
  private controller: AbortController | null = null;
  private currentTaskId: string | null = null;

  /**
   * 发送流式消息（使用拆分端点）
   *
   * 流程:
   * 1. POST /chat/{appId}/start - 启动任务，获取 task_id
   * 2. GET /chat/{task_id}/messages - 订阅消息流
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

    const token = localStorage.getItem('access_token');

    try {
      // Step 1: Start the task
      const startResponse = await fetch(
        `${API_BASE_URL}${API_ENDPOINTS.CHAT.START(appId)}`,
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
        }
      );

      if (!startResponse.ok) {
        throw new Error(`Failed to start task: ${startResponse.status}`);
      }

      const taskInfo: StartTaskResponse = await startResponse.json();
      this.currentTaskId = taskInfo.task_id;
      console.log('✅ Task started:', taskInfo);

      // Step 2: Subscribe to messages stream
      this.controller = new AbortController();

      const messagesResponse = await fetch(
        `${API_BASE_URL}${API_ENDPOINTS.CHAT.MESSAGES(taskInfo.task_id)}`,
        {
          method: 'GET',
          headers: {
            Authorization: `Bearer ${token}`,
          },
          signal: this.controller.signal,
        }
      );

      if (!messagesResponse.ok) {
        throw new Error(`Failed to get messages: ${messagesResponse.status}`);
      }

      const reader = messagesResponse.body?.getReader();
      const decoder = new TextDecoder();

      if (!reader) {
        throw new Error('No reader available');
      }

      let buffer = '';
      const returnedConversationId = taskInfo.conversation_id;

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          this.currentTaskId = null;
          onComplete(returnedConversationId);
          break;
        }

        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmedLine = line.trim();

          if (!trimmedLine) continue;

          if (trimmedLine.startsWith('data: ')) {
            const data = trimmedLine.slice(6).trim();

            if (data === '[DONE]') {
              this.currentTaskId = null;
              onComplete(returnedConversationId);
              return;
            }

            try {
              const parsed: StreamEvent = JSON.parse(data);
              console.log('📥 SSE Event:', parsed.event, parsed.data);

              switch (parsed.event) {
                case 'metadata':
                  // Already have metadata from start response
                  console.log('📋 Metadata:', parsed.data);
                  break;

                case 'status':
                  console.log('📊 Status:', parsed.data);
                  if (options.onStatus) {
                    options.onStatus(parsed.data);
                  }
                  break;

                case 'chunk':
                  const chunkData = parsed.data as string;

                  if (chunkData.startsWith('Token: ')) {
                    const token = chunkData.slice(7);
                    if (token) {
                      console.log('🔤 Token:', token);
                      onChunk(token);
                    }
                  } else if (chunkData.startsWith('Full input: ')) {
                    console.log('📝 Full input received');
                  } else if (chunkData && !chunkData.startsWith('Input: ')) {
                    console.log('💬 Other chunk:', chunkData);
                    onChunk(chunkData);
                  }
                  break;

                case 'success':
                  console.log('✅ Stream completed successfully');
                  this.currentTaskId = null;
                  onComplete(returnedConversationId);
                  return;

                case 'error':
                  console.error('❌ Stream error:', parsed.data);
                  throw new Error(typeof parsed.data === 'string' ? parsed.data : 'Stream error');

                case 'cancelled':
                  console.log('🛑 Task cancelled:', parsed.data);
                  this.currentTaskId = null;
                  onComplete(returnedConversationId);
                  return;

                default:
                  console.warn('⚠️  Unknown event type:', parsed.event);
                  break;
              }
            } catch (parseError) {
              console.error('Failed to parse SSE data:', parseError, data);
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
          `${API_BASE_URL}${API_ENDPOINTS.CHAT.CANCEL(this.currentTaskId)}`,
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

  /**
   * 获取当前任务 ID
   */
  getCurrentTaskId(): string | null {
    return this.currentTaskId;
  }
}

export const streamService = new StreamService();
