import { useQuery } from '@tanstack/react-query';
import { artifactService } from '@/services/artifactService';

const ARTIFACT_LIST_STALE_MS = 10_000;

export const useArtifacts = (
  workspaceId: string,
  params?: { run_id?: string; artifact_type?: string; limit?: number; offset?: number },
  options?: { refetchInterval?: number }
) => {
  return useQuery({
    queryKey: ['artifacts', workspaceId, params],
    queryFn: () => artifactService.listArtifacts(workspaceId, params),
    enabled: !!workspaceId,
    refetchInterval: options?.refetchInterval,
    staleTime: ARTIFACT_LIST_STALE_MS,
  });
};
