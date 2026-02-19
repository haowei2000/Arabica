import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Loader2, ChevronDown, ChevronRight, Play, Layers, ListTodo, FileOutput, Plus } from 'lucide-react';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useRuns, useRunEvents } from '@/hooks/useRuns';
import { useStreamingChat } from '@/hooks/useStreamingChat';
import { MessageRole } from '@/types/message';
import { formatRelativeTime } from '@/utils/formatDate';
import { ThinkingBlock, ToolCallCard, PlanStepList, ApprovalCard } from '@/components/AgentEvents';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import type { Run } from '@/types/run';
import type { Event } from '@/types/event';

import WorkspaceContextTree from '@/components/WorkspaceContextTree';
import { useWorkspaceStream } from '@/hooks/useWorkspaceStream';
import { useRunEventsStore } from '@/stores/useRunEventsStore';

// ─── Run timeline helpers ──────────────────────────────────────────────────

// Keyframe for the running-event sweep animation (injected once)
const SHIMMER_STYLE = `
@keyframes run-sweep {
  0%   { background-position: 200% center; }
  100% { background-position: -200% center; }
}
.event-running {
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(250,204,21,0.12) 40%,
    rgba(250,204,21,0.20) 50%,
    rgba(250,204,21,0.12) 60%,
    transparent 100%
  );
  background-size: 200% 100%;
  animation: run-sweep 2s linear infinite;
}
`;

const EVENT_LABEL: Record<string, string> = {
  'user.message':     'User',
  'agent.thinking':   'Thinking',
  'agent.text':       'Response',
  'tool.called':      'Tool call',
  'tool.result':      'Tool result',
  'run.state.change': 'State',
};

function eventSummary(event: Event): string {
  const p = event.payload;
  switch (event.event_type) {
    case 'user.message':     return String(p.message || p.content || '').slice(0, 80);
    case 'agent.thinking':   return String(p.content || p.thinking || '').slice(0, 80);
    case 'agent.text':       return String(p.content || p.text || '').slice(0, 80);
    case 'tool.called':      return String(p.tool_name || p.name || '');
    case 'tool.result':      return String(p.tool_name || p.name || '');
    case 'run.state.change': return String(p.to_state || p.status || '');
    default:                 return '';
  }
}

function isFailedEvent(event: Event): boolean {
  if (event.event_type.includes('fail') || event.event_type.includes('error')) return true;
  if (event.event_type === 'run.state.change') {
    return event.payload.to_state === 'failed' || event.payload.status === 'failed';
  }
  return false;
}

function RunEventRow({
  event,
  isLast,
  isActiveEdge,   // last event of a currently-running run → sweep animation
}: {
  event: Event;
  isLast: boolean;
  isActiveEdge: boolean;
}) {
  const label   = EVENT_LABEL[event.event_type] ?? event.event_type;
  const summary = eventSummary(event);
  const failed  = isFailedEvent(event);

  return (
    <div className={cn(
      'relative flex items-center gap-2 py-1 px-3',
      !isLast  && 'border-b border-border/30',
      failed   && 'bg-red-500/8',
      isActiveEdge && 'event-running',
    )}>
      <span className={cn(
        'size-1 rounded-full shrink-0',
        failed       ? 'bg-red-500/70' :
        isActiveEdge ? 'bg-yellow-400/80 animate-pulse' :
                       'bg-muted-foreground/25'
      )} />
      <span className={cn(
        'text-[10px] font-medium shrink-0',
        failed       ? 'text-red-400' :
        isActiveEdge ? 'text-yellow-400/90' :
                       'text-foreground/60'
      )}>
        {label}
      </span>
      {summary && (
        <span className={cn(
          'text-[10px] truncate flex-1 min-w-0',
          failed ? 'text-red-400/60' : 'text-muted-foreground/60'
        )}>
          {summary}
        </span>
      )}
      <span className="text-[10px] text-muted-foreground/40 shrink-0 tabular-nums ml-auto">
        {formatRelativeTime(event.created_at)}
      </span>
    </div>
  );
}

function RunTimelineItem({
  run,
  isActive,
  latestSseEvent,
  onSelect,
}: {
  run: Run;
  isActive: boolean;
  latestSseEvent: Event | undefined;
  onSelect: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { data: eventsData, isLoading: eventsLoading } = useRunEvents(run.id, expanded);
  const events = eventsData?.items ?? [];

  const fullMessage = run.input_data?.message ? String(run.input_data.message) : '';
  const title = fullMessage ? fullMessage.slice(0, 55) : 'New run';

  const isRunning = run.status === 'running' || run.status === 'pending';
  const isFailed  = run.status === 'failed';

  const previewEvent: Event | undefined =
    latestSseEvent ??
    (events.length > 0 ? events[events.length - 1] : undefined);
  const validPreview = previewEvent && previewEvent.event_type !== 'agent.heartbeat';

  const dotCls = isRunning
    ? 'bg-yellow-400 animate-pulse'
    : isFailed
      ? 'bg-red-500'
      : run.status === 'finished'
        ? 'bg-green-500'
        : 'bg-muted-foreground/30';

  return (
    <div className={cn(
      'group relative rounded-lg border transition-colors',
      isActive ? 'border-primary/20 bg-primary/5' : 'border-border bg-card hover:bg-muted/30',
    )}>
      {/* ── Compact single-line header ── */}
      <div
        className="flex items-center gap-2 px-2.5 py-2 cursor-pointer select-none transition-colors"
        onClick={() => { onSelect(run.id); setExpanded((v) => !v); }}
      >
        <span className={cn('size-1.5 rounded-full shrink-0', dotCls)} />

        <span className={cn(
          'text-xs truncate flex-1 min-w-0',
          isActive ? 'text-foreground font-medium' : 'text-foreground/75'
        )}>
          {title}
        </span>

        {/* Live preview — only when running */}
        {isRunning && validPreview && (
          <span className="text-[10px] text-yellow-400/80 truncate max-w-20 shrink-0 hidden sm:block">
            {EVENT_LABEL[previewEvent.event_type] ?? previewEvent.event_type}
          </span>
        )}

        <span className="text-[10px] text-muted-foreground/40 shrink-0 tabular-nums">
          {formatRelativeTime(run.created_at)}
        </span>

        <span className={cn(
          'shrink-0 transition-opacity text-muted-foreground/40',
          expanded ? 'opacity-100' : 'opacity-0 group-hover:opacity-70'
        )}>
          {expanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        </span>
      </div>

      {/* ── Hover tooltip ── */}
      <div className="absolute right-0 top-full mt-1 z-50 w-72 rounded-xl border border-border bg-card shadow-lg shadow-black/10 p-3 invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-[opacity,visibility] duration-150 pointer-events-none">
        <div className="space-y-2 text-xs">
          {fullMessage ? (
            <p className="text-foreground/85 leading-relaxed line-clamp-4 break-words">{fullMessage}</p>
          ) : (
            <p className="text-muted-foreground italic">No message</p>
          )}
          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pt-1.5 border-t border-border/50 text-muted-foreground">
            <span>Status</span>
            <span className={cn(
              isRunning ? 'text-yellow-400' : isFailed ? 'text-red-500' : run.status === 'finished' ? 'text-green-500' : 'text-foreground'
            )}>{run.status}</span>
            <span>Events</span><span className="text-foreground tabular-nums">{run.last_event_sequence ?? 0}</span>
            <span>Created</span><span className="text-foreground">{formatRelativeTime(run.created_at)}</span>
          </div>
          {validPreview && (
            <div className="pt-1.5 border-t border-border/30">
              <span className="text-[10px] text-muted-foreground/60 uppercase tracking-wide">
                {EVENT_LABEL[previewEvent.event_type] ?? previewEvent.event_type}
              </span>
              {eventSummary(previewEvent) && (
                <p className="text-[10px] text-muted-foreground mt-0.5 line-clamp-2">{eventSummary(previewEvent)}</p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── Expanded event list ── */}
      {expanded && (
        <div className="border-t border-border/40 mx-1 mb-1 rounded-b-md overflow-hidden bg-muted/20">
          {eventsLoading ? (
            <div className="flex justify-center py-3">
              <Loader2 className="size-3 animate-spin text-muted-foreground/40" />
            </div>
          ) : (() => {
            const visible = events.filter((e) => e.event_type !== 'agent.heartbeat');
            return visible.length === 0 ? (
              <p className="text-[10px] text-muted-foreground/40 px-3 py-2">No events</p>
            ) : (
              <ScrollArea viewportClassName="max-h-48">
                <div>
                  {visible.map((e, i) => (
                    <RunEventRow
                      key={e.id}
                      event={e}
                      isLast={i === visible.length - 1}
                      isActiveEdge={isRunning && i === visible.length - 1}
                    />
                  ))}
                </div>
              </ScrollArea>
            );
          })()}
        </div>
      )}
    </div>
  );
}

// ─── Main component ────────────────────────────────────────────────────────────

export default function WorkspaceConsole() {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const { currentWorkspaceId, currentWorkspaceAppId } = useWorkspaceStore();
  const {
    currentRunId,
    messages,
    streamingMessage,
    isStreaming,
    isLoadingConversation,
    thinkingContent,
    activeToolCalls,
    planSteps,
    pendingApprovals,
    startNewRun,
    loadRun,
  } = useChatStore();

  const { data: runsData, isLoading: runsLoading } = useRuns(currentWorkspaceId || '', {
    page: 1,
    page_size: 50,
  });

  const latestRunEvents = useRunEventsStore((s) => s.latestEvents);

  const { sendMessage, stopStreaming, approveToolCall } = useStreamingChat(
    currentWorkspaceId || '',
    currentWorkspaceAppId
  );

  // Live workspace event stream — keeps runs list and context tree up to date
  useWorkspaceStream(currentWorkspaceId);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingMessage, thinkingContent, activeToolCalls, planSteps, pendingApprovals]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isStreaming || !currentWorkspaceId) return;
    const message = input.trim();
    setInput('');
    await sendMessage(message);
  };

  if (!currentWorkspaceId) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground">
        <p className="text-xs text-muted-foreground/50">Select a workspace to start</p>
      </div>
    );
  }

  return (
    <>
    <style>{SHIMMER_STYLE}</style>
    <div className="flex-1 overflow-hidden flex flex-col lg:flex-row bg-background">
      {/* ── Chat Column ── */}
      <div className="flex flex-col flex-1 lg:border-r border-border">
        <ScrollArea className="flex-1 px-6 py-4">
          <div className="space-y-4">
            {isLoadingConversation ? (
              <div className="flex items-center justify-center h-40">
                <Loader2 className="size-10 animate-spin text-muted-foreground" />
              </div>
            ) : messages.length === 0 && !streamingMessage ? (
              <div className="text-center text-muted-foreground mt-20">
                <p className="text-xs text-muted-foreground/40">Send a message to start</p>
              </div>
            ) : null}

            {messages.map((message) => (
              <div
                key={message.id}
                className={cn('flex gap-3', message.role === MessageRole.USER ? 'justify-end' : 'justify-start')}
              >
                {message.role === MessageRole.ASSISTANT && (
                  <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-primary-foreground text-xs shrink-0 mt-0.5 font-medium">
                    AI
                  </div>
                )}

                {message.role === MessageRole.USER ? (
                  <div className="max-w-2xl rounded-lg px-4 py-3 bg-primary text-primary-foreground">
                    <p className="text-sm">{message.content}</p>
                  </div>
                ) : (
                  <div className="max-w-2xl w-full space-y-2">
                    {message.thinkingContent && (
                      <ThinkingBlock content={message.thinkingContent} defaultCollapsed />
                    )}
                    {message.toolCalls && message.toolCalls.length > 0 && (
                      <div className="space-y-1">
                        {message.toolCalls.map((tc) => (
                          <ToolCallCard key={tc.tool_id} toolCall={tc} />
                        ))}
                      </div>
                    )}
                    {message.planSteps && message.planSteps.length > 0 && (
                      <PlanStepList steps={message.planSteps} />
                    )}
                    {message.content && (
                      <div className="rounded-lg px-4 py-3 bg-card border border-border">
                        <div className="prose prose-sm dark:prose-invert max-w-none">
                          <ReactMarkdown>{message.content}</ReactMarkdown>
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {message.role === MessageRole.USER && (
                  <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-muted-foreground text-xs shrink-0 font-medium">
                    You
                  </div>
                )}
              </div>
            ))}

            {isStreaming && (
              <div className="flex gap-3 justify-start">
                <div className="w-8 h-8 rounded-full bg-primary flex items-center justify-center text-primary-foreground text-xs shrink-0 mt-0.5 font-medium">
                  AI
                </div>
                <div className="max-w-2xl w-full space-y-2">
                  {thinkingContent && <ThinkingBlock content={thinkingContent} />}
                  {activeToolCalls.length > 0 && (
                    <div className="space-y-1">
                      {activeToolCalls.map((tc) => (
                        <ToolCallCard key={tc.tool_id} toolCall={tc} />
                      ))}
                    </div>
                  )}
                  {planSteps.length > 0 && <PlanStepList steps={planSteps} />}
                  {pendingApprovals.map((pending) => (
                    <ApprovalCard key={pending.tool_id} pending={pending} onApprove={approveToolCall} />
                  ))}
                  {streamingMessage && (
                    <div className="rounded-lg px-4 py-3 bg-card border border-border">
                      <div className="prose prose-sm dark:prose-invert max-w-none">
                        <ReactMarkdown>{streamingMessage}</ReactMarkdown>
                      </div>
                    </div>
                  )}
                  <div className="flex items-center gap-1 text-muted-foreground">
                    <span className={cn('w-2 h-2 rounded-full animate-pulse', pendingApprovals.length > 0 ? 'bg-amber-500' : 'bg-primary')} />
                    <span className="text-xs">
                      {pendingApprovals.length > 0
                        ? 'Waiting for your approval...'
                        : activeToolCalls.some((tc) => tc.status === 'pending')
                          ? 'Executing tools...'
                          : streamingMessage
                            ? 'Assistant is typing...'
                            : 'Preparing response...'}
                    </span>
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </ScrollArea>

        <div className="border-t border-border bg-card px-6 py-4">
          <form onSubmit={handleSubmit} className="flex gap-3">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type a message..."
              disabled={isStreaming}
              className="flex-1"
            />
            {isStreaming ? (
              <Button type="button" variant="destructive" onClick={stopStreaming}>
                Stop
              </Button>
            ) : (
              <Button type="submit" disabled={!input.trim()}>
                Send
              </Button>
            )}
          </form>
        </div>
      </div>

      {/* ── Right Panel ── */}
      <aside className="w-full lg:w-96 border-t lg:border-t-0 border-border bg-card flex flex-col">
        <Tabs defaultValue="runs" className="flex flex-col flex-1 min-h-0">
          <div className="px-4 pt-3 border-b border-border shrink-0">
            <TabsList className="w-full">
              <TabsTrigger value="runs" className="flex-1 gap-1" title="Runs"><Play className="size-3" /><span className="hidden sm:inline text-xs">Runs</span></TabsTrigger>
              <TabsTrigger value="context" className="flex-1 gap-1" title="Context"><Layers className="size-3" /><span className="hidden sm:inline text-xs">Context</span></TabsTrigger>
              <TabsTrigger value="tasks" className="flex-1 gap-1" title="Tasks"><ListTodo className="size-3" /><span className="hidden sm:inline text-xs">Tasks</span></TabsTrigger>
              <TabsTrigger value="results" className="flex-1 gap-1" title="Results"><FileOutput className="size-3" /><span className="hidden sm:inline text-xs">Results</span></TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="runs" className="flex-1 overflow-y-auto p-4 mt-0">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5">
                <Play className="size-3.5 text-muted-foreground" />
                <h2 className="text-xs font-semibold">Runs</h2>
              </div>
              <Button size="icon" className="size-6" onClick={startNewRun} title="New Run">
                <Plus className="size-3" />
              </Button>
            </div>
            {runsLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="size-6 animate-spin text-muted-foreground" />
              </div>
            ) : runsData?.items && runsData.items.length > 0 ? (
              <div className="space-y-1">
                {runsData.items.map((run) => (
                  <RunTimelineItem
                    key={run.id}
                    run={run}
                    isActive={currentRunId === run.id}
                    latestSseEvent={latestRunEvents[run.id]}
                    onSelect={loadRun}
                  />
                ))}
              </div>
            ) : (
              <div className="text-center py-12 text-muted-foreground">
                <Play className="mx-auto size-6 text-muted-foreground/20 mb-2" />
                <p className="text-[10px] text-muted-foreground/50">No runs yet</p>
              </div>
            )}
          </TabsContent>

          <TabsContent value="context" className="flex-1 overflow-hidden p-4 mt-0 flex flex-col">
            <WorkspaceContextTree workspaceId={currentWorkspaceId} />
          </TabsContent>

          <TabsContent value="tasks" className="flex-1 overflow-y-auto p-4 mt-0">
            <div className="flex items-center gap-1.5 mb-3">
              <ListTodo className="size-3.5 text-muted-foreground" />
              <span className="text-xs font-semibold">Tasks</span>
            </div>
            <div className="text-center py-10">
              <ListTodo className="mx-auto size-6 text-muted-foreground/20 mb-2" />
              <p className="text-[10px] text-muted-foreground/50">No tasks yet</p>
            </div>
          </TabsContent>

          <TabsContent value="results" className="flex-1 overflow-y-auto p-4 mt-0">
            <div className="flex items-center gap-1.5 mb-3">
              <FileOutput className="size-3.5 text-muted-foreground" />
              <span className="text-xs font-semibold">Results</span>
            </div>
            <div className="text-center py-10">
              <FileOutput className="mx-auto size-6 text-muted-foreground/20 mb-2" />
              <p className="text-[10px] text-muted-foreground/50">No results yet</p>
            </div>
          </TabsContent>
        </Tabs>
      </aside>
    </div>
    </>
  );
}
