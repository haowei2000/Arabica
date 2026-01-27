import {apiClient} from './api';
import {API_ENDPOINTS} from '@/constants/api';
import type {ChunkListResponse} from '@/types/chunk';

export interface ListChunksParams {
    page?: number;
    page_size?: number;
}

export const chunkService = {
    /**
     * List all chunks in a knowledge base
     */
    async listChunksByKnowledge(knowledgeId: string, params?: ListChunksParams): Promise<ChunkListResponse> {
        return apiClient.get(API_ENDPOINTS.CONTEXT.LIST_BY_KNOWLEDGE(knowledgeId), {params});
    },

    /**
     * List all chunks for a specific document
     */
    async listChunksByDocument(documentId: string, params?: ListChunksParams): Promise<ChunkListResponse> {
        return apiClient.get(API_ENDPOINTS.CONTEXT.LIST_BY_DOCUMENT(documentId), {params});
    },
};
