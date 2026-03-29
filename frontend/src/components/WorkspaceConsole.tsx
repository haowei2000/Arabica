import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Loader2, ChevronDown, ChevronRight, Play, Layers, ListTodo, FileOutput, Plus, Send, Square, Bot, MessageSquare, Settings, Save, Globe, Download, RefreshCw, CheckCircle2, Circle, XCircle, Clock, AlertCircle, Wrench } from 'lucide-react';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useRuns } from '@/hooks/useRuns';
import { useStreamingChat } from '@/hooks/useStreamingChat';
import { useToolList } from '@/hooks/useTools';
import { useWorkspaces, useUpdateWorkspace } from '@/hooks/useWorkspaces';
import { useTemplates } from '@/hooks/useApps';
import { MessageRole } from '@/types/message';
import { formatRelativeTime } from '@/utils/formatDate';
import { ThinkingBlock, ToolCallCard, PlanStepList, ApprovalCard, QueryCard } from '@/components/AgentEvents';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import type { Run } from '@/types/run';
import type { Event } from '@/types/event';

import WorkspaceContextTree from '@/components/WorkspaceContextTree';
import { useWorkspaceStream } from '@/hooks/useWorkspaceStream';
import { useRunEventsStore } from '@/stores/useRunEventsStore';
import { useRunEvents } from '@/hooks/useRunEvents';
import { useTasks } from '@/hooks/useTasks';
import { useArtifacts } from '@/hooks/useArtifacts';
import { artifactService, type Artifact } from '@/services/artifactService';
import { useQueryClient } from '@tanstack/react-query';

import remarkGfm from 'remark-gfm';

// ─── Polished Markdown Component ──────────────────────────────────────────

function Markdown({ content }: { content: string }) {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none prose-p:leading-relaxed prose-pre:bg-muted/50 prose-pre:border prose-pre:border-border/50 prose-code:text-primary prose-code:bg-primary/5 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-img:rounded-xl prose-img:border prose-img:border-border/50 prose-img:shadow-sm">
      <ReactMarkdown 
        remarkPlugins={[remarkGfm]}
        components={{
          img: ({ ...props }) => (
            <span className="block my-3">
              <img {...props} className="max-w-full h-auto rounded-lg border border-border/40 shadow-sm hover:shadow-md transition-shadow duration-200" loading="lazy" />
              {props.alt && <span className="block text-center text-[10px] text-muted-foreground mt-1.5 font-medium">{props.alt}</span>}
            </span>
          ),
          a: ({ ...props }) => (
            <a {...props} className="text-primary hover:underline font-medium" target="_blank" rel="noopener noreferrer" />
          ),
          table: ({ ...props }) => (
            <div className="my-4 w-full overflow-x-auto rounded-xl border border-border/60 bg-card/30">
              <table {...props} className="w-full border-collapse text-xs" />
            </div>
          ),
          thead: ({ ...props }) => <thead {...props} className="bg-muted/50 border-b border-border/60" />,
          th: ({ ...props }) => <th {...props} className="px-3 py-2 text-left font-semibold text-foreground/80" />,
          td: ({ ...props }) => <td {...props} className="px-3 py-1.5 border-t border-border/40" />,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

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
  'agent.message':    'Response',
  'tool.call':        'Tool call',
  'tool.pending':     'Tool pending',
  'tool.result':      'Tool result',
  'tool.error':       'Tool error',
  'run.state.change': 'State',
  'run.created':      'Run created',
  'to.executor':      'Tokens',
  'context.using':    'Context',
  'agent.plan.step':  'Plan step',
};

/** Event types too noisy to show in the timeline */
const HIDDEN_EVENT_TYPES = new Set([
  'agent.token',
  'agent.heartbeat',
]);


// ─── Task status helpers ───────────────────────────────────────────────────
const TASK_STATUS_ICON: Record<string, React.ReactNode> = {
  pending:     <Clock className="size-3 text-muted-foreground/60 shrink-0" />,
  in_progress: <Circle className="size-3 text-yellow-400 shrink-0 animate-pulse" />,
  done:        <CheckCircle2 className="size-3 text-green-500 shrink-0" />,
  failed:      <XCircle className="size-3 text-red-500 shrink-0" />,
  cancelled:   <AlertCircle className="size-3 text-muted-foreground/40 shrink-0" />,
};

const TASK_STATUS_TEXT: Record<string, string> = {
  pending:     'Pending',
  in_progress: 'In progress',
  done:        'Done',
  failed:      'Failed',
  cancelled:   'Cancelled',
};

const ARTIFACT_TYPE_COLOR: Record<string, string> = {
  text:     'bg-blue-500/10 text-blue-400',
  code:     'bg-purple-500/10 text-purple-400',
  file:     'bg-orange-500/10 text-orange-400',
  image:    'bg-pink-500/10 text-pink-400',
  document: 'bg-cyan-500/10 text-cyan-400',
  data:     'bg-green-500/10 text-green-400',
  other:    'bg-muted text-muted-foreground',
};

// ─── ArtifactCard component ────────────────────────────────────────────────
function ArtifactCard({
  artifact,
  workspaceId,
}: {
  artifact: Artifact;
  workspaceId: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const hasContent = !!artifact.content || !!artifact.s3_url;
  const colorCls = ARTIFACT_TYPE_COLOR[artifact.artifact_type] ?? ARTIFACT_TYPE_COLOR.other;

  const handleDownload = (e: React.MouseEvent) => {
    e.stopPropagation();
    setDownloading(true);
    try {
      artifactService.downloadArtifact(workspaceId, artifact.id, artifact.name);
    } finally {
      setTimeout(() => setDownloading(false), 1500);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      <div
        className={cn(
          'flex items-center gap-2 px-3 py-2 transition-colors',
          hasContent && 'cursor-pointer hover:bg-muted/40',
        )}
        onClick={() => hasContent && setExpanded((v) => !v)}
      >
        {hasContent && (
          <ChevronRight className={cn(
            'size-2.5 text-muted-foreground/40 transition-transform shrink-0',
            expanded && 'rotate-90'
          )} />
        )}
        {!hasContent && <div className="w-2.5 shrink-0" />}

        <span className={cn('text-[10px] px-1.5 py-0.5 rounded-full font-medium shrink-0', colorCls)}>
          {artifact.artifact_type}
        </span>

        <span className="text-xs font-medium flex-1 min-w-0 truncate">{artifact.name}</span>

        {artifact.version > 1 && (
          <span className="text-[10px] text-muted-foreground/40 shrink-0">v{artifact.version}</span>
        )}

        <button
          type="button"
          onClick={handleDownload}
          disabled={downloading || !hasContent}
          title="Download"
          className={cn(
            'size-5 flex items-center justify-center rounded transition-colors shrink-0',
            hasContent
              ? 'text-muted-foreground/50 hover:text-foreground hover:bg-muted/60'
              : 'text-muted-foreground/20 cursor-default'
          )}
        >
          {downloading
            ? <Loader2 className="size-3 animate-spin" />
            : <Download className="size-3" />}
        </button>
      </div>

      {expanded && artifact.content && (
        <div className="border-t border-border/40 bg-muted/20 px-3 py-2">
          <pre className="text-[10px] font-mono text-foreground/75 whitespace-pre-wrap break-all max-h-48 overflow-y-auto leading-relaxed">
            {artifact.content}
          </pre>
        </div>
      )}
    </div>
  );
}

function eventSummary(event: Event): string {
  const p = event.payload;
  switch (event.event_type) {
    case 'user.message':     return String(p.message || p.content || '').slice(0, 80);
    case 'agent.thinking':   return String(p.content || p.thinking || '').slice(0, 80);
    case 'agent.message':    return String(p.content || p.message || '').slice(0, 80);
    case 'tool.call':        return String(p.tool_name || p.name || '');
    case 'tool.pending':     return String(p.tool_name || p.name || '');
    case 'tool.result':      return String(p.tool_name || p.name || '');
    case 'tool.error':       return String(p.tool_name || p.name || '');
    case 'run.state.change': return String(p.new_state || p.to_state || p.status || '');
    case 'context.using':    return String(p.context_type || p.context_name || '');
    case 'agent.plan.step':  return String(p.step_description || '').slice(0, 80);
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
  isActiveEdge,
}: {
  event: Event;
  isLast: boolean;
  isActiveEdge: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const label   = EVENT_LABEL[event.event_type] ?? event.event_type;
  const summary = eventSummary(event);
  const failed  = isFailedEvent(event);
  const hasPayload = event.payload && Object.keys(event.payload).length > 0;

  return (
    <div className={cn(
      'relative',
      !isLast  && 'border-b border-border/30',
      failed   && 'bg-red-500/8',
      isActiveEdge && 'event-running',
    )}>
      <button
        type="button"
        onClick={() => hasPayload && setExpanded(!expanded)}
        className={cn(
          'w-full flex items-center gap-2 py-1 px-3 text-left transition-colors',
          hasPayload && 'hover:bg-muted/40 cursor-pointer',
          !hasPayload && 'cursor-default'
        )}
      >
        {hasPayload && (
          <ChevronRight className={cn(
            'size-2.5 text-muted-foreground/40 transition-transform shrink-0',
            expanded && 'rotate-90'
          )} />
        )}
        {!hasPayload && <div className="w-2.5 shrink-0" />}
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
        {!summary && <span className="flex-1" />}
        {((event.input_tokens ?? 0) > 0 || (event.output_tokens ?? 0) > 0) && (
          <span className="text-[9px] text-sky-400/70 shrink-0 tabular-nums font-mono bg-sky-500/8 px-1 py-0.5 rounded">
            ↑{event.input_tokens ?? 0} ↓{event.output_tokens ?? 0}
          </span>
        )}
        <span className="text-[10px] text-muted-foreground/40 shrink-0 tabular-nums">
          {formatRelativeTime(event.created_at)}
        </span>
      </button>

      {expanded && hasPayload && (
        <div className="px-3 pb-2 pt-1 bg-muted/30 space-y-1.5">
          {/* Context breakdown — agent.message events carry _ctx from the executor */}
          {event.event_type === 'agent.message' && (() => {
            const ctx = event.payload?._ctx as { total_messages?: number; by_role?: Record<string, { count: number; chars: number }> } | undefined;
            if (!ctx) return null;
            const ROLE_LABEL: Record<string, string> = {
              system: 'System prompt',
              user: 'User',
              assistant: 'Assistant history',
              tool: 'Tool results',
            };
            const totalChars = Object.values(ctx.by_role ?? {}).reduce((s, v) => s + v.chars, 0) || 1;
            return (
              <div className="rounded-md bg-card border border-border/50 p-2">
                <div className="text-[9px] text-muted-foreground/60 uppercase tracking-wide mb-1.5 font-medium">
                  Context window · {ctx.total_messages} msgs
                </div>
                <div className="space-y-1">
                  {Object.entries(ctx.by_role ?? {}).map(([role, stat]) => {
                    const pct = Math.round((stat.chars / totalChars) * 100);
                    return (
                      <div key={role} className="flex items-center gap-2">
                        <span className="text-[10px] text-muted-foreground/70 w-28 shrink-0">
                          {ROLE_LABEL[role] ?? role}
                        </span>
                        <div className="flex-1 h-1 bg-muted rounded-full overflow-hidden">
                          <div
                            className="h-full bg-sky-500/50 rounded-full"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                        <span className="text-[9px] text-muted-foreground/50 tabular-nums w-12 text-right shrink-0">
                          {stat.count}× ~{(stat.chars / 4).toFixed(0)}t
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })()}
          {/* Token usage summary — to.executor events carry run-lifetime token totals */}
          {event.event_type === 'to.executor' && ((event.input_tokens ?? 0) > 0 || (event.output_tokens ?? 0) > 0) && (
            <div className="rounded-md bg-card border border-border/50 p-2">
              <div className="text-[9px] text-muted-foreground/60 uppercase tracking-wide mb-1.5 font-medium">
                Token usage · this run turn
              </div>
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] text-muted-foreground/70">Input</span>
                  <span className="text-[10px] text-sky-400 tabular-nums font-mono">{event.input_tokens ?? 0}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] text-muted-foreground/70">Output</span>
                  <span className="text-[10px] text-sky-400 tabular-nums font-mono">{event.output_tokens ?? 0}</span>
                </div>
              </div>
            </div>
          )}
          <div className="rounded-md bg-card border border-border/50 p-2">
            <div className="text-[9px] text-muted-foreground/60 uppercase tracking-wide mb-1 font-medium">Payload</div>
            <pre className="text-[10px] text-foreground/80 font-mono overflow-x-auto leading-relaxed whitespace-pre-wrap break-all max-h-60 overflow-y-auto">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}

function RunTimelineItem({
  run,
  latestSseEvent,
  isActive,
  onSelect,
}: {
  run: Run;
  latestSseEvent: Event | undefined;
  isActive: boolean;
  onSelect: (runId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  // Merge PG history + live SSE stream events.
  // PG fetch only fires when the item is first expanded (lazy load).
  const { events, isLoading: eventsLoading } = useRunEvents(run.id, expanded);

  const fullMessage = run.input_data?.message ? String(run.input_data.message) : '';
  const title = fullMessage ? fullMessage.slice(0, 55) : 'New run';

  const isRunning = run.status === 'running' || run.status === 'pending';
  const isFailed  = run.status === 'failed';

  const previewEvent: Event | undefined =
    latestSseEvent ??
    (events.length > 0 ? events[events.length - 1] : undefined);
  const validPreview = previewEvent && !HIDDEN_EVENT_TYPES.has(previewEvent.event_type);

  const dotCls = isRunning
    ? 'bg-yellow-400 animate-pulse'
    : isFailed
      ? 'bg-red-500'
      : run.status === 'finished'
        ? 'bg-green-500'
        : 'bg-muted-foreground/30';

  return (
    <div className={cn(
      'group relative rounded-lg border transition-all duration-200',
      isActive 
        ? 'border-primary/50 bg-primary/5 shadow-sm' 
        : 'border-border bg-card hover:bg-muted/30 hover:border-border/80',
    )}>
      <div className="flex items-center gap-1 px-1">
        <div
          className="flex-1 flex items-center gap-2 px-1.5 py-2 cursor-pointer select-none min-w-0"
          onClick={() => onSelect(run.id)}
        >
          <span className={cn('size-1.5 rounded-full shrink-0', dotCls)} />
          <span className={cn(
            'text-xs truncate flex-1 min-w-0',
            isActive ? 'font-semibold text-foreground' : 'text-foreground/75'
          )}>
            {title}
          </span>
          {isRunning && validPreview && (
            <span className="text-[10px] text-yellow-400/80 truncate max-w-20 shrink-0 hidden sm:block">
              {EVENT_LABEL[previewEvent.event_type] ?? previewEvent.event_type}
            </span>
          )}
          {!isRunning && ((run.input_tokens ?? 0) > 0 || (run.output_tokens ?? 0) > 0) && (
            <span className="text-[9px] text-sky-400/60 shrink-0 tabular-nums font-mono hidden sm:block">
              ↑{run.input_tokens ?? 0} ↓{run.output_tokens ?? 0}
            </span>
          )}
          <span className="text-[10px] text-muted-foreground/40 shrink-0 tabular-nums">
            {formatRelativeTime(run.created_at)}
          </span>
        </div>
        
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
          className={cn(
            'size-7 flex items-center justify-center rounded-md transition-all shrink-0',
            expanded ? 'text-primary' : 'text-muted-foreground/40 hover:text-foreground hover:bg-muted/60'
          )}
        >
          {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        </button>
      </div>

      {/* Hover tooltip */}
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
            {((run.input_tokens ?? 0) > 0 || (run.output_tokens ?? 0) > 0) && (
              <>
                <span>Tokens</span>
                <span className="text-sky-400 tabular-nums font-mono">
                  ↑{run.input_tokens ?? 0} ↓{run.output_tokens ?? 0}
                </span>
              </>
            )}
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

      {expanded && (
        <div className="border-t border-border/40 mx-1 mb-1 rounded-b-md overflow-hidden bg-muted/20">
          {eventsLoading ? (
            <div className="flex items-center gap-1.5 px-3 py-2">
              <Loader2 className="size-2.5 animate-spin text-muted-foreground/40" />
              <span className="text-[10px] text-muted-foreground/40">Loading events…</span>
            </div>
          ) : (() => {
            const visible = events.filter((e) => !HIDDEN_EVENT_TYPES.has(e.event_type));
            return visible.length === 0 ? (
              <p className="text-[10px] text-muted-foreground/40 px-3 py-2">No events</p>
            ) : (
              <ScrollArea viewportClassName="max-h-48">
                <div>
                  {visible.map((e, i) => (
                    <RunEventRow
                      key={e.id || `${e.sequence}`}
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
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
    pendingQueries,
    streamingInputTokens,
    streamingOutputTokens,
    loadRun,
    startNewRun,
  } = useChatStore();

  const { data: runsData, isLoading: runsLoading } = useRuns(currentWorkspaceId || '', {
    page: 1,
    page_size: 50,
  });

  // Automatically load latest run history when entering workspace
  useEffect(() => {
    if (currentWorkspaceId && runsData?.items && runsData.items.length > 0 && !currentRunId && !isStreaming) {
      // Find the most recent run (sorted by created_at desc in backend)
      const latestRun = runsData.items[0];
      if (latestRun) {
        loadRun(latestRun.id);
      }
    }
  }, [currentWorkspaceId, runsData?.items, currentRunId, isStreaming, loadRun]);

  const latestRunEvents = useRunEventsStore((s) => s.latestEvents);

  // ── Settings tab state ────────────────────────────────────────────────────
  const { data: workspacesData } = useWorkspaces({ page: 1, page_size: 50 });
  const { data: templates = [] } = useTemplates();
  const updateWorkspace = useUpdateWorkspace();

  const currentWorkspace = workspacesData?.items?.find((w) => w.id === currentWorkspaceId);
  const wsConfig = currentWorkspace?.executor_config as Record<string, unknown> | null | undefined;
  const wsModel = wsConfig?.model as { name?: string; provider?: string } | undefined;

  const [sExecutorCode, setSExecutorCode] = useState('');
  const [sModelName, setSModelName] = useState('');
  const [sModelProvider, setSModelProvider] = useState('tongyi');
  const [sGlobalEvent, setSGlobalEvent] = useState(true);
  const [settingsSaving, setSettingsSaving] = useState(false);

  // Sync settings state when workspace loads or changes
  useEffect(() => {
    if (!currentWorkspace) return;
    setSExecutorCode(currentWorkspace.executor_code ?? '');
    setSModelName(wsModel?.name ?? '');
    setSModelProvider(wsModel?.provider ?? 'tongyi');
    setSGlobalEvent(wsConfig?.global_event !== undefined ? Boolean(wsConfig.global_event) : true);
  }, [currentWorkspaceId, currentWorkspace, wsConfig?.global_event, wsModel?.name, wsModel?.provider]);

  const handleSettingsSave = async () => {
    if (!currentWorkspaceId) return;
    setSettingsSaving(true);
    try {
      const executorConfig: Record<string, unknown> = { global_event: sGlobalEvent };
      if (sModelName.trim()) {
        executorConfig.model = { name: sModelName.trim(), provider: sModelProvider };
      }
      await updateWorkspace.mutateAsync({
        workspaceId: currentWorkspaceId,
        data: {
          executor_code: sExecutorCode || undefined,
          executor_config: executorConfig,
        },
      });
    } catch (err) {
      alert(`Save failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    } finally {
      setSettingsSaving(false);
    }
  };

  const { sendMessage, stopStreaming, approveToolCall, respondToQuery } = useStreamingChat(
    currentWorkspaceId || '',
    currentWorkspaceAppId
  );

  // ── Tool mention (@) ──────────────────────────────────────────────────────
  const { data: toolListData } = useToolList({ enabled_only: true });
  const allTools = toolListData?.tools ?? [];

  const mentionMatches = mentionQuery !== null
    ? allTools.filter((t) =>
        t.name.toLowerCase().includes(mentionQuery.toLowerCase()) ||
        t.display_name.toLowerCase().includes(mentionQuery.toLowerCase())
      ).slice(0, 8)
    : [];

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setInput(value);
    const cursor = e.target.selectionStart ?? value.length;
    const before = value.slice(0, cursor);
    const atMatch = before.match(/@(\w*)$/);
    if (atMatch) {
      setMentionQuery(atMatch[1]);
      setMentionIndex(0);
    } else {
      setMentionQuery(null);
    }
  };

  const insertMention = (toolName: string) => {
    const cursor = textareaRef.current?.selectionStart ?? input.length;
    const before = input.slice(0, cursor);
    const atIdx = before.lastIndexOf('@');
    const newVal = input.slice(0, atIdx) + `@${toolName} ` + input.slice(cursor);
    setInput(newVal);
    setMentionQuery(null);
    setTimeout(() => {
      textareaRef.current?.focus();
      const pos = atIdx + toolName.length + 2;
      textareaRef.current?.setSelectionRange(pos, pos);
    }, 0);
  };

  useWorkspaceStream(currentWorkspaceId);

  const queryClient = useQueryClient();

  // Real-time tasks: poll every 3 s while any run is active
  const hasActiveRun = runsData?.items?.some(
    (r) => r.status === 'running' || r.status === 'pending'
  ) ?? false;
  const pollInterval = hasActiveRun ? 3000 : undefined;

  const { data: tasksData, isLoading: tasksLoading } = useTasks(
    currentWorkspaceId || '',
    { limit: 100 },
    { refetchInterval: pollInterval }
  );
  const { data: artifactsData, isLoading: artifactsLoading } = useArtifacts(
    currentWorkspaceId || '',
    { limit: 100 },
    { refetchInterval: pollInterval }
  );

  const handleRefreshTasks = () => {
    queryClient.invalidateQueries({ queryKey: ['tasks', currentWorkspaceId] });
  };
  const handleRefreshArtifacts = () => {
    queryClient.invalidateQueries({ queryKey: ['artifacts', currentWorkspaceId] });
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingMessage, thinkingContent, activeToolCalls, planSteps, pendingApprovals, pendingQueries]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 160) + 'px';
    }
  }, [input]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isStreaming || !currentWorkspaceId) return;
    const message = input.trim();
    const forcedTools = [...message.matchAll(/@(\w+)/g)].map((m) => m[1]);
    setInput('');
    setMentionQuery(null);
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    await sendMessage(message, forcedTools.length > 0 ? forcedTools : undefined);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionQuery !== null && mentionMatches.length > 0) {
      if (e.key === 'ArrowDown') { e.preventDefault(); setMentionIndex((i) => Math.min(i + 1, mentionMatches.length - 1)); return; }
      if (e.key === 'ArrowUp')   { e.preventDefault(); setMentionIndex((i) => Math.max(i - 1, 0)); return; }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); insertMention(mentionMatches[mentionIndex].name); return; }
      if (e.key === 'Escape')    { e.preventDefault(); setMentionQuery(null); return; }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
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
      <div className="flex flex-col flex-1 min-h-0 overflow-hidden lg:border-r border-border">
        <ScrollArea className="flex-1 px-4 sm:px-6 py-4">
          <div className="max-w-3xl mx-auto space-y-5">
            {isLoadingConversation ? (
              <div className="flex items-center justify-center h-40">
                <Loader2 className="size-8 animate-spin text-muted-foreground/40" />
              </div>
            ) : messages.length === 0 && !streamingMessage ? (
              <div className="flex flex-col items-center justify-center pt-20 pb-10 animate-fade-in">
                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary-100 to-primary-200 dark:from-primary-900/40 dark:to-primary-800/30 flex items-center justify-center mb-5">
                  <MessageSquare className="size-8 text-primary-500" />
                </div>
                <h3 className="text-lg font-semibold text-foreground mb-2">Start a conversation</h3>
                <p className="text-sm text-muted-foreground text-center max-w-sm">
                  Type a message below to begin interacting with the AI agent.
                </p>
              </div>
            ) : null}

            {messages.map((message) => (
              <div
                key={message.id}
                className={cn(
                  'flex gap-3 animate-fade-in group/message',
                  message.role === MessageRole.USER ? 'justify-end' : 'justify-start'
                )}
              >
                {message.role === MessageRole.ASSISTANT && (
                  <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center text-white shrink-0 mt-0.5 shadow-sm shadow-primary-500/20">
                    <Bot className="size-4" />
                  </div>
                )}

                {message.role === MessageRole.USER ? (
                  <div className="max-w-2xl rounded-2xl rounded-br-md px-4 py-2.5 bg-primary text-primary-foreground shadow-sm hover:shadow-md transition-shadow duration-200">
                    <p className="text-[13px] sm:text-sm leading-relaxed whitespace-pre-wrap">{message.content}</p>
                  </div>
                ) : (
                  <div className="max-w-2xl w-full space-y-2.5">
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
                      <div className="rounded-2xl rounded-bl-md px-4 py-3 bg-card border border-border/60 shadow-sm hover:shadow-md hover:border-border/80 transition-all duration-200">
                        <Markdown content={message.content} />
                      </div>
                    )}
                    {(message.inputTokens || message.outputTokens) && (
                      <div className="flex items-center gap-1.5 px-1 opacity-0 group-hover/message:opacity-100 transition-opacity">
                        <span className="text-[10px] text-muted-foreground/40 tabular-nums font-mono">
                          ↑{message.inputTokens ?? 0} ↓{message.outputTokens ?? 0} tokens
                        </span>
                      </div>
                    )}
                  </div>
                )}

                {message.role === MessageRole.USER && (
                  <div className="w-8 h-8 rounded-xl bg-secondary flex items-center justify-center text-secondary-foreground text-[10px] shrink-0 font-bold shadow-sm ring-1 ring-border/20">
                    YOU
                  </div>
                )}
              </div>
            ))}

            {isStreaming && (
              <div className="flex gap-3 justify-start animate-fade-in">
                <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center text-white shrink-0 mt-0.5 shadow-sm shadow-primary-500/20">
                  <Bot className="size-4" />
                </div>
                <div className="max-w-2xl w-full space-y-2.5">
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
                  {pendingQueries.map((query) => (
                    <QueryCard key={query.tool_id} query={query} onRespond={respondToQuery} />
                  ))}
                  {streamingMessage && (
                    <div className="rounded-2xl rounded-bl-md px-4 py-3 bg-card border border-border/60 shadow-sm">
                      <Markdown content={streamingMessage} />
                    </div>
                  )}
                  <div className="flex items-center gap-2 text-muted-foreground px-1">
                    <span className={cn('w-1.5 h-1.5 rounded-full animate-pulse', 
                      pendingApprovals.length > 0 ? 'bg-amber-500' : 
                      pendingQueries.length > 0 ? 'bg-indigo-500' : 
                      'bg-primary')} 
                    />
                    <span className="text-[11px] font-medium tracking-wide uppercase opacity-70">
                      {pendingApprovals.length > 0
                        ? 'Waiting for your approval...'
                        : pendingQueries.length > 0
                          ? 'Waiting for your answer...'
                          : activeToolCalls.some((tc) => tc.status === 'pending')
                            ? 'Executing tools...'
                            : streamingMessage
                              ? 'Assistant is typing...'
                              : 'Preparing response...'}
                    </span>
                    {(streamingInputTokens > 0 || streamingOutputTokens > 0) && (
                      <span className="text-[10px] text-sky-400/70 tabular-nums font-mono ml-auto">
                        ↑{streamingInputTokens} ↓{streamingOutputTokens}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </ScrollArea>

        {/* ── Modern input area ── */}
        <div className="border-t border-border/60 bg-gradient-to-t from-background to-background/80 px-4 sm:px-6 py-4">
          <div className="max-w-3xl mx-auto">
            <form onSubmit={handleSubmit} className="relative">
              {/* @ mention dropdown */}
              {mentionQuery !== null && mentionMatches.length > 0 && (
                <div className="absolute bottom-full mb-1 left-0 right-0 z-50 rounded-xl border border-border bg-card shadow-lg shadow-black/10 overflow-hidden">
                  <div className="px-3 py-1.5 border-b border-border/40 flex items-center gap-1.5">
                    <Wrench className="size-3 text-muted-foreground/50" />
                    <span className="text-[10px] text-muted-foreground/60 font-medium">Tools</span>
                  </div>
                  <div className="max-h-52 overflow-y-auto">
                    {mentionMatches.map((tool, i) => (
                      <button
                        key={tool.name}
                        type="button"
                        onMouseDown={(e) => { e.preventDefault(); insertMention(tool.name); }}
                        className={cn(
                          'w-full flex items-start gap-2 px-3 py-2 text-left transition-colors',
                          i === mentionIndex ? 'bg-primary/10 text-foreground' : 'hover:bg-muted/50 text-foreground/80',
                        )}
                      >
                        <Wrench className="size-3 shrink-0 mt-0.5 text-muted-foreground/50" />
                        <div className="min-w-0">
                          <p className="text-xs font-medium truncate">{tool.display_name}</p>
                          <p className="text-[10px] text-muted-foreground/60 font-mono">{tool.name}</p>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex items-end gap-2 rounded-2xl border border-border/80 bg-card shadow-sm focus-within:border-primary/40 focus-within:ring-2 focus-within:ring-primary/10 transition-all duration-200">
                <textarea
                  ref={textareaRef}
                  value={input}
                  onChange={handleInputChange}
                  onKeyDown={handleKeyDown}
                  placeholder="Type a message… use @tool_name to invoke a tool (Shift+Enter for new line)"
                  disabled={isStreaming}
                  rows={1}
                  className="flex-1 resize-none bg-transparent px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground/50 focus:outline-none disabled:opacity-50 max-h-40"
                />
                <div className="pr-2 pb-2 shrink-0">
                  {isStreaming ? (
                    <Button
                      type="button"
                      size="icon"
                      variant="destructive"
                      onClick={stopStreaming}
                      className="size-8 rounded-xl"
                      title="Stop generating"
                    >
                      <Square className="size-3.5" />
                    </Button>
                  ) : (
                    <Button
                      type="submit"
                      size="icon"
                      disabled={!input.trim()}
                      className={cn(
                        'size-8 rounded-xl transition-all duration-200',
                        input.trim()
                          ? 'bg-primary hover:bg-primary/90 shadow-sm shadow-primary/20'
                          : 'bg-muted text-muted-foreground'
                      )}
                      title="Send message"
                    >
                      <Send className="size-3.5" />
                    </Button>
                  )}
                </div>
              </div>
            </form>
          </div>
        </div>
      </div>

      {/* ── Right Panel ── */}
      <aside className="w-full lg:w-96 border-t lg:border-t-0 border-border bg-card flex flex-col overflow-hidden">
        <Tabs defaultValue="runs" className="flex flex-col flex-1 min-h-0">
          <div className="px-4 pt-3 border-b border-border shrink-0">
            <TabsList className="w-full">
              <TabsTrigger value="runs" className="flex-1 gap-1" title="Runs"><Play className="size-3" /><span className="hidden sm:inline text-xs">Runs</span></TabsTrigger>
              <TabsTrigger value="context" className="flex-1 gap-1" title="Context"><Layers className="size-3" /><span className="hidden sm:inline text-xs">Context</span></TabsTrigger>
              <TabsTrigger value="tasks" className="flex-1 gap-1" title="Tasks"><ListTodo className="size-3" /><span className="hidden sm:inline text-xs">Tasks</span></TabsTrigger>
              <TabsTrigger value="results" className="flex-1 gap-1" title="Results"><FileOutput className="size-3" /><span className="hidden sm:inline text-xs">Results</span></TabsTrigger>
              <TabsTrigger value="settings" className="flex-1 gap-1" title="Settings"><Settings className="size-3" /><span className="hidden sm:inline text-xs">Settings</span></TabsTrigger>
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
                    latestSseEvent={latestRunEvents[run.id]}
                    isActive={run.id === currentRunId}
                    onSelect={(id) => loadRun(id)}
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
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5">
                <ListTodo className="size-3.5 text-muted-foreground" />
                <span className="text-xs font-semibold">Tasks</span>
                {tasksData && (
                  <span className="text-[10px] text-muted-foreground/50 tabular-nums">({tasksData.total})</span>
                )}
              </div>
              <Button
                size="icon"
                variant="ghost"
                className="size-6"
                onClick={handleRefreshTasks}
                title="Refresh tasks"
              >
                <RefreshCw className="size-3" />
              </Button>
            </div>
            {tasksLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="size-5 animate-spin text-muted-foreground/40" />
              </div>
            ) : tasksData?.items && tasksData.items.length > 0 ? (
              <div className="space-y-1">
                {tasksData.items.map((task) => (
                  <div
                    key={task.id}
                    className="rounded-lg border border-border bg-card px-3 py-2 space-y-1"
                  >
                    <div className="flex items-start gap-2">
                      {TASK_STATUS_ICON[task.status] ?? <Circle className="size-3 shrink-0" />}
                      <span className="text-xs font-medium leading-tight flex-1 min-w-0 break-words">
                        {task.title}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 pl-5">
                      <span className={cn(
                        'text-[10px] px-1.5 py-0.5 rounded-full',
                        task.status === 'done'        ? 'bg-green-500/10 text-green-400' :
                        task.status === 'in_progress' ? 'bg-yellow-500/10 text-yellow-400' :
                        task.status === 'failed'      ? 'bg-red-500/10 text-red-400' :
                        task.status === 'cancelled'   ? 'bg-muted text-muted-foreground' :
                                                        'bg-muted text-muted-foreground/60'
                      )}>
                        {TASK_STATUS_TEXT[task.status] ?? task.status}
                      </span>
                      {task.assignee && (
                        <span className="text-[10px] text-muted-foreground/50 truncate">{task.assignee}</span>
                      )}
                      {task.run_id && (
                        <span className="text-[10px] text-muted-foreground/30 font-mono ml-auto">
                          {task.run_id.slice(0, 8)}
                        </span>
                      )}
                    </div>
                    {task.description && (
                      <p className="text-[10px] text-muted-foreground/60 pl-5 line-clamp-2 leading-relaxed">
                        {task.description}
                      </p>
                    )}
                    {task.result && task.status === 'done' && (
                      <p className="text-[10px] text-green-400/70 pl-5 line-clamp-2 leading-relaxed">
                        {task.result}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-center py-10">
                <ListTodo className="mx-auto size-6 text-muted-foreground/20 mb-2" />
                <p className="text-[10px] text-muted-foreground/50">No tasks yet</p>
              </div>
            )}
          </TabsContent>

          <TabsContent value="results" className="flex-1 overflow-y-auto p-4 mt-0">
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-1.5">
                <FileOutput className="size-3.5 text-muted-foreground" />
                <span className="text-xs font-semibold">Artifacts</span>
                {artifactsData && (
                  <span className="text-[10px] text-muted-foreground/50 tabular-nums">({artifactsData.total})</span>
                )}
              </div>
              <Button
                size="icon"
                variant="ghost"
                className="size-6"
                onClick={handleRefreshArtifacts}
                title="Refresh artifacts"
              >
                <RefreshCw className="size-3" />
              </Button>
            </div>
            {artifactsLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="size-5 animate-spin text-muted-foreground/40" />
              </div>
            ) : artifactsData?.items && artifactsData.items.length > 0 ? (
              <div className="space-y-1.5">
                {artifactsData.items.map((artifact) => (
                  <ArtifactCard
                    key={artifact.id}
                    artifact={artifact}
                    workspaceId={currentWorkspaceId || ''}
                  />
                ))}
              </div>
            ) : (
              <div className="text-center py-10">
                <FileOutput className="mx-auto size-6 text-muted-foreground/20 mb-2" />
                <p className="text-[10px] text-muted-foreground/50">No artifacts yet</p>
              </div>
            )}
          </TabsContent>

          <TabsContent value="settings" className="flex-1 overflow-y-auto p-4 mt-0">
            <div className="flex items-center gap-1.5 mb-4">
              <Settings className="size-3.5 text-muted-foreground" />
              <span className="text-xs font-semibold">Workspace Settings</span>
            </div>

            <div className="space-y-4">
              {/* Executor */}
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Executor</Label>
                <select
                  value={sExecutorCode}
                  onChange={(e) => setSExecutorCode(e.target.value)}
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                >
                  <option value="">— default —</option>
                  {templates.map((t) => (
                    <option key={t.executor_code} value={t.executor_code}>
                      {t.executor_name || t.executor_code}
                    </option>
                  ))}
                </select>
                {currentWorkspace?.executor_code && (
                  <p className="text-[10px] text-muted-foreground">
                    Current: <span className="font-mono">{currentWorkspace.executor_code}</span>
                  </p>
                )}
              </div>

              {/* Model */}
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Model</Label>
                <div className="flex gap-2">
                  <select
                    value={sModelProvider}
                    onChange={(e) => setSModelProvider(e.target.value)}
                    className="rounded-md border border-input bg-background px-2 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 w-24 shrink-0"
                  >
                    <option value="tongyi">tongyi</option>
                    <option value="ollama">ollama</option>
                  </select>
                  <Input
                    value={sModelName}
                    onChange={(e) => setSModelName(e.target.value)}
                    placeholder="e.g. qwen-plus"
                    className="flex-1 text-sm h-9"
                  />
                </div>
                <p className="text-[10px] text-muted-foreground">Leave blank to use executor default.</p>
              </div>

              {/* Global Event */}
              <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <Globe className="size-3.5 text-muted-foreground shrink-0" />
                  <div>
                    <p className="text-xs font-medium leading-none">Global Event</p>
                    <p className="text-[10px] text-muted-foreground mt-0.5">
                      Load history from all runs in workspace.
                    </p>
                  </div>
                </div>
                <Checkbox
                  checked={sGlobalEvent}
                  onCheckedChange={(v) => setSGlobalEvent(Boolean(v))}
                />
              </div>

              {/* Save */}
              <Button
                size="sm"
                className="w-full gap-1.5"
                onClick={handleSettingsSave}
                disabled={settingsSaving}
              >
                {settingsSaving
                  ? <><Loader2 className="size-3.5 animate-spin" />Saving…</>
                  : <><Save className="size-3.5" />Save Settings</>}
              </Button>
            </div>
          </TabsContent>
        </Tabs>
      </aside>
    </div>
    </>
  );
}
