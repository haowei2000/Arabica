import { apiClient } from '@/services/api';
import { API_ENDPOINTS } from '@/constants/api';

export interface Task {
  id: string;
  workspace_id: string;
  run_id: string | null;
  parent_task_id: string | null;
  title: string;
  description: string | null;
  result: string | null;
  status: 'pending' | 'in_progress' | 'done' | 'failed' | 'cancelled';
  priority: number;
  assignee: string | null;
  meta: Record<string, unknown> | null;
  created_at: string;
  completed_at: string | null;
  updated_at: string | null;
}

export interface TaskListResponse {
  total: number;
  items: Task[];
}

export const taskService = {
  async listTasks(
    workspaceId: string,
    params?: { run_id?: string; status?: string; limit?: number; offset?: number }
  ): Promise<TaskListResponse> {
    return apiClient.get(API_ENDPOINTS.TASKS.LIST(workspaceId), { params });
  },
};
