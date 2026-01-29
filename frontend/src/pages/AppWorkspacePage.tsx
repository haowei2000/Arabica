import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun } from 'lucide-react';
import { authService } from '@/services/authService';
import { useUIStore } from '@/stores/useUIStore';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useAppStore } from '@/stores/useAppStore';
import { useApp, useTemplates } from '@/hooks/useApps';
import { useWorkspaces, useCreateWorkspace } from '@/hooks/useWorkspaces';
import WorkspaceConsole from '@/components/WorkspaceConsole';

export default function AppWorkspacePage() {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [workspaceName, setWorkspaceName] = useState('');
  const [workspaceDescription, setWorkspaceDescription] = useState('');
  const [workspaceTemplateId, setWorkspaceTemplateId] = useState('');
  const [historyOpen, setHistoryOpen] = useState(true);

  const navigate = useNavigate();
  const { currentAppId, currentAppCode, clearCurrentApp } = useAppStore();
  const { data: appData } = useApp(currentAppId || '');
  const { data: workspacesData, isLoading: workspacesLoading } = useWorkspaces({
    page: 1,
    page_size: 50,
  });
  const { data: templatesData } = useTemplates();
  const createWorkspaceMutation = useCreateWorkspace();
  const { currentWorkspaceId, setCurrentWorkspace, clearCurrentWorkspace } = useWorkspaceStore();
  const { reset: resetChat } = useChatStore();
  const { toggleTheme, theme } = useUIStore();

  useEffect(() => {
    if (!authService.isAuthenticated()) {
      navigate('/login');
      return;
    }

    if (!currentAppId) {
      navigate('/home');
    }
  }, [navigate, currentAppId]);

  useEffect(() => {
    if (appData?.agent_template_id && !workspaceTemplateId) {
      setWorkspaceTemplateId(appData.agent_template_id);
    }
  }, [appData?.agent_template_id, workspaceTemplateId]);

  const handleCreateWorkspace = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createWorkspaceMutation.mutateAsync({
        name: workspaceName,
        description: workspaceDescription || undefined,
        agent_template_id: workspaceTemplateId || undefined,
      });
      setShowCreateForm(false);
      setWorkspaceName('');
      setWorkspaceDescription('');
      setWorkspaceTemplateId('');
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleOpenWorkspace = (
    workspaceId: string,
    name: string,
    agentTemplateId?: string | null
  ) => {
    resetChat();
    setCurrentWorkspace(workspaceId, name, agentTemplateId || null);
  };

  const handleBackToHome = () => {
    resetChat();
    clearCurrentWorkspace();
    clearCurrentApp();
    navigate('/home');
  };

  const handleLogout = () => {
    authService.logout();
    resetChat();
    clearCurrentWorkspace();
    clearCurrentApp();
    navigate('/login');
  };

  if (!currentAppId) {
    return null;
  }

  return (
    <div className="min-h-screen bg-navy-50 dark:bg-navy-950 flex flex-col">
      <header className="bg-white dark:bg-navy-900 border-b border-secondary-200 dark:border-navy-700 px-6 py-4">
        <div className="flex items-center justify-between max-w-6xl mx-auto">
          <div>
            <button
              onClick={handleBackToHome}
              className="text-sm text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100"
            >
              Back to Apps
            </button>
            <h1 className="text-xl font-semibold text-navy-900 dark:text-navy-100 mt-1">
              {currentAppCode || 'App'} Workspace
            </h1>
            {appData?.agent_template_id && (
              <p className="text-sm text-secondary-500 dark:text-secondary-400 mt-1">
                Default template: {appData.agent_template_id}
              </p>
            )}
          </div>
          <div className="flex items-center gap-4">
            <button
              onClick={toggleTheme}
              className="p-2 text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100 rounded-lg hover:bg-navy-100 dark:hover:bg-navy-800"
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
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

      <div className="flex-1 flex flex-col lg:flex-row">
        <aside
          className={`border-r border-secondary-200 dark:border-navy-700 bg-white dark:bg-navy-900 transition-all duration-200 ${
            historyOpen ? 'lg:w-80 w-full' : 'lg:w-0 w-full'
          }`}
        >
          <div className="h-full flex flex-col">
            <div className="px-4 py-4 border-b border-secondary-200 dark:border-navy-700 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold text-navy-900 dark:text-navy-100">
                  Workspace History
                </h2>
                <p className="text-xs text-secondary-500 dark:text-secondary-400 mt-1">
                  Select or create a workspace
                </p>
              </div>
              <button
                onClick={() => setHistoryOpen(false)}
                className="text-xs text-secondary-500 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100 lg:inline-flex hidden"
              >
                Hide
              </button>
            </div>

            <div className="px-4 py-4">
              <button
                onClick={() => setShowCreateForm(true)}
                className="w-full px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-500"
              >
                + Create Workspace
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-4 pb-4">
              {workspacesLoading ? (
                <div className="flex items-center justify-center py-12">
                  <div className="text-secondary-500 dark:text-secondary-400">Loading...</div>
                </div>
              ) : workspacesData?.items && workspacesData.items.length > 0 ? (
                <div className="space-y-2">
                  {workspacesData.items.map((workspace) => (
                    <button
                      key={workspace.id}
                      onClick={() =>
                        handleOpenWorkspace(
                          workspace.id,
                          workspace.name,
                          workspace.agent_template_id
                        )
                      }
                      className={`w-full text-left px-3 py-3 rounded-lg border transition-colors ${
                        currentWorkspaceId === workspace.id
                          ? 'bg-primary-50 dark:bg-primary-900/20 border-primary-200 dark:border-primary-800'
                          : 'border-transparent hover:bg-navy-100 dark:hover:bg-navy-800'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-sm font-medium text-navy-900 dark:text-navy-100 truncate">
                          {workspace.name}
                        </p>
                        <span className="text-xs text-secondary-400 dark:text-secondary-500">
                          {workspace.status}
                        </span>
                      </div>
                      <p className="text-xs text-secondary-500 dark:text-secondary-400 mt-1">
                        Runs: {workspace.run_count}
                      </p>
                    </button>
                  ))}
                </div>
              ) : (
                <div className="text-center py-12">
                  <p className="text-sm text-secondary-500 dark:text-secondary-400">
                    No workspaces yet
                  </p>
                  <p className="text-xs text-secondary-400 dark:text-secondary-500 mt-1">
                    Create your first workspace to start
                  </p>
                </div>
              )}
            </div>
          </div>
        </aside>

        {!historyOpen && (
          <div className="lg:hidden border-b border-secondary-200 dark:border-navy-700 bg-white dark:bg-navy-900 px-4 py-2">
            <button
              onClick={() => setHistoryOpen(true)}
              className="text-sm text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100"
            >
              Show workspace history
            </button>
          </div>
        )}

        <main className="flex-1 flex flex-col">
          {!historyOpen && (
            <div className="hidden lg:flex items-center justify-between px-4 py-2 border-b border-secondary-200 dark:border-navy-700 bg-white dark:bg-navy-900">
              <button
                onClick={() => setHistoryOpen(true)}
                className="text-sm text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100"
              >
                Show workspace history
              </button>
            </div>
          )}

          <WorkspaceConsole />
        </main>
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
                  placeholder="e.g., launch-ops"
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
                  Agent Template (optional)
                </label>
                <select
                  value={workspaceTemplateId}
                  onChange={(e) => setWorkspaceTemplateId(e.target.value)}
                  className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                             bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                             focus:outline-none focus:ring-2 focus:ring-primary-500"
                >
                  <option value="">No default template</option>
                  {templatesData?.map((template) => (
                    <option key={template.id} value={template.id}>
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
                    setWorkspaceName('');
                    setWorkspaceDescription('');
                    setWorkspaceTemplateId('');
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
    </div>
  );
}
