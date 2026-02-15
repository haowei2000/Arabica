import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Send, StopCircle, Sparkles, User, History, Layers, CheckSquare, FileText, Plus, Trash2 } from 'lucide-react';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useRuns } from '@/hooks/useRuns';
import { useStreamingChat } from '@/hooks/useStreamingChat';
import { useWorkspaceContexts, useRemoveWorkspaceContext } from '@/hooks/useWorkspaces';
import { MessageRole } from '@/types/message';
import { formatRelativeTime } from '@/utils/formatDate';
import { ThinkingBlock, ToolCallCard, PlanStepList, ApprovalCard, ErrorMessage, ContextUsageCard, OutcomeCard } from '@/components/AgentEvents';
import { Card, CardBody, Button, Badge } from '@/components/ui';
import ContextSelectModal from '@/components/ContextSelectModal';

type PanelTab = 'runs' | 'context' | 'tasks' | 'results';

export default function WorkspaceConsole() {
  const [input, setInput] = useState('');
  const [panelTab, setPanelTab] = useState<PanelTab>('runs');
  const [showContextModal, setShowContextModal] = useState(false);
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
    contextUsages,
    outcomes,
    lastEventTimestamp,
    reconnecting,
    reconnectAttempt,
    startNewRun,
    loadRun,
  } = useChatStore();

  const { data: runsData, isLoading: runsLoading } = useRuns(currentWorkspaceId || '', {
    page: 1,
    page_size: 50,
  });

  const { sendMessage, stopStreaming, approveToolCall, streamError, retryLastMessage } = useStreamingChat(
    currentWorkspaceId || '',
    currentWorkspaceAppId
  );

  // Track seconds since last event for dynamic status text
  const [timeSinceLastEvent, setTimeSinceLastEvent] = useState(0);

  useEffect(() => {
    if (!isStreaming) {
      setTimeSinceLastEvent(0);
      return;
    }
    const interval = setInterval(() => {
      if (lastEventTimestamp) {
        setTimeSinceLastEvent(Math.floor((Date.now() - lastEventTimestamp) / 1000));
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [isStreaming, lastEventTimestamp]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingMessage, thinkingContent.length, activeToolCalls, planSteps, pendingApprovals, contextUsages, outcomes, streamError]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!input.trim() || isStreaming || !currentWorkspaceId) {
      return;
    }

    const message = input.trim();
    setInput('');
    await sendMessage(message);
  };

  const { data: wsContextsData, isLoading: wsContextsLoading } = useWorkspaceContexts(currentWorkspaceId || '');
  const removeMutation = useRemoveWorkspaceContext(currentWorkspaceId || '');

  const handleRemoveContext = async (contextId: string) => {
    try {
      await removeMutation.mutateAsync(contextId);
    } catch {
      // error available via removeMutation.error
    }
  };

  if (!currentWorkspaceId) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center space-y-4 animate-fade-in">
          <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-secondary-100 to-secondary-200 dark:from-secondary-900/30 dark:to-secondary-800/30 flex items-center justify-center mx-auto">
            <Layers className="w-8 h-8 text-secondary-400" />
          </div>
          <p className="text-secondary-500 dark:text-secondary-400">
            Select a workspace to start chatting
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-hidden flex flex-col lg:flex-row">
      {/* Main Chat Area */}
      <div className="flex flex-col flex-1 lg:border-r border-secondary-200 dark:border-navy-700/60">
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
          {isLoadingConversation ? (
            <div className="flex items-center justify-center h-full">
              <div className="relative">
                <div className="w-12 h-12 rounded-full border-4 border-primary-200 dark:border-primary-900/30 border-t-primary-500 animate-spin"></div>
              </div>
            </div>
          ) : messages.length === 0 && !streamingMessage ? (
            <div className="flex flex-col items-center justify-center h-full text-center space-y-6 animate-fade-in">
              <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-2xl shadow-primary-500/30">
                <Sparkles className="w-10 h-10 text-white" />
              </div>
              <div>
                <h2 className="text-2xl font-bold text-navy-900 dark:text-navy-100 mb-2">
                  Start a Conversation
                </h2>
                <p className="text-secondary-500 dark:text-secondary-400 max-w-md">
                  Ask anything, and your AI agent will help you with intelligent responses
                </p>
              </div>
            </div>
          ) : null}

          {messages.map((message, index) => (
            <div
              key={message.id}
              className={`flex gap-4 animate-slide-in ${
                message.role === MessageRole.USER ? 'justify-end' : 'justify-start'
              }`}
              style={{ animationDelay: `${index * 50}ms` }}
            >
              {message.role === MessageRole.ASSISTANT && (
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center text-white font-bold shadow-lg shadow-primary-500/30 shrink-0">
                  <Sparkles className="w-5 h-5" />
                </div>
              )}

              {message.role === MessageRole.USER ? (
                <div className="max-w-2xl">
                  <Card className="bg-gradient-to-br from-primary-500 to-primary-600 border-none shadow-xl shadow-primary-500/20">
                    <CardBody className="py-3">
                      <p className="text-sm text-white">{message.content}</p>
                    </CardBody>
                  </Card>
                </div>
              ) : (
                <div className="max-w-3xl w-full space-y-3">
                  {message.thinkingContent && message.thinkingContent.length > 0 && (
                    message.thinkingContent.map((tc, i) => (
                      <ThinkingBlock key={i} content={tc} defaultCollapsed />
                    ))
                  )}
                  {message.toolCalls && message.toolCalls.length > 0 && (
                    <div className="space-y-2">
                      {message.toolCalls.map((tc) => (
                        <ToolCallCard key={tc.tool_id} toolCall={tc} />
                      ))}
                    </div>
                  )}
                  {message.planSteps && message.planSteps.length > 0 && (
                    <PlanStepList steps={message.planSteps} />
                  )}
                  {message.contextUsages && message.contextUsages.length > 0 && (
                    <div className="space-y-2">
                      {message.contextUsages.map((cu, i) => (
                        <ContextUsageCard key={i} usage={cu} />
                      ))}
                    </div>
                  )}
                  {message.outcomes && message.outcomes.length > 0 && (
                    <div className="space-y-2">
                      {message.outcomes.map((o, i) => (
                        <OutcomeCard key={i} outcome={o} />
                      ))}
                    </div>
                  )}
                  {message.content && (
                    <Card>
                      <CardBody>
                        <div className="prose prose-sm dark:prose-invert max-w-none">
                          <ReactMarkdown>{message.content}</ReactMarkdown>
                        </div>
                      </CardBody>
                    </Card>
                  )}
                </div>
              )}

              {message.role === MessageRole.USER && (
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-secondary-500 to-secondary-600 flex items-center justify-center text-white font-bold shadow-lg shadow-secondary-500/20 shrink-0">
                  <User className="w-5 h-5" />
                </div>
              )}
            </div>
          ))}

          {isStreaming && (
            <div className="flex gap-4 justify-start animate-fade-in">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center text-white font-bold shadow-lg shadow-primary-500/30 shrink-0 relative">
                <Sparkles className="w-5 h-5" />
                <span className="absolute -top-1 -right-1 w-3 h-3 bg-emerald-500 rounded-full animate-pulse"></span>
              </div>
              <div className="max-w-3xl w-full space-y-3">
                {thinkingContent.length > 0 && thinkingContent.map((tc, i) => (
                  <ThinkingBlock key={i} content={tc} defaultCollapsed={i < thinkingContent.length - 1} />
                ))}
                {activeToolCalls.length > 0 && (
                  <div className="space-y-2">
                    {activeToolCalls.map((tc) => (
                      <ToolCallCard key={tc.tool_id} toolCall={tc} />
                    ))}
                  </div>
                )}
                {planSteps.length > 0 && <PlanStepList steps={planSteps} />}
                {contextUsages.length > 0 && (
                  <div className="space-y-2">
                    {contextUsages.map((cu, i) => (
                      <ContextUsageCard key={i} usage={cu} />
                    ))}
                  </div>
                )}
                {outcomes.length > 0 && (
                  <div className="space-y-2">
                    {outcomes.map((o, i) => (
                      <OutcomeCard key={i} outcome={o} />
                    ))}
                  </div>
                )}
                {pendingApprovals.map((pending) => (
                  <ApprovalCard
                    key={pending.tool_id}
                    pending={pending}
                    onApprove={approveToolCall}
                  />
                ))}
                {streamingMessage && (
                  <Card>
                    <CardBody>
                      <div className="prose prose-sm dark:prose-invert max-w-none">
                        <ReactMarkdown>{streamingMessage}</ReactMarkdown>
                      </div>
                    </CardBody>
                  </Card>
                )}
                <div className="flex items-center gap-2">
                  <div className="flex gap-1">
                    <span className="w-2 h-2 rounded-full bg-primary-500 animate-bounce"></span>
                    <span className="w-2 h-2 rounded-full bg-primary-500 animate-bounce" style={{ animationDelay: '0.1s' }}></span>
                    <span className="w-2 h-2 rounded-full bg-primary-500 animate-bounce" style={{ animationDelay: '0.2s' }}></span>
                  </div>
                  <span className={`text-xs ${timeSinceLastEvent > 180 ? 'text-orange-500 dark:text-orange-400' : 'text-secondary-500 dark:text-secondary-400'}`}>
                    {reconnecting
                      ? `Reconnecting... (attempt ${reconnectAttempt})`
                      : pendingApprovals.length > 0
                        ? 'Waiting for approval...'
                        : activeToolCalls.some((tc) => tc.status === 'pending')
                          ? 'Executing tools...'
                          : timeSinceLastEvent > 180
                            ? 'This is taking unusually long...'
                            : timeSinceLastEvent > 60
                              ? 'Taking longer than expected...'
                              : timeSinceLastEvent > 30
                                ? 'Still working...'
                                : 'Thinking...'}
                  </span>
                </div>
              </div>
            </div>
          )}

          {streamError && (
            <div className="flex gap-4 justify-start animate-fade-in">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-red-500 to-red-600 flex items-center justify-center text-white font-bold shadow-lg shadow-red-500/30 shrink-0">
                <Sparkles className="w-5 h-5" />
              </div>
              <div className="max-w-3xl w-full">
                <ErrorMessage error={streamError} onRetry={streamError.retryable ? retryLastMessage : undefined} />
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Modern Input Area */}
        <div className="glass border-t border-secondary-200/50 dark:border-navy-700/50 px-6 py-4">
          <form onSubmit={handleSubmit} className="flex gap-3">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={isStreaming ? "AI is responding..." : "Type your message..."}
              disabled={isStreaming}
              className="input-modern flex-1"
            />
            {isStreaming ? (
              <Button
                type="button"
                onClick={stopStreaming}
                variant="danger"
                size="md"
                icon={<StopCircle className="w-5 h-5" />}
              >
                Stop
              </Button>
            ) : (
              <Button
                type="submit"
                disabled={!input.trim()}
                variant="primary"
                size="md"
                icon={<Send className="w-5 h-5" />}
              >
                Send
              </Button>
            )}
          </form>
        </div>
      </div>

      {/* Modern Sidebar */}
      <aside className="w-full lg:w-96 glass border-t lg:border-t-0 lg:border-l border-secondary-200/50 dark:border-navy-700/50 flex flex-col">
        <div className="px-4 py-3 border-b border-secondary-200/50 dark:border-navy-700/50">
          <div className="flex gap-1.5 p-1 rounded-lg bg-secondary-100/50 dark:bg-navy-800/50">
            {[
              { id: 'runs', icon: History, label: 'Runs' },
              { id: 'context', icon: Layers, label: 'Context' },
              { id: 'tasks', icon: CheckSquare, label: 'Tasks' },
              { id: 'results', icon: FileText, label: 'Results' },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setPanelTab(tab.id as PanelTab)}
                className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-md text-xs font-medium transition-all duration-200 ${
                  panelTab === tab.id
                    ? 'bg-white dark:bg-navy-700 text-primary-600 dark:text-primary-400 shadow-sm'
                    : 'text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100'
                }`}
              >
                <tab.icon className="w-4 h-4" />
                <span className="hidden sm:inline">{tab.label}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {panelTab === 'runs' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-bold text-navy-900 dark:text-navy-100">Run History</h2>
                <Button
                  onClick={startNewRun}
                  variant="primary"
                  size="sm"
                >
                  New
                </Button>
              </div>

              {runsLoading ? (
                <div className="flex items-center justify-center py-12">
                  <div className="w-8 h-8 rounded-full border-4 border-primary-200 dark:border-primary-900/30 border-t-primary-500 animate-spin"></div>
                </div>
              ) : runsData?.items && runsData.items.length > 0 ? (
                <div className="space-y-2">
                  {runsData.items.map((run, index) => (
                    <Card
                      key={run.id}
                      hover
                      onClick={() => loadRun(run.id)}
                      className={currentRunId === run.id ? 'ring-2 ring-primary-500/50' : ''}
                      style={{ animationDelay: `${index * 30}ms` }}
                    >
                      <CardBody className="py-3 space-y-2">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-sm font-medium text-navy-900 dark:text-navy-100 line-clamp-2 flex-1">
                            {run.input_data?.message || 'New run'}
                          </p>
                          <Badge variant="neutral" size="sm">
                            {run.status}
                          </Badge>
                        </div>
                        <p className="text-xs text-secondary-400 dark:text-secondary-500">
                          {formatRelativeTime(run.created_at)}
                        </p>
                      </CardBody>
                    </Card>
                  ))}
                </div>
              ) : (
                <div className="text-center py-12 space-y-3">
                  <div className="w-12 h-12 rounded-xl bg-secondary-100 dark:bg-secondary-900/30 flex items-center justify-center mx-auto">
                    <History className="w-6 h-6 text-secondary-400" />
                  </div>
                  <div>
                    <p className="text-sm font-medium text-navy-900 dark:text-navy-100">No runs yet</p>
                    <p className="text-xs text-secondary-500 dark:text-secondary-400 mt-1">
                      Start a conversation to create your first run
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}

          {panelTab === 'context' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-bold text-navy-900 dark:text-navy-100">Workspace Context</h2>
                <Button
                  onClick={() => setShowContextModal(true)}
                  variant="primary"
                  size="sm"
                  icon={<Plus className="w-4 h-4" />}
                >
                  Add Context
                </Button>
              </div>

              {wsContextsLoading ? (
                <div className="flex items-center justify-center py-12">
                  <div className="w-8 h-8 rounded-full border-4 border-primary-200 dark:border-primary-900/30 border-t-primary-500 animate-spin" />
                </div>
              ) : wsContextsData?.items && wsContextsData.items.length > 0 ? (
                <div className="space-y-2">
                  {wsContextsData.items.map((ctx) => (
                    <Card key={ctx.id}>
                      <CardBody className="py-3 space-y-2">
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-navy-900 dark:text-navy-100 truncate">
                              {ctx.name}
                            </p>
                            <div className="flex items-center gap-2 mt-1">
                              {ctx.content_type && (
                                <Badge variant="info" size="sm">{ctx.content_type}</Badge>
                              )}
                              {ctx.path && (
                                <span className="text-xs text-secondary-400 dark:text-secondary-500 truncate">
                                  {ctx.path}
                                </span>
                              )}
                            </div>
                          </div>
                          <button
                            onClick={() => handleRemoveContext(ctx.id)}
                            disabled={removeMutation.isPending}
                            className="p-1 rounded-md text-secondary-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                        <p className="text-xs text-secondary-400 dark:text-secondary-500">
                          {formatRelativeTime(ctx.created_at)}
                          {ctx.size_bytes != null && ` \u00b7 ${(ctx.size_bytes / 1024).toFixed(1)} KB`}
                        </p>
                      </CardBody>
                    </Card>
                  ))}
                </div>
              ) : (
                <div className="text-center py-12 space-y-3">
                  <div className="w-12 h-12 rounded-xl bg-secondary-100 dark:bg-secondary-900/30 flex items-center justify-center mx-auto">
                    <Layers className="w-6 h-6 text-secondary-400" />
                  </div>
                  <div>
                    <p className="text-sm font-medium text-navy-900 dark:text-navy-100">No context added</p>
                    <p className="text-xs text-secondary-500 dark:text-secondary-400 mt-1">
                      Add context from your library to use in this workspace
                    </p>
                  </div>
                </div>
              )}

              {showContextModal && currentWorkspaceId && (
                <ContextSelectModal
                  workspaceId={currentWorkspaceId}
                  onClose={() => setShowContextModal(false)}
                />
              )}
            </div>
          )}

          {panelTab === 'tasks' && (
            <div className="text-center py-12 space-y-3">
              <div className="w-12 h-12 rounded-xl bg-secondary-100 dark:bg-secondary-900/30 flex items-center justify-center mx-auto">
                <CheckSquare className="w-6 h-6 text-secondary-400" />
              </div>
              <div>
                <p className="text-sm font-medium text-navy-900 dark:text-navy-100">No tasks yet</p>
                <p className="text-xs text-secondary-500 dark:text-secondary-400 mt-1">
                  Task progress will appear here
                </p>
              </div>
            </div>
          )}

          {panelTab === 'results' && (
            <div className="text-center py-12 space-y-3">
              <div className="w-12 h-12 rounded-xl bg-secondary-100 dark:bg-secondary-900/30 flex items-center justify-center mx-auto">
                <FileText className="w-6 h-6 text-secondary-400" />
              </div>
              <div>
                <p className="text-sm font-medium text-navy-900 dark:text-navy-100">No results yet</p>
                <p className="text-xs text-secondary-500 dark:text-secondary-400 mt-1">
                  Run outputs will appear here
                </p>
              </div>
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
