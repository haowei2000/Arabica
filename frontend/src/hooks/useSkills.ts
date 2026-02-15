import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { skillService } from '@/services/skillService';
import type { SkillCreate, SkillUpdate } from '@/types/skill';

export const useSkills = (params?: {
  tags?: string;
  page?: number;
  page_size?: number;
}) => {
  return useQuery({
    queryKey: ['skills', 'list', params],
    queryFn: () => skillService.getSkills(params),
  });
};

export const useSkill = (id: string | null) => {
  return useQuery({
    queryKey: ['skills', 'detail', id],
    queryFn: () => skillService.getSkill(id!),
    enabled: !!id,
  });
};

export const useCreateSkill = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: SkillCreate) => skillService.createSkill(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['skills'] });
    },
  });
};

export const useUpdateSkill = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: SkillUpdate }) =>
      skillService.updateSkill(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['skills'] });
    },
  });
};

export const useDeleteSkill = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (id: string) => skillService.deleteSkill(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['skills'] });
    },
  });
};

export const useSearchSkills = (params: {
  q: string;
  page?: number;
  page_size?: number;
}) => {
  return useQuery({
    queryKey: ['skills', 'search', params],
    queryFn: () => skillService.searchSkills(params),
    enabled: params.q.length > 0,
  });
};

export const useProcessSkill = () => {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, embedding_model }: { id: string; embedding_model?: string }) =>
      skillService.processSkill(id, embedding_model),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['skills'] });
    },
  });
};
