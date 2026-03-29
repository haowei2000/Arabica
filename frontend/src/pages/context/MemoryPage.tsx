import { useState } from 'react';
import { ChevronRight, Brain, Loader2, Play, Trash2, ChevronDown } from 'lucide-react';
import { useWorkspaces, useWorkspaceRuns, useRunEvents } from '@/hooks/useMemory';
import { useDeleteWorkspace } from '@/hooks/useWorkspaces';
import { useMemories } from '@/hooks/useEntityContext';
import { ScrollArea } from '@/components/ui/scroll-area';
import { ViewToggle, type ViewMode } from '@/components/ViewToggle';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/formatDate';

const STATUS_DOT: Record<string, string> = {
  completed: 'bg-green-500',
  finished:  'bg-green-500',
  running:   'bg-yellow-400 animate-pulse',
  active:    'bg-yellow-400 animate-pulse',
  failed:    'bg-red-500',
  error:     'bg-red-500',
};
function statusDot(status: string) {
  return STATUS_DOT[status.toLowerCase()] ?? 'bg-muted-foreground/30';
}

const RUN_STATUS_CHIP: Record<string, string> = {
  finished:  'bg-green-100 text-green-700 border-green-200/70 dark:bg-green-900/30 dark:text-green-300 dark:border-green-800/50',
  completed: 'bg-green-100 text-green-700 border-green-200/70 dark:bg-green-900/30 dark:text-green-300 dark:border-green-800/50',
  failed:    'bg-red-100 text-red-700 border-red-200/70 dark:bg-red-900/30 dark:text-red-300 dark:border-red-800/50',
  error:     'bg-red-100 text-red-700 border-red-200/70 dark:bg-red-900/30 dark:text-red-300 dark:border-red-800/50',
  running:   'bg-yellow-100 text-yellow-700 border-yellow-200/70 dark:bg-yellow-900/30 dark:text-yellow-300 dark:border-yellow-800/50',
};
function runStatusChip(status: string) {
  return RUN_STATUS_CHIP[status.toLowerCase()] ?? 'bg-muted text-muted-foreground border-border/50';
}

export default function MemoryPage() {
  const [expandedWorkspaces, setExpandedWorkspaces] = useState<Set<string>>(new Set());
  const [expandedRuns, setExpandedRuns] = useState<Set<string>>(new Set());
  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const [hiddenEventTypes, setHiddenEventTypes] = useState<Set<string>>(new Set(['agent_token']));
  const [viewMode, setViewMode] = useState<ViewMode>('card');

  const [showShortMemories, setShowShortMemories] = useState(false);
  const { data: workspaceData, isLoading: workspacesLoading } = useWorkspaces({ page_size: 50 });
  const { data: memoriesData } = useMemories({ page_size: 50 });
  const { data: runsData } = useWorkspaceRuns(selectedWorkspace, expandedWorkspaces.has(selectedWorkspace || '') || viewMode === 'drawer');
  const { data: eventsData } = useRunEvents(selectedRun, expandedRuns.has(selectedRun || '') || (viewMode === 'drawer' && !!selectedRun));
  const deleteWorkspaceMutation = useDeleteWorkspace();

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

  // List mode handlers (toggle expand/collapse)
  const toggleWorkspace = (workspaceId: string) => {
    const next = new Set(expandedWorkspaces);
    if (next.has(workspaceId)) { next.delete(workspaceId); setSelectedWorkspace(null); }
    else { next.add(workspaceId); setSelectedWorkspace(workspaceId); }
    setExpandedWorkspaces(next);
  };
  const toggleRun = (runId: string) => {
    const next = new Set(expandedRuns);
    if (next.has(runId)) { next.delete(runId); setSelectedRun(null); }
    else { next.add(runId); setSelectedRun(runId); }
    setExpandedRuns(next);
  };

  // Drawer (Miller column) handlers — select, don't toggle
  const selectWorkspace = (id: string) => {
    setSelectedWorkspace(id === selectedWorkspace ? null : id);
    setSelectedRun(null);
  };
  const selectRun = (id: string) => {
    setSelectedRun(id === selectedRun ? null : id);
  };

  const handleModeToggle = (m: ViewMode) => setViewMode(m);

  const handleDeleteWorkspace = async (e: React.MouseEvent, workspaceId: string, name: string) => {
    e.stopPropagation();
    if (!confirm(`Are you sure you want to delete workspace "${name}" and all its runs and events? This action cannot be undone.`)) return;
    try {
      await deleteWorkspaceMutation.mutateAsync(workspaceId);
      // Clean up local state
      if (selectedWorkspace === workspaceId) setSelectedWorkspace(null);
      if (selectedRun) setSelectedRun(null);
      expandedWorkspaces.delete(workspaceId);
      setExpandedWorkspaces(new Set(expandedWorkspaces));
    } catch (error) {
      alert('Failed to delete workspace. Please try again.');
    }
  };

  if (workspacesLoading) {
    return <div className="flex items-center justify-center py-16"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>;
  }

  // ── Event filter bar (shared between modes) ────────────────────────────────
  const filterBar = allEventTypes.length > 0 && (
    <div className="flex items-center gap-2 mb-4 flex-wrap">
      <span className="text-[10px] text-muted-foreground shrink-0">Filter:</span>
      {allEventTypes.map((eventType) => {
        const isHidden = hiddenEventTypes.has(eventType);
        return (
          <button key={eventType} type="button" onClick={() => toggleEventTypeFilter(eventType)}
            className={cn('text-[10px] px-2.5 py-1 rounded-full border font-medium transition-colors',
              isHidden
                ? 'bg-muted/50 text-muted-foreground/40 border-border/30 line-through'
                : 'bg-muted/50 text-muted-foreground border-border/50 hover:border-foreground/30'
            )}>
            {eventType}
          </button>
        );
      })}
    </div>
  );

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Brain className="size-3.5 text-muted-foreground" />
            <h2 className="text-sm font-semibold">Memory</h2>
          </div>
          <span className="text-xs text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">
            {workspaces.length}
          </span>
        </div>
        <ViewToggle mode={viewMode} onToggle={handleModeToggle} />
      </div>

      {filterBar}

      {/* Short Memories section */}
      {memoriesData && memoriesData.total > 0 && (
        <div className="mb-4 rounded-xl border border-border bg-card overflow-hidden">
          <button
            type="button"
            className="w-full flex items-center gap-2 px-4 py-2.5 hover:bg-muted/40 transition-colors text-left"
            onClick={() => setShowShortMemories((v) => !v)}
          >
            <Brain className="size-3.5 text-muted-foreground shrink-0" />
            <span className="text-xs font-medium">Short Memories</span>
            <span className="text-[10px] text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">{memoriesData.total}</span>
            <ChevronDown className={cn('size-3.5 text-muted-foreground ml-auto transition-transform', showShortMemories && 'rotate-180')} />
          </button>
          {showShortMemories && (
            <div className="divide-y divide-border/50 border-t border-border/50">
              {memoriesData.items.map((mem) => (
                <div key={mem.id} className="px-4 py-2.5 space-y-1">
                  <p className="text-xs text-foreground/80 leading-relaxed">{mem.glance ?? mem.content.slice(0, 120)}</p>
                  {mem.path && <p className="text-[10px] font-mono text-muted-foreground/60">{mem.path}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {workspaces.length === 0 ? (
        <div className="text-center py-16 border border-dashed border-border rounded-xl">
          <Brain className="mx-auto size-8 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground">No workspaces yet</p>
        </div>
      ) : viewMode === 'card' ? (
        // ── Card grid ─────────────────────────────────────────────────────────
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {workspaces.map((workspace) => (
            <div key={workspace.id} className="rounded-xl border border-border bg-card p-4 flex flex-col gap-2.5 hover:bg-muted/20 transition-colors group relative">
              <button
                type="button"
                onClick={(e) => handleDeleteWorkspace(e, workspace.id, workspace.name)}
                disabled={deleteWorkspaceMutation.isPending}
                className="absolute top-2 right-2 p-1.5 rounded-md hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors opacity-0 group-hover:opacity-100"
                title="Delete workspace"
              >
                <Trash2 className="size-3" />
              </button>
              <div className="flex items-start gap-2.5">
                <span className={cn('size-2 rounded-full mt-1 shrink-0', statusDot(workspace.status))} />
                <p className="text-sm font-semibold leading-snug flex-1 min-w-0 truncate pr-6">{workspace.name}</p>
              </div>
              {workspace.description && <p className="text-xs text-muted-foreground line-clamp-2">{workspace.description}</p>}
              <div className="flex flex-wrap gap-1.5">
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 capitalize">
                  {workspace.status}
                </span>
              </div>
              <div className="flex items-center gap-3 text-[10px] text-muted-foreground mt-auto">
                <span className="inline-flex items-center gap-0.5 tabular-nums"><Play className="size-2.5" />{workspace.run_count}</span>
                <span className="ml-auto">{formatRelativeTime(workspace.created_at)}</span>
              </div>
            </div>
          ))}
        </div>
      ) : viewMode === 'list' ? (
        // ── List view: nested folder tree ─────────────────────────────────────
        <div className="rounded-xl border border-border bg-card overflow-visible divide-y divide-border/50">
          {workspaces.map((workspace) => {
            const isExpanded = expandedWorkspaces.has(workspace.id);
            const workspaceRuns = isExpanded ? runs : [];
            return (
              <div key={workspace.id} className="group relative">
                <div role="button" tabIndex={0} onClick={() => toggleWorkspace(workspace.id)}
                  onKeyDown={(e) => e.key === 'Enter' && toggleWorkspace(workspace.id)}
                  className="w-full flex items-center gap-3 px-4 py-3 hover:bg-muted/40 transition-colors cursor-pointer">
                  <span className={cn('size-2 rounded-full shrink-0', statusDot(workspace.status))} />
                  <ChevronRight className={cn('size-3 text-muted-foreground/50 transition-transform shrink-0', isExpanded && 'rotate-90')} />
                  <span className="text-sm font-medium flex-1 min-w-0 truncate">{workspace.name}</span>
                  {workspace.description && (
                    <span className="text-[10px] text-muted-foreground truncate hidden sm:block max-w-48">{workspace.description}</span>
                  )}
                  <span className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground/60 tabular-nums shrink-0"><Play className="size-2.5" />{workspace.run_count}</span>
                  <span className="text-[10px] text-muted-foreground/50 shrink-0">{formatRelativeTime(workspace.created_at)}</span>
                  <button
                    type="button"
                    onClick={(e) => handleDeleteWorkspace(e, workspace.id, workspace.name)}
                    disabled={deleteWorkspaceMutation.isPending}
                    className="p-1.5 rounded-md hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors opacity-0 group-hover:opacity-100"
                    title="Delete workspace"
                  >
                    <Trash2 className="size-3" />
                  </button>
                </div>

                {isExpanded && (
                  <div className="bg-muted/20 border-t border-border/50">
                    {workspaceRuns.length > 0 ? (
                      <div className="divide-y divide-border/30">
                        {workspaceRuns.map((run) => {
                          const isRunExpanded = expandedRuns.has(run.id);
                          const runEvents = isRunExpanded ? events : [];
                          return (
                            <div key={run.id}>
                              <button type="button" onClick={() => toggleRun(run.id)}
                                className="w-full flex items-center gap-3 pl-10 pr-4 py-2.5 hover:bg-muted/40 transition-colors text-left">
                                <span className={cn('size-1.5 rounded-full shrink-0', statusDot(run.status))} />
                                <ChevronRight className={cn('size-3 text-muted-foreground/40 transition-transform shrink-0', isRunExpanded && 'rotate-90')} />
                                <span className="text-xs font-medium flex-1 min-w-0 truncate">
                                  {run.input_data?.message ? String(run.input_data.message).slice(0, 60) : `Run ${run.id.slice(0, 8)}`}
                                </span>
                                <span className={cn('text-[10px] px-2 py-0.5 rounded-full border shrink-0', runStatusChip(run.status))}>{run.status}</span>
                                <span className="text-[10px] text-muted-foreground/50 tabular-nums shrink-0">seq:{run.last_event_sequence}</span>
                                <span className="text-[10px] text-muted-foreground/50 shrink-0">{formatRelativeTime(run.created_at)}</span>
                              </button>
                              {isRunExpanded && (
                                <div className="bg-muted/30 border-t border-border/30">
                                  {runEvents.length > 0 ? (
                                    <div className="divide-y divide-border/20">
                                      {runEvents.map((event) => (
                                        <div key={event.id} className="flex items-start gap-3 pl-16 pr-4 py-2 hover:bg-muted/20 transition-colors">
                                          <span className="text-[10px] font-mono text-muted-foreground/50 w-5 text-right shrink-0 mt-0.5 tabular-nums">{event.sequence}</span>
                                          <div className="flex-1 min-w-0">
                                            <span className="text-[10px] font-semibold text-foreground/70">{event.event_type}</span>
                                            {event.payload && Object.keys(event.payload).length > 0 && (
                                              <pre className="text-[10px] text-muted-foreground mt-1 overflow-x-auto leading-relaxed font-mono whitespace-pre-wrap break-all">
                                                {JSON.stringify(event.payload, null, 2)}
                                              </pre>
                                            )}
                                          </div>
                                          <span className="text-[10px] text-muted-foreground/40 shrink-0 whitespace-nowrap">{formatRelativeTime(event.created_at)}</span>
                                        </div>
                                      ))}
                                    </div>
                                  ) : (
                                    <p className="text-xs text-muted-foreground/50 pl-16 py-2">No events</p>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground/50 pl-10 py-3">No runs</p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        // ── Drawer view: Miller columns ────────────────────────────────────────
        <div className="rounded-xl border border-border bg-card overflow-hidden">
          <div className="flex h-[500px]">

            {/* Pane 1: Workspaces */}
            <div className="w-48 shrink-0 border-r border-border flex flex-col">
              <div className="px-3 py-2 border-b border-border/50 bg-muted/20 shrink-0">
                <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">Workspaces</span>
              </div>
              <ScrollArea className="flex-1">
                {workspaces.map((ws) => (
                  <div key={ws.id} className="group relative">
                    <div role="button" tabIndex={0} onClick={() => selectWorkspace(ws.id)}
                      onKeyDown={(e) => e.key === 'Enter' && selectWorkspace(ws.id)}
                      className={cn(
                        'w-full flex items-center gap-2 px-3 py-2.5 cursor-pointer',
                        'border-b border-border/30 last:border-0 hover:bg-muted/40 transition-colors',
                        selectedWorkspace === ws.id && 'bg-primary/8 border-l-2 border-l-primary'
                      )}>
                      <span className={cn('size-1.5 rounded-full shrink-0', statusDot(ws.status))} />
                      <span className="text-xs font-medium flex-1 min-w-0 truncate">{ws.name}</span>
                      <button
                        type="button"
                        onClick={(e) => handleDeleteWorkspace(e, ws.id, ws.name)}
                        disabled={deleteWorkspaceMutation.isPending}
                        className="p-1 rounded-md hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors opacity-0 group-hover:opacity-100 z-10"
                        title="Delete workspace"
                      >
                        <Trash2 className="size-3" />
                      </button>
                      <ChevronRight className="size-3 text-muted-foreground/40 shrink-0" />
                    </div>
                  </div>
                ))}
              </ScrollArea>
            </div>

            {/* Pane 2: Runs — slides in when workspace selected */}
            <div className={cn(
              'border-r border-border flex flex-col overflow-hidden',
              'transition-[width,opacity] duration-200 ease-in-out',
              selectedWorkspace ? 'w-64 opacity-100' : 'w-0 opacity-0'
            )}>
              <div className="px-3 py-2 border-b border-border/50 bg-muted/20 shrink-0 min-w-0">
                <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">Runs</span>
              </div>
              <ScrollArea className="flex-1">
                {runs.map((run) => (
                  <button key={run.id} type="button" onClick={() => selectRun(run.id)}
                    className={cn(
                      'w-full flex items-center gap-2 px-3 py-2.5 text-left',
                      'border-b border-border/30 last:border-0 hover:bg-muted/40 transition-colors',
                      selectedRun === run.id && 'bg-primary/8 border-l-2 border-l-primary'
                    )}>
                    <span className={cn('size-1.5 rounded-full shrink-0', statusDot(run.status))} />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium truncate">
                        {run.input_data?.message ? String(run.input_data.message).slice(0, 36) : `Run ${run.id.slice(0, 8)}`}
                      </p>
                      <p className="text-[10px] text-muted-foreground truncate">
                        {run.status} · {formatRelativeTime(run.created_at)}
                      </p>
                    </div>
                    <ChevronRight className="size-3 text-muted-foreground/40 shrink-0" />
                  </button>
                ))}
                {selectedWorkspace && runs.length === 0 && (
                  <p className="text-[10px] text-muted-foreground/50 px-3 py-4">No runs</p>
                )}
              </ScrollArea>
            </div>

            {/* Pane 3: Events — fills remaining space */}
            <div className="flex-1 flex flex-col min-w-0">
              <div className="px-3 py-2 border-b border-border/50 bg-muted/20 shrink-0 flex items-center gap-2">
                <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide">Events</span>
                {selectedRun && events.length > 0 && (
                  <span className="text-[10px] text-muted-foreground/50 tabular-nums">{events.length}</span>
                )}
              </div>
              <ScrollArea className="flex-1">
                {events.length > 0 ? (
                  events.map((event) => (
                    <div key={event.id} className="flex items-start gap-3 px-3 py-2.5 border-b border-border/20 last:border-0 hover:bg-muted/20 transition-colors">
                      <span className="text-[10px] font-mono text-muted-foreground/50 w-5 text-right shrink-0 mt-0.5 tabular-nums">{event.sequence}</span>
                      <div className="flex-1 min-w-0">
                        <p className="text-[10px] font-semibold text-foreground/70">{event.event_type}</p>
                        {event.payload && Object.keys(event.payload).length > 0 && (
                          <pre className="text-[10px] text-muted-foreground mt-1 overflow-x-auto leading-relaxed font-mono whitespace-pre-wrap break-all max-h-20 overflow-y-auto">
                            {JSON.stringify(event.payload, null, 2)}
                          </pre>
                        )}
                      </div>
                      <span className="text-[10px] text-muted-foreground/40 shrink-0 whitespace-nowrap">
                        {formatRelativeTime(event.created_at)}
                      </span>
                    </div>
                  ))
                ) : (
                  <div className="flex flex-col items-center justify-center h-full py-12 gap-2">
                    <span className="text-[10px] text-muted-foreground/30">
                      {!selectedWorkspace ? 'Select a workspace' : !selectedRun ? 'Select a run' : 'No events'}
                    </span>
                  </div>
                )}
              </ScrollArea>
            </div>

          </div>
        </div>
      )}
    </div>
  );
}
