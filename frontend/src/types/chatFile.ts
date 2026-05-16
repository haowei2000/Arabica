export interface ChatFileAttachment {
  id: string;
  name: string;
  path: string;
  content_type: string | null;
  size_bytes: number | null;
  download_url: string | null;
  parse_status: string | null;
  created_at?: string;
}

export interface ChatFileUploadResponse {
  total: number;
  items: ChatFileAttachment[];
}
