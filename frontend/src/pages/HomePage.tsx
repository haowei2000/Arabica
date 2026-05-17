import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
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
import { APP_ICONS } from '@/constants/icons';

import KnowledgePage from './context/KnowledgePage';
import ToolPage from './context/ToolPage';
import MemoryPage from './context/MemoryPage';
import SkillPage from './context/SkillPage';
import LLMModelsPage from './LLMModelsPage';
import TriggerPage from './TriggerPage';

type HomeTab = 'app' | 'trigger' | 'context' | 'models';

interface HomePageProps {
  defaultTab?: HomeTab;
}

const ADJECTIVES = ['swift', 'bright', 'calm', 'clever', 'bold', 'sharp', 'keen', 'agile', 'vivid', 'crisp', 'brisk', 'lofty'];
const NOUNS = ['falcon', 'river', 'cloud', 'spark', 'wave', 'peak', 'grove', 'forge', 'dawn', 'crest', 'prism', 'vault'];

const AppIcon = APP_ICONS.app;
const BrandIcon = APP_ICONS.brand;
const ContextIcon = APP_ICONS.context;
const DeleteIcon = APP_ICONS.delete;
const KnowledgeIcon = APP_ICONS.knowledge;
const MemoryIcon = APP_ICONS.memory;
const ModelIcon = APP_ICONS.model;
const NewIcon = APP_ICONS.new;
const OpenIcon = APP_ICONS.open;
const LogoutIcon = APP_ICONS.logout;
const SkillIcon = APP_ICONS.skill;
const ThemeDarkIcon = APP_ICONS.themeDark;
const ThemeLightIcon = APP_ICONS.themeLight;
const ToolIcon = APP_ICONS.tool;
const TriggerIcon = APP_ICONS.trigger;
const WorkspaceIcon = APP_ICONS.workspace;

function randomAppCode() {
  const adj = ADJECTIVES[Math.floor(Math.random() * ADJECTIVES.length)];
  const noun = NOUNS[Math.floor(Math.random() * NOUNS.length)];
  const num = Math.floor(Math.random() * 90) + 10;
  return `${adj}-${noun}-${num}`;
}

export default function HomePage({ defaultTab = 'app' }: HomePageProps) {
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
              <AppIcon className="size-3.5 text-muted-foreground" />
              <h2 className="text-sm font-semibold text-foreground">Apps</h2>
            </div>
            <span className="text-xs text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">
              {apps.length}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <ViewToggle mode={appViewMode} onToggle={(m) => { setAppViewMode(m); if (m !== 'drawer') setOpenAppId(null); }} />
            <Button size="sm" className="gap-1.5" onClick={() => { setShowCreateAppForm(true); setAppCode(randomAppCode()); }}>
              <NewIcon className="size-3.5" />New
            </Button>
          </div>
        </div>

        {apps.length > 0 ? appViewMode === 'card' ? (
          /* ── Card grid ── */
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {apps.map((app) => (
              <div key={app.id}
                className={cn('rounded-lg border border-border bg-card p-3 flex flex-col gap-2 hover:bg-muted/20 transition-colors cursor-pointer', currentAppId === app.id && 'ring-1 ring-primary/30')}
                onClick={() => handleOpenApp(app.id, app.app_code)}>
                <div className="flex items-start gap-2">
                  <span className={cn('size-2 rounded-full mt-1 shrink-0', app.enabled ? 'bg-green-500' : 'bg-muted-foreground/30')} />
                  <p className="text-sm font-semibold font-mono leading-snug flex-1 min-w-0 truncate">{app.app_code}</p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(app.executor_code || app.executor_id) && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20 font-mono">
                      {app.executor_code || app.executor_id}
                    </span>
                  )}
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 font-mono">
                    v{app.version}
                  </span>
                </div>
                <div className="flex items-center text-[10px] text-muted-foreground mt-auto">
                  <span className="ml-auto">{formatRelativeTime(app.created_at)}</span>
                </div>
                <div className="flex gap-1.5 pt-2 border-t border-border/40" onClick={(e) => e.stopPropagation()}>
                  <Button size="sm" className="flex-1 gap-1.5 h-8" onClick={() => handleOpenApp(app.id, app.app_code)}>
                    <OpenIcon className="size-3.5" />Open
                  </Button>
                  <Button size="sm" variant="outline" className="h-8 text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                    disabled={deleteAppMutation.isPending} onClick={(e) => handleDeleteApp(app.id, app.app_code, e)}>
                    <DeleteIcon className="size-3.5" />
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
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20 shrink-0 font-mono">
                  {app.executor_code}
                </span>
              ) : null;

              if (appViewMode === 'list') {
                return (
                  <div key={app.id}
                    className={cn('group relative flex items-center gap-2.5 px-3 py-2 hover:bg-muted/40 transition-colors cursor-pointer', currentAppId === app.id && 'bg-muted/60')}
                    title={`${app.app_code} · ${app.enabled ? 'enabled' : 'disabled'} · ${formatRelativeTime(app.created_at)}`}
                    onClick={() => handleOpenApp(app.id, app.app_code)}>
                    {statusDot}
                    <span className="text-sm font-medium flex-1 min-w-0 truncate font-mono">{app.app_code}</span>
                    {executorChip}
                    <span className="text-[10px] text-muted-foreground/60 shrink-0 tabular-nums">v{app.version}</span>
                    <OpenIcon className="size-3.5 text-muted-foreground/40 group-hover:text-muted-foreground/70 transition-colors shrink-0" />
                    <button type="button" className="size-7 flex items-center justify-center rounded opacity-0 group-hover:opacity-100 transition-opacity hover:bg-destructive/10 shrink-0"
                      onClick={(e) => handleDeleteApp(app.id, app.app_code, e)}>
                      <DeleteIcon className="size-3.5 text-destructive/70" />
                    </button>
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
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <span className="inline-flex items-center gap-1.5 text-foreground">
                          <span className={cn('size-1.5 rounded-full', app.enabled ? 'bg-green-500' : 'bg-muted-foreground/30')} />
                          {app.enabled ? 'On' : 'Off'}
                        </span>
                        <span className="font-mono">v{app.version}</span>
                        {app.executor_code && <span className="font-mono truncate max-w-[12rem]">{app.executor_code}</span>}
                        <span>{formatRelativeTime(app.created_at)}</span>
                      </div>
                      <div className="flex gap-2 pt-2 border-t border-border/40">
                        <Button size="sm" className="flex-1 gap-1.5 h-8" onClick={() => handleOpenApp(app.id, app.app_code)}>
                          <OpenIcon className="size-3.5" />Open
                        </Button>
                        <Button size="sm" variant="outline" className="h-8 text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                          disabled={deleteAppMutation.isPending}
                          onClick={(e) => handleDeleteApp(app.id, app.app_code, e)}>
                          <DeleteIcon className="size-3.5" />
                        </Button>
                      </div>
                    </div>
                  }
                />
              );
            })}
          </div>
        ) : (
          <div className="text-center py-12 border border-dashed border-border rounded-lg">
            <AppIcon className="mx-auto size-7 text-muted-foreground/25 mb-2" />
            <p className="text-xs text-muted-foreground mb-3">No apps</p>
            <Button size="sm" className="gap-1.5" onClick={() => { setShowCreateAppForm(true); setAppCode(randomAppCode()); }}>
              <NewIcon className="size-3.5" />New
            </Button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-background">
      <header className="bg-card/80 backdrop-blur-sm border-b border-border px-4 sm:px-6 py-3 sticky top-0 z-30">
        <div className="flex items-center justify-between max-w-[1600px] mx-auto">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-md shadow-primary-500/20">
              <BrandIcon className="size-4 text-white" />
            </div>
            <h1 className="text-base font-bold text-foreground tracking-tight">Structure</h1>
          </div>
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => { resetChat(); navigate('/app'); }}
              className="h-9 rounded-xl text-muted-foreground hover:text-foreground"
              title="Open Workspaces"
            >
              <WorkspaceIcon className="size-4" />
              <span className="hidden sm:inline">Workspaces</span>
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={toggleTheme}
              className="size-9 rounded-xl text-muted-foreground hover:text-foreground"
              title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
            >
              {theme === 'dark'
                ? <ThemeLightIcon className="size-4.5" />
                : <ThemeDarkIcon className="size-4.5" />}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={handleLogout}
              className="size-9 rounded-xl text-muted-foreground hover:text-foreground"
              title="Logout"
            >
              <LogoutIcon className="size-4" />
            </Button>
          </div>
        </div>
      </header>

      <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-5">
        <Tabs defaultValue={defaultTab} className="space-y-4">
          <TabsList className="h-9 w-full sm:w-auto p-1 bg-muted/50">
            <TabsTrigger value="app" className="flex-1 sm:flex-none text-[11px] sm:text-xs px-1.5 sm:px-3 gap-1 sm:gap-1.5 rounded-md data-[state=active]:bg-background data-[state=active]:shadow-sm"><AppIcon className="size-3.5" />App</TabsTrigger>
            <TabsTrigger value="trigger" className="flex-1 sm:flex-none text-[11px] sm:text-xs px-1.5 sm:px-3 gap-1 sm:gap-1.5 rounded-md data-[state=active]:bg-background data-[state=active]:shadow-sm"><TriggerIcon className="size-3.5" />Trigger</TabsTrigger>
            <TabsTrigger value="context" className="flex-1 sm:flex-none text-[11px] sm:text-xs px-1.5 sm:px-3 gap-1 sm:gap-1.5 rounded-md data-[state=active]:bg-background data-[state=active]:shadow-sm"><ContextIcon className="size-3.5" />Context</TabsTrigger>
            <TabsTrigger value="models" className="flex-1 sm:flex-none text-[11px] sm:text-xs px-1.5 sm:px-3 gap-1 sm:gap-1.5 rounded-md data-[state=active]:bg-background data-[state=active]:shadow-sm"><ModelIcon className="size-3.5" />Models</TabsTrigger>
          </TabsList>

          <TabsContent value="app" className="mt-0 outline-none">
            {renderAppContent()}
          </TabsContent>

          <TabsContent value="trigger" className="mt-0 outline-none">
            <TriggerPage />
          </TabsContent>

          <TabsContent value="context" className="mt-0 outline-none">
            <Tabs defaultValue="knowledge">
              <TabsList className="h-8 p-1 bg-muted/30 mb-4">
                <TabsTrigger value="knowledge" className="text-xs px-3 gap-1.5 rounded-md data-[state=active]:bg-background"><KnowledgeIcon className="size-3.5" />Knowledge</TabsTrigger>
                <TabsTrigger value="tool" className="text-xs px-3 gap-1.5 rounded-md data-[state=active]:bg-background"><ToolIcon className="size-3.5" />Tool</TabsTrigger>
                <TabsTrigger value="memory" className="text-xs px-3 gap-1.5 rounded-md data-[state=active]:bg-background"><MemoryIcon className="size-3.5" />Memory</TabsTrigger>
                <TabsTrigger value="skill" className="text-xs px-3 gap-1.5 rounded-md data-[state=active]:bg-background"><SkillIcon className="size-3.5" />Skill</TabsTrigger>
              </TabsList>
              <TabsContent value="knowledge" className="outline-none"><KnowledgePage /></TabsContent>
              <TabsContent value="tool" className="outline-none"><ToolPage /></TabsContent>
              <TabsContent value="memory" className="outline-none"><MemoryPage /></TabsContent>
              <TabsContent value="skill" className="outline-none"><SkillPage /></TabsContent>
            </Tabs>
          </TabsContent>

          <TabsContent value="models" className="mt-0 outline-none">
            <LLMModelsPage />
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
                    <SelectItem value="_loading" disabled>Loading…</SelectItem>
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
              {createAppMutation.isPending ? 'Creating…' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
