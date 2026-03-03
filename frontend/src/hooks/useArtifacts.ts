import { useQuery } from '@tanstack/react-query';
import { artifactService } from '@/services/artifactService';

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
  });
};
