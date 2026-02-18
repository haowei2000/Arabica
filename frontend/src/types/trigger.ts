export interface Trigger {
  id: string;
  name: string;
  description?: string | null;
  event_type: string;
  condition_type: string;
  condition_value?: string | null;
  condition_field?: string | null;
  action_type: string;
  action_params?: Record<string, unknown> | null;
  priority: number;
  enabled: boolean;
  user_id?: string | null;
  workspace_id?: string | null;
  created_by?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface TriggerCreate {
  name: string;
  description?: string;
  event_type: string;
  condition_type: string;
  condition_value?: string;
  condition_field?: string;
  action_type: string;
  action_params?: Record<string, unknown>;
  priority?: number;
  enabled?: boolean;
}

export interface TriggerUpdate extends Partial<TriggerCreate> {}

export interface TriggerListResponse {
  total: number;
  items: Trigger[];
  page: number;
  page_size: number;
}
