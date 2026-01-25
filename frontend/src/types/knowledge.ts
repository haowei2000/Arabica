// Knowledge base type
export interface Knowledge {
    id: string;
    name: string;
    description?: string;
    user_id: string;
    owner_id?: string;
    provider: string;
    indexing_technique: string;
    embedding_model?: string;
    preprocess_id?: string;
    status: string;
    permission: string;
    meta?: Record<string, any>;
    document_count: number;
    chunk_count: number;
    created_at: string;
    updated_at?: string;
}

// Create knowledge request
export interface KnowledgeCreate {
    name: string;
    description?: string;
    provider?: string;
    indexing_technique?: string;
    embedding_model?: string;
    preprocess_id?: string;
    permission?: string;
    meta?: Record<string, any>;
}

// Update knowledge request
export interface KnowledgeUpdate {
    name?: string;
    description?: string;
    status?: string;
    permission?: string;
    embedding_model?: string;
    preprocess_id?: string;
    meta?: Record<string, any>;
}
