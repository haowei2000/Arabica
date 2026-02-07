/**
 * Modern UI blocks for agent stream events with enhanced visuals
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight, Brain, Wrench, CheckCircle2, XCircle, Clock, AlertCircle } from 'lucide-react';
import type { ToolCallState, ToolPendingState, AgentPlanStepPayload } from '@/types/events';
import { Card, CardBody, Badge, Button } from '@/components/ui';

// ── ThinkingBlock ────────────────────────────────────────────────────────────

export function ThinkingBlock({
  content,
  defaultCollapsed = false,
}: {
  content: string;
  defaultCollapsed?: boolean;
}) {
  const [expanded, setExpanded] = useState(!defaultCollapsed);

  return (
    <Card className="border-purple-200 dark:border-purple-800/50 bg-gradient-to-r from-purple-50 to-purple-100/50 dark:from-purple-950/30 dark:to-purple-900/20 animate-scale-in">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-purple-100/50 dark:hover:bg-purple-900/30 rounded-t-xl"
      >
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-500 to-purple-600 flex items-center justify-center shadow-lg shadow-purple-500/30">
          <Brain className="w-4 h-4 text-white" />
        </div>
        <span className="flex-1 text-sm font-semibold text-purple-900 dark:text-purple-100">
          AI Thinking
        </span>
        {expanded ? (
          <ChevronDown className="w-5 h-5 text-purple-600 dark:text-purple-400" />
        ) : (
          <ChevronRight className="w-5 h-5 text-purple-600 dark:text-purple-400" />
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-2">
          <div className="text-sm text-purple-800 dark:text-purple-200 whitespace-pre-wrap bg-white/50 dark:bg-navy-900/30 rounded-lg p-3 font-mono">
            {content}
          </div>
        </div>
      )}
    </Card>
  );
}

// ── ToolCallCard ─────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: ToolCallState['status'] }) {
  if (status === 'pending') {
    return (
      <Badge variant="warning" size="sm" dot>
        <Clock className="w-3 h-3 mr-1" />
        Running
      </Badge>
    );
  }

  if (status === 'completed') {
    return (
      <Badge variant="success" size="sm" dot>
        <CheckCircle2 className="w-3 h-3 mr-1" />
        Done
      </Badge>
    );
  }

  return (
    <Badge variant="error" size="sm" dot>
      <XCircle className="w-3 h-3 mr-1" />
      Failed
    </Badge>
  );
}

export function ToolCallCard({ toolCall }: { toolCall: ToolCallState }) {
  const [open, setOpen] = useState(false);

  const borderColor = {
    pending: 'border-amber-200 dark:border-amber-800/50',
    completed: 'border-emerald-200 dark:border-emerald-800/50',
    error: 'border-red-200 dark:border-red-800/50',
  }[toolCall.status];

  const bgGradient = {
    pending: 'from-amber-50 to-amber-100/50 dark:from-amber-950/30 dark:to-amber-900/20',
    completed: 'from-emerald-50 to-emerald-100/50 dark:from-emerald-950/30 dark:to-emerald-900/20',
    error: 'from-red-50 to-red-100/50 dark:from-red-950/30 dark:to-red-900/20',
  }[toolCall.status];

  return (
    <Card className={`${borderColor} bg-gradient-to-r ${bgGradient} animate-scale-in`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left transition-all rounded-t-xl hover:opacity-80"
      >
        <div className={`w-8 h-8 rounded-lg bg-gradient-to-br ${
          toolCall.status === 'pending'
            ? 'from-amber-500 to-amber-600 shadow-amber-500/30'
            : toolCall.status === 'completed'
              ? 'from-emerald-500 to-emerald-600 shadow-emerald-500/30'
              : 'from-red-500 to-red-600 shadow-red-500/30'
        } flex items-center justify-center shadow-lg`}>
          <Wrench className="w-4 h-4 text-white" />
        </div>
        <span className="text-sm font-semibold text-navy-900 dark:text-navy-100 flex-1">
          {toolCall.tool_name}
        </span>
        <StatusBadge status={toolCall.status} />
        {open ? (
          <ChevronDown className="w-5 h-5 text-secondary-400" />
        ) : (
          <ChevronRight className="w-5 h-5 text-secondary-400" />
        )}
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3 border-t border-secondary-200/50 dark:border-navy-700/50 pt-3">
          <div>
            <p className="text-xs font-bold text-secondary-600 dark:text-secondary-400 uppercase tracking-wider mb-2">
              Arguments
            </p>
            <pre className="text-xs text-secondary-700 dark:text-secondary-300 bg-white/60 dark:bg-navy-900/40 rounded-lg px-3 py-2 overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(toolCall.arguments, null, 2)}
            </pre>
          </div>

          {(toolCall.result !== undefined || toolCall.error_message) && (
            <div>
              <p className="text-xs font-bold text-secondary-600 dark:text-secondary-400 uppercase tracking-wider mb-2 flex items-center gap-2">
                {toolCall.status === 'error' ? 'Error' : 'Result'}
                {toolCall.execution_time_ms != null && (
                  <Badge variant="neutral" size="sm">
                    {toolCall.execution_time_ms}ms
                  </Badge>
                )}
              </p>
              <pre
                className={`text-xs rounded-lg px-3 py-2 overflow-x-auto whitespace-pre-wrap ${
                  toolCall.status === 'error'
                    ? 'text-red-700 dark:text-red-300 bg-red-100/60 dark:bg-red-900/40'
                    : 'text-secondary-700 dark:text-secondary-300 bg-white/60 dark:bg-navy-900/40'
                }`}
              >
                {toolCall.status === 'error'
                  ? String(toolCall.error_message)
                  : typeof toolCall.result === 'string'
                    ? toolCall.result
                    : JSON.stringify(toolCall.result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

// ── PlanStepList ─────────────────────────────────────────────────────────────

function StepIcon({ status }: { status: AgentPlanStepPayload['status'] }) {
  if (status === 'completed') {
    return (
      <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-emerald-500 to-emerald-600 flex items-center justify-center shadow-lg shadow-emerald-500/30">
        <CheckCircle2 className="w-4 h-4 text-white" />
      </div>
    );
  }

  if (status === 'in_progress') {
    return (
      <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center shadow-lg shadow-blue-500/30 animate-pulse">
        <div className="w-2 h-2 rounded-full bg-white"></div>
      </div>
    );
  }

  if (status === 'failed') {
    return (
      <div className="w-6 h-6 rounded-lg bg-gradient-to-br from-red-500 to-red-600 flex items-center justify-center shadow-lg shadow-red-500/30">
        <XCircle className="w-4 h-4 text-white" />
      </div>
    );
  }

  return (
    <div className="w-6 h-6 rounded-lg border-2 border-secondary-300 dark:border-navy-600 bg-secondary-100 dark:bg-navy-800"></div>
  );
}

export function PlanStepList({ steps }: { steps: AgentPlanStepPayload[] }) {
  return (
    <Card className="border-blue-200 dark:border-blue-800/50 bg-gradient-to-r from-blue-50 to-blue-100/50 dark:from-blue-950/30 dark:to-blue-900/20 animate-scale-in">
      <div className="px-4 py-3 border-b border-blue-200/50 dark:border-blue-700/50">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-blue-600 flex items-center justify-center shadow-lg shadow-blue-500/30">
            <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
            </svg>
          </div>
          <span className="text-sm font-bold text-blue-900 dark:text-blue-100">Execution Plan</span>
        </div>
      </div>

      <div className="p-4 space-y-3">
        {steps.map((step, index) => (
          <div key={step.step_number} className="flex items-start gap-3 group">
            <div className="mt-1">
              <StepIcon status={step.status} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-start gap-2">
                <Badge variant="neutral" size="sm" className="shrink-0">
                  #{step.step_number}
                </Badge>
                <p
                  className={`text-sm font-medium ${
                    step.status === 'completed'
                      ? 'text-secondary-500 dark:text-secondary-400 line-through'
                      : 'text-navy-900 dark:text-navy-100'
                  }`}
                >
                  {step.step_description}
                </p>
              </div>
              {step.output && (
                <p className="text-xs text-secondary-500 dark:text-secondary-400 mt-1.5 pl-10 bg-white/40 dark:bg-navy-900/30 rounded px-2 py-1">
                  {step.output}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ── ApprovalCard ─────────────────────────────────────────────────────────────

export function ApprovalCard({
  pending,
  onApprove,
}: {
  pending: ToolPendingState;
  onApprove: (toolId: string, approved: boolean, result?: string) => void;
}) {
  const [expanded, setExpanded] = useState(true);

  return (
    <Card className="border-amber-300 dark:border-amber-700/80 bg-gradient-to-r from-amber-50 to-amber-100/50 dark:from-amber-950/40 dark:to-amber-900/30 animate-scale-in ring-2 ring-amber-400/30 dark:ring-amber-600/30">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-amber-100/50 dark:hover:bg-amber-900/40 rounded-t-xl"
      >
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-amber-500 to-amber-600 flex items-center justify-center shadow-lg shadow-amber-500/40 relative">
          <AlertCircle className="w-4 h-4 text-white" />
          <span className="absolute -top-1 -right-1 w-3 h-3 bg-red-500 rounded-full animate-pulse"></span>
        </div>
        <span className="flex-1 text-sm font-bold text-amber-900 dark:text-amber-100">
          Approval Required: <span className="font-mono">{pending.tool_name}</span>
        </span>
        {expanded ? (
          <ChevronDown className="w-5 h-5 text-amber-600 dark:text-amber-400" />
        ) : (
          <ChevronRight className="w-5 h-5 text-amber-600 dark:text-amber-400" />
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-4 space-y-3 border-t border-amber-200/50 dark:border-amber-700/50 pt-3">
          <Badge variant="warning" size="sm">
            {pending.reason}
          </Badge>

          <div>
            <p className="text-xs font-bold text-amber-800 dark:text-amber-300 uppercase tracking-wider mb-2">
              Tool Arguments
            </p>
            <pre className="text-xs text-amber-900 dark:text-amber-200 bg-white/60 dark:bg-amber-950/40 rounded-lg px-3 py-2 overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(pending.arguments, null, 2)}
            </pre>
          </div>

          <div className="flex gap-2 pt-2">
            <Button
              type="button"
              onClick={() => onApprove(pending.tool_id, true)}
              variant="primary"
              size="md"
              className="flex-1 bg-gradient-to-r from-emerald-500 to-emerald-600 hover:from-emerald-600 hover:to-emerald-700"
              icon={<CheckCircle2 className="w-4 h-4" />}
            >
              Approve
            </Button>
            <Button
              type="button"
              onClick={() => onApprove(pending.tool_id, false)}
              variant="danger"
              size="md"
              className="flex-1"
              icon={<XCircle className="w-4 h-4" />}
            >
              Deny
            </Button>
          </div>
        </div>
      )}
    </Card>
  );
}
