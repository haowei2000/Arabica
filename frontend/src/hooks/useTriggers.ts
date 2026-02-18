import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { triggerService } from '@/services/triggerService';
import type { TriggerCreate, TriggerUpdate } from '@/types/trigger';

export const useTriggers = (params?: {
  page?: number;
  page_size?: number;
  event_type?: string;
  enabled?: boolean;
}) => {
  return useQuery({
    queryKey: ['triggers', params],
    queryFn: () => triggerService.listTriggers(params),
  });
};

export const useCreateTrigger = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (data: TriggerCreate) => triggerService.createTrigger(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['triggers'] });
    },
  });
};

export const useUpdateTrigger = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: TriggerUpdate }) =>
      triggerService.updateTrigger(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['triggers'] });
    },
  });
};

export const useDeleteTrigger = () => {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => triggerService.deleteTrigger(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['triggers'] });
    },
  });
};
