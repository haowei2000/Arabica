export interface ContextEntry {
  id: string;
  user_id: string;
  source_id: string | null;
  path: string | null;
  s3_key: string | null;
  context_type: string;
  glance: string | null;
  content: string;
  tags: string[] | null;
  meta: Record<string, unknown> | null;
  importance: number | null;
  created_at: string;
  updated_at: string | null;
}

export interface ContextListResponse {
  total: number;
  items: ContextEntry[];
  page: number;
  page_size: number;
}

export type EntityContextType = 'skill' | 'tool' | 'knowledge' | 'document' | 'memory';
