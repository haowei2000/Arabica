import type { Message } from './message';

// 对话模式
export type ConversationMode = 'completion' | 'chat';

// 对话状态
export type ConversationStatus = 'normal' | 'archived' | 'deleted';

// 对话
export interface Conversation {
  id: string;
  app_id: string;
  name: string;
  mode: ConversationMode;
  status: ConversationStatus;
  dialogue_count: number;
  summary?: string;
  from_source: string;
  from_end_user_id?: string;
  from_account_id?: string;
  created_at: string;
  updated_at: string;
  is_deleted: boolean;
}

// 创建对话请求
export interface ConversationCreate {
  app_id: string;
  name: string;
  status?: ConversationStatus;
  summary?: string;
  from_source: string;
  from_account_id?: string;
  from_end_user_id?: string;
}

// 更新对话请求
export interface ConversationUpdate {
  name?: string;
  summary?: string;
  status?: ConversationStatus;
  read_at?: string;
  read_account_id?: string;
}

// 对话详情（包含消息列表）
export interface ConversationDetail extends Conversation {
  messages: Message[];
}
