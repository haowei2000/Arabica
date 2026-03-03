import { apiClient } from '@/services/api';
import { API_BASE_URL, API_ENDPOINTS } from '@/constants/api';

export interface Artifact {
  id: string;
  workspace_id: string;
  run_id: string | null;
  name: string;
  artifact_type: 'text' | 'code' | 'file' | 'image' | 'document' | 'data' | 'other';
  content_type: string | null;
  content: string | null;
  s3_key: string | null;
  s3_url: string | null;
  version: number;
  meta: Record<string, unknown> | null;
  created_at: string;
  updated_at: string | null;
}

export interface ArtifactListResponse {
  total: number;
  items: Artifact[];
}

export const artifactService = {
  async listArtifacts(
    workspaceId: string,
    params?: { run_id?: string; artifact_type?: string; limit?: number; offset?: number }
  ): Promise<ArtifactListResponse> {
    return apiClient.get(API_ENDPOINTS.ARTIFACTS.LIST(workspaceId), { params });
  },

  async getArtifact(workspaceId: string, artifactId: string): Promise<Artifact> {
    return apiClient.get(API_ENDPOINTS.ARTIFACTS.GET(workspaceId, artifactId));
  },

  getDownloadUrl(workspaceId: string, artifactId: string): string {
    const token = localStorage.getItem('access_token');
    const base = API_BASE_URL.replace(/\/$/, '');
    const path = API_ENDPOINTS.ARTIFACTS.DOWNLOAD(workspaceId, artifactId);
    return `${base}${path}${token ? `?token=${encodeURIComponent(token)}` : ''}`;
  },

  downloadArtifact(workspaceId: string, artifactId: string, filename: string): void {
    const token = localStorage.getItem('access_token');
    const base = API_BASE_URL.replace(/\/$/, '');
    const path = API_ENDPOINTS.ARTIFACTS.DOWNLOAD(workspaceId, artifactId);
    const url = `${base}${path}`;

    // Use fetch to send auth header, then trigger browser download
    fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Download failed: ${res.status}`);
        return res.blob();
      })
      .then((blob) => {
        const objectUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = objectUrl;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(objectUrl);
      });
  },
};
