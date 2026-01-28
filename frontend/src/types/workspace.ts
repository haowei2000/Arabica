export interface Workspace {
  id: string;
  name: string;
  description?: string | null;
  owner_id: string;
  app_id?: string | null;
  visibility: string;
  is_shared: boolean;
  settings?: Record<string, unknown> | null;
  status: string;
  run_count: number;
  member_count: number;
  legacy_conversation_id?: string | null;
  created_at: string;
  updated_at?: string | null;
}

export interface WorkspaceListResponse {
  total: number;
  items: Workspace[];
  page: number;
  page_size: number;
}

export interface WorkspaceCreate {
  name: string;
  description?: string;
  app_id?: string;
  visibility?: 'private' | 'team' | 'public';
  settings?: Record<string, unknown>;
}
