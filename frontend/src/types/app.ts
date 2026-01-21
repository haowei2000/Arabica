// Agent 模板
export interface AgentTemplate {
  id: string;
  template_code: string;
  template_name: string;
  enabled: boolean;
  config?: Record<string, any>;
  version: number;
  created_at: string;
  updated_at?: string;
}

// App (Agent 实例)
export interface App {
  id: string;
  app_code: string;
  agent_template_id?: string;
  user_id?: string;
  enabled: boolean;
  config?: Record<string, any>;
  version: number;
  created_at: string;
  updated_at?: string;
}

// 创建 App 请求
export interface AppCreate {
  app_code: string;
  agent_template_code?: string;
  agent_template_id?: string;
  user_id?: string;
  enabled?: boolean;
  config?: Record<string, any>;
  version?: number;
}

// 更新 App 请求
export interface AppUpdate {
  agent_template_id?: string;
  enabled?: boolean;
  config?: Record<string, any>;
  version?: number;
}
