import { Plus, MessageSquare, X, Loader2 } from 'lucide-react';
import { useRuns } from '@/hooks/useRuns';
import { useChatStore } from '@/stores/useChatStore';
import { useUIStore } from '@/stores/useUIStore';
import { formatRelativeTime } from '@/utils/formatDate';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';

interface SidebarProps {
  workspaceId: string;
  onNewChat: () => void;
}

export default function Sidebar({ workspaceId, onNewChat }: SidebarProps) {
  const { sidebarOpen, toggleSidebar } = useUIStore();
  const { currentRunId, loadRun } = useChatStore();
  const { data: runsData, isLoading } = useRuns(workspaceId, { page: 1, page_size: 50 });

  if (!sidebarOpen) return null;

  return (
    <aside className="w-72 bg-card border-r border-border flex flex-col shrink-0">
      <div className="p-3 border-b border-border flex items-center justify-between">
        <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Run History</h2>
        <Button variant="ghost" size="icon" className="size-6" onClick={toggleSidebar} title="Close sidebar">
          <X className="size-3.5" />
        </Button>
      </div>

      <div className="p-3">
        <Button onClick={onNewChat} size="sm" className="w-full gap-1.5">
          <Plus className="size-3.5" />
          New Run
        </Button>
      </div>

      <ScrollArea className="flex-1 px-2">
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : runsData?.items && runsData.items.length > 0 ? (
          <div className="space-y-0.5 pb-4">
            {runsData.items.map((run) => (
              <div
                key={run.id}
                onClick={() => loadRun(run.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && loadRun(run.id)}
                className={cn(
                  'w-full text-left px-2.5 py-2 rounded-lg transition-colors cursor-pointer',
                  currentRunId === run.id
                    ? 'bg-primary/8 text-foreground'
                    : 'hover:bg-muted/50'
                )}
              >
                <div className="flex items-start gap-2">
                  <MessageSquare className={cn('size-3.5 mt-0.5 shrink-0', currentRunId === run.id ? 'text-primary' : 'text-muted-foreground/50')} />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-medium truncate leading-snug">{run.input_data?.message || 'New Run'}</p>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className={cn(
                        'text-[10px]',
                        run.status === 'running' ? 'text-yellow-500' :
                        run.status === 'failed'  ? 'text-red-500' :
                        run.status === 'finished' ? 'text-green-500' :
                        'text-muted-foreground/50'
                      )}>{run.status}</span>
                      <span className="text-[10px] text-muted-foreground/40">{formatRelativeTime(run.created_at)}</span>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
            <MessageSquare className="size-8 text-muted-foreground/20 mb-3" />
            <p className="text-xs text-muted-foreground">No runs yet</p>
            <p className="text-[10px] text-muted-foreground/50 mt-1">Create a new run to start chatting</p>
          </div>
        )}
      </ScrollArea>
    </aside>
  );
}
