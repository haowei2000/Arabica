import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun, ArrowLeft, LogOut, Plus, History, X, Menu, Trash2 } from 'lucide-react';
import { authService } from '@/services/authService';
import { useUIStore } from '@/stores/useUIStore';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useAppStore } from '@/stores/useAppStore';
import { useApp } from '@/hooks/useApps';
import { useWorkspaces, useCreateWorkspace, useDeleteWorkspace } from '@/hooks/useWorkspaces';
import { Card, CardBody, Button, Input, Badge } from '@/components/ui';
import WorkspaceConsole from '@/components/WorkspaceConsole.v2';

export default function AppWorkspacePage() {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [workspaceName, setWorkspaceName] = useState('');
  const [workspaceDescription, setWorkspaceDescription] = useState('');
  const [sidebarOpen, setSidebarOpen] = useState(false); // Mobile sidebar

  const navigate = useNavigate();
  const { currentAppId, currentAppCode, clearCurrentApp } = useAppStore();
  const { data: appData } = useApp(currentAppId || '');
  const { data: workspacesData, isLoading: workspacesLoading } = useWorkspaces({
    page: 1,
    page_size: 50,
    app_id: currentAppId || undefined,
  });
  const createWorkspaceMutation = useCreateWorkspace();
  const deleteWorkspaceMutation = useDeleteWorkspace();
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

  const handleCreateWorkspace = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createWorkspaceMutation.mutateAsync({
        name: workspaceName,
        description: workspaceDescription || undefined,
        app_id: currentAppId || undefined,
      });
      setShowCreateForm(false);
      setWorkspaceName('');
      setWorkspaceDescription('');
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleDeleteWorkspace = async (e: React.MouseEvent, workspaceId: string, name: string) => {
    e.stopPropagation();
    if (!confirm(`Are you sure you want to delete workspace "${name}"?`)) return;
    try {
      await deleteWorkspaceMutation.mutateAsync(workspaceId);
      if (currentWorkspaceId === workspaceId) {
        resetChat();
        clearCurrentWorkspace();
      }
    } catch (error) {
      alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleOpenWorkspace = (
    workspaceId: string,
    name: string,
    appId?: string | null
  ) => {
    resetChat();
    setCurrentWorkspace(workspaceId, name, appId || null);
    setSidebarOpen(false); // Close sidebar on mobile
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
    <div className="min-h-screen flex flex-col">
      {/* Responsive Header */}
      <header className="glass border-b border-secondary-200/50 dark:border-navy-700/50 z-20">
        <div className="px-3 sm:px-4 md:px-6 py-3 md:py-4">
          <div className="flex items-center justify-between gap-2 sm:gap-4 max-w-7xl mx-auto">
            {/* Left Section */}
            <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
              {/* Mobile Sidebar Toggle */}
              <Button
                onClick={() => setSidebarOpen(!sidebarOpen)}
                variant="ghost"
                size="sm"
                icon={sidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
                className="lg:hidden"
              />

              <Button
                onClick={handleBackToHome}
                variant="ghost"
                size="sm"
                icon={<ArrowLeft className="w-4 h-4 sm:w-5 sm:h-5" />}
                className="shrink-0 hidden sm:inline-flex"
              >
                <span className="hidden md:inline">Back</span>
              </Button>

              <div className="min-w-0">
                <h1 className="text-sm sm:text-base md:text-lg font-bold text-navy-900 dark:text-navy-100 truncate">
                  {currentAppCode || 'App'} Workspace
                </h1>
                <p className="text-xs text-secondary-500 dark:text-secondary-400 truncate hidden sm:block">
                  {currentAppId}
                </p>
              </div>
            </div>

            {/* Right Section */}
            <div className="flex items-center gap-1 sm:gap-2 shrink-0">
              <Button
                onClick={toggleTheme}
                variant="ghost"
                size="sm"
                icon={theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
                className="hidden sm:inline-flex"
              />
              <Button
                onClick={handleLogout}
                variant="ghost"
                size="sm"
                icon={<LogOut className="w-4 h-4" />}
                className="hidden md:inline-flex"
              >
                <span className="hidden lg:inline">Logout</span>
              </Button>
            </div>
          </div>
        </div>
      </header>

      <div className="flex-1 flex flex-col lg:flex-row overflow-hidden relative">
        {/* Sidebar - Desktop and Mobile Overlay */}
        <aside
          className={`
            fixed lg:relative inset-y-0 left-0 z-30
            w-full sm:w-80 lg:w-96
            glass border-r border-secondary-200/50 dark:border-navy-700/50
            flex flex-col
            transition-transform duration-300 ease-in-out
            ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
          `}
        >
          {/* Sidebar Header */}
          <div className="px-4 py-4 border-b border-secondary-200/50 dark:border-navy-700/50">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-secondary-500 to-secondary-600 flex items-center justify-center shadow-lg shadow-secondary-500/30">
                  <History className="w-4 h-4 text-white" />
                </div>
                <div>
                  <h2 className="text-sm font-bold text-navy-900 dark:text-navy-100">
                    Workspaces
                  </h2>
                  <p className="text-xs text-secondary-500 dark:text-secondary-400">
                    Select or create
                  </p>
                </div>
              </div>
              <Button
                onClick={() => setSidebarOpen(false)}
                variant="ghost"
                size="sm"
                icon={<X className="w-4 h-4" />}
                className="lg:hidden"
              />
            </div>
          </div>

          {/* Create Button */}
          <div className="px-4 py-4">
            <Button
              onClick={() => setShowCreateForm(true)}
              variant="primary"
              size="md"
              icon={<Plus className="w-4 h-4" />}
              className="w-full"
            >
              Create Workspace
            </Button>
          </div>

          {/* Workspace List */}
          <div className="flex-1 overflow-y-auto px-4 pb-4">
            {workspacesLoading ? (
              <div className="flex items-center justify-center py-12">
                <div className="w-8 h-8 rounded-full border-4 border-primary-200 dark:border-primary-900/30 border-t-primary-500 animate-spin"></div>
              </div>
            ) : workspacesData?.items && workspacesData.items.length > 0 ? (
              <div className="space-y-2">
                {workspacesData.items.map((workspace, index) => (
                  <Card
                    key={workspace.id}
                    hover
                    onClick={() =>
                      handleOpenWorkspace(
                        workspace.id,
                        workspace.name,
                        workspace.app_id
                      )
                    }
                    className={currentWorkspaceId === workspace.id ? 'ring-2 ring-primary-500/50' : ''}
                    style={{ animationDelay: `${index * 30}ms` }}
                  >
                    <CardBody className="py-3 space-y-2">
                      <div className="flex items-start justify-between gap-2">
                        <p className="text-sm font-medium text-navy-900 dark:text-navy-100 line-clamp-2 flex-1">
                          {workspace.name}
                        </p>
                        <div className="flex items-center gap-1 shrink-0">
                          <Badge variant="neutral" size="sm">
                            {workspace.status}
                          </Badge>
                          <button
                            onClick={(e) => handleDeleteWorkspace(e, workspace.id, workspace.name)}
                            disabled={deleteWorkspaceMutation.isPending}
                            className="p-1 rounded-md text-secondary-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 transition-colors"
                            title="Delete workspace"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                      <div className="flex items-center gap-3 text-xs text-secondary-500 dark:text-secondary-400">
                        <span>Runs: {workspace.run_count}</span>
                      </div>
                    </CardBody>
                  </Card>
                ))}
              </div>
            ) : (
              <div className="text-center py-12 space-y-3 animate-fade-in">
                <div className="w-12 h-12 rounded-xl bg-secondary-100 dark:bg-secondary-900/30 flex items-center justify-center mx-auto">
                  <History className="w-6 h-6 text-secondary-400" />
                </div>
                <div>
                  <p className="text-sm font-medium text-navy-900 dark:text-navy-100">
                    No workspaces yet
                  </p>
                  <p className="text-xs text-secondary-500 dark:text-secondary-400 mt-1">
                    Create your first workspace
                  </p>
                </div>
              </div>
            )}
          </div>
        </aside>

        {/* Mobile Overlay */}
        {sidebarOpen && (
          <div
            className="fixed inset-0 bg-navy-950/60 backdrop-blur-sm z-20 lg:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}

        {/* Main Content */}
        <main className="flex-1 flex flex-col overflow-hidden">
          <WorkspaceConsole />
        </main>
      </div>

      {/* Create Workspace Modal */}
      {showCreateForm && (
        <div className="fixed inset-0 bg-navy-950/60 backdrop-blur-sm flex items-center justify-center z-50 p-4 animate-fade-in">
          <Card className="max-w-md w-full animate-scale-in">
            <div className="px-6 py-4 border-b border-secondary-100 dark:border-navy-700/60">
              <h3 className="text-xl font-bold text-navy-900 dark:text-navy-100">
                Create Workspace
              </h3>
              <p className="text-sm text-secondary-500 dark:text-secondary-400 mt-1">
                Set up a new workspace for collaboration
              </p>
            </div>

            <form onSubmit={handleCreateWorkspace}>
              <CardBody className="space-y-4">
                <Input
                  label="Workspace Name"
                  type="text"
                  value={workspaceName}
                  onChange={(e) => setWorkspaceName(e.target.value)}
                  placeholder="e.g., launch-ops"
                  required
                  helperText="Choose a unique name"
                />

                <div>
                  <label className="block text-sm font-medium text-secondary-700 dark:text-secondary-300 mb-2">
                    Description (optional)
                  </label>
                  <textarea
                    value={workspaceDescription}
                    onChange={(e) => setWorkspaceDescription(e.target.value)}
                    rows={3}
                    className="input-modern w-full resize-none"
                    placeholder="Describe this workspace..."
                  />
                </div>
              </CardBody>

              <div className="px-6 py-4 bg-secondary-50/50 dark:bg-navy-900/50 border-t border-secondary-100 dark:border-navy-700/60 flex gap-3 rounded-b-xl">
                <Button
                  type="button"
                  onClick={() => {
                    setShowCreateForm(false);
                    setWorkspaceName('');
                    setWorkspaceDescription('');
                  }}
                  variant="secondary"
                  className="flex-1"
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  loading={createWorkspaceMutation.isPending}
                  variant="primary"
                  className="flex-1"
                >
                  Create
                </Button>
              </div>
            </form>
          </Card>
        </div>
      )}
    </div>
  );
}
