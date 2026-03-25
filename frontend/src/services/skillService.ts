import { apiClient } from './api';
import { API_ENDPOINTS } from '@/constants/api';
import type {
  Skill,
  SkillCreate,
  SkillUpdate,
  SkillListResponse,
} from '@/types/skill';

export const skillService = {
  async getSkills(params?: {
    tags?: string;
    page?: number;
    page_size?: number;
  }): Promise<SkillListResponse> {
    return apiClient.get(API_ENDPOINTS.SKILLS.LIST, { params });
  },

  async getSkill(id: string): Promise<Skill> {
    return apiClient.get(API_ENDPOINTS.SKILLS.GET(id));
  },

  async createSkill(data: SkillCreate): Promise<Skill> {
    return apiClient.post(API_ENDPOINTS.SKILLS.CREATE, data);
  },

  async updateSkill(id: string, data: SkillUpdate): Promise<Skill> {
    return apiClient.put(API_ENDPOINTS.SKILLS.UPDATE(id), data);
  },

  async deleteSkill(id: string): Promise<void> {
    return apiClient.delete(API_ENDPOINTS.SKILLS.DELETE(id));
  },

  async searchSkills(params: {
    q: string;
    page?: number;
    page_size?: number;
  }): Promise<SkillListResponse> {
    return apiClient.get(API_ENDPOINTS.SKILLS.SEARCH, { params });
  },

  async processSkill(id: string, embedding_model?: string): Promise<Skill> {
    return apiClient.post(API_ENDPOINTS.SKILLS.PROCESS(id), null, {
      params: embedding_model ? { embedding_model } : undefined,
    });
  },

  async uploadSkillFolder(
    files: File[],
    paths: string[],
    tags?: string,
  ): Promise<Skill> {
    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
      formData.append('files', files[i], paths[i]);
    }
    const params: Record<string, string> = {};
    if (tags) params['tags'] = tags;
    // Remove the default `application/json` Content-Type so the browser can
    // set `multipart/form-data; boundary=...` automatically for FormData.
    return apiClient.post(API_ENDPOINTS.SKILLS.UPLOAD_FOLDER, formData, {
      params,
      headers: { 'Content-Type': undefined as unknown as string },
    });
  },
};
