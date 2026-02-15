import { useState } from 'react';
import { useWorkspaces, useWorkspaceRuns, useRunEvents } from '@/hooks/useMemory';
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

  // Get all unique event types for filtering
  const allEventTypes = Array.from(new Set(allEvents.map(event => event.event_type))).sort();

  // Filter events based on hidden event types
  const events = allEvents.filter(event => !hiddenEventTypes.has(event.event_type));

  const toggleEventTypeFilter = (eventType: string) => {
    const newHidden = new Set(hiddenEventTypes);
    if (newHidden.has(eventType)) {
      newHidden.delete(eventType);
    } else {
      newHidden.add(eventType);
    }
    setHiddenEventTypes(newHidden);
  };

  const toggleWorkspace = (workspaceId: string) => {
    const newExpanded = new Set(expandedWorkspaces);
    if (newExpanded.has(workspaceId)) {
      newExpanded.delete(workspaceId);
      setSelectedWorkspace(null);
    } else {
      newExpanded.add(workspaceId);
      setSelectedWorkspace(workspaceId);
    }
    setExpandedWorkspaces(newExpanded);
  };

  const toggleRun = (runId: string) => {
    const newExpanded = new Set(expandedRuns);
    if (newExpanded.has(runId)) {
      newExpanded.delete(runId);
      setSelectedRun(null);
    } else {
      newExpanded.add(runId);
      setSelectedRun(runId);
    }
    setExpandedRuns(newExpanded);
  };

  const getStatusColor = (status: string) => {
    switch (status.toLowerCase()) {
      case 'completed':
        return 'text-green-600 dark:text-green-400 bg-green-100 dark:bg-green-900/30';
      case 'running':
      case 'active':
        return 'text-blue-600 dark:text-blue-400 bg-blue-100 dark:bg-blue-900/30';
      case 'failed':
      case 'error':
        return 'text-red-600 dark:text-red-400 bg-red-100 dark:bg-red-900/30';
      case 'waiting':
      case 'pending':
        return 'text-yellow-600 dark:text-yellow-400 bg-yellow-100 dark:bg-yellow-900/30';
      default:
        return 'text-secondary-600 dark:text-secondary-400 bg-secondary-100 dark:bg-secondary-800';
    }
  };

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const formatEventType = (type: string) => {
    return type.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());
  };

  if (workspacesLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="text-secondary-500 dark:text-secondary-400">Loading...</div>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <h2 className="text-xl font-bold text-navy-900 dark:text-navy-100">Context Memory</h2>
        <p className="text-sm text-secondary-500 dark:text-secondary-400 mt-1">
          View your conversation history, workspaces, runs, and events
        </p>
      </div>

      {/* Event Type Filter */}
      {allEventTypes.length > 0 && (
        <div className="mb-6 bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700 p-4">
          <div className="flex items-center gap-2 mb-3">
            <svg className="w-4 h-4 text-secondary-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z" />
            </svg>
            <h3 className="text-sm font-semibold text-navy-900 dark:text-navy-100">
              Event Type Filter
            </h3>
            <span className="text-xs text-secondary-500 dark:text-secondary-400">
              (Click to hide/show event types)
            </span>
          </div>
          <div className="flex flex-wrap gap-2">
            {allEventTypes.map((eventType) => {
              const isHidden = hiddenEventTypes.has(eventType);
              return (
                <button
                  key={eventType}
                  onClick={() => toggleEventTypeFilter(eventType)}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium transition-all ${
                    isHidden
                      ? 'bg-secondary-100 dark:bg-secondary-800 text-secondary-400 dark:text-secondary-500 line-through opacity-60 hover:opacity-80'
                      : 'bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-400 hover:bg-primary-200 dark:hover:bg-primary-900/50'
                  }`}
                >
                  {formatEventType(eventType)}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Workspaces List */}
      {workspaces.length > 0 ? (
        <div className="space-y-4">
          {workspaces.map((workspace) => {
            const isExpanded = expandedWorkspaces.has(workspace.id);
            const workspaceRuns = isExpanded ? runs : [];

            return (
              <div
                key={workspace.id}
                className="bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700 overflow-hidden"
              >
                {/* Workspace Header */}
                <button
                  onClick={() => toggleWorkspace(workspace.id)}
                  className="w-full px-6 py-4 flex items-center justify-between hover:bg-secondary-50 dark:hover:bg-navy-700 transition-colors"
                >
                  <div className="flex items-center gap-4 flex-1 min-w-0">
                    {/* Expand Icon */}
                    <svg
                      className={`w-5 h-5 text-secondary-500 transition-transform ${
                        isExpanded ? 'rotate-90' : ''
                      }`}
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M9 5l7 7-7 7"
                      />
                    </svg>

                    {/* Workspace Info */}
                    <div className="flex-1 min-w-0 text-left">
                      <h3 className="text-lg font-semibold text-navy-900 dark:text-navy-100 truncate">
                        {workspace.name}
                      </h3>
                      {workspace.description && (
                        <p className="text-sm text-secondary-500 dark:text-secondary-400 truncate">
                          {workspace.description}
                        </p>
                      )}
                    </div>

                    {/* Metadata */}
                    <div className="flex items-center gap-4 text-sm">
                      <span className="text-secondary-500 dark:text-secondary-400">
                        {workspace.run_count} runs
                      </span>
                      <span className={`px-2 py-1 rounded-full text-xs font-medium ${getStatusColor(workspace.status)}`}>
                        {workspace.status}
                      </span>
                      <span className="text-secondary-400 dark:text-secondary-500 text-xs">
                        {formatDate(workspace.created_at)}
                      </span>
                    </div>
                  </div>
                </button>

                {/* Runs List */}
                {isExpanded && (
                  <div className="border-t border-secondary-200 dark:border-navy-700 bg-secondary-50 dark:bg-navy-900">
                    {workspaceRuns.length > 0 ? (
                      <div className="divide-y divide-secondary-200 dark:divide-navy-700">
                        {workspaceRuns.map((run) => {
                          const isRunExpanded = expandedRuns.has(run.id);
                          const runEvents = isRunExpanded ? events : [];

                          return (
                            <div key={run.id} className="bg-white dark:bg-navy-800">
                              {/* Run Header */}
                              <button
                                onClick={() => toggleRun(run.id)}
                                className="w-full px-12 py-3 flex items-center justify-between hover:bg-secondary-50 dark:hover:bg-navy-700 transition-colors"
                              >
                                <div className="flex items-center gap-3 flex-1 min-w-0">
                                  {/* Expand Icon */}
                                  <svg
                                    className={`w-4 h-4 text-secondary-500 transition-transform ${
                                      isRunExpanded ? 'rotate-90' : ''
                                    }`}
                                    fill="none"
                                    viewBox="0 0 24 24"
                                    stroke="currentColor"
                                  >
                                    <path
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                      strokeWidth={2}
                                      d="M9 5l7 7-7 7"
                                    />
                                  </svg>

                                  {/* Run Info */}
                                  <div className="flex-1 min-w-0 text-left">
                                    <div className="flex items-center gap-2">
                                      <span className="text-sm font-medium text-navy-900 dark:text-navy-100">
                                        Run
                                      </span>
                                      <code className="text-xs text-secondary-500 dark:text-secondary-400 font-mono">
                                        {run.id.substring(0, 8)}
                                      </code>
                                    </div>
                                    <p className="text-xs text-secondary-500 dark:text-secondary-400">
                                      Trigger: {run.trigger_type}
                                    </p>
                                  </div>

                                  {/* Metadata */}
                                  <div className="flex items-center gap-3 text-xs">
                                    <span className={`px-2 py-1 rounded-full font-medium ${getStatusColor(run.status)}`}>
                                      {run.status}
                                    </span>
                                    <span className="text-secondary-400 dark:text-secondary-500">
                                      Seq: {run.last_event_sequence}
                                    </span>
                                    <span className="text-secondary-400 dark:text-secondary-500">
                                      {formatDate(run.created_at)}
                                    </span>
                                  </div>
                                </div>
                              </button>

                              {/* Events List */}
                              {isRunExpanded && (
                                <div className="border-t border-secondary-200 dark:border-navy-700 bg-secondary-50 dark:bg-navy-900 px-16 py-4">
                                  {runEvents.length > 0 ? (
                                    <div className="space-y-2">
                                      <h4 className="text-xs font-semibold text-secondary-600 dark:text-secondary-400 mb-3">
                                        Events ({runEvents.length})
                                      </h4>
                                      {runEvents.map((event) => (
                                        <div
                                          key={event.id}
                                          className="bg-white dark:bg-navy-800 rounded p-3 border border-secondary-200 dark:border-navy-700"
                                        >
                                          <div className="flex items-start justify-between gap-3">
                                            <div className="flex-1 min-w-0">
                                              <div className="flex items-center gap-2 mb-1">
                                                <span className="text-xs font-mono text-secondary-500 dark:text-secondary-400">
                                                  #{event.sequence}
                                                </span>
                                                <span className="text-sm font-medium text-navy-900 dark:text-navy-100">
                                                  {formatEventType(event.event_type)}
                                                </span>
                                              </div>
                                              {event.payload && Object.keys(event.payload).length > 0 && (
                                                <pre className="text-xs text-secondary-600 dark:text-secondary-400 mt-2 overflow-x-auto">
                                                  {JSON.stringify(event.payload, null, 2)}
                                                </pre>
                                              )}
                                            </div>
                                            <span className="text-xs text-secondary-400 dark:text-secondary-500 whitespace-nowrap">
                                              {formatDate(event.created_at)}
                                            </span>
                                          </div>
                                        </div>
                                      ))}
                                    </div>
                                  ) : (
                                    <p className="text-sm text-secondary-400 dark:text-secondary-500 text-center py-4">
                                      No events found
                                    </p>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="text-sm text-secondary-400 dark:text-secondary-500 text-center py-8">
                        No runs in this workspace
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        /* Empty State */
        <div className="text-center py-12">
          <div className="text-secondary-400 dark:text-secondary-600 mb-4">
            <svg
              className="mx-auto h-12 w-12"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M9 3v2m6-2v2M9 19v2m6-2v2M5 9H3m2 6H3m18-6h-2m2 6h-2M7 19h10a2 2 0 002-2V7a2 2 0 00-2-2H7a2 2 0 00-2 2v10a2 2 0 002 2zM9 9h6v6H9V9z"
              />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-navy-900 dark:text-navy-100 mb-1">
            No Workspaces Yet
          </h3>
          <p className="text-secondary-500 dark:text-secondary-400">
            Start a conversation to create your first workspace
          </p>
        </div>
      )}
    </div>
  );
}
