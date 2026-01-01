// API 响应基础类型
export interface APIResponse<T> {
  data: T;
  message?: string;
  error?: string;
}

// 分页响应
export interface PaginatedResponse<T> {
  total: number;
  items: T[];
  page: number;
  page_size: number;
}

// SSE 流式响应块
export interface StreamChunk {
  content?: string;
  delta?: string;
  text?: string;
  done?: boolean;
  error?: string;
}

// 聊天请求
export interface ChatRequest {
  query: string;
  conversation_id?: string;
  conversation_name?: string;
  from_source?: string;
  from_account_id?: string;
}

// 错误响应
export interface ErrorResponse {
  code: number;
  message: string;
  detail?: any;
}
