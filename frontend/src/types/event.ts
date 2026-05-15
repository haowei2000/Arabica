/**
 * Event-related TypeScript types.
 */

export interface Event {
  id: string;
  workspace_id: string;
  run_id: string | null;
  app_id?: string | null;
  user_id?: string | null;
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

export interface EventArchiveRequest {
  keep_last?: number | null;
  include_pinned?: boolean;
  event_types?: string[] | null;
  include_run_events?: boolean;
  dry_run?: boolean;
  strategy?: string;
  strategy_config?: Record<string, any>;
  max_events_per_archive_context?: number;
  max_chars_per_archive_context?: number;
  bulk_update_chunk_size?: number;
  reason?: string;
}

export interface EventArchiveResponse {
  scope: string;
  scope_id: string;
  dry_run: boolean;
  archived_count: number;
  skipped_count: number;
  active_count_before: number;
  archive_context_id: string | null;
  archive_path: string | null;
  reason: string;
  strategy: string;
  archive_context_ids: string[];
  archive_paths: string[];
  archive_chunks: number;
}
