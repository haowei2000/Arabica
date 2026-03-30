import {apiClient} from './api';
import {API_BASE_URL, API_ENDPOINTS} from '@/constants/api';
import type {Document, DocumentListResponse, DocumentUploadResponse, TaskStatusResponse} from '@/types/document';

export interface DocumentPreviewResponse {
    document_id: string;
    original_name: string;
    mime_type: string | null;
    content: string | null;
    content_length: number;
}

export type StructureType = 'document' | 'table' | 'code' | 'markdown';

export interface FolderUploadResult {
    uploads: DocumentUploadResponse[];
    total: number;
    failed: number;
}

export interface UploadDocumentParams {
    file: File;
    knowledge_id: string;
    embedding_provider?: string;
    embedding_model?: string;
    embedding_dimension?: number;
}

export const documentService = {
    /**
     * Upload a document to a knowledge base
     */
    async uploadDocument(params: UploadDocumentParams): Promise<DocumentUploadResponse> {
        const formData = new FormData();
        formData.append('file', params.file);
        formData.append('knowledge_id', params.knowledge_id);

        if (params.embedding_provider) {
            formData.append('embedding_provider', params.embedding_provider);
        }
        if (params.embedding_model) {
            formData.append('embedding_model', params.embedding_model);
        }
        if (params.embedding_dimension) {
            formData.append('embedding_dimension', params.embedding_dimension.toString());
        }

        return apiClient.post(API_ENDPOINTS.DOCUMENT.UPLOAD, formData, {
            headers: {
                'Content-Type': 'multipart/form-data',
            },
        });
    },

    /**
     * Upload all files from a folder to a knowledge base.
     * Structure type is auto-detected per file on the server.
     */
    async uploadFolder(params: {
        files: File[];
        knowledge_id: string;
        embedding_provider?: string;
        embedding_model?: string;
        embedding_dimension?: number;
    }): Promise<FolderUploadResult> {
        const formData = new FormData();
        formData.append('knowledge_id', params.knowledge_id);
        for (const file of params.files) {
            formData.append('files', file, file.webkitRelativePath || file.name);
        }
        if (params.embedding_provider) formData.append('embedding_provider', params.embedding_provider);
        if (params.embedding_model) formData.append('embedding_model', params.embedding_model);
        if (params.embedding_dimension) formData.append('embedding_dimension', params.embedding_dimension.toString());

        return apiClient.post(API_ENDPOINTS.DOCUMENT.UPLOAD_FOLDER, formData, {
            headers: { 'Content-Type': 'multipart/form-data' },
        });
    },

    /**
     * Get document by ID
     */
    async getDocument(id: string): Promise<Document> {
        return apiClient.get(API_ENDPOINTS.DOCUMENT.GET(id));
    },

    /**
     * List documents in a knowledge base
     */
    async listDocuments(knowledgeId: string, params?: {
        page?: number;
        page_size?: number;
    }): Promise<DocumentListResponse> {
        return apiClient.get(API_ENDPOINTS.DOCUMENT.LIST_BY_KNOWLEDGE(knowledgeId), {params});
    },

    /**
     * Delete a document
     */
    async deleteDocument(id: string): Promise<void> {
        return apiClient.post(API_ENDPOINTS.DOCUMENT.DELETE(id));
    },

    /**
     * Get task status for document processing
     */
    async getTaskStatus(taskId: string): Promise<TaskStatusResponse> {
        return apiClient.get(API_ENDPOINTS.DOCUMENT.TASK_STATUS(taskId));
    },

    /**
     * Get document preview (parsed text content)
     */
    async getPreview(id: string): Promise<DocumentPreviewResponse> {
        return apiClient.get(API_ENDPOINTS.DOCUMENT.PREVIEW(id));
    },

    /**
     * Get download URL for a document
     */
    getDownloadUrl(id: string): string {
        const token = localStorage.getItem('access_token');
        return `${API_BASE_URL}${API_ENDPOINTS.DOCUMENT.DOWNLOAD(id)}?token=${token}`;
    },
};
