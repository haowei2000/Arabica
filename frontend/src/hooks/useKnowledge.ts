import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {knowledgeService} from '@/services/knowledgeService';
import type {KnowledgeCreate, KnowledgeUpdate} from '@/types/knowledge';

/**
 * Get knowledge list
 */
export const useKnowledgeList = (params?: {
    page?: number;
    page_size?: number;
    status?: string;
    permission?: string;
}) => {
    return useQuery({
        queryKey: ['knowledge', 'list', params],
        queryFn: () => knowledgeService.getKnowledgeList(params),
    });
};

/**
 * Get single knowledge
 */
export const useKnowledge = (id: string) => {
    return useQuery({
        queryKey: ['knowledge', id],
        queryFn: () => knowledgeService.getKnowledge(id),
        enabled: !!id,
    });
};

/**
 * Create knowledge
 */
export const useCreateKnowledge = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: (data: KnowledgeCreate) => knowledgeService.createKnowledge(data),
        onSuccess: () => {
            queryClient.invalidateQueries({queryKey: ['knowledge']});
        },
    });
};

/**
 * Update knowledge
 */
export const useUpdateKnowledge = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: ({id, data}: { id: string; data: KnowledgeUpdate }) =>
            knowledgeService.updateKnowledge(id, data),
        onSuccess: () => {
            queryClient.invalidateQueries({queryKey: ['knowledge']});
        },
    });
};

/**
 * Delete knowledge
 */
export const useDeleteKnowledge = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: (id: string) => knowledgeService.deleteKnowledge(id),
        onSuccess: () => {
            queryClient.invalidateQueries({queryKey: ['knowledge']});
        },
    });
};

/**
 * Search knowledge
 */
export const useSearchKnowledge = (params: {
    q: string;
    page?: number;
    page_size?: number;
}) => {
    return useQuery({
        queryKey: ['knowledge', 'search', params],
        queryFn: () => knowledgeService.searchKnowledge(params),
        enabled: !!params.q,
    });
};
