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
    <aside className="w-80 bg-card border-r border-border flex flex-col shrink-0">
      <div className="p-4 border-b border-border flex items-center justify-between">
        <h2 className="text-base font-semibold">Run 历史</h2>
        <Button variant="ghost" size="icon" className="size-7" onClick={toggleSidebar} title="关闭侧边栏">
          <X className="size-4" />
        </Button>
      </div>

      <div className="p-4">
        <Button onClick={onNewChat} className="w-full">
          <Plus className="size-4" />
          新建 Run
        </Button>
      </div>

      <ScrollArea className="flex-1 px-2">
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : runsData?.items && runsData.items.length > 0 ? (
          <div className="space-y-1 pb-4">
            {runsData.items.map((run) => (
              <div
                key={run.id}
                onClick={() => loadRun(run.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && loadRun(run.id)}
                className={cn(
                  'w-full text-left px-3 py-3 rounded-lg transition-colors cursor-pointer border',
                  currentRunId === run.id
                    ? 'bg-primary/10 border-primary/30 text-foreground'
                    : 'border-transparent hover:bg-muted'
                )}
              >
                <div className="flex items-start gap-3">
                  <MessageSquare className={cn('size-4 mt-0.5 shrink-0', currentRunId === run.id ? 'text-primary' : 'text-muted-foreground')} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <h3 className="text-sm font-medium truncate">{run.input_data?.message || '新 Run'}</h3>
                      <span className="text-xs text-muted-foreground shrink-0">{run.status}</span>
                    </div>
                    <span className="text-xs text-muted-foreground mt-1 block">{formatRelativeTime(run.created_at)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
            <MessageSquare className="size-10 text-muted-foreground/40 mb-3" />
            <p className="text-sm text-muted-foreground">暂无 Run 历史</p>
            <p className="text-xs text-muted-foreground/70 mt-1">新建 Run 开始对话</p>
          </div>
        )}
      </ScrollArea>
    </aside>
  );
}
