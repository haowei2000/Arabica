import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Loader2, ChevronDown, ChevronRight, Square, Globe, Download, RefreshCw, AlertCircle, Paperclip } from 'lucide-react';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useRuns } from '@/hooks/useRuns';
import { useStreamingChat } from '@/hooks/useStreamingChat';
import { useToolList } from '@/hooks/useTools';
import { useWorkspaces, useUpdateWorkspace } from '@/hooks/useWorkspaces';
import { useTemplates } from '@/hooks/useApps';
import { MessageRole } from '@/types/message';
import { formatRelativeTime } from '@/utils/formatDate';
import { ThinkingBlock, ToolCallCard, PlanStepList, ApprovalCard, QueryCard, OutcomeCard } from '@/components/AgentEvents';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import type { Run } from '@/types/run';
import type { Event } from '@/types/event';
import type { WorkspaceListResponse } from '@/types/workspace';

import WorkspaceContextTree from '@/components/WorkspaceContextTree';
import {
  ChatAttachmentChips,
  ChatFilePreviewDialog,
  SelectedFilePreviewList,
  type ChatFilePreviewTarget,
} from '@/components/ChatFilePreview';
import { useWorkspaceStream } from '@/hooks/useWorkspaceStream';
import { useRunEventsStore } from '@/stores/useRunEventsStore';
import { useRunEvents } from '@/hooks/useRunEvents';
import { useArtifacts } from '@/hooks/useArtifacts';
import { artifactService, type Artifact } from '@/services/artifactService';
import { chatFileService } from '@/services/chatFileService';
import type { ChatFileAttachment } from '@/types/chatFile';
import { useQueryClient } from '@tanstack/react-query';
import { APP_ICONS } from '@/constants/icons';

import remarkGfm from 'remark-gfm';

const AssistantIcon = APP_ICONS.brand;
const ChatIcon = APP_ICONS.chat;
const ContextIcon = APP_ICONS.context;
const NewIcon = APP_ICONS.new;
const ResultIcon = APP_ICONS.results;
const RunIcon = APP_ICONS.runs;
const SaveIcon = APP_ICONS.save;
const SendIcon = APP_ICONS.send;
const SettingsIcon = APP_ICONS.settings;
const ToolIcon = APP_ICONS.tool;

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
    case 'context.using':    return p.tool_names
      ? `Tools loaded: ${(p.tool_names as string[]).join(', ')}`
      : String(p.context_type || p.context_name || '');
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

type WorkspaceConsoleProps = {
  onRunCountChange?: (workspaceId: string, runCount: number) => void;
};

export default function WorkspaceConsole({ onRunCountChange }: WorkspaceConsoleProps) {
  const [input, setInput] = useState('');
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [previewTarget, setPreviewTarget] = useState<ChatFilePreviewTarget | null>(null);
  const [uploadingFiles, setUploadingFiles] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

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
    contextUsages,
    outcomes,
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

  const hasLoadedInitialRun = useRef<string | null>(null);

  // Automatically load latest run history when entering workspace
  useEffect(() => {
    if (currentWorkspaceId && runsData?.items && runsData.items.length > 0 && !currentRunId && !isStreaming) {
      // Only auto-load if we haven't already loaded an initial run for THIS workspace
      if (hasLoadedInitialRun.current !== currentWorkspaceId) {
        const latestRun = runsData.items[0];
        if (latestRun) {
          hasLoadedInitialRun.current = currentWorkspaceId;
          loadRun(latestRun.id);
        }
      }
    }
    
    // If workspace changes, and we don't have a run, we might need to reset the ref
    if (hasLoadedInitialRun.current && hasLoadedInitialRun.current !== currentWorkspaceId) {
       hasLoadedInitialRun.current = null;
    }
  }, [currentWorkspaceId, runsData?.items, currentRunId, isStreaming, loadRun]);

  const latestRunEvents = useRunEventsStore((s) => s.latestEvents);

  // ── Settings tab state ────────────────────────────────────────────────────
  const { data: workspacesData } = useWorkspaces({ page: 1, page_size: 50 });
  const { data: templates = [] } = useTemplates();
  const updateWorkspace = useUpdateWorkspace();

  const currentWorkspace = workspacesData?.items?.find((w) => w.id === currentWorkspaceId);
  const wsConfig = currentWorkspace?.executor_config as Record<string, unknown> | null | undefined;

  const [sExecutorCode, setSExecutorCode] = useState('');
  const [sGlobalEvent, setSGlobalEvent] = useState(true);
  const [settingsSaving, setSettingsSaving] = useState(false);

  // Sync settings state when workspace loads or changes
  useEffect(() => {
    if (!currentWorkspace) return;
    setSExecutorCode(currentWorkspace.executor_code ?? '');
    setSGlobalEvent(wsConfig?.global_event !== undefined ? Boolean(wsConfig.global_event) : true);
  }, [currentWorkspaceId, currentWorkspace, wsConfig?.global_event]);

  const handleSettingsSave = async () => {
    if (!currentWorkspaceId) return;
    setSettingsSaving(true);
    try {
      const executorConfig: Record<string, unknown> = { global_event: sGlobalEvent };
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

  const { sendMessage, stopStreaming, approveToolCall, respondToQuery, streamError, retryLastMessage } = useStreamingChat(
    currentWorkspaceId || '',
    currentWorkspaceAppId
  );
  const clearStreamError = useChatStore((s) => s.setStreamError);

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

  const runListCount = runsData?.items?.length;

  useEffect(() => {
    if (!currentWorkspaceId || runListCount === undefined) return;
    onRunCountChange?.(currentWorkspaceId, runListCount);
  }, [currentWorkspaceId, runListCount, onRunCountChange]);

  useEffect(() => {
    if (!currentWorkspaceId || typeof runsData?.total !== 'number') return;

    const workspaceQueries = queryClient.getQueryCache().findAll({
      predicate: (query) =>
        Array.isArray(query.queryKey) && query.queryKey[0] === 'workspaces',
    });

    workspaceQueries.forEach((query) => {
      queryClient.setQueryData<WorkspaceListResponse>(query.queryKey, (current) => {
        if (!current?.items?.length) return current;

        let changed = false;
        const items = current.items.map((workspace) => {
          if (workspace.id !== currentWorkspaceId || workspace.run_count === runsData.total) {
            return workspace;
          }

          changed = true;
          return {
            ...workspace,
            run_count: runsData.total,
          };
        });

        return changed ? { ...current, items } : current;
      });
    });
  }, [currentWorkspaceId, runsData?.total, queryClient]);

  // Real-time tasks: poll every 3 s while any run is active
  const hasActiveRun = runsData?.items?.some(
    (r) => r.status === 'running' || r.status === 'pending'
  ) ?? false;
  const pollInterval = hasActiveRun ? 3000 : undefined;

  const { data: artifactsData, isLoading: artifactsLoading } = useArtifacts(
    currentWorkspaceId || '',
    { limit: 100 },
    { refetchInterval: pollInterval }
  );

  const handleRefreshArtifacts = () => {
    queryClient.invalidateQueries({ queryKey: ['artifacts', currentWorkspaceId] });
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingMessage, thinkingContent, activeToolCalls, planSteps, outcomes, pendingApprovals, pendingQueries]);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 160) + 'px';
    }
  }, [input]);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    if (files.length === 0) return;
    setUploadError(null);
    setSelectedFiles((current) => {
      const seen = new Set(current.map((file) => `${file.name}:${file.size}:${file.lastModified}`));
      const next = [...current];
      for (const file of files) {
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        if (!seen.has(key)) {
          seen.add(key);
          next.push(file);
        }
      }
      return next;
    });
    e.target.value = '';
  };

  const removeSelectedFile = (target: File) => {
    setSelectedFiles((current) =>
      current.filter(
        (file) =>
          file.name !== target.name ||
          file.size !== target.size ||
          file.lastModified !== target.lastModified,
      ),
    );
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if ((!input.trim() && selectedFiles.length === 0) || isStreaming || uploadingFiles || !currentWorkspaceId) return;
    let message = input.trim();
    const forcedTools = [...message.matchAll(/@(\w+)/g)].map((m) => m[1]);
    let attachments: ChatFileAttachment[] = [];

    try {
      setUploadError(null);
      if (selectedFiles.length > 0) {
        setUploadingFiles(true);
        const uploaded = await chatFileService.uploadFiles(currentWorkspaceId, selectedFiles);
        attachments = uploaded.items;
        await queryClient.invalidateQueries({ queryKey: ['workspace-contexts', currentWorkspaceId] });
      }

      if (!message && attachments.length > 0) {
        message = 'Please review the attached file(s).';
      }

      setInput('');
      setSelectedFiles([]);
      setMentionQuery(null);
      if (textareaRef.current) textareaRef.current.style.height = 'auto';
      await sendMessage(
        message,
        forcedTools.length > 0 ? forcedTools : undefined,
        attachments.length > 0 ? attachments : undefined,
      );
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'File upload failed');
    } finally {
      setUploadingFiles(false);
    }
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
        <ScrollArea className="flex-1 px-4 sm:px-6 py-5">
          <div className="max-w-4xl xl:max-w-5xl mx-auto space-y-4">
            {isLoadingConversation ? (
              <div className="flex items-center justify-center h-40">
                <Loader2 className="size-10 animate-spin text-muted-foreground/30" />
              </div>
            ) : messages.length === 0 && !streamingMessage ? (
              <div className="flex flex-col items-center justify-center pt-16 pb-8 animate-fade-in">
                <div className="size-14 rounded-2xl bg-primary/10 flex items-center justify-center mb-3 shadow-sm">
                  <ChatIcon className="size-7 text-primary-500" />
                </div>
                <h3 className="text-sm font-semibold text-muted-foreground tracking-tight">New chat</h3>
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
                  <div className="size-8 rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center text-white shrink-0 mt-0.5 shadow-md shadow-primary-500/20">
                    <AssistantIcon className="size-4.5" />
                  </div>
                )}

                {message.role === MessageRole.USER ? (
                  <div className="max-w-[85%] sm:max-w-2xl rounded-xl rounded-br-md px-4 py-2.5 bg-primary text-primary-foreground shadow-md hover:shadow-lg transition-all duration-200">
                    <p className="text-sm leading-relaxed whitespace-pre-wrap">{message.content}</p>
                    {message.attachments && (
                      <ChatAttachmentChips
                        attachments={message.attachments}
                        compact
                        onPreview={(attachment) =>
                          setPreviewTarget({
                            kind: 'remote',
                            attachment,
                            workspaceId: currentWorkspaceId,
                          })
                        }
                      />
                    )}
                  </div>
                ) : (
                  <div className="max-w-[90%] sm:max-w-3xl w-full space-y-3">
                    {message.contextUsages && message.contextUsages.some(c => c.tool_names) && (
                      <div className="flex items-center gap-2 flex-wrap text-[11px] text-muted-foreground/80 px-1">
                        <ToolIcon className="size-3.5 shrink-0" />
                        <span className="font-semibold">Tools:</span>
                        {message.contextUsages.filter(c => c.tool_names).flatMap(c => c.tool_names!).map(name => (
                          <span key={name} className="px-2 py-0.5 rounded-md bg-muted border border-border/50 font-mono font-medium">{name}</span>
                        ))}
                      </div>
                    )}
                    {message.thinkingContent && (
                      <ThinkingBlock content={message.thinkingContent} defaultCollapsed />
                    )}
                    {message.toolCalls && message.toolCalls.length > 0 && (
                      <div className="space-y-1.5">
                        {message.toolCalls.map((tc) => (
                          <ToolCallCard key={tc.tool_id} toolCall={tc} />
                        ))}
                      </div>
                    )}
                    {message.planSteps && message.planSteps.length > 0 && (
                      <PlanStepList steps={message.planSteps} />
                    )}
                    {message.outcomes && message.outcomes.length > 0 && (
                      <div className="space-y-1.5">
                        {message.outcomes.map((outcome, index) => (
                          <OutcomeCard
                            key={`${outcome.artifact_id ?? outcome.outcome_name}-${index}`}
                            outcome={outcome}
                            workspaceId={currentWorkspaceId}
                          />
                        ))}
                      </div>
                    )}
                    {message.content && (
                      <div className="rounded-2xl rounded-bl-md px-5 py-4 bg-card border border-border/60 shadow-md hover:shadow-lg hover:border-border/80 transition-all duration-300">
                        <Markdown content={message.content} />
                      </div>
                    )}
                    {(message.inputTokens || message.outputTokens) && (
                      <div className="flex items-center gap-2 px-1 opacity-0 group-hover/message:opacity-100 transition-opacity">
                        <span className="text-[11px] text-muted-foreground/50 tabular-nums font-mono">
                          ↑{message.inputTokens ?? 0} ↓{message.outputTokens ?? 0}
                        </span>
                      </div>
                    )}
                  </div>
                )}

                {message.role === MessageRole.USER && (
                  <div className="size-8 rounded-xl bg-secondary flex items-center justify-center text-secondary-foreground text-[10px] shrink-0 font-black shadow-md ring-1 ring-border/20">
                    YOU
                  </div>
                )}
              </div>
            ))}

            {isStreaming && (
              <div className="flex gap-3 justify-start animate-fade-in">
                <div className="size-8 rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center text-white shrink-0 mt-0.5 shadow-md shadow-primary-500/20">
                  <AssistantIcon className="size-4.5" />
                </div>
                <div className="max-w-[90%] sm:max-w-3xl w-full space-y-3">
                  {contextUsages.some(c => c.tool_names) && (
                    <div className="flex items-center gap-2 flex-wrap text-[11px] text-muted-foreground/80 px-1">
                      <ToolIcon className="size-3.5 shrink-0" />
                      <span className="font-semibold">Tools:</span>
                      {contextUsages.filter(c => c.tool_names).flatMap(c => c.tool_names!).map(name => (
                        <span key={name} className="px-2 py-0.5 rounded-md bg-muted border border-border/50 font-mono font-medium">{name}</span>
                      ))}
                    </div>
                  )}
                  {thinkingContent && <ThinkingBlock content={thinkingContent} />}
                  {activeToolCalls.length > 0 && (
                    <div className="space-y-1.5">
                      {activeToolCalls.map((tc) => (
                        <ToolCallCard key={tc.tool_id} toolCall={tc} />
                      ))}
                    </div>
                  )}
                  {planSteps.length > 0 && <PlanStepList steps={planSteps} />}
                  {outcomes.length > 0 && (
                    <div className="space-y-1.5">
                      {outcomes.map((outcome, index) => (
                        <OutcomeCard
                          key={`${outcome.artifact_id ?? outcome.outcome_name}-${index}`}
                          outcome={outcome}
                          workspaceId={currentWorkspaceId}
                        />
                      ))}
                    </div>
                  )}
                  {pendingApprovals.map((pending) => (
                    <ApprovalCard key={pending.tool_id} pending={pending} onApprove={approveToolCall} />
                  ))}
                  {pendingQueries.map((query) => (
                    <QueryCard key={query.tool_id} query={query} onRespond={respondToQuery} />
                  ))}
                  {streamingMessage && (
                    <div className="rounded-2xl rounded-bl-md px-5 py-4 bg-card border border-border/60 shadow-md">
                      <Markdown content={streamingMessage} />
                    </div>
                  )}
                  <div className="flex items-center gap-3 text-muted-foreground px-1">
                    <span className={cn('w-2 h-2 rounded-full animate-pulse', 
                      pendingApprovals.length > 0 ? 'bg-amber-500' : 
                      pendingQueries.length > 0 ? 'bg-indigo-500' : 
                      'bg-primary')} 
                    />
                    <span className="text-xs font-bold tracking-wider uppercase opacity-80">
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
                      <span className="text-[11px] text-sky-400/80 tabular-nums font-mono ml-auto">
                        ↑{streamingInputTokens} ↓{streamingOutputTokens}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            )}

            {streamError && (
              <div
                role="alert"
                className="flex items-start gap-3 rounded-xl border border-red-500/40 bg-red-500/5 px-4 py-3 text-sm text-red-600 dark:text-red-300 animate-fade-in"
              >
                <AlertCircle className="size-4 mt-0.5 shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="font-semibold">{streamError.category.replace(/_/g, ' ')}</p>
                  <p className="text-red-700/80 dark:text-red-200/80 break-words">{streamError.message}</p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {streamError.retryable && (
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="h-7 px-2 text-xs"
                      onClick={() => {
                        clearStreamError(null);
                        retryLastMessage();
                      }}
                    >
                      Retry
                    </Button>
                  )}
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-7 px-2 text-xs"
                    onClick={() => clearStreamError(null)}
                  >
                    Dismiss
                  </Button>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        </ScrollArea>

        {/* ── Modern input area ── */}
        <div className="border-t border-border/60 bg-gradient-to-t from-background to-background/80 px-4 sm:px-6 py-4">
          <div className="max-w-4xl xl:max-w-5xl mx-auto">
            <form onSubmit={handleSubmit} className="relative">
              {/* @ mention dropdown */}
              {mentionQuery !== null && mentionMatches.length > 0 && (
                <div className="absolute bottom-full mb-2 left-0 right-0 z-50 rounded-xl border border-border bg-card shadow-xl shadow-black/20 overflow-hidden">
                  <div className="px-3 py-1.5 border-b border-border/40 flex items-center gap-2 bg-muted/30">
                    <ToolIcon className="size-3.5 text-muted-foreground/50" />
                    <span className="text-[11px] text-muted-foreground/70 font-bold uppercase tracking-tight">Tools</span>
                  </div>
                  <div className="max-h-64 overflow-y-auto">
                    {mentionMatches.map((tool, i) => (
                      <button
                        key={tool.name}
                        type="button"
                        onMouseDown={(e) => { e.preventDefault(); insertMention(tool.name); }}
                        className={cn(
                          'w-full flex items-start gap-2.5 px-3 py-2 text-left transition-colors',
                          i === mentionIndex ? 'bg-primary/10 text-foreground' : 'hover:bg-muted/50 text-foreground/80',
                        )}
                      >
                        <ToolIcon className="size-4 shrink-0 mt-0.5 text-muted-foreground/50" />
                        <div className="min-w-0">
                          <p className="text-xs font-semibold truncate">{tool.display_name}</p>
                          <p className="text-[11px] text-muted-foreground/60 font-mono">{tool.name}</p>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={handleFileSelect}
              />
              <div className="rounded-xl border border-border/80 bg-card shadow-md focus-within:border-primary/50 focus-within:ring-4 focus-within:ring-primary/5 transition-all duration-300 overflow-hidden">
                <SelectedFilePreviewList
                  files={selectedFiles}
                  onRemove={removeSelectedFile}
                  onPreview={(file) => setPreviewTarget({ kind: 'local', file })}
                />
                {uploadError && (
                  <div className="mx-4 mt-3 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs text-red-600 dark:text-red-300">
                    {uploadError}
                  </div>
                )}
                <div className="flex items-end gap-2">
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isStreaming || uploadingFiles}
                    className="ml-3 mb-2.5 size-9 inline-flex items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-40"
                    title="Attach files"
                  >
                    <Paperclip className="size-4" />
                  </button>
                  <textarea
                    ref={textareaRef}
                    value={input}
                    onChange={handleInputChange}
                    onKeyDown={handleKeyDown}
                    aria-label="Message composer"
                    placeholder="Message… (@tool)"
                    disabled={isStreaming || uploadingFiles}
                    rows={1}
                    className="flex-1 resize-none bg-transparent px-2 py-3 text-sm text-foreground placeholder:text-muted-foreground/40 focus:outline-none disabled:opacity-50 max-h-48"
                  />
                <div className="pr-3 pb-2.5 shrink-0">
                  {isStreaming ? (
                    <Button
                      type="button"
                      size="icon"
                      variant="destructive"
                      onClick={stopStreaming}
                      className="size-9 rounded-lg shadow-md"
                      title="Stop generating"
                    >
                      <Square className="size-4" />
                    </Button>
                  ) : (
                    <Button
                      type="submit"
                      size="icon"
                      disabled={uploadingFiles || (!input.trim() && selectedFiles.length === 0)}
                      className={cn(
                        'size-9 rounded-lg transition-all duration-300 shadow-md',
                        input.trim() || selectedFiles.length > 0
                          ? 'bg-primary hover:bg-primary/90 shadow-primary/20'
                          : 'bg-muted text-muted-foreground opacity-50'
                      )}
                      title="Send message"
                    >
                      {uploadingFiles ? <Loader2 className="size-4 animate-spin" /> : <SendIcon className="size-4" />}
                    </Button>
                  )}
                </div>
                </div>
              </div>
            </form>
          </div>
        </div>
      </div>

      {/* ── Right Panel ── */}
      <aside className="w-full lg:w-[380px] xl:w-[420px] border-t lg:border-t-0 border-border bg-card/50 backdrop-blur-sm flex flex-col overflow-hidden transition-all duration-300">
        <Tabs defaultValue="runs" className="flex flex-col flex-1 min-h-0">
          <div className="px-3 pt-2 border-b border-border shrink-0">
            <TabsList className="w-full h-8">
              <TabsTrigger value="runs" className="flex-1" title="Runs"><RunIcon className="size-3.5" /><span className="sr-only">Runs</span></TabsTrigger>
              <TabsTrigger value="context" className="flex-1" title="Context"><ContextIcon className="size-3.5" /><span className="sr-only">Context</span></TabsTrigger>
              <TabsTrigger value="results" className="flex-1" title="Results"><ResultIcon className="size-3.5" /><span className="sr-only">Results</span></TabsTrigger>
              <TabsTrigger value="settings" className="flex-1" title="Settings"><SettingsIcon className="size-3.5" /><span className="sr-only">Settings</span></TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="runs" className="flex-1 overflow-y-auto p-3 mt-0">
            <div className="flex items-center justify-between mb-2.5">
              <div className="flex items-center gap-1.5">
                <RunIcon className="size-3.5 text-muted-foreground" />
                <h2 className="text-xs font-semibold">Runs</h2>
              </div>
              <Button size="icon" className="size-6" onClick={startNewRun} title="New Run">
                <NewIcon className="size-3" />
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
                <RunIcon className="mx-auto size-6 text-muted-foreground/20 mb-2" />
                <p className="text-[10px] text-muted-foreground/50">No runs yet</p>
              </div>
            )}
          </TabsContent>

          <TabsContent value="context" className="flex-1 overflow-hidden p-3 mt-0 flex flex-col">
            <WorkspaceContextTree workspaceId={currentWorkspaceId} />
          </TabsContent>

          <TabsContent value="results" className="flex-1 overflow-y-auto p-3 mt-0">
            <div className="flex items-center justify-between mb-2.5">
              <div className="flex items-center gap-1.5">
                <ResultIcon className="size-3.5 text-muted-foreground" />
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
                <ResultIcon className="mx-auto size-6 text-muted-foreground/20 mb-2" />
                <p className="text-[10px] text-muted-foreground/50">No artifacts yet</p>
              </div>
            )}
          </TabsContent>

          <TabsContent value="settings" className="flex-1 overflow-y-auto p-3 mt-0">
            <div className="flex items-center gap-1.5 mb-3">
              <SettingsIcon className="size-3.5 text-muted-foreground" />
              <span className="text-xs font-semibold">Settings</span>
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

              {/* Global Event */}
              <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
                <div className="flex items-center gap-2">
                  <Globe className="size-3.5 text-muted-foreground shrink-0" />
                  <div>
                    <p className="text-xs font-medium leading-none">Global Event</p>
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
                  : <><SaveIcon className="size-3.5" />Save</>}
              </Button>
            </div>
          </TabsContent>
        </Tabs>
      </aside>
    </div>
    <ChatFilePreviewDialog
      target={previewTarget}
      onClose={() => setPreviewTarget(null)}
    />
    </>
  );
}
