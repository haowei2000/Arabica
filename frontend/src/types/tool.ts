/**
 * Tool-related TypeScript types.
 *
 * Mirrors the backend Pydantic schemas in:
 * - schemas/tools/tool_template.py (ToolTemplate)
 * - schemas/tools/user_tool.py (UserTool*)
 */

// ─── Tool Templates ──────────────────────────────────────────────────────────

export interface ToolTemplate {
  id: string;
  name: string;
  description: string;
  execution_mode: string;
  inner_tool_name?: string | null;
  category: string;
  tags: string[];
  source: string;
  template: Record<string, unknown>;
}

export interface ToolTemplateListResponse {
  templates: ToolTemplate[];
  total: number;
}

// ─── User Tools ──────────────────────────────────────────────────────────────

export interface UserTool {
  id: string;
  tool_code: string;
  user_id: string | null;
  workspace_id?: string | null;
  name: string;
  display_name: string;
  description: string;
  execution_mode: string;
  inner_tool_name?: string | null;
  parameter_mapping?: Record<string, string> | null;
  tool_type: string;
  input_schema: Record<string, unknown>;
  output_schema?: Record<string, unknown> | null;
  category: string;
  tags?: string[] | null;
  version: number | string;
  timeout: number;
  enabled: boolean;
  is_public: boolean;
  verified: boolean;
  usage_count: number;
  last_used_at?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface UserToolCreate {
  name: string;
  display_name: string;
  description: string;
  execution_mode: string;
  input_schema: Record<string, unknown>;
  inner_tool_name?: string | null;
  parameter_mapping?: Record<string, string> | null;
  category?: string;
  tags?: string[];
  timeout?: number;
  enabled?: boolean;
  is_public?: boolean;
}

export type UserToolUpdate = Partial<UserToolCreate>;

export interface UserToolListResponse {
  tools: UserTool[];
  total: number;
}

// ─── Tool Testing ─────────────────────────────────────────────────────────────

export interface UserToolTestRequest {
  parameters: Record<string, unknown>;
}

export interface UserToolTestResponse {
  success: boolean;
  message?: string | null;
  data?: Record<string, unknown> | null;
  error?: string | null;
  execution_time_ms?: number | null;
  tool_name: string;
  tool_id: string;
}

// ─── Tool Export / Import ─────────────────────────────────────────────────────

export interface ToolExportData {
  name: string;
  display_name: string;
  description: string;
  execution_mode: string;
  input_schema: Record<string, unknown>;
  output_schema?: Record<string, unknown> | null;
  category: string;
  tags: string[];
  timeout: number;
  inner_tool_name?: string | null;
  parameter_mapping?: Record<string, string> | null;
  chain?: Record<string, unknown>[] | null;
}

// ─── Inner Tools ──────────────────────────────────────────────────────────────

export interface InnerToolInfo {
  name: string;
  display_name: string;
  description: string;
  category: string;
  tags: string[];
  timeout: number;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
}

export interface InnerToolListResponse {
  inner_tools: InnerToolInfo[];
  total: number;
}
