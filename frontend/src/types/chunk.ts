export interface ChunkMeta {
    document_id?: string;
    chunk_id?: string;
    position?: number;
    start_char?: number;
    end_char?: number;
    content_length?: number;

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
