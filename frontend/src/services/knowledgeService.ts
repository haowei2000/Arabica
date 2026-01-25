import {apiClient} from './api';
import {API_ENDPOINTS} from '@/constants/api';
import type {Knowledge, KnowledgeCreate, KnowledgeUpdate} from '@/types/knowledge';
import type {PaginatedResponse} from '@/types/api';

export const knowledgeService = {
    /**
     * Get knowledge list with pagination
     */
    async getKnowledgeList(params?: {
        page?: number;
        page_size?: number;
        status?: string;
        permission?: string;
    }): Promise<PaginatedResponse<Knowledge>> {
        return apiClient.get(API_ENDPOINTS.KNOWLEDGE.LIST, {params});
    },

    /**
     * Get single knowledge by ID
     */
    async getKnowledge(id: string): Promise<Knowledge> {
        return apiClient.get(API_ENDPOINTS.KNOWLEDGE.GET(id));
    },

    /**
     * Create new knowledge base
     */
    async createKnowledge(data: KnowledgeCreate): Promise<Knowledge> {
        return apiClient.post(API_ENDPOINTS.KNOWLEDGE.CREATE, data);
    },

    /**
     * Update knowledge base
     */
    async updateKnowledge(id: string, data: KnowledgeUpdate): Promise<Knowledge> {
        return apiClient.post(API_ENDPOINTS.KNOWLEDGE.UPDATE(id), data);
    },

    /**
     * Delete knowledge base
     */
    async deleteKnowledge(id: string): Promise<void> {
        return apiClient.post(API_ENDPOINTS.KNOWLEDGE.DELETE(id));
    },

    /**
     * Search knowledge bases
     */
    async searchKnowledge(params: {
        q: string;
        page?: number;
        page_size?: number;
    }): Promise<PaginatedResponse<Knowledge>> {
        return apiClient.get(API_ENDPOINTS.KNOWLEDGE.SEARCH, {params});
    },
};
