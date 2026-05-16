import { Gauge } from 'lucide-react';

import { useMyQuota } from '@/hooks/useQuota';
import { cn } from '@/lib/utils';

const compact = new Intl.NumberFormat('en', {
  notation: 'compact',
  maximumFractionDigits: 1,
});

export default function QuotaMeter() {
  const { data: quota, isLoading, isError } = useMyQuota();

  if (isError) return null;

  const total = quota
    ? quota.free_quota_total + quota.paid_quota_total
    : 0;
  const remaining = quota?.remaining_tokens ?? 0;
  const percent = total > 0 ? Math.max(Math.min((remaining / total) * 100, 100), 0) : 0;
  const isLow = quota ? percent <= 10 : false;

  return (
    <div
      className={cn(
        'hidden sm:flex h-8 min-w-36 items-center gap-2 rounded-xl border border-border bg-background/70 px-2.5',
        isLow && 'border-red-500/30 bg-red-500/5'
      )}
      title={quota ? `${remaining.toLocaleString()} tokens remaining` : 'Loading quota'}
    >
      <Gauge className={cn('size-4 shrink-0 text-muted-foreground', isLow && 'text-red-500')} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Quota
          </span>
          <span className={cn('text-[11px] font-bold tabular-nums', isLow && 'text-red-500')}>
            {isLoading ? '...' : compact.format(remaining)}
          </span>
        </div>
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              'h-full rounded-full bg-primary transition-all duration-300',
              isLow && 'bg-red-500'
            )}
            style={{ width: `${isLoading ? 100 : percent}%` }}
          />
        </div>
      </div>
    </div>
  );
}
