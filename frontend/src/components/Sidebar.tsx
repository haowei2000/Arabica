import { Plus, MessageSquare, X } from 'lucide-react';
import { useRuns } from '@/hooks/useRuns';
import { useChatStore } from '@/stores/useChatStore';
import { useUIStore } from '@/stores/useUIStore';
import { formatRelativeTime } from '@/utils/formatDate';

interface SidebarProps {
  workspaceId: string;
  onNewChat: () => void;
}

export default function Sidebar({ workspaceId, onNewChat }: SidebarProps) {
  const { sidebarOpen, toggleSidebar } = useUIStore();
  const { currentRunId, loadRun } = useChatStore();
  const { data: runsData, isLoading } = useRuns(workspaceId, {
    page: 1,
    page_size: 50,
  });

  const handleSelectRun = (runId: string) => {
    loadRun(runId);
  };

  if (!sidebarOpen) {
    return null;
  }

  return (
    <aside className="w-80 bg-white dark:bg-navy-900 border-r border-secondary-200 dark:border-navy-700 flex flex-col shrink-0">
      <div className="p-4 border-b border-secondary-200 dark:border-navy-700 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-navy-900 dark:text-navy-100">Run 历史</h2>
        <button
          onClick={toggleSidebar}
          className="p-1 rounded-lg hover:bg-navy-100 dark:hover:bg-navy-800"
          title="关闭侧边栏"
        >
          <X className="w-5 h-5 text-secondary-500 dark:text-secondary-400" />
        </button>
      </div>

      <div className="p-4">
        <button
          onClick={onNewChat}
          className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600 transition-colors"
        >
          <Plus className="w-4 h-4" />
          <span>新建 Run</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2">
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500"></div>
          </div>
        ) : runsData?.items && runsData.items.length > 0 ? (
          <div className="space-y-1">
            {runsData.items.map((run) => (
              <div
                key={run.id}
                onClick={() => handleSelectRun(run.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => e.key === 'Enter' && handleSelectRun(run.id)}
                className={`w-full text-left px-3 py-3 rounded-lg transition-colors group cursor-pointer ${
                  currentRunId === run.id
                    ? 'bg-primary-50 dark:bg-primary-900/20 border border-primary-200 dark:border-primary-800'
                    : 'hover:bg-navy-100 dark:hover:bg-navy-800'
                }`}
              >
                <div className="flex items-start gap-3">
                  <MessageSquare
                    className={`w-4 h-4 mt-0.5 shrink-0 ${
                      currentRunId === run.id
                        ? 'text-primary-500'
                        : 'text-secondary-400 dark:text-secondary-500'
                    }`}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2">
                      <h3
                        className={`text-sm font-medium truncate ${
                          currentRunId === run.id
                            ? 'text-primary-900 dark:text-primary-100'
                            : 'text-navy-900 dark:text-navy-100'
                        }`}
                      >
                        {run.input_data?.message || '新 Run'}
                      </h3>
                      <span className="text-xs text-secondary-400 dark:text-secondary-500">
                        {run.status}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 mt-1.5">
                      <span className="text-xs text-secondary-400 dark:text-secondary-500">
                        {formatRelativeTime(run.created_at)}
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
            <MessageSquare className="w-12 h-12 text-secondary-300 dark:text-secondary-600 mb-3" />
            <p className="text-sm text-secondary-500 dark:text-secondary-400">暂无 Run 历史</p>
            <p className="text-xs text-secondary-400 dark:text-secondary-500 mt-1">
              新建 Run 开始对话
            </p>
          </div>
        )}
      </div>
    </aside>
  );
}
