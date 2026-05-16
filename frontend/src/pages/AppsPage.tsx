import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Bot,
  CalendarClock,
  CheckCircle2,
  Circle,
  Cpu,
  ExternalLink,
  Loader2,
  LogOut,
  Plus,
  Trash2,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useApps, useCreateApp, useDeleteApp, useTemplates } from '@/hooks/useApps';
import { cn } from '@/lib/utils';
import { authService } from '@/services/authService';
import { useAppStore } from '@/stores/useAppStore';
import { useChatStore } from '@/stores/useChatStore';
import { formatRelativeTime } from '@/utils/formatDate';

export default function AppsPage() {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [appCode, setAppCode] = useState('');
  const [executorCode, setExecutorCode] = useState('');
  const [formError, setFormError] = useState<string | null>(null);

  const navigate = useNavigate();
  const { data: appsData, isLoading } = useApps();
  const { data: templates } = useTemplates();
  const createAppMutation = useCreateApp();
  const deleteAppMutation = useDeleteApp();
  const { currentAppId, setCurrentApp, clearCurrentApp } = useAppStore();
  const { reset: resetChat } = useChatStore();

  const apps = appsData?.items ?? [];

  const resetCreateForm = () => {
    setShowCreateForm(false);
    setAppCode('');
    setExecutorCode('');
    setFormError(null);
  };

  const handleCreateApp = async (e: FormEvent) => {
    e.preventDefault();
    setFormError(null);

    try {
      await createAppMutation.mutateAsync({
        app_code: appCode,
        executor_code: executorCode || undefined,
        enabled: true,
      });
      resetCreateForm();
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'App creation failed');
    }
  };

  const handleDeleteApp = async (appId: string, code: string) => {
    if (!confirm(`Delete app "${code}"? This cannot be undone.`)) return;

    try {
      await deleteAppMutation.mutateAsync(appId);
      if (currentAppId === appId) {
        clearCurrentApp();
      }
    } catch (error) {
      alert(`Delete failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleStartChat = (appId: string, code: string) => {
    resetChat();
    setCurrentApp(appId, code);
    setTimeout(() => navigate('/chat'), 0);
  };

  const handleLogout = () => {
    authService.logout();
    resetChat();
    navigate('/login');
  };

  return (
    <div className="min-h-screen bg-background text-foreground">
      <header className="border-b border-border bg-card/80 px-4 py-3 backdrop-blur-sm">
        <div className="mx-auto flex max-w-[1600px] items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm shadow-primary/20">
              <Bot className="size-4" />
            </div>
            <div>
              <h1 className="text-base font-bold tracking-tight">AI Agent Platform</h1>
              <p className="text-[11px] text-muted-foreground">Apps</p>
            </div>
          </div>
          <Button type="button" variant="ghost" size="sm" className="gap-2" onClick={handleLogout}>
            <LogOut className="size-4" />
            Logout
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-4 py-8 sm:px-8">
        <section className="mb-5 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              <Bot className="size-3.5 text-muted-foreground" />
              <h2 className="text-sm font-semibold">Apps</h2>
            </div>
            <span className="rounded-full bg-muted px-2 py-0.5 text-xs tabular-nums text-muted-foreground">
              {apps.length}
            </span>
          </div>
          <Button type="button" size="sm" className="gap-1.5" onClick={() => setShowCreateForm(true)}>
            <Plus className="size-3.5" />
            New App
          </Button>
        </section>

        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : apps.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {apps.map((app) => (
              <article
                key={app.id}
                className={cn(
                  'rounded-xl border border-border bg-card p-4 shadow-sm transition-colors hover:bg-muted/20',
                  currentAppId === app.id && 'ring-1 ring-primary/30',
                )}
              >
                <div className="flex items-start gap-3">
                  <span
                    className={cn(
                      'mt-1 size-2 rounded-full shrink-0',
                      app.enabled ? 'bg-emerald-500' : 'bg-muted-foreground/30',
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <h3 className="truncate font-mono text-sm font-semibold">{app.app_code}</h3>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      <Badge variant={app.enabled ? 'default' : 'secondary'} className="gap-1 px-2 py-0 text-[10px]">
                        {app.enabled ? <CheckCircle2 className="size-3" /> : <Circle className="size-3" />}
                        {app.enabled ? 'Enabled' : 'Disabled'}
                      </Badge>
                      {(app.executor_code || app.executor_id) && (
                        <Badge variant="outline" className="gap-1 px-2 py-0 text-[10px] font-mono text-muted-foreground">
                          <Cpu className="size-3" />
                          {app.executor_code || app.executor_id}
                        </Badge>
                      )}
                      <Badge variant="secondary" className="px-2 py-0 text-[10px] font-mono">
                        v{app.version}
                      </Badge>
                    </div>
                  </div>
                </div>

                <div className="mt-4 flex items-center gap-2 text-[11px] text-muted-foreground">
                  <CalendarClock className="size-3.5" />
                  <span>{formatRelativeTime(app.created_at)}</span>
                  <span className="ml-auto max-w-36 truncate font-mono" title={app.id}>
                    {app.id}
                  </span>
                </div>

                <div className="mt-4 flex gap-2 border-t border-border/40 pt-3">
                  <Button
                    type="button"
                    size="sm"
                    className="flex-1 gap-1.5"
                    onClick={() => handleStartChat(app.id, app.app_code)}
                  >
                    <ExternalLink className="size-3.5" />
                    Start Chat
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="border-destructive/30 text-destructive hover:bg-destructive/10 hover:text-destructive"
                    disabled={deleteAppMutation.isPending}
                    onClick={() => handleDeleteApp(app.id, app.app_code)}
                  >
                    <Trash2 className="size-3.5" />
                    <span className="sr-only">Delete {app.app_code}</span>
                  </Button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="rounded-xl border border-dashed border-border py-16 text-center">
            <Bot className="mx-auto mb-3 size-8 text-muted-foreground/30" />
            <h3 className="text-sm font-semibold">No Apps Yet</h3>
            <p className="mt-1 text-sm text-muted-foreground">Create an app to start a chat workflow.</p>
            <Button type="button" size="sm" className="mt-4 gap-1.5" onClick={() => setShowCreateForm(true)}>
              <Plus className="size-3.5" />
              New App
            </Button>
          </div>
        )}

        {appsData && appsData.total > appsData.page_size && (
          <p className="mt-8 text-center text-sm text-muted-foreground">
            Showing {appsData.items.length} of {appsData.total} apps
          </p>
        )}
      </main>

      <Dialog open={showCreateForm} onOpenChange={(open) => { if (!open) resetCreateForm(); }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create App</DialogTitle>
          </DialogHeader>
          <form onSubmit={handleCreateApp} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="app-code" className="text-sm font-medium">
                App Code
              </label>
              <input
                id="app-code"
                name="app_code"
                type="text"
                value={appCode}
                onChange={(e) => setAppCode(e.target.value)}
                placeholder="example-agent"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                required
              />
              <p className="text-xs text-muted-foreground">
                Use letters, numbers, and hyphens.
              </p>
            </div>

            <div className="space-y-1.5">
              <label htmlFor="executor-code" className="text-sm font-medium">
                Executor
              </label>
              <select
                id="executor-code"
                name="executor_code"
                value={executorCode}
                onChange={(e) => setExecutorCode(e.target.value)}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
              >
                {!templates ? (
                  <option value="" disabled>Loading executors…</option>
                ) : templates.length === 0 ? (
                  <option value="" disabled>No executors available</option>
                ) : (
                  <>
                    <option value="">Select an executor</option>
                    {templates.map((template) => (
                      <option key={template.id} value={template.executor_code}>
                        {template.executor_name || template.executor_code}
                      </option>
                    ))}
                  </>
                )}
              </select>
            </div>

            {formError && (
              <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
                {formError}
              </div>
            )}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={resetCreateForm}>
                Cancel
              </Button>
              <Button type="submit" disabled={createAppMutation.isPending || !appCode.trim()}>
                {createAppMutation.isPending ? (
                  <>
                    <Loader2 className="size-4 animate-spin" />
                    Creating…
                  </>
                ) : (
                  'Create App'
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
