import { Plus, MessageSquare, Trash2, X } from 'lucide-react';
import { useEffect } from 'react';
import { useConversations, useDeleteConversation } from '@/hooks/useConversations';
import { useChatStore } from '@/stores/useChatStore';
import { useUIStore } from '@/stores/useUIStore';
import { formatRelativeTime } from '@/utils/formatDate';
import type { Conversation } from '@/types/conversation';

interface SidebarProps {
  appId: string;
  onNewChat: () => void;
}

export default function Sidebar({ appId, onNewChat }: SidebarProps) {
  const { sidebarOpen, toggleSidebar } = useUIStore();
  const { currentConversationId, loadConversation } = useChatStore();
  const { data: conversationsData, isLoading, error, refetch } = useConversations(appId, {
    page: 1,
    page_size: 50,
  });
  const deleteConversation = useDeleteConversation();

  // Debug logging
  console.log('🔍 Sidebar Debug:', {
    appId,
    sidebarOpen,
    isLoading,
    hasError: !!error,
    error,
    conversationsCount: conversationsData?.items?.length ?? 0,
    total: conversationsData?.total,
    hasData: !!conversationsData,
    items: conversationsData?.items
  });

  // Refetch conversations when current conversation changes (new one created)
  useEffect(() => {
    if (currentConversationId) {
      refetch();
    }
  }, [currentConversationId, refetch]);

  const handleSelectConversation = (conversation: Conversation) => {
    loadConversation(conversation.id);
  };

  const handleDeleteConversation = async (e: React.MouseEvent, conversationId: string) => {
    e.stopPropagation();
    if (confirm('确定要删除这个对话吗?')) {
      await deleteConversation.mutateAsync(conversationId);
      if (conversationId === currentConversationId) {
        onNewChat();
      }
    }
  };

  const handleNewChat = () => {
    onNewChat();
  };

  // Don't render if sidebar is closed
  if (!sidebarOpen) {
    return null;
  }

  return (
      <aside
        className="w-80 bg-white dark:bg-gray-800 border-r border-gray-200 dark:border-gray-700 flex flex-col shrink-0"
      >
        {/* Header */}
        <div className="p-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">对话历史</h2>
          <button
            onClick={toggleSidebar}
            className="p-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700"
            title="关闭侧边栏"
          >
            <X className="w-5 h-5 text-gray-500 dark:text-gray-400" />
          </button>
        </div>

        {/* New Chat Button */}
        <div className="p-4">
          <button
            onClick={handleNewChat}
            className="w-full flex items-center justify-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            <Plus className="w-4 h-4" />
            <span>新建对话</span>
          </button>
        </div>

        {/* Conversations List */}
        <div className="flex-1 overflow-y-auto px-2">
          {isLoading ? (
            <div className="flex items-center justify-center py-8">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
            </div>
          ) : conversationsData?.items && conversationsData.items.length > 0 ? (
            <div className="space-y-1">
              {conversationsData.items.map((conversation) => (
                <div
                  key={conversation.id}
                  onClick={() => handleSelectConversation(conversation)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === 'Enter' && handleSelectConversation(conversation)}
                  className={`w-full text-left px-3 py-3 rounded-lg transition-colors group cursor-pointer ${
                    currentConversationId === conversation.id
                      ? 'bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800'
                      : 'hover:bg-gray-100 dark:hover:bg-gray-700'
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <MessageSquare
                      className={`w-4 h-4 mt-0.5 shrink-0 ${
                        currentConversationId === conversation.id
                          ? 'text-blue-600 dark:text-blue-400'
                          : 'text-gray-400 dark:text-gray-500'
                      }`}
                    />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between gap-2">
                        <h3
                          className={`text-sm font-medium truncate ${
                            currentConversationId === conversation.id
                              ? 'text-blue-900 dark:text-blue-100'
                              : 'text-gray-900 dark:text-gray-100'
                          }`}
                        >
                          {conversation.name || '新对话'}
                        </h3>
                        <button
                          onClick={(e) => handleDeleteConversation(e, conversation.id)}
                          className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20 transition-opacity"
                          title="删除对话"
                        >
                          <Trash2 className="w-3.5 h-3.5 text-red-500" />
                        </button>
                      </div>
                      {conversation.summary && (
                        <p className="text-xs text-gray-500 dark:text-gray-400 truncate mt-1">
                          {conversation.summary}
                        </p>
                      )}
                      <div className="flex items-center gap-2 mt-1.5">
                        <span className="text-xs text-gray-400 dark:text-gray-500">
                          {formatRelativeTime(conversation.updated_at)}
                        </span>
                        {conversation.dialogue_count > 0 && (
                          <span className="text-xs text-gray-400 dark:text-gray-500">
                            · {conversation.dialogue_count} 条消息
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
              <MessageSquare className="w-12 h-12 text-gray-300 dark:text-gray-600 mb-3" />
              <p className="text-sm text-gray-500 dark:text-gray-400">暂无对话历史</p>
              <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                开始新的对话吧
              </p>
            </div>
          )}
        </div>
      </aside>
  );
}
