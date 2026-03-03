import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Moon, Sun, Trash2, Plus,
  ChevronLeft, ChevronRight, Pencil,
  Bot, LogOut, Play, FolderKanban, Cpu, Home,
} from 'lucide-react';
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

const STATUS_DOT: Record<string, string> = {
  active:   'bg-green-500',
  inactive: 'bg-muted-foreground/30',
  running:  'bg-yellow-400 animate-pulse',
  error:    'bg-red-500',
};

export default function AppWorkspacePage() {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingWorkspace, setEditingWorkspace] = useState<Workspace | null>(null);
  const [historyOpen, setHistoryOpen] = useState(true);

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
    } else if (!currentWorkspaceId) {
      // Auto-select first workspace
      const first = workspaces[0];
      setCurrentWorkspace(first.id, first.name, first.app_id);
    }
  }, [workspacesLoading, workspacesData]);

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

  const handleLogout = () => {
    authService.logout();
    resetChat();
    clearCurrentWorkspace();
    clearCurrentApp();
    navigate('/login');
  };

  const workspaces = workspacesData?.items ?? [];

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Header */}
      <header className="bg-card/80 backdrop-blur-sm border-b border-border px-4 py-2.5 shrink-0 sticky top-0 z-30">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-sm shadow-primary-500/20">
              <Bot className="size-3.5 text-white" />
            </div>
            <span className="text-sm font-semibold cursor-default">AI Agent Platform</span>
          </div>

          <div className="flex items-center gap-1">
            <Button
              variant="ghost" size="icon" className="size-7"
              onClick={() => navigate('/home')} title="Home"
            >
              <Home className="size-3.5 text-muted-foreground" />
            </Button>
            <Button
              variant="ghost" size="icon" className="size-7"
              onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {theme === 'dark'
                ? <Sun className="size-3.5 text-muted-foreground" />
                : <Moon className="size-3.5 text-muted-foreground" />}
            </Button>
            <Button
              variant="ghost" size="icon" className="size-7"
              onClick={handleLogout} title="Logout"
            >
              <LogOut className="size-3.5 text-muted-foreground" />
            </Button>
          </div>
        </div>
      </header>

      <div className="flex-1 flex flex-col lg:flex-row min-h-0">
        {/* Workspace Sidebar */}
        <aside
          className={cn(
            'border-r border-border bg-card flex flex-col transition-all duration-200 shrink-0',
            historyOpen ? 'lg:w-64 w-full' : 'lg:w-0 overflow-hidden w-full'
          )}
        >
          <div className="flex flex-col h-full">
            {/* Sidebar header */}
            <div className="px-3 py-2.5 border-b border-border flex items-center justify-between shrink-0">
              <div className="flex items-center gap-1.5">
                <FolderKanban className="size-3.5 text-muted-foreground" />
                <span className="text-xs font-semibold">Workspaces</span>
                {workspaces.length > 0 && (
                  <span className="text-[10px] text-muted-foreground bg-muted rounded-full px-1.5 py-0.5 tabular-nums leading-none">
                    {workspaces.length}
                  </span>
                )}
              </div>
              <Button
                variant="ghost" size="icon" className="size-6 hidden lg:flex"
                onClick={() => setHistoryOpen(false)} title="Collapse sidebar"
              >
                <ChevronLeft className="size-3.5" />
              </Button>
            </div>

            {/* Create button */}
            <div className="px-3 py-2 shrink-0">
              <Button
                size="sm" className="w-full gap-1.5 h-7 text-xs"
                onClick={() => setShowCreateForm(true)}
              >
                <Plus className="size-3.5" />
                New Workspace
              </Button>
            </div>

            {/* Workspace list */}
            <ScrollArea className="flex-1 px-2 pb-2">
              {workspacesLoading ? (
                <div className="flex items-center justify-center py-10">
                  <span className="text-[10px] text-muted-foreground">Loading…</span>
                </div>
              ) : workspaces.length > 0 ? (
                <div className="space-y-px">
                  {workspaces.map((workspace) => {
                    const dotCls = STATUS_DOT[workspace.status] ?? 'bg-muted-foreground/30';
                    const isActive = currentWorkspaceId === workspace.id;
                    return (
                      <div
                        key={workspace.id}
                        role="button"
                        tabIndex={0}
                        onClick={() => handleOpenWorkspace(workspace.id, workspace.name, workspace.app_id)}
                        onKeyDown={(e) => e.key === 'Enter' && handleOpenWorkspace(workspace.id, workspace.name, workspace.app_id)}
                        className={cn(
                          'group relative w-full text-left px-2.5 py-2 rounded-lg border transition-colors cursor-pointer',
                          isActive
                            ? 'bg-primary/8 border-primary/20'
                            : 'border-transparent hover:bg-muted/60'
                        )}
                      >
                        <div className="flex items-center gap-2">
                          {/* Status dot with tooltip for status text */}
                          <span
                            className={cn('size-1.5 rounded-full shrink-0', dotCls)}
                            title={workspace.status}
                          />
                          {/* Name */}
                          <span className={cn(
                            'text-xs font-medium truncate flex-1 min-w-0',
                            isActive ? 'text-primary' : 'text-foreground'
                          )}>
                            {workspace.name}
                          </span>
                          {/* Run count */}
                          <span
                            className="inline-flex items-center gap-0.5 text-[10px] text-muted-foreground/60 tabular-nums shrink-0"
                            title={`${workspace.run_count} runs`}
                          >
                            <Play className="size-2.5" />
                            {workspace.run_count}
                          </span>
                          {/* Actions — visible on hover only */}
                          <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                            <button
                              type="button"
                              className="size-5 flex items-center justify-center rounded hover:bg-muted transition-colors"
                              onClick={(e) => { e.stopPropagation(); setEditingWorkspace(workspace); }}
                              title="Edit workspace"
                            >
                              <Pencil className="size-3 text-muted-foreground" />
                            </button>
                            <button
                              type="button"
                              className="size-5 flex items-center justify-center rounded hover:bg-destructive/10 transition-colors"
                              onClick={(e) => handleDeleteWorkspace(e, workspace.id, workspace.name)}
                              disabled={deleteWorkspaceMutation.isPending}
                              title="Delete workspace"
                            >
                              <Trash2 className="size-3 text-destructive/70" />
                            </button>
                          </div>
                        </div>
                        {/* Hover tooltip — full workspace detail */}
                        <div className="absolute left-full ml-2 top-0 z-50 w-56 rounded-xl border border-border bg-card shadow-lg shadow-black/10 p-3 invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-[opacity,visibility] duration-150 pointer-events-none hidden lg:block">
                          <div className="space-y-1.5 text-xs">
                            <p className="font-semibold text-foreground leading-snug">{workspace.name}</p>
                            {workspace.description && (
                              <p className="text-muted-foreground leading-relaxed">{workspace.description}</p>
                            )}
                            <div className="grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 pt-1.5 border-t border-border/50 text-muted-foreground">
                              <span>Status</span><span className="text-foreground capitalize">{workspace.status}</span>
                              <span>Runs</span><span className="text-foreground tabular-nums">{workspace.run_count}</span>
                              {(workspace.executor_code || workspace.app_id) && (
                                <>
                                  <span className="flex items-center gap-0.5"><Cpu className="size-2.5" />Executor</span>
                                  <span className="text-foreground font-mono truncate">
                                    {workspace.executor_code || `app:${workspace.app_id?.slice(0, 6)}…`}
                                  </span>
                                </>
                              )}
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="text-center py-10">
                  <FolderKanban className="mx-auto size-6 text-muted-foreground/20 mb-2" />
                  <p className="text-[10px] text-muted-foreground/60">No workspaces yet</p>
                </div>
              )}
            </ScrollArea>
          </div>
        </aside>

        {/* Expand toggle (desktop, when collapsed) */}
        {!historyOpen && (
          <div className="hidden lg:flex items-start px-1 pt-2 border-r border-border bg-card shrink-0">
            <Button
              variant="ghost" size="icon" className="size-7"
              onClick={() => setHistoryOpen(true)} title="Expand sidebar"
            >
              <ChevronRight className="size-3.5" />
            </Button>
          </div>
        )}

        {/* Mobile toggle bar */}
        {!historyOpen && (
          <div className="lg:hidden border-b border-border bg-card px-3 py-1.5 shrink-0">
            <Button variant="ghost" size="sm" className="gap-1.5 h-7 text-xs" onClick={() => setHistoryOpen(true)}>
              <FolderKanban className="size-3" />
              Workspaces
            </Button>
          </div>
        )}

        <main className="flex-1 flex flex-col min-h-0 min-w-0">
          <WorkspaceConsole />
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
