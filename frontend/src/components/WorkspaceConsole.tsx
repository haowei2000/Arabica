import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Loader2, ChevronDown, ChevronRight } from 'lucide-react';
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
      'relative flex items-start gap-2.5 py-1.5 px-3',
      !isLast  && 'border-b border-border/40',
      failed   && 'bg-red-500/8',
      isActiveEdge && 'event-running',
    )}>
      <span className={cn(
        'size-1 rounded-full shrink-0 mt-2',
        failed       ? 'bg-red-500/70' :
        isActiveEdge ? 'bg-yellow-400/80 animate-pulse' :
                       'bg-muted-foreground/30'
      )} />
      <div className="flex-1 min-w-0">
        <span className={cn(
          'text-xs font-medium',
          failed       ? 'text-red-400' :
          isActiveEdge ? 'text-yellow-400/90' :
                         'text-foreground/70'
        )}>
          {label}
        </span>
        {summary && (
          <p className={cn(
            'text-[11px] truncate mt-0.5 leading-snug',
            failed ? 'text-red-400/70' : 'text-muted-foreground'
          )}>
            {summary}
          </p>
        )}
      </div>
      <span className="text-[10px] text-muted-foreground/60 shrink-0 mt-0.5">
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

  const title = run.input_data?.message
    ? String(run.input_data.message).slice(0, 60)
    : 'New run';

  const isRunning = run.status === 'running' || run.status === 'pending';
  const isFailed  = run.status === 'failed';

  // Latest event preview: SSE live data takes priority, else last loaded event
  const previewEvent: Event | undefined =
    latestSseEvent ??
    (events.length > 0 ? events[events.length - 1] : undefined);
  const validPreview = previewEvent && previewEvent.event_type !== 'agent.heartbeat';

  return (
    <div className={cn(
      'rounded-lg border transition-colors',
      isActive   ? 'border-border bg-muted/40' :
      isFailed   ? 'border-border bg-card' :
                   'border-border bg-card',
    )}>
      {/* Header */}
      <div
        className="flex items-start gap-2.5 px-3 py-2.5 cursor-pointer select-none hover:bg-muted/30 rounded-lg transition-colors"
        onClick={() => { onSelect(run.id); setExpanded((v) => !v); }}
      >
        {/* Status indicator */}
        <span className={cn(
          'size-1.5 rounded-full shrink-0 mt-[5px]',
          isRunning            ? 'bg-yellow-400 animate-pulse' :
          isFailed             ? 'bg-red-500' :
          run.status === 'finished' ? 'bg-green-500' :
                                 'bg-muted-foreground/30'
        )} />

        <div className="flex-1 min-w-0">
          {/* Title + status */}
          <div className="flex items-baseline gap-2">
            <p className={cn(
              'text-sm truncate flex-1 leading-snug',
              isActive ? 'text-foreground font-medium' : 'text-foreground/80'
            )}>
              {title}
            </p>
            <span className={cn(
              'text-[10px] shrink-0 font-mono',
              isRunning                 ? 'text-yellow-400' :
              isFailed                  ? 'text-red-500' :
              run.status === 'finished' ? 'text-green-500' :
                                          'text-muted-foreground/60'
            )}>
              {run.status}
            </span>
          </div>

          {/* Latest event preview */}
          {validPreview && (
            <p className="text-[11px] text-muted-foreground truncate mt-0.5 leading-snug">
              <span className="text-foreground/50 font-medium">
                {EVENT_LABEL[previewEvent.event_type] ?? previewEvent.event_type}
              </span>
              {eventSummary(previewEvent) && (
                <span className="ml-1">{eventSummary(previewEvent)}</span>
              )}
            </p>
          )}

          <p className="text-[10px] text-muted-foreground/50 mt-0.5">
            {formatRelativeTime(run.created_at)}
          </p>
        </div>

        <span className="text-muted-foreground/40 mt-0.5 shrink-0">
          {expanded
            ? <ChevronDown className="size-3" />
            : <ChevronRight className="size-3" />}
        </span>
      </div>

      {/* Expanded events */}
      {expanded && (
        <div className="border-t border-border/50 mx-1 mb-1 rounded-b-md overflow-hidden bg-muted/20">
          {eventsLoading ? (
            <div className="flex justify-center py-3">
              <Loader2 className="size-3.5 animate-spin text-muted-foreground/50" />
            </div>
          ) : events.length === 0 ? (
            <p className="text-xs text-muted-foreground/50 px-3 py-2">No events yet.</p>
          ) : (() => {
            const visible = events.filter((e) => e.event_type !== 'agent.heartbeat');
            return (
              <ScrollArea viewportClassName="max-h-56">
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
        Select a workspace to start chatting.
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
                <p className="text-lg">Start a new conversation</p>
                <p className="text-sm mt-2">Send a message to create a new run</p>
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
              <TabsTrigger value="runs" className="flex-1">Runs</TabsTrigger>
              <TabsTrigger value="context" className="flex-1">Context</TabsTrigger>
              <TabsTrigger value="tasks" className="flex-1">Tasks</TabsTrigger>
              <TabsTrigger value="results" className="flex-1">Results</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="runs" className="flex-1 overflow-y-auto p-4 mt-0">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold">Run History</h2>
              <Button size="sm" onClick={startNewRun}>New Run</Button>
            </div>
            {runsLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="size-6 animate-spin text-muted-foreground" />
              </div>
            ) : runsData?.items && runsData.items.length > 0 ? (
              <div className="space-y-2">
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
                <p className="text-sm">No runs yet</p>
                <p className="text-xs mt-1">Start a run to see history</p>
              </div>
            )}
          </TabsContent>

          <TabsContent value="context" className="flex-1 overflow-hidden p-4 mt-0 flex flex-col">
            <WorkspaceContextTree workspaceId={currentWorkspaceId} />
          </TabsContent>

          <TabsContent value="tasks" className="flex-1 overflow-y-auto p-4 mt-0">
            <p className="text-sm font-semibold mb-2">Tasks</p>
            <p className="text-sm text-muted-foreground">No tasks yet. Start a run to see task progress here.</p>
          </TabsContent>

          <TabsContent value="results" className="flex-1 overflow-y-auto p-4 mt-0">
            <p className="text-sm font-semibold mb-2">Results</p>
            <p className="text-sm text-muted-foreground">Run outputs will appear here as they complete.</p>
          </TabsContent>
        </Tabs>
      </aside>
    </div>
    </>
  );
}
