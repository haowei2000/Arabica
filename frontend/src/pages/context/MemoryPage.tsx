import { useState } from 'react';
import { ChevronRight, Cpu, Loader2 } from 'lucide-react';
import { useWorkspaces, useWorkspaceRuns, useRunEvents } from '@/hooks/useMemory';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import type { Workspace } from '@/types/workspace';
import type { Run } from '@/types/run';

export default function MemoryPage() {
  const [expandedWorkspaces, setExpandedWorkspaces] = useState<Set<string>>(new Set());
  const [expandedRuns, setExpandedRuns] = useState<Set<string>>(new Set());
  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [hiddenEventTypes, setHiddenEventTypes] = useState<Set<string>>(new Set(['agent_token']));

  const { data: workspaceData, isLoading: workspacesLoading } = useWorkspaces({ page_size: 50 });
  const { data: runsData } = useWorkspaceRuns(selectedWorkspace, expandedWorkspaces.has(selectedWorkspace || ''));
  const { data: eventsData } = useRunEvents(selectedRun, expandedRuns.has(selectedRun || ''));

  const workspaces = workspaceData?.items ?? [];
  const runs = runsData?.items ?? [];
  const allEvents = eventsData?.items ?? [];
  const allEventTypes = Array.from(new Set(allEvents.map((e) => e.event_type))).sort();
  const events = allEvents.filter((e) => !hiddenEventTypes.has(e.event_type));

  const toggleEventTypeFilter = (eventType: string) => {
    const next = new Set(hiddenEventTypes);
    next.has(eventType) ? next.delete(eventType) : next.add(eventType);
    setHiddenEventTypes(next);
  };

  const toggleWorkspace = (workspaceId: string) => {
    const next = new Set(expandedWorkspaces);
    if (next.has(workspaceId)) {
      next.delete(workspaceId);
      setSelectedWorkspace(null);
    } else {
      next.add(workspaceId);
      setSelectedWorkspace(workspaceId);
    }
    setExpandedWorkspaces(next);
  };

  const toggleRun = (runId: string) => {
    const next = new Set(expandedRuns);
    if (next.has(runId)) {
      next.delete(runId);
      setSelectedRun(null);
    } else {
      next.add(runId);
      setSelectedRun(runId);
    }
    setExpandedRuns(next);
  };

  const getStatusVariant = (status: string): 'default' | 'secondary' | 'destructive' | 'outline' => {
    switch (status.toLowerCase()) {
      case 'completed': return 'default';
      case 'running': case 'active': return 'secondary';
      case 'failed': case 'error': return 'destructive';
      default: return 'outline';
    }
  };

  const formatDate = (dateString: string) =>
    new Date(dateString).toLocaleString('en-US', {
      year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });

  const formatEventType = (type: string) =>
    type.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());

  if (workspacesLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-xl font-bold">Context Memory</h2>
        <p className="text-sm text-muted-foreground mt-1">View your conversation history, workspaces, runs, and events</p>
      </div>

      {/* Event Type Filter */}
      {allEventTypes.length > 0 && (
        <div className="mb-6 bg-card rounded-lg border border-border p-4">
          <div className="flex items-center gap-2 mb-3">
            <h3 className="text-sm font-semibold">Event Type Filter</h3>
            <span className="text-xs text-muted-foreground">(Click to hide/show event types)</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {allEventTypes.map((eventType) => {
              const isHidden = hiddenEventTypes.has(eventType);
              return (
                <button
                  key={eventType}
                  type="button"
                  onClick={() => toggleEventTypeFilter(eventType)}
                  className={cn(
                    'px-3 py-1.5 rounded-full text-xs font-medium transition-all',
                    isHidden
                      ? 'bg-muted text-muted-foreground line-through opacity-60 hover:opacity-80'
                      : 'bg-primary/10 text-primary hover:bg-primary/20'
                  )}
                >
                  {formatEventType(eventType)}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {workspaces.length > 0 ? (
        <div className="space-y-4">
          {workspaces.map((workspace) => {
            const isExpanded = expandedWorkspaces.has(workspace.id);
            const workspaceRuns = isExpanded ? runs : [];

            return (
              <div key={workspace.id} className="bg-card rounded-lg border border-border overflow-hidden">
                <button
                  type="button"
                  onClick={() => toggleWorkspace(workspace.id)}
                  className="w-full px-6 py-4 flex items-center justify-between hover:bg-muted/40 transition-colors"
                >
                  <div className="flex items-center gap-4 flex-1 min-w-0">
                    <ChevronRight className={cn('size-5 text-muted-foreground transition-transform shrink-0', isExpanded && 'rotate-90')} />
                    <div className="flex-1 min-w-0 text-left">
                      <h3 className="text-lg font-semibold truncate">{workspace.name}</h3>
                      {workspace.description && (
                        <p className="text-sm text-muted-foreground truncate">{workspace.description}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-4 text-sm shrink-0">
                      <span className="text-muted-foreground">{workspace.run_count} runs</span>
                      <Badge variant={getStatusVariant(workspace.status)}>{workspace.status}</Badge>
                      <span className="text-muted-foreground text-xs">{formatDate(workspace.created_at)}</span>
                    </div>
                  </div>
                </button>

                {isExpanded && (
                  <div className="border-t border-border bg-muted/30">
                    {workspaceRuns.length > 0 ? (
                      <div className="divide-y divide-border">
                        {workspaceRuns.map((run) => {
                          const isRunExpanded = expandedRuns.has(run.id);
                          const runEvents = isRunExpanded ? events : [];

                          return (
                            <div key={run.id} className="bg-card">
                              <button
                                type="button"
                                onClick={() => toggleRun(run.id)}
                                className="w-full px-12 py-3 flex items-center justify-between hover:bg-muted/40 transition-colors"
                              >
                                <div className="flex items-center gap-3 flex-1 min-w-0">
                                  <ChevronRight className={cn('size-4 text-muted-foreground transition-transform shrink-0', isRunExpanded && 'rotate-90')} />
                                  <div className="flex-1 min-w-0 text-left">
                                    <div className="flex items-center gap-2">
                                      <span className="text-sm font-medium">Run</span>
                                      <code className="text-xs text-muted-foreground font-mono">{run.id.substring(0, 8)}</code>
                                    </div>
                                    <p className="text-xs text-muted-foreground">Trigger: {run.trigger_type}</p>
                                  </div>
                                  <div className="flex items-center gap-3 text-xs shrink-0">
                                    <Badge variant={getStatusVariant(run.status)}>{run.status}</Badge>
                                    <span className="text-muted-foreground">Seq: {run.last_event_sequence}</span>
                                    <span className="text-muted-foreground">{formatDate(run.created_at)}</span>
                                  </div>
                                </div>
                              </button>

                              {isRunExpanded && (
                                <div className="border-t border-border bg-muted/30 px-16 py-4">
                                  {runEvents.length > 0 ? (
                                    <div className="space-y-2">
                                      <h4 className="text-xs font-semibold text-muted-foreground mb-3">Events ({runEvents.length})</h4>
                                      {runEvents.map((event) => (
                                        <div key={event.id} className="bg-card rounded p-3 border border-border">
                                          <div className="flex items-start justify-between gap-3">
                                            <div className="flex-1 min-w-0">
                                              <div className="flex items-center gap-2 mb-1">
                                                <span className="text-xs font-mono text-muted-foreground">#{event.sequence}</span>
                                                <span className="text-sm font-medium">{formatEventType(event.event_type)}</span>
                                              </div>
                                              {event.payload && Object.keys(event.payload).length > 0 && (
                                                <pre className="text-xs text-muted-foreground mt-2 overflow-x-auto">
                                                  {JSON.stringify(event.payload, null, 2)}
                                                </pre>
                                              )}
                                            </div>
                                            <span className="text-xs text-muted-foreground whitespace-nowrap shrink-0">
                                              {formatDate(event.created_at)}
                                            </span>
                                          </div>
                                        </div>
                                      ))}
                                    </div>
                                  ) : (
                                    <p className="text-sm text-muted-foreground text-center py-4">No events found</p>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground text-center py-8">No runs in this workspace</p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="text-center py-12">
          <Cpu className="mx-auto size-12 text-muted-foreground/40 mb-4" />
          <h3 className="text-lg font-medium mb-1">No Workspaces Yet</h3>
          <p className="text-muted-foreground">Start a conversation to create your first workspace</p>
        </div>
      )}
    </div>
  );
}
