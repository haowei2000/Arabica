import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useRuns } from '@/hooks/useRuns';
import { useStreamingChat } from '@/hooks/useStreamingChat';
import { MessageRole } from '@/types/message';
import { formatRelativeTime } from '@/utils/formatDate';
import { ThinkingBlock, ToolCallCard, PlanStepList, ApprovalCard } from '@/components/AgentEvents';

import KnowledgePage from '@/pages/context/KnowledgePage';
import ToolPage from '@/pages/context/ToolPage';
import MemoryPage from '@/pages/context/MemoryPage';
import SkillPage from '@/pages/context/SkillPage';

type PanelTab = 'runs' | 'context' | 'tasks' | 'results';
type ContextTab = 'knowledge' | 'tool' | 'memory' | 'skill';

export default function WorkspaceConsole() {
  const [input, setInput] = useState('');
  const [panelTab, setPanelTab] = useState<PanelTab>('runs');
  const [contextTab, setContextTab] = useState<ContextTab>('knowledge');
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

  const { sendMessage, stopStreaming, approveToolCall } = useStreamingChat(
    currentWorkspaceId || '',
    currentWorkspaceAppId
  );

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingMessage, thinkingContent, activeToolCalls, planSteps, pendingApprovals]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!input.trim() || isStreaming || !currentWorkspaceId) {
      return;
    }

    const message = input.trim();
    setInput('');
    await sendMessage(message);
  };

  const renderContextContent = () => {
    switch (contextTab) {
      case 'knowledge':
        return <KnowledgePage />;
      case 'tool':
        return <ToolPage />;
      case 'memory':
        return <MemoryPage />;
      case 'skill':
        return <SkillPage />;
      default:
        return <KnowledgePage />;
    }
  };

  if (!currentWorkspaceId) {
    return (
      <div className="flex items-center justify-center h-full text-secondary-500 dark:text-secondary-400">
        Select a workspace to start chatting.
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-hidden flex flex-col lg:flex-row bg-navy-50 dark:bg-navy-950">
      <div className="flex flex-col flex-1 lg:border-r border-secondary-200 dark:border-navy-700">
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {isLoadingConversation ? (
            <div className="flex items-center justify-center h-full">
              <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary-500"></div>
            </div>
          ) : messages.length === 0 && !streamingMessage ? (
            <div className="text-center text-secondary-500 dark:text-secondary-400 mt-20">
              <p className="text-lg">Start a new conversation</p>
              <p className="text-sm mt-2">Send a message to create a new run</p>
            </div>
          ) : null}

          {messages.map((message) => (
            <div
              key={message.id}
              className={`flex gap-3 ${
                message.role === MessageRole.USER ? 'justify-end' : 'justify-start'
              }`}
            >
              {message.role === MessageRole.ASSISTANT && (
                <div className="w-8 h-8 rounded-full bg-primary-500 flex items-center justify-center text-white text-sm shrink-0 mt-0.5">
                  AI
                </div>
              )}

              {message.role === MessageRole.USER ? (
                <div className="max-w-2xl rounded-lg px-4 py-3 bg-primary-500 text-white">
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
                    <div className="rounded-lg px-4 py-3 bg-white dark:bg-navy-800 border border-secondary-200 dark:border-navy-700">
                      <div className="prose prose-sm dark:prose-invert max-w-none">
                        <ReactMarkdown>{message.content}</ReactMarkdown>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {message.role === MessageRole.USER && (
                <div className="w-8 h-8 rounded-full bg-secondary-500 flex items-center justify-center text-white text-sm shrink-0">
                  You
                </div>
              )}
            </div>
          ))}

          {isStreaming && (
            <div className="flex gap-3 justify-start">
              <div className="w-8 h-8 rounded-full bg-primary-500 flex items-center justify-center text-white text-sm shrink-0 mt-0.5">
                AI
              </div>
              <div className="max-w-2xl w-full space-y-2">
                {thinkingContent && (
                  <ThinkingBlock content={thinkingContent} />
                )}
                {activeToolCalls.length > 0 && (
                  <div className="space-y-1">
                    {activeToolCalls.map((tc) => (
                      <ToolCallCard key={tc.tool_id} toolCall={tc} />
                    ))}
                  </div>
                )}
                {planSteps.length > 0 && (
                  <PlanStepList steps={planSteps} />
                )}
                {/* HITL approval cards – rendered above the text bubble */}
                {pendingApprovals.map((pending) => (
                  <ApprovalCard
                    key={pending.tool_id}
                    pending={pending}
                    onApprove={approveToolCall}
                  />
                ))}
                {streamingMessage && (
                  <div className="rounded-lg px-4 py-3 bg-white dark:bg-navy-800 border border-secondary-200 dark:border-navy-700">
                    <div className="prose prose-sm dark:prose-invert max-w-none">
                      <ReactMarkdown>{streamingMessage}</ReactMarkdown>
                    </div>
                  </div>
                )}
                <div className="flex items-center gap-1 text-secondary-400 dark:text-secondary-500">
                  <span className={`w-2 h-2 rounded-full animate-pulse ${
                    pendingApprovals.length > 0 ? 'bg-amber-500' : 'bg-primary-500'
                  }`}></span>
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

        <div className="border-t border-secondary-200 dark:border-navy-700 bg-white dark:bg-navy-900 px-6 py-4">
          <form onSubmit={handleSubmit} className="flex gap-3">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Type a message..."
              disabled={isStreaming}
              className="flex-1 px-4 py-2 border border-secondary-200 dark:border-navy-600 rounded-lg
                         bg-white dark:bg-navy-800 text-navy-900 dark:text-navy-100
                         placeholder-secondary-400 dark:placeholder-secondary-500
                         focus:outline-none focus:ring-2 focus:ring-primary-500
                         disabled:bg-navy-100 dark:disabled:bg-navy-800"
            />
            {isStreaming ? (
              <button
                type="button"
                onClick={stopStreaming}
                className="px-6 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-red-500"
              >
                Stop
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                className="px-6 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600
                           focus:outline-none focus:ring-2 focus:ring-primary-500
                           disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Send
              </button>
            )}
          </form>
        </div>
      </div>

      <aside className="w-full lg:w-96 border-t lg:border-t-0 lg:border-l border-secondary-200 dark:border-navy-700 bg-white dark:bg-navy-900 flex flex-col">
        <div className="px-4 py-3 border-b border-secondary-200 dark:border-navy-700">
          <div className="flex flex-wrap gap-2">
            {(['runs', 'context', 'tasks', 'results'] as PanelTab[]).map((tab) => (
              <button
                key={tab}
                onClick={() => setPanelTab(tab)}
                className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                  panelTab === tab
                    ? 'bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-400'
                    : 'text-secondary-600 dark:text-secondary-400 hover:bg-navy-100 dark:hover:bg-navy-800'
                }`}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {panelTab === 'runs' && (
            <div>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold text-navy-900 dark:text-navy-100">Run History</h2>
                <button
                  onClick={startNewRun}
                  className="px-3 py-1.5 text-xs bg-primary-500 text-white rounded-md hover:bg-primary-600"
                >
                  New Run
                </button>
              </div>

              {runsLoading ? (
                <div className="flex items-center justify-center py-8">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500"></div>
                </div>
              ) : runsData?.items && runsData.items.length > 0 ? (
                <div className="space-y-2">
                  {runsData.items.map((run) => (
                    <button
                      key={run.id}
                      onClick={() => loadRun(run.id)}
                      className={`w-full text-left px-3 py-3 rounded-lg transition-colors border ${
                        currentRunId === run.id
                          ? 'bg-primary-50 dark:bg-primary-900/20 border-primary-200 dark:border-primary-800'
                          : 'border-transparent hover:bg-navy-100 dark:hover:bg-navy-800'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p
                          className={`text-sm font-medium truncate ${
                            currentRunId === run.id
                              ? 'text-primary-900 dark:text-primary-100'
                              : 'text-navy-900 dark:text-navy-100'
                          }`}
                        >
                          {run.input_data?.message || 'New run'}
                        </p>
                        <span className="text-xs text-secondary-400 dark:text-secondary-500">
                          {run.status}
                        </span>
                      </div>
                      <div className="text-xs text-secondary-400 dark:text-secondary-500 mt-1">
                        {formatRelativeTime(run.created_at)}
                      </div>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="text-center py-12 text-secondary-500 dark:text-secondary-400">
                  <p className="text-sm">No runs yet</p>
                  <p className="text-xs mt-1">Start a run to see history</p>
                </div>
              )}
            </div>
          )}

          {panelTab === 'context' && (
            <div>
              <div className="flex flex-wrap gap-2 mb-4">
                {(['knowledge', 'tool', 'memory', 'skill'] as ContextTab[]).map((tab) => (
                  <button
                    key={tab}
                    onClick={() => setContextTab(tab)}
                    className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                      contextTab === tab
                        ? 'bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-400'
                        : 'text-secondary-600 dark:text-secondary-400 hover:bg-navy-100 dark:hover:bg-navy-800'
                    }`}
                  >
                    {tab.charAt(0).toUpperCase() + tab.slice(1)}
                  </button>
                ))}
              </div>
              {renderContextContent()}
            </div>
          )}

          {panelTab === 'tasks' && (
            <div className="text-sm text-secondary-500 dark:text-secondary-400">
              <p className="font-medium text-navy-900 dark:text-navy-100 mb-2">Tasks</p>
              <p>No tasks yet. Start a run to see task progress here.</p>
            </div>
          )}

          {panelTab === 'results' && (
            <div className="text-sm text-secondary-500 dark:text-secondary-400">
              <p className="font-medium text-navy-900 dark:text-navy-100 mb-2">Results</p>
              <p>Run outputs will appear here as they complete.</p>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
