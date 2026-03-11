/**
 * Skill-related TypeScript types.
 */

export interface Skill {
  id: string;
  user_id: string;
  source_id?: string | null;
  workspace_id?: string | null;
  path?: string | null;
  name: string;
  description?: string | null;
  content: string;
  glance?: string | null;
  summary?: string | null;
  tags?: string[] | null;
  has_embedding: boolean;
  files?: Record<string, string> | null;
  created_at: string;
  updated_at?: string | null;
}

export interface SkillCreate {
  name: string;
  description?: string | null;
  content: string;
  tags?: string[];
  workspace_id?: string | null;
  source_id?: string | null;
  path?: string | null;
  meta?: Record<string, unknown> | null;
}

export interface SkillUpdate {
  name?: string;
  description?: string | null;
  content?: string;
  tags?: string[];
  meta?: Record<string, unknown> | null;
}

export interface SkillListResponse {
  total: number;
  items: Skill[];
  page: number;
  page_size: number;
}
