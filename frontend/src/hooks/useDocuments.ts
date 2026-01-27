import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {documentService} from '@/services/documentService';
import type {UploadDocumentParams} from '@/services/documentService';

/**
 * Get documents list for a knowledge base
 */
export const useDocumentList = (knowledgeId: string, params?: {
    page?: number;
    page_size?: number;
}) => {
    return useQuery({
        queryKey: ['documents', knowledgeId, params],
        queryFn: () => documentService.listDocuments(knowledgeId, params),
        enabled: !!knowledgeId,
    });
};

/**
 * Get single document
 */
export const useDocument = (id: string) => {
    return useQuery({
        queryKey: ['document', id],
        queryFn: () => documentService.getDocument(id),
        enabled: !!id,
    });
};

/**
 * Upload document mutation
 */
export const useUploadDocument = () => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: (params: UploadDocumentParams) => documentService.uploadDocument(params),
        onSuccess: (_data, variables) => {
            queryClient.invalidateQueries({queryKey: ['documents', variables.knowledge_id]});
            queryClient.invalidateQueries({queryKey: ['knowledge']});
        },
    });
};

/**
 * Delete document mutation
 */
export const useDeleteDocument = (knowledgeId: string) => {
    const queryClient = useQueryClient();

    return useMutation({
        mutationFn: (documentId: string) => documentService.deleteDocument(documentId),
        onSuccess: () => {
            queryClient.invalidateQueries({queryKey: ['documents', knowledgeId]});
            queryClient.invalidateQueries({queryKey: ['knowledge']});
        },
    });
};

/**
 * Get task status for document processing
 */
export const useTaskStatus = (taskId: string | null, options?: { refetchInterval?: number }) => {
    return useQuery({
        queryKey: ['task-status', taskId],
        queryFn: () => documentService.getTaskStatus(taskId!),
        enabled: !!taskId && taskId !== 'duplicate',
        refetchInterval: options?.refetchInterval ?? 2000, // Poll every 2 seconds by default
    });
};
