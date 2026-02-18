import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { Loader2 } from 'lucide-react';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useRuns } from '@/hooks/useRuns';
import { useStreamingChat } from '@/hooks/useStreamingChat';
import { MessageRole } from '@/types/message';
import { formatRelativeTime } from '@/utils/formatDate';
import { ThinkingBlock, ToolCallCard, PlanStepList, ApprovalCard } from '@/components/AgentEvents';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';

import WorkspaceContextTree from '@/components/WorkspaceContextTree';
import { useWorkspaceStream } from '@/hooks/useWorkspaceStream';

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
            <div className="flex items-center justify-between mb-4">
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
                  <button
                    key={run.id}
                    type="button"
                    onClick={() => loadRun(run.id)}
                    className={cn(
                      'w-full text-left px-3 py-3 rounded-lg transition-colors border',
                      currentRunId === run.id
                        ? 'bg-primary/10 border-primary/30'
                        : 'border-transparent hover:bg-muted'
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className={cn('text-sm font-medium truncate', currentRunId === run.id ? 'text-primary' : 'text-foreground')}>
                        {run.input_data?.message || 'New run'}
                      </p>
                      <span className="text-xs text-muted-foreground shrink-0">{run.status}</span>
                    </div>
                    <div className="text-xs text-muted-foreground mt-1">{formatRelativeTime(run.created_at)}</div>
                  </button>
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
  );
}
