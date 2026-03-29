/**
 * Event types and payloads - mirrors backend EventPublisher format.
 *
 * All events from the backend have the same structure:
 * {
 *   id: string,
 *   event_type: EventType,
 *   workspace_id: string,
 *   run_id: string | null,
 *   user_id: string | null,
 *   payload: EventPayload,
 *   sequence: number,
 *   created_at: string (ISO timestamp)
 * }
 */

// ============================================================================
// Event Types (matches backend EventType enum)
// ============================================================================

export const EventType = {
  // User events
  USER_MESSAGE: 'user.message',
  USER_FEEDBACK: 'user.feedback',

  // Agent events
  AGENT_TOKEN: 'agent.token',
  AGENT_MESSAGE: 'agent.message',
  AGENT_THINKING: 'agent.thinking',
  AGENT_HEARTBEAT: 'agent.heartbeat',
  AGENT_PLAN_STEP: 'agent.plan.step',
  AGENT_QUERY: 'agent.query',

  // Tool events
  TOOL_CALL: 'tool.call',
  TOOL_RESULT: 'tool.result',
  TOOL_ERROR: 'tool.error',
  TOOL_PENDING: 'tool.pending',

  // Context events
  USING_CONTEXT: 'context.using',
  PUT_OUTCOME: 'context.put_outcome',

  // Run lifecycle events
  RUN_STATE_CHANGE: 'run.state.change',
  RUN_FAILED: 'run.failed',
  RUN_CANCELLED: 'run.cancelled',
  RUN_COMPLETED: 'run.completed',

  // Internal worker-routing event; carries the original event to the executor.
  // Bears the run's total token usage in its input_tokens / output_tokens fields.
  TO_EXECUTOR: 'to.executor',
} as const;

export type EventType = (typeof EventType)[keyof typeof EventType];

// ============================================================================
// Event Payloads (what's inside event.payload)
// ============================================================================

export interface AgentTokenPayload {
  token: string;
  token_index: number;
  is_final: boolean;
}

export interface AgentMessagePayload {
  content: string;
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

export interface ToolPendingPayload {
  tool_name: string;
  tool_id: string;
  reason: string;
  requires_approval: boolean;
  arguments: Record<string, unknown>;
}

export interface AgentQueryPayload {
  question: string;
  tool_name: string;
  tool_id: string;
}

export interface UsingContextPayload {
  context_type: string;       // e.g. "knowledge", "memory", "skill"
  context_name: string;       // human-readable name of the context source
  query?: string;             // the retrieval query, if any
  results_count?: number;     // number of results retrieved
  details?: Record<string, unknown>;
  tool_names?: string[];      // tool names loaded for this run
  tool_count?: number;        // number of tools loaded
}

export interface PutOutcomePayload {
  outcome_type: string;       // e.g. "file", "artifact", "result"
  outcome_name: string;       // human-readable name
  summary?: string;           // short description of what was produced
  details?: Record<string, unknown>;
}

export interface RunStateChangePayload {
  previous_state: string;
  new_state: string;
  reason: string;
  triggered_by: string;
}

export interface UserMessagePayload {
  content: string;
  attachments?: unknown[];
}

// ============================================================================
// Unified Event Structure (from EventPublisher)
// ============================================================================

export interface StreamEvent<T = Record<string, unknown>> {
  id: string;
  event_type: EventType;
  workspace_id: string;
  run_id: string | null;
  user_id: string | null;
  payload: T;
  sequence: number;
  created_at: string;
}

// Type guards for payload types
export function isAgentTokenPayload(payload: unknown): payload is AgentTokenPayload {
  return (
    typeof payload === 'object' &&
    payload !== null &&
    'token' in payload &&
    'token_index' in payload
  );
}

export function isAgentMessagePayload(payload: unknown): payload is AgentMessagePayload {
  return (
    typeof payload === 'object' &&
    payload !== null &&
    'content' in payload
  );
}

export function isToolCallPayload(payload: unknown): payload is ToolCallPayload {
  return (
    typeof payload === 'object' &&
    payload !== null &&
    'tool_name' in payload &&
    'tool_id' in payload
  );
}

export function isRunStateChangePayload(payload: unknown): payload is RunStateChangePayload {
  return (
    typeof payload === 'object' &&
    payload !== null &&
    'new_state' in payload
  );
}

// ============================================================================
// Runtime State (for UI components)
// ============================================================================

/**
 * Runtime state for a single tool invocation.
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

/**
 * Runtime state for a pending-approval tool.
 */
export interface ToolPendingState {
  tool_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  reason: string;
}

/**
 * Runtime state for a pending user-input query (ask_for_user).
 */
export interface AgentQueryState {
  tool_id: string;
  tool_name: string;
  question: string;
}

/**
 * Runtime state for a context retrieval event.
 */
export interface ContextUsageState {
  context_type: string;
  context_name: string;
  query?: string;
  results_count?: number;
  details?: Record<string, unknown>;
  tool_names?: string[];
  tool_count?: number;
}

/**
 * Runtime state for an outcome produced by the agent.
 */
export interface OutcomeState {
  outcome_type: string;
  outcome_name: string;
  summary?: string;
  details?: Record<string, unknown>;
}

// ============================================================================
// Error Categories (for categorized error display)
// ============================================================================

export enum ErrorCategory {
  NETWORK = 'network',
  TIMEOUT = 'timeout',
  RUN_FAILED = 'run_failed',
  UNKNOWN = 'unknown',
}

export interface StreamError {
  category: ErrorCategory;
  message: string;
  retryable: boolean;
}
