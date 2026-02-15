import { useState } from 'react';
import { X } from 'lucide-react';
import { useUserContexts, useCopyContexts } from '@/hooks/useWorkspaces';
import { Button, Badge } from '@/components/ui';

type ContextTypeFilter = 'all' | 'knowledge' | 'tool' | 'user_memory' | 'skill';

interface ContextSelectModalProps {
  workspaceId: string;
  onClose: () => void;
}

const TYPE_BADGE_VARIANT: Record<string, 'success' | 'warning' | 'error' | 'info' | 'neutral'> = {
  knowledge: 'info',
  tool: 'warning',
  user_memory: 'success',
  skill: 'neutral',
  conversation: 'neutral',
  chunk: 'neutral',
};

export default function ContextSelectModal({ workspaceId, onClose }: ContextSelectModalProps) {
  const [typeFilter, setTypeFilter] = useState<ContextTypeFilter>('all');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [page, setPage] = useState(1);

  const queryParams = {
    page,
    page_size: 20,
    ...(typeFilter !== 'all' ? { context_type: typeFilter } : {}),
  };

  const { data, isLoading } = useUserContexts(queryParams);
  const copyMutation = useCopyContexts(workspaceId);

  const items = (data as any)?.items ?? [];
  const total = (data as any)?.total ?? 0;
  const totalPages = Math.ceil(total / 20);

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const handleCopy = async () => {
    if (selectedIds.size === 0) return;
    try {
      await copyMutation.mutateAsync({ context_ids: Array.from(selectedIds) });
      onClose();
    } catch {
      // error is available via copyMutation.error
    }
  };

  const tabs: { id: ContextTypeFilter; label: string }[] = [
    { id: 'all', label: 'All' },
    { id: 'knowledge', label: 'Knowledge' },
    { id: 'tool', label: 'Tool' },
    { id: 'user_memory', label: 'Memory' },
    { id: 'skill', label: 'Skill' },
  ];

  return (
    <div className="fixed inset-0 bg-black/50 dark:bg-black/70 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-navy-800 rounded-xl shadow-2xl w-full max-w-lg mx-4 flex flex-col max-h-[80vh] border border-secondary-200 dark:border-navy-700">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-secondary-200 dark:border-navy-700">
          <h3 className="text-lg font-semibold text-navy-900 dark:text-navy-100">
            Add Context to Workspace
          </h3>
          <button
            onClick={onClose}
            className="p-1 rounded-md hover:bg-secondary-100 dark:hover:bg-navy-700 text-secondary-500"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Type filter tabs */}
        <div className="px-5 py-3 border-b border-secondary-200 dark:border-navy-700 flex flex-wrap gap-2">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => { setTypeFilter(tab.id); setPage(1); setSelectedIds(new Set()); }}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                typeFilter === tab.id
                  ? 'bg-primary-500 text-white shadow-md'
                  : 'bg-secondary-100 dark:bg-navy-700 text-secondary-600 dark:text-secondary-400 hover:bg-secondary-200 dark:hover:bg-navy-600'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto px-5 py-3 space-y-2">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="w-8 h-8 rounded-full border-4 border-primary-200 dark:border-primary-900/30 border-t-primary-500 animate-spin" />
            </div>
          ) : items.length === 0 ? (
            <div className="text-center py-12 text-sm text-secondary-500 dark:text-secondary-400">
              No contexts found
            </div>
          ) : (
            items.map((ctx: any) => (
              <label
                key={ctx.id}
                className={`flex items-start gap-3 p-3 rounded-lg cursor-pointer border transition-all ${
                  selectedIds.has(ctx.id)
                    ? 'border-primary-500 bg-primary-50 dark:bg-primary-900/20'
                    : 'border-secondary-200 dark:border-navy-700 hover:border-secondary-300 dark:hover:border-navy-600'
                }`}
              >
                <input
                  type="checkbox"
                  checked={selectedIds.has(ctx.id)}
                  onChange={() => toggleSelect(ctx.id)}
                  className="mt-1 h-4 w-4 rounded border-secondary-300 text-primary-500 focus:ring-primary-500"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge variant={TYPE_BADGE_VARIANT[ctx.context_type] ?? 'neutral'} size="sm">
                      {ctx.context_type}
                    </Badge>
                    <span className="text-xs text-secondary-400 dark:text-secondary-500">
                      {new Date(ctx.created_at).toLocaleDateString()}
                    </span>
                  </div>
                  <p className="text-sm text-navy-900 dark:text-navy-100 line-clamp-2 break-all">
                    {ctx.summary || ctx.content?.slice(0, 120) || `context-${ctx.id}`}
                  </p>
                </div>
              </label>
            ))
          )}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="px-5 py-2 border-t border-secondary-200 dark:border-navy-700 flex items-center justify-between text-xs text-secondary-500">
            <span>{total} total</span>
            <div className="flex gap-2">
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="px-2 py-1 rounded hover:bg-secondary-100 dark:hover:bg-navy-700 disabled:opacity-40"
              >
                Prev
              </button>
              <span className="px-2 py-1">{page} / {totalPages}</span>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
                className="px-2 py-1 rounded hover:bg-secondary-100 dark:hover:bg-navy-700 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="px-5 py-4 border-t border-secondary-200 dark:border-navy-700 flex items-center justify-between">
          <span className="text-sm text-secondary-500 dark:text-secondary-400">
            {selectedIds.size} selected
          </span>
          <div className="flex gap-3">
            <Button variant="secondary" size="sm" onClick={onClose}>
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={selectedIds.size === 0 || copyMutation.isPending}
              onClick={handleCopy}
            >
              {copyMutation.isPending ? 'Adding...' : 'Add Selected'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
