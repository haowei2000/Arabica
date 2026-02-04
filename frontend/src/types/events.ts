/**
 * Typed event payloads – mirrors the backend *Payload Pydantic schemas.
 * These are the shapes that arrive inside every AgentEvent.payload
 * delivered over the SSE stream.
 */

export interface AgentTokenPayload {
  token: string;
  token_index: number;
  is_final: boolean;
}

export interface AgentThinkingPayload {
  content: string;
}

export interface AgentPlanStepPayload {
  step_number: number;
  step_description: string;
  status: 'pending' | 'in_progress' | 'completed' | 'failed';
  output: string | null;
}

export interface ToolCallPayload {
  tool_name: string;
  tool_id: string;
  arguments: Record<string, unknown>;
}

export interface ToolResultPayload {
  tool_name: string;
  tool_id: string;
  result: unknown;
  success: boolean;
  error_message: string | null;
  execution_time_ms: number | null;
}

/**
 * Runtime state for a single tool invocation, tracked from
 * TOOL_CALL  →  TOOL_RESULT / TOOL_ERROR.
 */
export interface ToolCallState {
  tool_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  status: 'pending' | 'completed' | 'error';
  result?: unknown;
  error_message?: string | null;
  execution_time_ms?: number | null;
}

export interface ToolPendingPayload {
  tool_name: string;
  tool_id: string;
  reason: string;
  requires_approval: boolean;
  arguments: Record<string, unknown>;
}

/**
 * Runtime state for a single pending-approval tool, tracked from
 * TOOL_PENDING  →  user approve / deny  →  cleared on resume.
 */
export interface ToolPendingState {
  tool_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  reason: string;
}
