import {useQuery} from '@tanstack/react-query';
import type {ListChunksParams} from '@/services/chunkService';
import {chunkService} from '@/services/chunkService';

/**
 * Get all chunks in a knowledge base
 */
export const useChunksByKnowledge = (knowledgeId: string, params?: ListChunksParams) => {
    return useQuery({
        queryKey: ['chunks', 'knowledge', knowledgeId, params],
        queryFn: () => chunkService.listChunksByKnowledge(knowledgeId, params),
        enabled: !!knowledgeId,
    });
};

/**
 * Get all chunks for a specific document
 */
export const useChunksByDocument = (documentId: string, params?: ListChunksParams) => {
    return useQuery({
        queryKey: ['chunks', 'document', documentId, params],
        queryFn: () => chunkService.listChunksByDocument(documentId, params),
        enabled: !!documentId,
    });
};
