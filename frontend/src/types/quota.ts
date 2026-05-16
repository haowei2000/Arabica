export interface Quota {
  free_quota_total: number;
  free_quota_used: number;
  paid_quota_total: number;
  paid_quota_used: number;
  remaining_tokens: number;
  period_start?: string | null;
  period_end?: string | null;
}
