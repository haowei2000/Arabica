// 消息角色枚举
export const MessageRole = {
  USER: 'user',
  ASSISTANT: 'assistant',
  SYSTEM: 'system',
} as const;

export type MessageRoleType = typeof MessageRole[keyof typeof MessageRole];

// 消息状态
export type MessageStatus = 'normal' | 'deleted' | 'error';

// 消息内容项
export interface MessageContent {
    role: MessageRoleType;
    content: string;
}

// 消息
export interface Message {
  id: string;
  app_id: string;
  conversation_id: string;
  query: string;
  answer: string;
    message: MessageContent[];
  status: MessageStatus;
  message_tokens: number;
  answer_tokens: number;
  from_source: string;
  from_end_user_id?: string;
  from_account_id?: string;
  model_provider?: string;
  model_id?: string;
  currency: string;
  created_at: string;
  updated_at: string;
}

// 创建消息请求
export interface MessageCreate {
  app_id: string;
  conversation_id: string;
  query: string;
    message: MessageContent[];
  answer?: string;
  status?: MessageStatus;
  from_source: string;
  from_end_user_id?: string;
  from_account_id?: string;
}

// 更新消息请求
export interface MessageUpdate {
  answer?: string;
  status?: MessageStatus;
  error?: string;
  answer_tokens?: number;
  total_price?: number;
}

// 简化的消息类型（用于 UI 显示）
export interface SimpleMessage {
  id: string;
  role: MessageRoleType;
  content: string;
  timestamp: Date;
  isStreaming?: boolean;
  /** Reasoning trace captured from AGENT_THINKING (assistant only). */
  thinkingContent?: string;
  /** Tool calls captured from TOOL_CALL / TOOL_RESULT (assistant only). */
  toolCalls?: import('@/types/events').ToolCallState[];
  /** Plan steps captured from AGENT_PLAN_STEP (assistant only). */
  planSteps?: import('@/types/events').AgentPlanStepPayload[];
}
