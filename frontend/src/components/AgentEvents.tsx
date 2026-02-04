/**
 * Reusable UI blocks for every non-text event the agent stream can emit.
 *
 *   ThinkingBlock   – collapsible reasoning trace   (AGENT_THINKING)
 *   ToolCallCard    – single tool invocation card   (TOOL_CALL / TOOL_RESULT / TOOL_ERROR)
 *   PlanStepList    – ordered step-progress list    (AGENT_PLAN_STEP)
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { ToolCallState, ToolPendingState, AgentPlanStepPayload } from '@/types/events';

// ── ThinkingBlock ────────────────────────────────────────────────────────────

export function ThinkingBlock({
  content,
  defaultCollapsed = false,
}: {
  content: string;
  /** Start collapsed – use for already-finished messages. */
  defaultCollapsed?: boolean;
}) {
  const [expanded, setExpanded] = useState(!defaultCollapsed);

  return (
    <div className="rounded-lg border border-secondary-200 dark:border-navy-600 overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left
                   bg-secondary-50 dark:bg-navy-800
                   hover:bg-secondary-100 dark:hover:bg-navy-700
                   text-secondary-600 dark:text-secondary-400
                   text-sm font-medium transition-colors"
      >
        {expanded ? (
          <ChevronDown className="w-4 h-4 shrink-0" />
        ) : (
          <ChevronRight className="w-4 h-4 shrink-0" />
        )}
        <span>Thinking</span>
        {!expanded && (
          <span className="ml-auto text-xs text-secondary-400 dark:text-secondary-500">
            click to expand
          </span>
        )}
      </button>

      {expanded && (
        <div className="px-3 py-2 text-sm text-secondary-600 dark:text-secondary-400 whitespace-pre-wrap">
          {content}
        </div>
      )}
    </div>
  );
}

// ── ToolCallCard ─────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: ToolCallState['status'] }) {
  if (status === 'pending') {
    return (
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full
                   text-xs font-medium
                   bg-amber-100 dark:bg-amber-900/30
                   text-amber-700 dark:text-amber-400"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
        Running
      </span>
    );
  }

  if (status === 'completed') {
    return (
      <span
        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full
                   text-xs font-medium
                   bg-emerald-100 dark:bg-emerald-900/30
                   text-emerald-700 dark:text-emerald-400"
      >
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
        Done
      </span>
    );
  }

  // error
  return (
    <span
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full
                 text-xs font-medium
                 bg-red-100 dark:bg-red-900/30
                 text-red-700 dark:text-red-400"
    >
      <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
      Failed
    </span>
  );
}

export function ToolCallCard({ toolCall }: { toolCall: ToolCallState }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-lg border border-secondary-200 dark:border-navy-600 overflow-hidden">
      {/* header row: chevron + name + badge */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left
                   bg-secondary-50 dark:bg-navy-800
                   hover:bg-secondary-100 dark:hover:bg-navy-700
                   transition-colors"
      >
        {open ? (
          <ChevronDown className="w-4 h-4 text-secondary-400 shrink-0" />
        ) : (
          <ChevronRight className="w-4 h-4 text-secondary-400 shrink-0" />
        )}
        <span className="text-sm font-medium text-navy-900 dark:text-navy-100 flex-1 truncate">
          {toolCall.tool_name}
        </span>
        <StatusBadge status={toolCall.status} />
      </button>

      {/* expandable detail: arguments + result/error */}
      {open && (
        <div className="px-3 py-2 space-y-2 border-t border-secondary-200 dark:border-navy-600">
          <div>
            <p className="text-xs font-medium text-secondary-500 dark:text-secondary-400 uppercase tracking-wide">
              Arguments
            </p>
            <pre className="mt-1 text-xs text-secondary-600 dark:text-secondary-400
                            bg-secondary-50 dark:bg-navy-900 rounded px-2 py-1
                            overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(toolCall.arguments, null, 2)}
            </pre>
          </div>

          {(toolCall.result !== undefined || toolCall.error_message) && (
            <div>
              <p className="text-xs font-medium text-secondary-500 dark:text-secondary-400 uppercase tracking-wide">
                {toolCall.status === 'error' ? 'Error' : 'Result'}
                {toolCall.execution_time_ms != null && (
                  <span className="ml-2 normal-case font-normal text-secondary-400">
                    ({toolCall.execution_time_ms} ms)
                  </span>
                )}
              </p>
              <pre
                className={`mt-1 text-xs rounded px-2 py-1 overflow-x-auto whitespace-pre-wrap
                            bg-secondary-50 dark:bg-navy-900
                            ${
                              toolCall.status === 'error'
                                ? 'text-red-600 dark:text-red-400'
                                : 'text-secondary-600 dark:text-secondary-400'
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
    </div>
  );
}

// ── PlanStepList ─────────────────────────────────────────────────────────────

function StepIcon({ status }: { status: AgentPlanStepPayload['status'] }) {
  if (status === 'completed') {
    return (
      <div className="w-5 h-5 rounded-full bg-emerald-500 flex items-center justify-center shrink-0">
        <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
        </svg>
      </div>
    );
  }

  if (status === 'in_progress') {
    return (
      <div className="w-5 h-5 rounded-full border-2 border-primary-500 border-t-transparent animate-spin shrink-0" />
    );
  }

  if (status === 'failed') {
    return (
      <div className="w-5 h-5 rounded-full bg-red-500 flex items-center justify-center shrink-0">
        <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </div>
    );
  }

  // pending
  return (
    <div className="w-5 h-5 rounded-full border-2 border-secondary-300 dark:border-navy-600 shrink-0" />
  );
}

export function PlanStepList({ steps }: { steps: AgentPlanStepPayload[] }) {
  return (
    <div
      className="rounded-lg border border-secondary-200 dark:border-navy-600 overflow-hidden
                 bg-secondary-50 dark:bg-navy-800"
    >
      <div className="px-3 py-2 border-b border-secondary-200 dark:border-navy-600">
        <p className="text-xs font-semibold text-secondary-500 dark:text-secondary-400 uppercase tracking-wide">
          Plan
        </p>
      </div>

      <div className="p-3 space-y-3">
        {steps.map((step) => (
          <div key={step.step_number} className="flex items-start gap-3">
            <div className="mt-0.5">
              <StepIcon status={step.status} />
            </div>
            <div className="flex-1 min-w-0">
              <p
                className={`text-sm font-medium ${
                  step.status === 'completed'
                    ? 'text-secondary-500 dark:text-secondary-500'
                    : 'text-navy-900 dark:text-navy-100'
                }`}
              >
                {step.step_description}
              </p>
              {step.output && (
                <p className="text-xs text-secondary-500 dark:text-secondary-400 mt-0.5 truncate">
                  {step.output}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── ApprovalCard ─────────────────────────────────────────────────────────────

export function ApprovalCard({
  pending,
  onApprove,
}: {
  pending: ToolPendingState;
  /** Called with (toolId, approved, optionalResult) */
  onApprove: (toolId: string, approved: boolean, result?: string) => void;
}) {
  const [expanded, setExpanded] = useState(true);

  return (
    <div className="rounded-lg border border-amber-300 dark:border-amber-700 overflow-hidden
                    bg-amber-50 dark:bg-amber-900/20">
      {/* header */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left transition-colors
                   hover:bg-amber-100 dark:hover:bg-amber-900/40"
      >
        <span className="w-2 h-2 rounded-full bg-amber-500 animate-pulse shrink-0" />
        <span className="text-sm font-semibold text-amber-800 dark:text-amber-200 flex-1">
          Tool approval required: <span className="font-mono">{pending.tool_name}</span>
        </span>
        {expanded ? (
          <ChevronDown className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />
        ) : (
          <ChevronRight className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />
        )}
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-3 border-t border-amber-200 dark:border-amber-700/50">
          {/* reason chip */}
          <div className="pt-2">
            <span className="inline-block text-xs px-2 py-0.5 rounded-full
                             bg-amber-100 dark:bg-amber-800/40
                             text-amber-700 dark:text-amber-300">
              {pending.reason}
            </span>
          </div>

          {/* arguments preview */}
          <div>
            <p className="text-xs font-medium text-amber-700 dark:text-amber-400 uppercase tracking-wide mb-1">
              Arguments
            </p>
            <pre className="text-xs text-amber-800 dark:text-amber-300
                            bg-amber-100 dark:bg-amber-900/40 rounded px-2 py-1.5
                            overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(pending.arguments, null, 2)}
            </pre>
          </div>

          {/* action buttons */}
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              onClick={() => onApprove(pending.tool_id, true)}
              className="flex-1 px-3 py-1.5 text-sm font-medium rounded-lg
                         bg-emerald-500 hover:bg-emerald-600
                         text-white transition-colors"
            >
              Approve
            </button>
            <button
              type="button"
              onClick={() => onApprove(pending.tool_id, false)}
              className="flex-1 px-3 py-1.5 text-sm font-medium rounded-lg
                         bg-red-500 hover:bg-red-600
                         text-white transition-colors"
            >
              Deny
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
