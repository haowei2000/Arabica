import {useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {useApps, useCreateApp, useDeleteApp, useTemplates} from '@/hooks/useApps';
import {useAppStore} from '@/stores/useAppStore';
import {useChatStore} from '@/stores/useChatStore';
import {authService} from '@/services/authService';

export default function AppsPage() {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [appCode, setAppCode] = useState('');
  const [templateCode, setTemplateCode] = useState('DEFAULT001');

  const navigate = useNavigate();
  const { data: appsData, isLoading } = useApps();
  const { data: templates } = useTemplates();
  const createAppMutation = useCreateApp();
  const deleteAppMutation = useDeleteApp();
  const { setCurrentApp } = useAppStore();
  const { reset: resetChat } = useChatStore();

  const handleCreateApp = async (e: React.FormEvent) => {
    e.preventDefault();

    try {
      await createAppMutation.mutateAsync({
        app_code: appCode,
        agent_template_code: templateCode,
        enabled: true,
      });

      setShowCreateForm(false);
      setAppCode('');
      alert('App 创建成功！');
    } catch (error) {
      alert(`创建失败: ${error instanceof Error ? error.message : '未知错误'}`);
    }
  };

    const handleDeleteApp = async (appId: string, appCode: string) => {
    if (!confirm(`确定要删除 App "${appCode}" 吗？`)) return;

    try {
        await deleteAppMutation.mutateAsync(appId);
      alert('App 删除成功！');
    } catch (error) {
      alert(`删除失败: ${error instanceof Error ? error.message : '未知错误'}`);
    }
  };

  const handleStartChat = (appId: string, appCode: string) => {
    console.log('🚀 Starting chat with app:', { appId, appCode });
    resetChat();
    setCurrentApp(appId, appCode);

    // Use setTimeout to ensure state update completes before navigation
    setTimeout(() => {
      console.log('✅ App set in store, navigating to /chat');
      navigate('/chat');
    }, 0);
  };

  const handleLogout = () => {
    authService.logout();
    resetChat();
    navigate('/login');
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-500">加载中...</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 dark:bg-gray-900">
      {/* Header */}
      <header className="bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 px-6 py-4">
        <div className="flex items-center justify-between max-w-6xl mx-auto">
          <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">App 管理</h1>
          <button
            onClick={handleLogout}
            className="text-sm text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100"
          >
            退出登录
          </button>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Create Button */}
        <div className="mb-6 flex justify-between items-center">
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100">我的 Apps</h2>
          <button
            onClick={() => setShowCreateForm(true)}
            className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            + 创建 App
          </button>
        </div>

        {/* Create Form Modal */}
        {showCreateForm && (
          <div className="fixed inset-0 bg-black bg-opacity-50 dark:bg-opacity-70 flex items-center justify-center z-50">
            <div className="bg-white dark:bg-gray-800 rounded-lg p-6 max-w-md w-full mx-4">
              <h3 className="text-lg font-semibold mb-4 text-gray-900 dark:text-gray-100">创建新 App</h3>

              <form onSubmit={handleCreateApp} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    App Code *
                  </label>
                  <input
                    type="text"
                    value={appCode}
                    onChange={(e) => setAppCode(e.target.value)}
                    placeholder="例如: my-chat-app"
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 placeholder-gray-400 dark:placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500"
                    required
                  />
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                    唯一标识符，只能包含字母、数字、中划线
                  </p>
                </div>

                <div>
                  <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                    Agent 模板
                  </label>
                  <select
                    value={templateCode}
                    onChange={(e) => setTemplateCode(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="DEFAULT001">默认模板</option>
                    {templates?.map((template) => (
                      <option key={template.id} value={template.template_code}>
                        {template.template_name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="flex gap-3 pt-4">
                  <button
                    type="button"
                    onClick={() => {
                      setShowCreateForm(false);
                      setAppCode('');
                    }}
                    className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700"
                  >
                    取消
                  </button>
                  <button
                    type="submit"
                    disabled={createAppMutation.isPending}
                    className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50"
                  >
                    {createAppMutation.isPending ? '创建中...' : '创建'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {/* Apps Grid */}
        {appsData?.items && appsData.items.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {appsData.items.map((app) => (
              <div
                key={app.id}
                className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-6 hover:shadow-lg dark:hover:shadow-gray-900/50 transition-shadow"
              >
                <div className="flex items-start justify-between mb-4">
                  <div className="flex-1">
                    <h3 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-1">
                      {app.app_code}
                    </h3>
                    <span
                      className={`inline-block px-2 py-1 text-xs rounded ${
                        app.enabled
                          ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                          : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-400'
                      }`}
                    >
                      {app.enabled ? '已启用' : '已禁用'}
                    </span>
                  </div>
                </div>

                <div className="text-sm text-gray-500 dark:text-gray-400 mb-4 space-y-1">
                  <p>ID: {app.id}</p>
                  <p>版本: v{app.version}</p>
                  <p className="text-xs">
                    创建于: {new Date(app.created_at).toLocaleDateString()}
                  </p>
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() => handleStartChat(app.id, app.app_code)}
                    className="flex-1 px-4 py-2 bg-blue-600 text-white text-sm rounded-md hover:bg-blue-700"
                  >
                    开始对话
                  </button>
                  <button
                      onClick={() => handleDeleteApp(app.id, app.app_code)}
                    disabled={deleteAppMutation.isPending}
                    className="px-4 py-2 border border-red-300 dark:border-red-700 text-red-600 dark:text-red-400 text-sm rounded-md hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
                  >
                    删除
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-12">
            <div className="text-gray-400 dark:text-gray-600 mb-4">
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
                  d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"
                />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-gray-900 dark:text-gray-100 mb-1">
              还没有 App
            </h3>
            <p className="text-gray-500 dark:text-gray-400 mb-4">创建你的第一个 App 开始对话吧</p>
            <button
              onClick={() => setShowCreateForm(true)}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700"
            >
              创建 App
            </button>
          </div>
        )}

        {/* Pagination */}
        {appsData && appsData.total > appsData.page_size && (
          <div className="mt-8 flex justify-center">
            <div className="text-sm text-gray-500 dark:text-gray-400">
              显示 {appsData.items.length} / {appsData.total} 个 Apps
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
