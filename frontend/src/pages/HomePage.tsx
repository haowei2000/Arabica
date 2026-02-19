import { useState } from 'react';

const ADJECTIVES = ['swift', 'bright', 'calm', 'clever', 'bold', 'sharp', 'keen', 'agile', 'vivid', 'crisp', 'brisk', 'lofty'];
const NOUNS = ['falcon', 'river', 'cloud', 'spark', 'wave', 'peak', 'grove', 'forge', 'dawn', 'crest', 'prism', 'vault'];

function randomAppCode() {
  const adj = ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)];
  const noun = NOUNS[Math.floor(Math.random() * NOUNS.length)];
  const num = Math.floor(Math.random() * 90) + 10;
  return `${adj}-${noun}-${num}`;
}
import { useNavigate } from 'react-router-dom';
import { Moon, Sun, Trash2, Bot, ExternalLink, Loader2, Zap, Layers, Library, Wrench, Brain, Sparkles } from 'lucide-react';
import { authService } from '@/services/authService';
import { useChatStore } from '@/stores/useChatStore';
import { useUIStore } from '@/stores/useUIStore';
import { useApps, useCreateApp, useDeleteApp, useTemplates } from '@/hooks/useApps';
import { useAppStore } from '@/stores/useAppStore';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/formatDate';
import { ViewToggle, type ViewMode } from '@/components/ViewToggle';
import { AccordionItem } from '@/components/AccordionItem';

import KnowledgePage from './context/KnowledgePage';
import ToolPage from './context/ToolPage';
import MemoryPage from './context/MemoryPage';
import SkillPage from './context/SkillPage';
import TriggerPage from './TriggerPage';

export default function HomePage() {
  const [showCreateAppForm, setShowCreateAppForm] = useState(false);
  const [appCode, setAppCode] = useState(() => randomAppCode());
  const [executorCode, setExecutorCode] = useState('');
  const [appViewMode, setAppViewMode] = useState<ViewMode>('card');
  const [openAppId, setOpenAppId] = useState<string | null>(null);

  const navigate = useNavigate();
  const { data: appsData, isLoading: appsLoading } = useApps({ page: 1, page_size: 50 });
  const { data: templatesData } = useTemplates();
  const createAppMutation = useCreateApp();
  const deleteAppMutation = useDeleteApp();
  const { currentAppId, setCurrentApp, clearCurrentApp } = useAppStore();
  const { reset: resetChat } = useChatStore();
  const { toggleTheme, theme } = useUIStore();

  const handleCreateApp = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createAppMutation.mutateAsync({
        app_code: appCode,
        executor_code: executorCode || undefined,
        enabled: true,
      });
      setShowCreateAppForm(false);
      setAppCode(randomAppCode());
      setExecutorCode('');
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleDeleteApp = async (appId: string, appCodeVal: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete app "${appCodeVal}"?`)) return;
    try {
      await deleteAppMutation.mutateAsync(appId);
      if (currentAppId === appId) clearCurrentApp();
    } catch (error) {
      alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleOpenApp = (appId: string, appCodeValue: string) => {
    resetChat();
    setCurrentApp(appId, appCodeValue);
    navigate('/app');
  };

  const handleLogout = () => {
    authService.logout();
    resetChat();
    navigate('/login');
  };

  const apps = appsData?.items ?? [];

  const renderAppContent = () => {
    if (appsLoading) {
      return (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      );
    }

    return (
      <div>
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              <Bot className="size-3.5 text-muted-foreground" />
              <h2 className="text-sm font-semibold text-foreground">Apps</h2>
            </div>
            <span className="text-xs text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">
              {apps.length}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <ViewToggle mode={appViewMode} onToggle={(m) => { setAppViewMode(m); if (m !== 'drawer') setOpenAppId(null); }} />
            <Button size="sm" onClick={() => { setShowCreateAppForm(true); setAppCode(randomAppCode()); }}>
              + New App
            </Button>
          </div>
        </div>

        {apps.length > 0 ? appViewMode === 'card' ? (
          /* ── Card grid ── */
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {apps.map((app) => (
              <div key={app.id}
                className={cn('rounded-xl border border-border bg-card p-4 flex flex-col gap-2.5 hover:bg-muted/20 transition-colors cursor-pointer', currentAppId === app.id && 'ring-1 ring-primary/30')}
                onClick={() => handleOpenApp(app.id, app.app_code)}>
                <div className="flex items-start gap-2.5">
                  <span className={cn('size-2 rounded-full mt-1 shrink-0', app.enabled ? 'bg-green-500' : 'bg-muted-foreground/30')} />
                  <p className="text-sm font-semibold font-mono leading-snug flex-1 min-w-0 truncate">{app.app_code}</p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {app.executor_code && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border border-blue-200/70 dark:border-blue-800/50 font-mono">
                      {app.executor_code}
                    </span>
                  )}
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 font-mono">
                    v{app.version}
                  </span>
                </div>
                <div className="flex items-center gap-3 text-[10px] text-muted-foreground mt-auto">
                  <span className={cn(app.enabled ? 'text-green-600' : '')}>{app.enabled ? 'enabled' : 'disabled'}</span>
                  <span className="ml-auto">{formatRelativeTime(app.created_at)}</span>
                </div>
                <div className="flex gap-2 pt-2 border-t border-border/40" onClick={(e) => e.stopPropagation()}>
                  <Button size="sm" className="flex-1 gap-1.5" onClick={() => handleOpenApp(app.id, app.app_code)}>
                    <ExternalLink className="size-3.5" />Start Chat
                  </Button>
                  <Button size="sm" variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                    disabled={deleteAppMutation.isPending} onClick={(e) => handleDeleteApp(app.id, app.app_code, e)}>
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="rounded-xl border border-border bg-card overflow-visible divide-y divide-border/50">
            {apps.map((app) => {
              const statusDot = (
                <span className={cn('size-2 rounded-full shrink-0', app.enabled ? 'bg-green-500' : 'bg-muted-foreground/30')} />
              );
              const executorChip = app.executor_code ? (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border border-blue-200/70 dark:border-blue-800/50 shrink-0 font-mono">
                  {app.executor_code}
                </span>
              ) : null;

              if (appViewMode === 'list') {
                return (
                  <div key={app.id}
                    className={cn('group relative flex items-center gap-3 px-4 py-3 hover:bg-muted/40 transition-colors cursor-pointer', currentAppId === app.id && 'bg-muted/60')}
                    onClick={() => handleOpenApp(app.id, app.app_code)}>
                    {statusDot}
                    <span className="text-sm font-medium flex-1 min-w-0 truncate font-mono">{app.app_code}</span>
                    {executorChip}
                    <span className="text-[10px] text-muted-foreground/60 shrink-0 tabular-nums">v{app.version}</span>
                    <ExternalLink className="size-3.5 text-muted-foreground/40 group-hover:text-muted-foreground/70 transition-colors shrink-0" />
                    <button type="button" className="size-7 flex items-center justify-center rounded opacity-0 group-hover:opacity-100 transition-opacity hover:bg-destructive/10 shrink-0"
                      onClick={(e) => handleDeleteApp(app.id, app.app_code, e)}>
                      <Trash2 className="size-3.5 text-destructive/70" />
                    </button>
                    <div className="absolute right-2 top-full mt-1 z-50 w-64 rounded-xl border border-border bg-card shadow-lg shadow-black/10 p-3 invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-[opacity,visibility] duration-150 pointer-events-none">
                      <div className="space-y-2 text-xs">
                        <div className="flex items-center gap-2">
                          <span className={cn('size-1.5 rounded-full', app.enabled ? 'bg-green-500' : 'bg-muted-foreground/40')} />
                          <span className="font-medium text-foreground">{app.enabled ? 'Enabled' : 'Disabled'}</span>
                        </div>
                        <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pt-1.5 border-t border-border/50 text-muted-foreground">
                          <span>Version</span><span className="text-foreground font-mono">v{app.version}</span>
                          {app.executor_code && (<><span>Executor</span><span className="text-foreground font-mono truncate">{app.executor_code}</span></>)}
                          <span>Created</span><span className="text-foreground">{formatRelativeTime(app.created_at)}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              }

              // Drawer mode
              return (
                <AccordionItem
                  key={app.id}
                  isOpen={openAppId === app.id}
                  onToggle={() => setOpenAppId(openAppId === app.id ? null : app.id)}
                  header={
                    <>{statusDot}
                      <span className="text-sm font-medium flex-1 min-w-0 truncate font-mono">{app.app_code}</span>
                      {executorChip}
                      <span className="text-[10px] text-muted-foreground/60 shrink-0 tabular-nums">v{app.version}</span>
                    </>
                  }
                  detail={
                    <div className="space-y-3">
                      <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                        <span className="text-muted-foreground">Status</span>
                        <span className="flex items-center gap-1.5">
                          <span className={cn('size-1.5 rounded-full', app.enabled ? 'bg-green-500' : 'bg-muted-foreground/30')} />
                          {app.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                        <span className="text-muted-foreground">Version</span>
                        <span className="font-mono">v{app.version}</span>
                        {app.executor_code && (<><span className="text-muted-foreground">Executor</span><span className="font-mono">{app.executor_code}</span></>)}
                        <span className="text-muted-foreground">Created</span>
                        <span>{formatRelativeTime(app.created_at)}</span>
                        <span className="text-muted-foreground">ID</span>
                        <span className="font-mono text-[10px] text-muted-foreground truncate">{app.id}</span>
                      </div>
                      <div className="flex gap-2 pt-2 border-t border-border/40">
                        <Button size="sm" className="flex-1 gap-1.5" onClick={() => handleOpenApp(app.id, app.app_code)}>
                          <ExternalLink className="size-3.5" />Start Chat
                        </Button>
                        <Button size="sm" variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                          disabled={deleteAppMutation.isPending}
                          onClick={(e) => handleDeleteApp(app.id, app.app_code, e)}>
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    </div>
                  }
                />
              );
            })}
          </div>
        ) : (
          <div className="text-center py-16 border border-dashed border-border rounded-xl">
            <Bot className="mx-auto size-8 text-muted-foreground/30 mb-3" />
            <p className="text-sm text-muted-foreground mb-3">No apps yet</p>
            <Button size="sm" onClick={() => { setShowCreateAppForm(true); setAppCode(randomAppCode()); }}>
              Create App
            </Button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-muted/30">
      <header className="bg-card/80 backdrop-blur-sm border-b border-border px-6 py-3 sticky top-0 z-30">
        <div className="flex items-center justify-between max-w-5xl mx-auto">
          <div className="flex items-center gap-2.5">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-sm shadow-primary-500/20">
              <Bot className="size-3.5 text-white" />
            </div>
            <h1 className="text-sm font-semibold text-foreground">AI Agent Platform</h1>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={toggleTheme}
              className="size-8 flex items-center justify-center rounded-lg hover:bg-muted transition-colors"
              title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
            >
              {theme === 'dark'
                ? <Sun className="size-4 text-muted-foreground" />
                : <Moon className="size-4 text-muted-foreground" />}
            </button>
            <button
              type="button"
              onClick={handleLogout}
              className="h-8 px-3 text-xs text-muted-foreground rounded-lg hover:bg-muted transition-colors"
            >
              Logout
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-5xl mx-auto px-6 py-6">
        <Tabs defaultValue="app" className="space-y-5">
          <TabsList className="h-8">
            <TabsTrigger value="app" className="text-xs px-3 gap-1.5"><Bot className="size-3" />App</TabsTrigger>
            <TabsTrigger value="trigger" className="text-xs px-3 gap-1.5"><Zap className="size-3" />Trigger</TabsTrigger>
            <TabsTrigger value="context" className="text-xs px-3 gap-1.5"><Layers className="size-3" />Context</TabsTrigger>
          </TabsList>

          <TabsContent value="app">
            {renderAppContent()}
          </TabsContent>

          <TabsContent value="trigger">
            <TriggerPage />
          </TabsContent>

          <TabsContent value="context">
            <Tabs defaultValue="knowledge">
              <TabsList className="h-7 mb-4">
                <TabsTrigger value="knowledge" className="text-xs px-3 gap-1.5"><Library className="size-3" />Knowledge</TabsTrigger>
                <TabsTrigger value="tool" className="text-xs px-3 gap-1.5"><Wrench className="size-3" />Tool</TabsTrigger>
                <TabsTrigger value="memory" className="text-xs px-3 gap-1.5"><Brain className="size-3" />Memory</TabsTrigger>
                <TabsTrigger value="skill" className="text-xs px-3 gap-1.5"><Sparkles className="size-3" />Skill</TabsTrigger>
              </TabsList>
              <TabsContent value="knowledge"><KnowledgePage /></TabsContent>
              <TabsContent value="tool"><ToolPage /></TabsContent>
              <TabsContent value="memory"><MemoryPage /></TabsContent>
              <TabsContent value="skill"><SkillPage /></TabsContent>
            </Tabs>
          </TabsContent>
        </Tabs>
      </div>

      {/* Create App Dialog */}
      <Dialog open={showCreateAppForm} onOpenChange={(open) => {
        if (!open) { setShowCreateAppForm(false); setAppCode(''); setExecutorCode(''); }
      }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>New App</DialogTitle>
          </DialogHeader>

          <form id="create-app-form" onSubmit={handleCreateApp} className="space-y-4">
            <div className="space-y-1.5">
              <Label>App Code *</Label>
              <Input
                value={appCode}
                onChange={(e) => setAppCode(e.target.value)}
                placeholder="e.g., product-agent"
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label>Executor (optional)</Label>
              <Select value={executorCode} onValueChange={setExecutorCode}>
                <SelectTrigger>
                  <SelectValue placeholder="Select an executor" />
                </SelectTrigger>
                <SelectContent>
                  {!templatesData ? (
                    <SelectItem value="_loading" disabled>Loading...</SelectItem>
                  ) : templatesData.length === 0 ? (
                    <SelectItem value="_none" disabled>No executors available</SelectItem>
                  ) : (
                    templatesData.map((template) => (
                      <SelectItem key={template.id} value={template.executor_code}>
                        {template.executor_name}
                      </SelectItem>
                    ))
                  )}
                </SelectContent>
              </Select>
            </div>
          </form>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => { setShowCreateAppForm(false); setAppCode(''); setExecutorCode(''); }}
            >
              Cancel
            </Button>
            <Button type="submit" form="create-app-form" disabled={createAppMutation.isPending}>
              {createAppMutation.isPending ? 'Creating...' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
