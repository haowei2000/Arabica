// LLM model types

export interface ChatModel {
  id: string;
  name: string;
  description?: string | null;
  user_id?: string | null;
  provider: string;
  model_id: string;
  base_url?: string | null;
  api_key_ref?: string | null;
  max_tokens?: number | null;
  context_window?: number | null;
  supports_vision: boolean;
  supports_function_call: boolean;
  supports_streaming: boolean;
  default_temperature?: number | null;
  default_top_p?: number | null;
  default_max_tokens?: number | null;
  input_price?: number | null;
  output_price?: number | null;
  currency: string;
  is_system: boolean;
  is_default: boolean;
  enabled: boolean;
  config?: Record<string, unknown> | null;
  meta?: Record<string, unknown> | null;
  created_at: string;
  updated_at?: string | null;
}

export interface ChatModelCreate {
  name: string;
  description?: string | null;
  provider: string;
  model_id: string;
  base_url?: string | null;
  api_key_ref?: string | null;
  max_tokens?: number | null;
  context_window?: number | null;
  supports_vision?: boolean;
  supports_function_call?: boolean;
  supports_streaming?: boolean;
  default_temperature?: number | null;
  default_top_p?: number | null;
  default_max_tokens?: number | null;
  input_price?: number | null;
  output_price?: number | null;
  currency?: string;
  is_default?: boolean;
  enabled?: boolean;
}

export type ChatModelUpdate = Partial<ChatModelCreate>;

export interface ChatModelListResponse {
  total: number;
  items: ChatModel[];
  page: number;
  page_size: number;
}

// Embedding model types

export interface EmbeddingModel {
  id: string;
  name: string;
  description?: string | null;
  user_id?: string | null;
  provider: string;
  model_id: string;
  base_url?: string | null;
  api_key_ref?: string | null;
  dimension: number;
  max_tokens?: number | null;
  supports_batch: boolean;
  batch_size: number;
  normalize: boolean;
  distance_metric: string;
  price?: number | null;
  currency: string;
  is_system: boolean;
  is_default: boolean;
  enabled: boolean;
  config?: Record<string, unknown> | null;
  meta?: Record<string, unknown> | null;
  created_at: string;
  updated_at?: string | null;
}

export interface EmbeddingModelCreate {
  name: string;
  description?: string | null;
  provider: string;
  model_id: string;
  base_url?: string | null;
  api_key_ref?: string | null;
  dimension: number;
  max_tokens?: number | null;
  supports_batch?: boolean;
  batch_size?: number;
  normalize?: boolean;
  distance_metric?: string;
  price?: number | null;
  currency?: string;
  is_default?: boolean;
  enabled?: boolean;
}

export type EmbeddingModelUpdate = Partial<EmbeddingModelCreate>;

export interface EmbeddingModelListResponse {
  total: number;
  items: EmbeddingModel[];
  page: number;
  page_size: number;
}
