/**
 * Tool-related TypeScript types.
 */

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

export interface UserToolListResponse {
  tools: UserTool[];
  total: number;
}

// ─── Tool Testing ─────────────────────────────────────────────────────────────

export interface UserToolTestResponse {
  success: boolean;
  message?: string | null;
  data?: Record<string, unknown> | null;
  error?: string | null;
  execution_time_ms?: number | null;
  tool_name: string;
  tool_id: string;
}

// ─── MCP Import ───────────────────────────────────────────────────────────────

export type MCPTransport = 'sse' | 'stdio';

export interface MCPServerConfig {
  transport: MCPTransport;
  url?: string | null;
  command?: string | null;
  args?: string[] | null;
  env?: Record<string, string> | null;
}

export interface MCPToolInfo {
  name: string;
  description: string;
  input_schema?: Record<string, unknown> | null;
}

export interface MCPProbeResponse {
  success: boolean;
  tools: MCPToolInfo[];
  error?: string | null;
}

export interface MCPImportRequest extends MCPServerConfig {
  tool_names: string[];
  is_public?: boolean;
}

export interface MCPImportResponse {
  imported: string[];
  skipped: string[];
  failed: string[];
  bundle_id?: string;
  bundle_name?: string;
}

// ─── Inner Tool Info (subset of UserTool, for tool pickers) ──────────────────

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

// ─── Tool Bundles ─────────────────────────────────────────────────────────────

export interface ToolBundle {
  id: string;
  bundle_type: string;
  source: string | null;
  user_id: string | null;
  name: string;
  description: string | null;
  tags: string[] | null;
  is_public: boolean;
  tool_ids: string[];
  tool_count: number;
  created_at: string;
  updated_at: string | null;
}

export interface ToolBundleListResponse {
  bundles: ToolBundle[];
  total: number;
}
