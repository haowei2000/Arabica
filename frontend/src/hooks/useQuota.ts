import { useQuery } from '@tanstack/react-query';

import { quotaService } from '@/services/quotaService';

export const useMyQuota = () => {
  return useQuery({
    queryKey: ['quota', 'me'],
    queryFn: () => quotaService.getMyQuota(),
    staleTime: 30 * 1000,
  });
};
