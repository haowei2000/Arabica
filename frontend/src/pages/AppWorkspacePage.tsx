import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun, Trash2, ArrowLeft, Plus, ChevronLeft, ChevronRight, Pencil } from 'lucide-react';
import { authService } from '@/services/authService';
import { useUIStore } from '@/stores/useUIStore';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useAppStore } from '@/stores/useAppStore';
import { useWorkspaces, useCreateWorkspace, useDeleteWorkspace } from '@/hooks/useWorkspaces';
import WorkspaceConsole from '@/components/WorkspaceConsole';
import WorkspaceCreateModal from '@/components/WorkspaceCreateModal';
import WorkspaceEditModal from '@/components/WorkspaceEditModal';
import type { Workspace } from '@/types/workspace';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import type { WorkspaceCreate } from '@/types/workspace';

export default function AppWorkspacePage() {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState<Workspace | null>(null);
  const [historyOpen, setHistoryOpen] = useState(true);

  const navigate = useNavigate();
  const { currentAppId, currentAppCode, clearCurrentApp } = useAppStore();
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
    if (!currentAppId) navigate('/home');
  }, [navigate, currentAppId]);

  const handleCreateWorkspace = async (data: WorkspaceCreate) => {
    try {
      await createWorkspaceMutation.mutateAsync(data);
      setShowCreateForm(false);
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

  const handleOpenWorkspace = (workspaceId: string, name: string, appId?: string | null) => {
    resetChat();
    setCurrentWorkspace(workspaceId, name, appId || null);
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

  if (!currentAppId) return null;

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Header */}
      <header className="bg-card border-b border-border px-6 py-3 shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="sm" onClick={handleBackToHome} className="gap-1.5">
              <ArrowLeft className="size-4" />
              Back to Apps
            </Button>
            <div className="h-4 w-px bg-border" />
            <div>
              <h1 className="text-base font-semibold">{currentAppCode || 'App'} Workspace</h1>
              {currentAppId && (
                <p className="text-xs text-muted-foreground">App ID: {currentAppId}</p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {theme === 'dark' ? <Sun className="size-5" /> : <Moon className="size-5" />}
            </Button>
            <Button variant="ghost" size="sm" onClick={handleLogout}>
              Logout
            </Button>
          </div>
        </div>
      </header>

      <div className="flex-1 flex flex-col lg:flex-row min-h-0">
        {/* Workspace Sidebar */}
        <aside
          className={cn(
            'border-r border-border bg-card flex flex-col transition-all duration-200 shrink-0',
            historyOpen ? 'lg:w-72 w-full' : 'lg:w-0 overflow-hidden w-full'
          )}
        >
          <div className="flex flex-col h-full">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between shrink-0">
              <div>
                <h2 className="text-sm font-semibold">Workspace History</h2>
                <p className="text-xs text-muted-foreground mt-0.5">Select or create a workspace</p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="size-7 hidden lg:flex"
                onClick={() => setHistoryOpen(false)}
                title="Collapse sidebar"
              >
                <ChevronLeft className="size-4" />
              </Button>
            </div>

            <div className="px-4 py-3 shrink-0">
              <Button className="w-full gap-2" onClick={() => setShowCreateForm(true)}>
                <Plus className="size-4" />
                Create Workspace
              </Button>
            </div>

            <ScrollArea className="flex-1 px-4 pb-4">
              {workspacesLoading ? (
                <div className="flex items-center justify-center py-12">
                  <p className="text-sm text-muted-foreground">Loading...</p>
                </div>
              ) : workspacesData?.items && workspacesData.items.length > 0 ? (
                <div className="space-y-1">
                  {workspacesData.items.map((workspace) => (
                    <div
                      key={workspace.id}
                      role="button"
                      tabIndex={0}
                      onClick={() => handleOpenWorkspace(workspace.id, workspace.name, workspace.app_id)}
                      onKeyDown={(e) => e.key === 'Enter' && handleOpenWorkspace(workspace.id, workspace.name, workspace.app_id)}
                      className={cn(
                        'w-full text-left px-3 py-3 rounded-lg border transition-colors cursor-pointer',
                        currentWorkspaceId === workspace.id
                          ? 'bg-primary/10 border-primary/30'
                          : 'border-transparent hover:bg-muted'
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <p className={cn('text-sm font-medium truncate', currentWorkspaceId === workspace.id ? 'text-primary' : 'text-foreground')}>
                          {workspace.name}
                        </p>
                        <div className="flex items-center gap-1 shrink-0">
                          <span className="text-xs text-muted-foreground">{workspace.status}</span>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-6 text-muted-foreground hover:text-foreground hover:bg-muted"
                            onClick={(e) => { e.stopPropagation(); setEditingWorkspace(workspace); }}
                            title="Edit workspace"
                          >
                            <Pencil className="size-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="size-6 text-muted-foreground hover:text-destructive hover:bg-destructive/10"
                            onClick={(e) => handleDeleteWorkspace(e, workspace.id, workspace.name)}
                            disabled={deleteWorkspaceMutation.isPending}
                            title="Delete workspace"
                          >
                            <Trash2 className="size-3.5" />
                          </Button>
                        </div>
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">Runs: {workspace.run_count}</p>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-12">
                  <p className="text-sm text-muted-foreground">No workspaces yet</p>
                  <p className="text-xs text-muted-foreground mt-1">Create your first workspace to start</p>
                </div>
              )}
            </ScrollArea>
          </div>
        </aside>

        {/* Collapse/expand toggle when closed */}
        {!historyOpen && (
          <div className="hidden lg:flex items-center justify-start px-2 py-3 border-b border-border bg-card shrink-0">
            <Button variant="ghost" size="icon" className="size-7" onClick={() => setHistoryOpen(true)} title="Expand sidebar">
              <ChevronRight className="size-4" />
            </Button>
          </div>
        )}

        {/* Mobile show/hide bar */}
        {!historyOpen && (
          <div className="lg:hidden border-b border-border bg-card px-4 py-2 shrink-0">
            <Button variant="ghost" size="sm" onClick={() => setHistoryOpen(true)}>
              Show workspace history
            </Button>
          </div>
        )}

        <main className="flex-1 flex flex-col min-h-0 min-w-0">
          <WorkspaceConsole />
        </main>
      </div>

      {showCreateForm && (
        <WorkspaceCreateModal
          appId={currentAppId || undefined}
          onConfirm={handleCreateWorkspace}
          onClose={() => setShowCreateForm(false)}
          isLoading={createWorkspaceMutation.isPending}
        />
      )}

      {editingWorkspace && (
        <WorkspaceEditModal
          workspace={editingWorkspace}
          onClose={() => setEditingWorkspace(null)}
        />
      )}
    </div>
  );
}
