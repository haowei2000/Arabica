import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useUserContexts, useCopyContexts } from '@/hooks/useWorkspaces';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';

type ContextTypeFilter = 'all' | 'knowledge' | 'tool' | 'user_memory' | 'skill';

interface ContextSelectModalProps {
  workspaceId: string;
  onClose: () => void;
}

// Map context_type to available shadcn Badge variants
const TYPE_BADGE_VARIANT: Record<string, 'default' | 'secondary' | 'destructive' | 'outline'> = {
  knowledge: 'default',
  tool: 'outline',
  user_memory: 'secondary',
  skill: 'secondary',
  conversation: 'secondary',
  chunk: 'secondary',
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
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleCopy = async () => {
    if (selectedIds.size === 0) return;
    try {
      await copyMutation.mutateAsync({ context_ids: Array.from(selectedIds) });
      onClose();
    } catch {
      // error available via copyMutation.error
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
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-w-lg flex flex-col max-h-[80vh] p-0 gap-0">
        <DialogHeader className="px-5 py-4 border-b border-border shrink-0">
          <DialogTitle>Add Context to Workspace</DialogTitle>
        </DialogHeader>

        {/* Type filter tabs */}
        <div className="px-5 py-3 border-b border-border shrink-0">
          <Tabs value={typeFilter} onValueChange={(v) => { setTypeFilter(v as ContextTypeFilter); setPage(1); setSelectedIds(new Set()); }}>
            <TabsList className="w-full">
              {tabs.map((tab) => (
                <TabsTrigger key={tab.id} value={tab.id} className="flex-1">{tab.label}</TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </div>

        {/* List */}
        <ScrollArea className="flex-1 px-5 py-3">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="size-8 animate-spin text-muted-foreground" />
            </div>
          ) : items.length === 0 ? (
            <div className="text-center py-12 text-sm text-muted-foreground">
              No contexts found
            </div>
          ) : (
            <div className="space-y-2">
              {items.map((ctx: any) => (
                <label
                  key={ctx.id}
                  className={cn(
                    'flex items-start gap-3 p-3 rounded-lg cursor-pointer border transition-all',
                    selectedIds.has(ctx.id)
                      ? 'border-primary bg-primary/5'
                      : 'border-border hover:border-border/80 hover:bg-muted/40'
                  )}
                >
                  <Checkbox
                    checked={selectedIds.has(ctx.id)}
                    onCheckedChange={() => toggleSelect(ctx.id)}
                    className="mt-0.5"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <Badge variant={TYPE_BADGE_VARIANT[ctx.context_type] ?? 'secondary'}>
                        {ctx.context_type}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {new Date(ctx.created_at).toLocaleDateString()}
                      </span>
                    </div>
                    <p className="text-sm text-foreground line-clamp-2 break-all">
                      {ctx.summary || ctx.content?.slice(0, 120) || `context-${ctx.id}`}
                    </p>
                  </div>
                </label>
              ))}
            </div>
          )}
        </ScrollArea>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="px-5 py-2 border-t border-border flex items-center justify-between text-xs text-muted-foreground shrink-0">
            <span>{total} total</span>
            <div className="flex gap-2 items-center">
              <Button variant="ghost" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                Prev
              </Button>
              <span>{page} / {totalPages}</span>
              <Button variant="ghost" size="sm" disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}>
                Next
              </Button>
            </div>
          </div>
        )}

        <DialogFooter className="px-5 py-4 border-t border-border flex items-center justify-between shrink-0">
          <span className="text-sm text-muted-foreground">{selectedIds.size} selected</span>
          <div className="flex gap-3">
            <Button variant="outline" size="sm" onClick={onClose}>Cancel</Button>
            <Button
              size="sm"
              disabled={selectedIds.size === 0 || copyMutation.isPending}
              onClick={handleCopy}
            >
              {copyMutation.isPending ? 'Adding...' : 'Add Selected'}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
