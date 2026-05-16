import { useState } from 'react';
import { ChevronDown, ChevronRight, BookOpen, Package, MessageCircleQuestion, Download, Loader2 } from 'lucide-react';
import type { ToolCallState, ToolPendingState, AgentPlanStepPayload, StreamError, ContextUsageState, OutcomeState, AgentQueryState } from '@/types/events';
import { ErrorCategory } from '@/types/events';
import { artifactService } from '@/services/artifactService';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

// ── ThinkingBlock ────────────────────────────────────────────────────────────

export function ThinkingBlock({ content, defaultCollapsed = false }: { content: string | string[]; defaultCollapsed?: boolean }) {
  const [expanded, setExpanded] = useState(!defaultCollapsed);
  const text = Array.isArray(content) ? content.join('') : content;

  return (
    <div className="rounded-xl border border-border/60 overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left bg-muted/50 hover:bg-muted/70 text-muted-foreground text-xs font-medium transition-colors"
      >
        {expanded ? <ChevronDown className="size-3.5 shrink-0" /> : <ChevronRight className="size-3.5 shrink-0" />}
        <span className="text-muted-foreground/70">Thinking...</span>
        {!expanded && <span className="ml-auto text-[10px] opacity-50">expand</span>}
      </button>
      {expanded && <div className="px-3 py-2 text-xs text-muted-foreground/80 whitespace-pre-wrap leading-relaxed border-t border-border/40">{text}</div>}
    </div>
  );
}

// ── StatusBadge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: ToolCallState['status'] }) {
  const styles = {
    pending: 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400',
    completed: 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400',
    error: 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400',
  } as const;
  const dots = { pending: 'bg-amber-500 animate-pulse', completed: 'bg-emerald-500', error: 'bg-red-500' } as const;
  const labels = { pending: 'Running', completed: 'Done', error: 'Failed' } as const;
  const s = status === 'error' ? 'error' : status;

  return (
    <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium', styles[s])}>
      <span className={cn('w-1.5 h-1.5 rounded-full', dots[s])} />
      {labels[s]}
    </span>
  );
}

// ── ToolCallCard ─────────────────────────────────────────────────────────────

export function ToolCallCard({ toolCall }: { toolCall: ToolCallState }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left bg-muted hover:bg-muted/80 transition-colors"
      >
        {open ? <ChevronDown className="size-4 text-muted-foreground shrink-0" /> : <ChevronRight className="size-4 text-muted-foreground shrink-0" />}
        <span className="text-sm font-medium flex-1 truncate">{toolCall.tool_name}</span>
        <StatusBadge status={toolCall.status} />
      </button>

      {open && (
        <div className="px-3 py-2 space-y-2 border-t border-border">
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">Arguments</p>
            <pre className="mt-1 text-xs text-muted-foreground bg-muted rounded px-2 py-1 overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(toolCall.arguments, null, 2)}
            </pre>
          </div>
          {(toolCall.result !== undefined || toolCall.error_message) && (
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                {toolCall.status === 'error' ? 'Error' : 'Result'}
                {toolCall.execution_time_ms != null && <span className="ml-2 normal-case font-normal">({toolCall.execution_time_ms} ms)</span>}
              </p>
              <pre className={cn('mt-1 text-xs rounded px-2 py-1 overflow-x-auto whitespace-pre-wrap bg-muted', toolCall.status === 'error' ? 'text-destructive' : 'text-muted-foreground')}>
                {toolCall.status === 'error' ? String(toolCall.error_message) : typeof toolCall.result === 'string' ? toolCall.result : JSON.stringify(toolCall.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── PlanStepList ─────────────────────────────────────────────────────────────

function StepIcon({ status }: { status: AgentPlanStepPayload['status'] }) {
  if (status === 'completed') return (
    <div className="w-5 h-5 rounded-full bg-emerald-500 flex items-center justify-center shrink-0">
      <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" /></svg>
    </div>
  );
  if (status === 'in_progress') return <div className="w-5 h-5 rounded-full border-2 border-primary border-t-transparent animate-spin shrink-0" />;
  if (status === 'failed') return (
    <div className="w-5 h-5 rounded-full bg-destructive flex items-center justify-center shrink-0">
      <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M6 18L18 6M6 6l12 12" /></svg>
    </div>
  );
  return <div className="w-5 h-5 rounded-full border-2 border-border shrink-0" />;
}

export function PlanStepList({ steps }: { steps: AgentPlanStepPayload[] }) {
  return (
    <div className="rounded-lg border border-border overflow-hidden bg-muted">
      <div className="px-3 py-2 border-b border-border">
        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Plan</p>
      </div>
      <div className="p-3 space-y-3">
        {steps.map((step) => (
          <div key={step.step_number} className="flex items-start gap-3">
            <div className="mt-0.5"><StepIcon status={step.status} /></div>
            <div className="flex-1 min-w-0">
              <p className={cn('text-sm font-medium', step.status === 'completed' ? 'text-muted-foreground' : 'text-foreground')}>{step.step_description}</p>
              {step.output && <p className="text-xs text-muted-foreground mt-0.5 truncate">{step.output}</p>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── ApprovalCard ─────────────────────────────────────────────────────────────

export function ApprovalCard({ pending, onApprove }: { pending: ToolPendingState; onApprove: (toolId: string, approved: boolean, result?: string) => void }) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="rounded-lg border border-amber-300 dark:border-amber-700 overflow-hidden bg-amber-50 dark:bg-amber-900/20">
      <button type="button" onClick={() => setExpanded((v) => !v)} className="w-full flex items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-amber-100 dark:hover:bg-amber-900/40">
        <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse shrink-0" />
        <span className="text-sm font-semibold text-amber-800 dark:text-amber-200 flex-1">
          Tool approval required: <span className="font-mono">{pending.tool_name}</span>
        </span>
        {expanded ? <ChevronDown className="size-4 text-amber-600 dark:text-amber-400 shrink-0" /> : <ChevronRight className="size-4 text-amber-600 dark:text-amber-400 shrink-0" />}
      </button>
      {expanded && (
        <div className="px-3 pb-3 space-y-3 border-t border-amber-200 dark:border-amber-700/50">
          <div className="pt-2">
            <span className="inline-block text-xs px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-800/40 text-amber-700 dark:text-amber-300">{pending.reason}</span>
          </div>
          <div>
            <p className="text-xs font-medium text-amber-700 dark:text-amber-400 uppercase tracking-wide mb-1">Arguments</p>
            <pre className="text-xs text-amber-800 dark:text-amber-300 bg-amber-100 dark:bg-amber-900/40 rounded px-2 py-1.5 overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(pending.arguments, null, 2)}
            </pre>
          </div>
          <div className="flex gap-2 pt-1">
            <Button type="button" onClick={() => onApprove(pending.tool_id, true)} className="flex-1 bg-emerald-500 hover:bg-emerald-600 text-white" size="sm">Approve</Button>
            <Button type="button" variant="destructive" onClick={() => onApprove(pending.tool_id, false)} className="flex-1" size="sm">Deny</Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── QueryCard ────────────────────────────────────────────────────────────────

export function QueryCard({ query, onRespond }: { query: AgentQueryState; onRespond: (toolId: string, answer: string) => void }) {
  const [answer, setAnswer] = useState('');

  const handleSubmit = () => {
    const trimmed = answer.trim();
    if (!trimmed) return;
    onRespond(query.tool_id, trimmed);
    setAnswer('');
  };

  return (
    <div className="rounded-lg border border-indigo-300 dark:border-indigo-700 overflow-hidden bg-indigo-50 dark:bg-indigo-900/20">
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-indigo-200 dark:border-indigo-700/50">
        <MessageCircleQuestion className="size-4 text-indigo-500 dark:text-indigo-400 shrink-0" />
        <span className="text-sm font-semibold text-indigo-800 dark:text-indigo-200 flex-1">Agent is asking a question</span>
        <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse shrink-0" />
      </div>
      <div className="px-3 py-3 space-y-3">
        <p className="text-sm text-indigo-900 dark:text-indigo-100">{query.question}</p>
        <textarea
          className="w-full rounded-md border border-indigo-200 dark:border-indigo-700 bg-white dark:bg-indigo-950/40 text-sm text-foreground px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-indigo-400 dark:focus:ring-indigo-500 placeholder:text-muted-foreground"
          rows={3}
          placeholder="Type your answer..."
          value={answer}
          onChange={(e) => setAnswer(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit();
          }}
        />
        <div className="flex justify-end">
          <Button type="button" onClick={handleSubmit} disabled={!answer.trim()} size="sm" className="bg-indigo-600 hover:bg-indigo-700 text-white disabled:opacity-50">
            Submit
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── ContextUsageCard ─────────────────────────────────────────────────────────

export function ContextUsageCard({ usage }: { usage: ContextUsageState }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border border-blue-200 dark:border-blue-800 overflow-hidden">
      <button type="button" onClick={() => setOpen((v) => !v)} className="w-full flex items-center gap-2 px-3 py-2 text-left bg-blue-50 dark:bg-blue-900/20 hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-colors">
        <BookOpen className="size-4 text-blue-500 dark:text-blue-400 shrink-0" />
        <span className="text-sm font-medium text-blue-800 dark:text-blue-200 flex-1 truncate">Using {usage.context_type}: {usage.context_name}</span>
        {usage.results_count != null && <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 dark:bg-blue-800/40 text-blue-700 dark:text-blue-300">{usage.results_count} results</span>}
        {open ? <ChevronDown className="size-4 text-blue-400 shrink-0" /> : <ChevronRight className="size-4 text-blue-400 shrink-0" />}
      </button>
      {open && (
        <div className="px-3 py-2 space-y-2 border-t border-blue-200 dark:border-blue-800">
          {usage.query && <div><p className="text-xs font-medium text-blue-600 dark:text-blue-400 uppercase tracking-wide">Query</p><p className="mt-1 text-xs text-blue-700 dark:text-blue-300">{usage.query}</p></div>}
          {usage.details && Object.keys(usage.details).length > 0 && (
            <div><p className="text-xs font-medium text-blue-600 dark:text-blue-400 uppercase tracking-wide">Details</p>
            <pre className="mt-1 text-xs text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-900/30 rounded px-2 py-1 overflow-x-auto whitespace-pre-wrap">{JSON.stringify(usage.details, null, 2)}</pre></div>
          )}
        </div>
      )}
    </div>
  );
}

// ── OutcomeCard ──────────────────────────────────────────────────────────────

export function OutcomeCard({
  outcome,
  workspaceId,
}: {
  outcome: OutcomeState;
  workspaceId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const canDownload = !!workspaceId && !!outcome.artifact_id;

  const handleDownload = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!workspaceId || !outcome.artifact_id) return;
    setDownloading(true);
    try {
      artifactService.downloadArtifact(
        workspaceId,
        outcome.artifact_id,
        outcome.outcome_name,
      );
    } finally {
      setTimeout(() => setDownloading(false), 1500);
    }
  };

  return (
    <div className="rounded-lg border border-emerald-200 dark:border-emerald-800 overflow-hidden">
      <div className="w-full flex items-center gap-2 px-3 py-2 bg-emerald-50 dark:bg-emerald-900/20 hover:bg-emerald-100 dark:hover:bg-emerald-900/40 transition-colors">
        <button type="button" onClick={() => setOpen((v) => !v)} className="min-w-0 flex-1 flex items-center gap-2 text-left">
          <Package className="size-4 text-emerald-500 dark:text-emerald-400 shrink-0" />
          <span className="text-sm font-medium text-emerald-800 dark:text-emerald-200 flex-1 truncate">Produced {outcome.outcome_type}: {outcome.outcome_name}</span>
        </button>
        {canDownload && (
          <button
            type="button"
            onClick={handleDownload}
            disabled={downloading}
            className="size-7 inline-flex items-center justify-center rounded-md text-emerald-700 dark:text-emerald-300 hover:bg-emerald-100 dark:hover:bg-emerald-800/50 transition-colors"
            title="Download artifact"
          >
            {downloading ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}
          </button>
        )}
        <button type="button" onClick={() => setOpen((v) => !v)} className="size-6 inline-flex items-center justify-center">
          {open ? <ChevronDown className="size-4 text-emerald-400 shrink-0" /> : <ChevronRight className="size-4 text-emerald-400 shrink-0" />}
        </button>
      </div>
      {open && (
        <div className="px-3 py-2 space-y-2 border-t border-emerald-200 dark:border-emerald-800">
          {outcome.summary && <div><p className="text-xs font-medium text-emerald-600 dark:text-emerald-400 uppercase tracking-wide">Summary</p><p className="mt-1 text-xs text-emerald-700 dark:text-emerald-300">{outcome.summary}</p></div>}
          {outcome.details && Object.keys(outcome.details).length > 0 && (
            <div><p className="text-xs font-medium text-emerald-600 dark:text-emerald-400 uppercase tracking-wide">Details</p>
            <pre className="mt-1 text-xs text-emerald-700 dark:text-emerald-300 bg-emerald-50 dark:bg-emerald-900/30 rounded px-2 py-1 overflow-x-auto whitespace-pre-wrap">{JSON.stringify(outcome.details, null, 2)}</pre></div>
          )}
        </div>
      )}
    </div>
  );
}

// ── ErrorMessage ─────────────────────────────────────────────────────────────

const errorLabels: Record<ErrorCategory, { label: string; cls: string }> = {
  [ErrorCategory.NETWORK]: { label: 'Network Error', cls: 'border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300' },
  [ErrorCategory.TIMEOUT]: { label: 'Timeout', cls: 'border-orange-200 dark:border-orange-800 bg-orange-50 dark:bg-orange-900/20 text-orange-700 dark:text-orange-300' },
  [ErrorCategory.RUN_FAILED]: { label: 'Run Failed', cls: 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300' },
  [ErrorCategory.UNKNOWN]: { label: 'Error', cls: 'border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300' },
};

export function ErrorMessage({ error, onRetry }: { error: StreamError; onRetry?: () => void }) {
  const config = errorLabels[error.category];
  return (
    <div className={cn('rounded-lg border px-3 py-3', config.cls)}>
      <div className="flex items-start gap-3">
        <span className="text-sm font-semibold shrink-0">{config.label}</span>
        <p className="text-sm flex-1">{error.message}</p>
        {error.retryable && onRetry && (
          <Button type="button" onClick={onRetry} size="sm" className="shrink-0">Retry</Button>
        )}
      </div>
    </div>
  );
}
