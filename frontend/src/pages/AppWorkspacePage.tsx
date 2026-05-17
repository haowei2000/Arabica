import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { authService } from '@/services/authService';
import { useUIStore } from '@/stores/useUIStore';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useAppStore } from '@/stores/useAppStore';
import { useWorkspaces, useCreateWorkspace, useDeleteWorkspace } from '@/hooks/useWorkspaces';
import WorkspaceConsole from '@/components/WorkspaceConsole';
import WorkspaceCreateModal from '@/components/WorkspaceCreateModal';
import WorkspaceEditModal from '@/components/WorkspaceEditModal';
import QuotaMeter from '@/components/QuotaMeter';
import type { Workspace } from '@/types/workspace';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import type { WorkspaceCreate } from '@/types/workspace';
import { APP_ICONS } from '@/constants/icons';

const STATUS_DOT: Record<string, string> = {
  active:   'bg-green-500',
  inactive: 'bg-muted-foreground/30',
  running:  'bg-yellow-400 animate-pulse',
  error:    'bg-red-500',
};

const BrandIcon = APP_ICONS.brand;
const CollapseIcon = APP_ICONS.collapse;
const DeleteIcon = APP_ICONS.delete;
const EditIcon = APP_ICONS.edit;
const ExpandIcon = APP_ICONS.expand;
const HomeIcon = APP_ICONS.home;
const LogoutIcon = APP_ICONS.logout;
const NewIcon = APP_ICONS.new;
const RunIcon = APP_ICONS.runs;
const ThemeDarkIcon = APP_ICONS.themeDark;
const ThemeLightIcon = APP_ICONS.themeLight;
const WorkspaceIcon = APP_ICONS.workspace;

export default function AppWorkspacePage() {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState<Workspace | null>(null);
  const [historyOpen, setHistoryOpen] = useState(true);
  const [runCountOverrides, setRunCountOverrides] = useState<Record<string, number>>({});

  const navigate = useNavigate();
  const { clearCurrentApp } = useAppStore();
  // Show all workspaces; optionally filtered by app when navigated from an app
  const { data: workspacesData, isLoading: workspacesLoading } = useWorkspaces({
    page: 1,
    page_size: 50,
  });
  const createWorkspaceMutation = useCreateWorkspace();
  const deleteWorkspaceMutation = useDeleteWorkspace();
  const { currentWorkspaceId, setCurrentWorkspace, clearCurrentWorkspace } = useWorkspaceStore();
  const { reset: resetChat } = useChatStore();
  const { toggleTheme, theme } = useUIStore();

  useEffect(() => {
    if (!authService.isAuthenticated()) {
      navigate('/login');
    }
  }, [navigate]);

  // Auto-create default workspace if none exist; auto-select first workspace
  useEffect(() => {
    if (workspacesLoading) return;
    const workspaces = workspacesData?.items ?? [];
    if (workspaces.length === 0 && !createWorkspaceMutation.isPending) {
      // Silently create a default workspace and auto-select it
      createWorkspaceMutation.mutateAsync({ name: 'Default' })
        .then((ws) => setCurrentWorkspace(ws.id, ws.name, ws.app_id))
        .catch(() => {});
    } else if (!currentWorkspaceId && workspaces.length > 0) {
      // Auto-select first workspace
      const first = workspaces[0];
      setCurrentWorkspace(first.id, first.name, first.app_id);
    }
  }, [workspacesLoading, workspacesData, createWorkspaceMutation, currentWorkspaceId, setCurrentWorkspace]);


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

  const handleRunCountChange = useCallback((workspaceId: string, runCount: number) => {
    setRunCountOverrides((current) => {
      if (current[workspaceId] === runCount) return current;
      return { ...current, [workspaceId]: runCount };
    });
  }, []);

  const handleLogout = () => {
    authService.logout();
    resetChat();
    clearCurrentWorkspace();
    clearCurrentApp();
    navigate('/login');
  };

  const workspaces = workspacesData?.items ?? [];

  return (
    <div className="h-screen bg-background flex flex-col overflow-hidden">
      {/* Header */}
      <header className="bg-card/80 backdrop-blur-sm border-b border-border px-4 py-2.5 shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="size-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-md shadow-primary-500/20">
              <BrandIcon className="size-4 text-white" />
            </div>
            <span className="text-sm font-bold tracking-tight cursor-default">Structure</span>
          </div>

          <div className="flex items-center gap-2">
            <QuotaMeter />
            <Button
              variant="ghost" size="icon" className="size-8 rounded-xl"
              onClick={() => navigate('/home')} title="Home"
            >
              <HomeIcon className="size-4.5 text-muted-foreground" />
            </Button>
            <Button
              variant="ghost" size="icon" className="size-8 rounded-xl"
              onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {theme === 'dark'
                ? <ThemeLightIcon className="size-4.5 text-muted-foreground" />
                : <ThemeDarkIcon className="size-4.5 text-muted-foreground" />}
            </Button>
            <Button
              variant="ghost" size="icon" className="size-8 rounded-xl"
              onClick={handleLogout} title="Logout"
            >
              <LogoutIcon className="size-4.5 text-muted-foreground" />
            </Button>
          </div>
        </div>
      </header>

      <div className="flex-1 flex flex-col lg:flex-row min-h-0">
        {/* Workspace Sidebar */}
        <aside
          className={cn(
            'border-r border-border bg-card/50 flex flex-col transition-all duration-300 shrink-0 overflow-hidden',
            historyOpen ? 'lg:w-[260px] w-full' : 'lg:w-0 w-full'
          )}
        >
          <div className="flex flex-col h-full">
            {/* Sidebar header */}
            <div className="px-3 py-2.5 border-b border-border flex items-center justify-between shrink-0 bg-muted/20">
              <div className="flex items-center gap-2">
                <WorkspaceIcon className="size-4 text-muted-foreground/70" />
                <span className="text-sm font-bold tracking-tight">Spaces</span>
                {workspaces.length > 0 && (
                  <span className="text-[11px] font-bold text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums leading-none ring-1 ring-border/50">
                    {workspaces.length}
                  </span>
                )}
              </div>
              <Button
                variant="ghost" size="icon" className="size-7 rounded-lg hidden lg:flex"
                onClick={() => setHistoryOpen(false)} title="Collapse sidebar"
              >
                <CollapseIcon className="size-4" />
              </Button>
            </div>

            {/* Create button */}
            <div className="px-3 py-2.5 shrink-0">
              <Button
                size="sm" className="w-full gap-1.5 h-8 text-xs font-bold rounded-lg shadow-sm"
                onClick={() => setShowCreateForm(true)}
              >
                <NewIcon className="size-3.5" />
                New
              </Button>
            </div>

            {/* Workspace list */}
            <ScrollArea className="flex-1 px-2 pb-3">
              {workspacesLoading ? (
                <div className="flex items-center justify-center py-12">
                  <span className="text-[11px] font-medium text-muted-foreground/50 uppercase tracking-widest animate-pulse">Loading…</span>
                </div>
              ) : workspaces.length > 0 ? (
                <div className="space-y-1">
                  {workspaces.map((workspace: Workspace) => {
                    const dotCls = STATUS_DOT[workspace.status] ?? 'bg-muted-foreground/30';
                    const isActive = currentWorkspaceId === workspace.id;
                    const runCount = Math.max(
                      workspace.run_count,
                      runCountOverrides[workspace.id] ?? workspace.run_count
                    );
                    return (
                      <div
                        key={workspace.id}
                        role="button"
                        tabIndex={0}
                        onClick={() => handleOpenWorkspace(workspace.id, workspace.name, workspace.app_id)}
                        onKeyDown={(e) => e.key === 'Enter' && handleOpenWorkspace(workspace.id, workspace.name, workspace.app_id)}
                        title={`${workspace.name} · ${workspace.status} · ${runCount} runs`}
                        className={cn(
                          'group relative w-full text-left px-2.5 py-2 rounded-lg border transition-all duration-200 cursor-pointer',
                          isActive
                            ? 'bg-primary/5 border-primary/20 shadow-sm'
                            : 'border-transparent hover:bg-muted/60'
                        )}
                      >
                        <div className="flex items-center gap-2.5">
                          <span
                            className={cn('size-2 rounded-full shrink-0 shadow-sm', dotCls)}
                            title={workspace.status}
                          />
                          <span className={cn(
                            'text-sm font-semibold truncate flex-1 min-w-0',
                            isActive ? 'text-primary' : 'text-foreground/80'
                          )}>
                            {workspace.name}
                          </span>
                          <span
                            className="inline-flex items-center gap-1 text-[11px] font-mono text-muted-foreground/40 tabular-nums shrink-0"
                            title={`${runCount} runs`}
                          >
                            <RunIcon className="size-3" />
                            {runCount}
                          </span>
                          <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-all shrink-0">
                            <button
                              type="button"
                              className="size-6 flex items-center justify-center rounded-lg hover:bg-muted transition-colors shadow-sm"
                              onClick={(e) => { e.stopPropagation(); setEditingWorkspace(workspace); }}
                              title="Edit workspace"
                            >
                              <EditIcon className="size-3.5 text-muted-foreground" />
                            </button>
                            <button
                              type="button"
                              className="size-6 flex items-center justify-center rounded-lg hover:bg-destructive/10 transition-colors shadow-sm"
                              onClick={(e) => handleDeleteWorkspace(e, workspace.id, workspace.name)}
                              disabled={deleteWorkspaceMutation.isPending}
                              title="Delete workspace"
                            >
                              <DeleteIcon className="size-3.5 text-destructive/70" />
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="text-center py-12">
                  <WorkspaceIcon className="mx-auto size-8 text-muted-foreground/15 mb-2" />
                  <p className="text-xs text-muted-foreground/50">No spaces</p>
                </div>
              )}
            </ScrollArea>
          </div>
        </aside>

        {/* Expand toggle (desktop, when collapsed) */}
        {!historyOpen && (
          <div className="hidden lg:flex items-start px-2 pt-3 border-r border-border bg-card shrink-0">
            <Button
              variant="ghost" size="icon" className="size-8 rounded-xl"
              onClick={() => setHistoryOpen(true)} title="Expand sidebar"
            >
              <ExpandIcon className="size-4.5" />
            </Button>
          </div>
        )}

        {/* Mobile toggle bar */}
        {!historyOpen && (
          <div className="lg:hidden border-b border-border bg-card px-4 py-2 shrink-0">
            <Button variant="ghost" size="sm" className="gap-2 h-8 text-xs font-bold rounded-lg" onClick={() => setHistoryOpen(true)}>
              <WorkspaceIcon className="size-3.5" />
              Spaces
            </Button>
          </div>
        )}

        <main className="flex-1 flex flex-col min-h-0 min-w-0">
          <WorkspaceConsole onRunCountChange={handleRunCountChange} />
        </main>
      </div>

      {showCreateForm && (
        <WorkspaceCreateModal
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
