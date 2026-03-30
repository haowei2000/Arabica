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
    <div className="h-screen bg-background flex flex-col overflow-hidden">
      {/* Header */}
      <header className="bg-card/80 backdrop-blur-sm border-b border-border px-4 py-3 shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-md shadow-primary-500/20">
              <Bot className="size-4 text-white" />
            </div>
            <span className="text-sm font-bold tracking-tight cursor-default">AI Agent Platform</span>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="ghost" size="icon" className="size-8 rounded-xl"
              onClick={() => navigate('/home')} title="Home"
            >
              <Home className="size-4.5 text-muted-foreground" />
            </Button>
            <Button
              variant="ghost" size="icon" className="size-8 rounded-xl"
              onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {theme === 'dark'
                ? <Sun className="size-4.5 text-muted-foreground" />
                : <Moon className="size-4.5 text-muted-foreground" />}
            </Button>
            <Button
              variant="ghost" size="icon" className="size-8 rounded-xl"
              onClick={handleLogout} title="Logout"
            >
              <LogOut className="size-4.5 text-muted-foreground" />
            </Button>
          </div>
        </div>
      </header>

      <div className="flex-1 flex flex-col lg:flex-row min-h-0">
        {/* Workspace Sidebar */}
        <aside
          className={cn(
            'border-r border-border bg-card/50 flex flex-col transition-all duration-300 shrink-0 overflow-hidden',
            historyOpen ? 'lg:w-[280px] w-full' : 'lg:w-0 w-full'
          )}
        >
          <div className="flex flex-col h-full">
            {/* Sidebar header */}
            <div className="px-4 py-3 border-b border-border flex items-center justify-between shrink-0 bg-muted/20">
              <div className="flex items-center gap-2">
                <FolderKanban className="size-4 text-muted-foreground/70" />
                <span className="text-sm font-bold tracking-tight">Workspaces</span>
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
                <ChevronLeft className="size-4" />
              </Button>
            </div>

            {/* Create button */}
            <div className="px-3 py-3 shrink-0">
              <Button
                size="sm" className="w-full gap-2 h-9 text-xs font-bold rounded-xl shadow-sm"
                onClick={() => setShowCreateForm(true)}
              >
                <Plus className="size-4" />
                New Workspace
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
                          'group relative w-full text-left px-3 py-2.5 rounded-xl border transition-all duration-200 cursor-pointer',
                          isActive
                            ? 'bg-primary/5 border-primary/20 shadow-sm'
                            : 'border-transparent hover:bg-muted/60'
                        )}
                      >
                        <div className="flex items-center gap-3">
                          {/* Status dot with tooltip for status text */}
                          <span
                            className={cn('size-2 rounded-full shrink-0 shadow-sm', dotCls)}
                            title={workspace.status}
                          />
                          {/* Name */}
                          <span className={cn(
                            'text-sm font-semibold truncate flex-1 min-w-0 tracking-tight',
                            isActive ? 'text-primary' : 'text-foreground/80'
                          )}>
                            {workspace.name}
                          </span>
                          {/* Run count */}
                          <span
                            className="inline-flex items-center gap-1 text-[11px] font-mono text-muted-foreground/40 tabular-nums shrink-0"
                            title={`${workspace.run_count} runs`}
                          >
                            <Play className="size-3" />
                            {workspace.run_count}
                          </span>
                          {/* Actions — visible on hover only */}
                          <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-all shrink-0">
                            <button
                              type="button"
                              className="size-6 flex items-center justify-center rounded-lg hover:bg-muted transition-colors shadow-sm"
                              onClick={(e) => { e.stopPropagation(); setEditingWorkspace(workspace); }}
                              title="Edit workspace"
                            >
                              <Pencil className="size-3.5 text-muted-foreground" />
                            </button>
                            <button
                              type="button"
                              className="size-6 flex items-center justify-center rounded-lg hover:bg-destructive/10 transition-colors shadow-sm"
                              onClick={(e) => handleDeleteWorkspace(e, workspace.id, workspace.name)}
                              disabled={deleteWorkspaceMutation.isPending}
                              title="Delete workspace"
                            >
                              <Trash2 className="size-3.5 text-destructive/70" />
                            </button>
                          </div>
                        </div>
                        {/* Hover tooltip — full workspace detail */}
                        <div className="absolute left-full ml-3 top-0 z-50 w-64 rounded-2xl border border-border bg-card shadow-xl shadow-black/10 p-4 invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-all duration-200 pointer-events-none hidden lg:block">
                          <div className="space-y-2 text-xs">
                            <p className="text-sm font-bold text-foreground leading-tight">{workspace.name}</p>
                            {workspace.description && (
                              <p className="text-muted-foreground leading-relaxed line-clamp-3">{workspace.description}</p>
                            )}
                            <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 pt-2.5 border-t border-border/50 text-muted-foreground">
                              <span className="font-medium">Status</span><span className="text-foreground font-bold capitalize">{workspace.status}</span>
                              <span className="font-medium">Runs</span><span className="text-foreground font-bold tabular-nums">{workspace.run_count}</span>
                              {(workspace.executor_code || workspace.app_id) && (
                                <>
                                  <span className="flex items-center gap-1 font-medium"><Cpu className="size-3" />Executor</span>
                                  <span className="text-foreground font-mono font-medium truncate">
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
                <div className="text-center py-16">
                  <FolderKanban className="mx-auto size-10 text-muted-foreground/10 mb-3" />
                  <p className="text-xs font-bold text-muted-foreground/40 uppercase tracking-widest">No workspaces yet</p>
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
              <ChevronRight className="size-4.5" />
            </Button>
          </div>
        )}

        {/* Mobile toggle bar */}
        {!historyOpen && (
          <div className="lg:hidden border-b border-border bg-card px-4 py-2 shrink-0">
            <Button variant="ghost" size="sm" className="gap-2 h-8 text-xs font-bold rounded-lg" onClick={() => setHistoryOpen(true)}>
              <FolderKanban className="size-3.5" />
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
