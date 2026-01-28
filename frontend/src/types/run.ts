export interface Run {
  id: string;
  workspace_id: string;
  app_id: string;
  user_id: string;
  parent_run_id?: string | null;
  status: string;
  trigger_type: string;
  input_data?: Record<string, any> | null;
  output_data?: Record<string, any> | null;
  error?: string | null;
  error_code?: string | null;
  waiting_for?: Record<string, any> | null;
  last_event_sequence: number;
  legacy_task_id?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at?: string | null;
}

export interface RunListResponse {
  total: number;
  items: Run[];
  page: number;
  page_size: number;
}

export interface RunStartRequest {
  message: string;
  app_id?: string;
  agent_template_id?: string;
  attachments?: Record<string, any>[];
  metadata?: Record<string, any>;
}

export interface RunStateMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
}

export interface RunState {
  messages: RunStateMessage[];
  tool_calls: Record<string, any>;
  status: string;
  last_sequence: number;
}
