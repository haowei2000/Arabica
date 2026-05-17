import { API_BASE_URL, API_ENDPOINTS } from '@/constants/api';
import type { ChatFileAttachment, ChatFileUploadResponse } from '@/types/chatFile';

export const chatFileService = {
  async uploadFiles(workspaceId: string, files: File[]): Promise<ChatFileUploadResponse> {
    const formData = new FormData();
    for (const file of files) {
      formData.append('files[]', file, file.name);
    }

    const token = localStorage.getItem('access_token');
    const response = await fetch(
      `${API_BASE_URL}${API_ENDPOINTS.CHAT_FILES.UPLOAD(workspaceId)}`,
      {
        method: 'POST',
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        body: formData,
      },
    );

    if (!response.ok) {
      let detail = `File upload failed: ${response.status}`;
      try {
        const body = await response.json();
        if (typeof body?.detail === 'string') {
          detail = body.detail;
        }
      } catch {
        // Keep the status-based fallback when the server returns no JSON body.
      }
      throw new Error(detail);
    }

    return response.json();
  },

  getDownloadUrl(workspaceId: string, file: ChatFileAttachment): string {
    const base = API_BASE_URL.replace(/\/$/, '');
    const endpoint = API_ENDPOINTS.CHAT_FILES.DOWNLOAD(workspaceId, file.id);
    return `${base}${endpoint}`;
  },

  async downloadBlob(workspaceId: string, file: ChatFileAttachment): Promise<Blob> {
    const token = localStorage.getItem('access_token');
    const response = await fetch(this.getDownloadUrl(workspaceId, file), {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
    if (!response.ok) {
      throw new Error(`File download failed: ${response.status}`);
    }
    return response.blob();
  },

  async downloadFile(workspaceId: string, file: ChatFileAttachment): Promise<void> {
    const blob = await this.downloadBlob(workspaceId, file);
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = file.name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(objectUrl);
  },
};
