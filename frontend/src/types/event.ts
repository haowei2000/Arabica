/**
 * Event-related TypeScript types.
 */

export interface Event {
  id: string;
  workspace_id: string;
  run_id: string;
  app_id: string;
  user_id: string;
  sequence: number;
  event_type: string;
  payload: Record<string, any>;
  input_tokens?: number;
  output_tokens?: number;
  metadata?: Record<string, any> | null;
  executor_code?: string | null;
  created_at: string;
}

export interface EventListResponse {
  total: number;
  items: Event[];
  last_sequence: number | null;
}

export interface EventFilterParams {
  workspace_id?: string;
  run_id?: string;
  event_types?: string;
  skip?: number;
  limit?: number;
}
