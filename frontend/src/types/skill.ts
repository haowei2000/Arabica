/**
 * Skill-related TypeScript types.
 */

export interface SkillFileMetadata {
  s3_key: string;
  size: number;
  etag?: string | null;
  content_type?: string | null;
}

export interface Skill {
  id: string;
  user_id: string;
  name: string;
  description?: string | null;
  tags?: string[] | null;
  has_embedding: boolean;
  files?: Record<string, SkillFileMetadata> | null;
  created_at: string;
  updated_at?: string | null;
}

export interface SkillCreate {
  name: string;
  description?: string | null;
  /** Markdown content — stored in the Context table, not on the Skill row. */
  content: string;
  tags?: string[];
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
