import { useState } from 'react';
import { ChevronDown, ChevronRight, Brain, Loader2, Tag } from 'lucide-react';
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useEntityContext } from '@/hooks/useEntityContext';
import { cn } from '@/lib/utils';
import type { EntityContextType, ContextEntry } from '@/types/context';

interface ContextViewerProps {
  open: boolean;
  onClose: () => void;
  entityType: EntityContextType;
  entityId: string;
  entityName: string;
}

function ContextEntryRow({ entry }: { entry: ContextEntry }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-lg border border-border/60 bg-muted/20 overflow-hidden">
      <button
        type="button"
        className="w-full flex items-start gap-2.5 px-3 py-2.5 text-left hover:bg-muted/40 transition-colors"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="mt-0.5 shrink-0 text-muted-foreground">
          {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </span>
        <div className="flex-1 min-w-0 space-y-0.5">
          <p className="text-[11px] font-mono text-muted-foreground truncate">{entry.path ?? entry.id}</p>
          {entry.glance && (
            <p className="text-xs text-foreground/80 leading-snug">{entry.glance}</p>
          )}
        </div>
        {entry.importance != null && entry.importance > 0 && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary shrink-0 tabular-nums">
            {entry.importance}
          </span>
        )}
      </button>

      {expanded && (
        <div className="border-t border-border/60 px-3 py-2.5 space-y-2">
          <pre className="text-[11px] text-foreground/80 whitespace-pre-wrap break-words font-mono leading-relaxed bg-muted/30 rounded p-2 max-h-64 overflow-y-auto">
            {entry.content}
          </pre>
          {entry.tags && entry.tags.length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap">
              <Tag className="size-3 text-muted-foreground shrink-0" />
              {entry.tags.map((tag) => (
                <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50">
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function ContextViewer({ open, onClose, entityType, entityId, entityName }: ContextViewerProps) {
  const { data, isLoading } = useEntityContext(entityType, open ? entityId : null, { page_size: 100 });

  const items = data?.items ?? [];

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Brain className="size-4 text-muted-foreground" />
            <span>Context</span>
            <span className="text-muted-foreground font-normal">— {entityName}</span>
            {data && (
              <span className="ml-auto text-xs font-normal text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">
                {data.total} {data.total === 1 ? 'entry' : 'entries'}
              </span>
            )}
          </DialogTitle>
        </DialogHeader>

        <ScrollArea className="flex-1 min-h-0 -mx-1 px-1">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : items.length === 0 ? (
            <div className="text-center py-12">
              <Brain className="mx-auto size-8 text-muted-foreground/30 mb-3" />
              <p className="text-sm text-muted-foreground">No context entries yet</p>
              <p className="text-xs text-muted-foreground/60 mt-1">Trigger a sync to generate context</p>
            </div>
          ) : (
            <div className={cn('space-y-2 py-1')}>
              {items.map((entry) => (
                <ContextEntryRow key={entry.id} entry={entry} />
              ))}
            </div>
          )}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}
