import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun } from 'lucide-react';
import { authService } from '@/services/authService';
import { useChatStore } from '@/stores/useChatStore';
import { useUIStore } from '@/stores/useUIStore';
import { useApps } from '@/hooks/useApps';
import { useWorkspaces, useCreateWorkspace } from '@/hooks/useWorkspaces';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';

import KnowledgePage from './context/KnowledgePage';
import ToolPage from './context/ToolPage';
import MemoryPage from './context/MemoryPage';
import SkillPage from './context/SkillPage';

type MainTab = 'workspace' | 'context';
type ContextTab = 'knowledge' | 'tool' | 'memory' | 'skill';

export default function HomePage() {
  const [mainTab, setMainTab] = useState<MainTab>('workspace');
  const [contextTab, setContextTab] = useState<ContextTab>('knowledge');
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [workspaceName, setWorkspaceName] = useState('');
  const [workspaceDescription, setWorkspaceDescription] = useState('');
  const [workspaceAppId, setWorkspaceAppId] = useState('');

  const navigate = useNavigate();
  const { data: workspacesData, isLoading: workspacesLoading } = useWorkspaces({
    page: 1,
    page_size: 50,
  });
  const { data: appsData } = useApps({ page: 1, page_size: 100 });
  const createWorkspaceMutation = useCreateWorkspace();
  const { setCurrentWorkspace } = useWorkspaceStore();
  const { reset: resetChat } = useChatStore();
  const { toggleTheme, theme } = useUIStore();

  const handleCreateWorkspace = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createWorkspaceMutation.mutateAsync({
        name: workspaceName,
        description: workspaceDescription || undefined,
        app_id: workspaceAppId || undefined,
      });
      setShowCreateForm(false);
      setWorkspaceName('');
      setWorkspaceDescription('');
      setWorkspaceAppId('');
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleOpenWorkspace = (workspaceId: string, name: string, appId?: string | null) => {
    resetChat();
    setCurrentWorkspace(workspaceId, name, appId || null);
    navigate('/chat');
  };

  const handleLogout = () => {
    authService.logout();
    resetChat();
    navigate('/login');
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

  const renderWorkspaceContent = () => {
    if (workspacesLoading) {
      return (
        <div className="flex items-center justify-center py-12">
          <div className="text-secondary-500 dark:text-secondary-400">Loading...</div>
        </div>
      );
    }

    return (
      <div>
        <div className="mb-6 flex justify-between items-center">
          <div>
            <h2 className="text-xl font-bold text-navy-900 dark:text-navy-100">My Workspaces</h2>
            <p className="text-sm text-secondary-500 dark:text-secondary-400 mt-1">
              Manage runs and conversations in workspaces
            </p>
          </div>
          <button
            onClick={() => setShowCreateForm(true)}
            className="px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-500"
          >
            + Create Workspace
          </button>
        </div>

        {showCreateForm && (
          <div className="fixed inset-0 bg-black bg-opacity-50 dark:bg-opacity-70 flex items-center justify-center z-50">
            <div className="bg-white dark:bg-navy-800 rounded-lg p-6 max-w-md w-full mx-4 border border-secondary-200 dark:border-navy-700">
              <h3 className="text-lg font-semibold mb-4 text-navy-900 dark:text-navy-100">
                Create New Workspace
              </h3>

              <form onSubmit={handleCreateWorkspace} className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                    Workspace Name *
                  </label>
                  <input
                    type="text"
                    value={workspaceName}
                    onChange={(e) => setWorkspaceName(e.target.value)}
                    placeholder="e.g., team-ai"
                    className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                               bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                               placeholder-secondary-400 dark:placeholder-secondary-500
                               focus:outline-none focus:ring-2 focus:ring-primary-500"
                    required
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                    Description
                  </label>
                  <textarea
                    value={workspaceDescription}
                    onChange={(e) => setWorkspaceDescription(e.target.value)}
                    rows={3}
                    className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                               bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                               placeholder-secondary-400 dark:placeholder-secondary-500
                               focus:outline-none focus:ring-2 focus:ring-primary-500"
                  />
                </div>

                <div>
                  <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                    Default App (optional)
                  </label>
                  <select
                    value={workspaceAppId}
                    onChange={(e) => setWorkspaceAppId(e.target.value)}
                    className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                               bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                               focus:outline-none focus:ring-2 focus:ring-primary-500"
                  >
                    <option value="">No default app</option>
                    {appsData?.items?.map((app) => (
                      <option key={app.id} value={app.id}>
                        {app.app_code}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="flex gap-3 pt-4">
                  <button
                    type="button"
                    onClick={() => {
                      setShowCreateForm(false);
                      setWorkspaceName('');
                      setWorkspaceDescription('');
                      setWorkspaceAppId('');
                    }}
                    className="flex-1 px-4 py-2 border border-secondary-200 dark:border-navy-600
                               text-secondary-600 dark:text-secondary-300 rounded-md
                               hover:bg-navy-50 dark:hover:bg-navy-700"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={createWorkspaceMutation.isPending}
                    className="flex-1 px-4 py-2 bg-primary-500 text-white rounded-md
                               hover:bg-primary-600 disabled:opacity-50"
                  >
                    {createWorkspaceMutation.isPending ? 'Creating...' : 'Create'}
                  </button>
                </div>
              </form>
            </div>
          </div>
        )}

        {workspacesData?.items && workspacesData.items.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {workspacesData.items.map((workspace) => (
              <div
                key={workspace.id}
                className="bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700
                           p-6 hover:shadow-lg dark:hover:shadow-navy-900/50 transition-shadow"
              >
                <div className="flex items-start justify-between mb-4">
                  <div className="flex-1">
                    <h3 className="text-lg font-semibold text-navy-900 dark:text-navy-100 mb-1">
                      {workspace.name}
                    </h3>
                    <span className="inline-block px-2 py-1 text-xs rounded bg-navy-100 dark:bg-navy-700 text-navy-600 dark:text-navy-300">
                      {workspace.status}
                    </span>
                  </div>
                </div>

                {workspace.description && (
                  <p className="text-sm text-secondary-500 dark:text-secondary-400 mb-3">
                    {workspace.description}
                  </p>
                )}

                <div className="text-sm text-secondary-500 dark:text-secondary-400 mb-4 space-y-1">
                  <p>Runs: {workspace.run_count}</p>
                  <p>Members: {workspace.member_count}</p>
                  <p className="text-xs">
                    Created: {new Date(workspace.created_at).toLocaleDateString()}
                  </p>
                </div>

                <div className="flex gap-2">
                  <button
                    onClick={() =>
                      handleOpenWorkspace(workspace.id, workspace.name, workspace.app_id)
                    }
                    className="flex-1 px-4 py-2 bg-primary-500 text-white text-sm rounded-md hover:bg-primary-600"
                  >
                    Open
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-12">
            <div className="text-secondary-400 dark:text-secondary-600 mb-4">
              <svg className="mx-auto h-12 w-12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"
                />
              </svg>
            </div>
            <h3 className="text-lg font-medium text-navy-900 dark:text-navy-100 mb-1">
              No Workspaces Yet
            </h3>
            <p className="text-secondary-500 dark:text-secondary-400 mb-4">
              Create your first workspace to get started
            </p>
            <button
              onClick={() => setShowCreateForm(true)}
              className="px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600"
            >
              Create Workspace
            </button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-navy-50 dark:bg-navy-950">
      <header className="bg-white dark:bg-navy-900 border-b border-secondary-200 dark:border-navy-700 px-6 py-4">
        <div className="flex items-center justify-between max-w-6xl mx-auto">
          <h1 className="text-xl font-semibold text-navy-900 dark:text-navy-100">
            AI Agent Platform
          </h1>
          <div className="flex items-center gap-4">
            <button
              onClick={toggleTheme}
              className="p-2 text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100 rounded-lg hover:bg-navy-100 dark:hover:bg-navy-800"
              title={theme === 'dark' ? '切换到亮色模式' : '切换到暗色模式'}
            >
              {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
            </button>
            <button
              onClick={handleLogout}
              className="text-sm text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100"
            >
              Logout
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-6 py-8">
        <div className="mb-6">
          <div className="border-b border-secondary-200 dark:border-navy-700">
            <nav className="-mb-px flex space-x-8">
              <button
                onClick={() => setMainTab('workspace')}
                className={`py-4 px-1 border-b-2 font-medium text-sm ${
                  mainTab === 'workspace'
                    ? 'border-primary-500 text-primary-500'
                    : 'border-transparent text-secondary-500 dark:text-secondary-400 hover:text-secondary-700 dark:hover:text-secondary-300 hover:border-secondary-300'
                }`}
              >
                Workspaces
              </button>
              <button
                onClick={() => setMainTab('context')}
                className={`py-4 px-1 border-b-2 font-medium text-sm ${
                  mainTab === 'context'
                    ? 'border-primary-500 text-primary-500'
                    : 'border-transparent text-secondary-500 dark:text-secondary-400 hover:text-secondary-700 dark:hover:text-secondary-300 hover:border-secondary-300'
                }`}
              >
                Context
              </button>
            </nav>
          </div>
        </div>

        {mainTab === 'context' && (
          <div className="mb-6">
            <div className="flex space-x-4">
              {(['knowledge', 'tool', 'memory', 'skill'] as ContextTab[]).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setContextTab(tab)}
                  className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                    contextTab === tab
                      ? 'bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-400'
                      : 'text-secondary-600 dark:text-secondary-400 hover:bg-navy-100 dark:hover:bg-navy-800'
                  }`}
                >
                  {tab.charAt(0).toUpperCase() + tab.slice(1)}
                </button>
              ))}
            </div>
          </div>
        )}

        {mainTab === 'workspace' ? renderWorkspaceContent() : renderContextContent()}
      </div>
    </div>
  );
}
