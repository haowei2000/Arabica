import { useQuery } from '@tanstack/react-query';
import { contextService } from '@/services/contextService';
import type { EntityContextType } from '@/types/context';

export function useEntityContext(
  entityType: EntityContextType,
  entityId: string | null,
  params?: { page?: number; page_size?: number },
) {
  return useQuery({
    queryKey: ['entity-context', entityType, entityId, params],
    queryFn: () => contextService.getEntityContext(entityType, entityId!, params),
    enabled: !!entityId,
  });
}

export function useMemories(params?: { page?: number; page_size?: number }) {
  return useQuery({
    queryKey: ['context-memories', params],
    queryFn: () => contextService.getMemories(params),
  });
}
