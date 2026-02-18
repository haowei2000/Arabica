export interface ChunkMeta {
    document_id?: string;
    document_name?: string;
    knowledge_id?: string;
    chunk_id?: string;
    position?: number;
    start_char?: number;
    end_char?: number;
    content_length?: number;
    // Structured pipeline section fields
    section_title?: string;
    section_level?: number;
    embedding_model?: string;
    embedding_provider?: string;
    // Strategy identifier: "document" | "table" | "code"
    structure_type?: string;
    // Code-oriented fields
    code_language?: string;
    context_heading?: string;
    // Table-oriented fields
    column_names?: string[];
    row_index?: number;

    [key: string]: unknown;
}

export interface Chunk {
    id: string;
    user_id: string;
    source_id: string | null;
    context_type: string;
    content: string;
    summary: string | null;
    keywords: string[] | null;
    meta: ChunkMeta | null;
    importance: number | null;
    created_at: string;
    updated_at: string | null;
}

export interface ChunkListResponse {
    total: number;
    items: Chunk[];
    page: number;
    page_size: number;
}
