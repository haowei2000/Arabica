// Document status type
export type DocumentStatus = 'pending' | 'processing' | 'completed' | 'failed';

// Document type
export interface Document {
    id: string;
    knowledge_id: string;
    user_id: string;
    original_name: string;
    object_key: string;
    file_url?: string;
    file_size: number;
    mime_type?: string;
    storage_type: string;
    bucket_name?: string;
    status: DocumentStatus;
    error_message?: string;
    chunk_count: number;
    created_at: string;
    updated_at?: string;
}

// Document upload response
export interface DocumentUploadResponse {
    document: Document;
    task_id: string;
}

// Document list response
export interface DocumentListResponse {
    total: number;
    items: Document[];
    page: number;
    page_size: number;
}

// Task status response
export interface TaskStatusResponse {
    task_id: string;
    status: 'PENDING' | 'STARTED' | 'PROGRESS' | 'SUCCESS' | 'FAILURE' | 'REVOKED';
    progress?: number;
    result?: any;
    error?: string;
}
