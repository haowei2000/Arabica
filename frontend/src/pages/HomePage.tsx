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
import { Moon, Sun, Trash2, Package } from 'lucide-react';
import { authService } from '@/services/authService';
import { useChatStore } from '@/stores/useChatStore';
import { useUIStore } from '@/stores/useUIStore';
import { useApps, useCreateApp, useDeleteApp, useTemplates } from '@/hooks/useApps';
import { useAppStore } from '@/stores/useAppStore';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';

import KnowledgePage from './context/KnowledgePage';
import ToolPage from './context/ToolPage';
import MemoryPage from './context/MemoryPage';
import SkillPage from './context/SkillPage';
import TriggerPage from './TriggerPage';

export default function HomePage() {
  const [showCreateAppForm, setShowCreateAppForm] = useState(false);
  const [appCode, setAppCode] = useState(() => randomAppCode());
  const [executorCode, setExecutorCode] = useState('');

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

  const handleDeleteApp = async (appId: string, appCodeVal: string) => {
    if (!confirm(`Are you sure you want to delete app "${appCodeVal}"?`)) return;
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

  const renderAppContent = () => {
    if (appsLoading) {
      return (
        <div className="flex items-center justify-center py-12">
          <p className="text-muted-foreground">Loading...</p>
        </div>
      );
    }

    return (
      <div>
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold">Apps</h2>
            <p className="text-sm text-muted-foreground mt-1">Create an app and start a workspace</p>
          </div>
          <Button onClick={() => { setShowCreateAppForm(true); setAppCode(randomAppCode()); }}>+ Create App</Button>
        </div>

        {appsData?.items && appsData.items.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
            {appsData.items.map((app) => (
              <div
                key={app.id}
                className="bg-card rounded-lg border border-border p-6 hover:shadow-lg transition-shadow"
              >
                <div className="flex items-start justify-between mb-4">
                  <div className="flex-1">
                    <h3 className="text-lg font-semibold mb-1">{app.app_code}</h3>
                    <Badge variant={app.enabled ? 'default' : 'secondary'}>
                      {app.enabled ? 'Enabled' : 'Disabled'}
                    </Badge>
                  </div>
                </div>

                <div className="text-sm text-muted-foreground mb-4 space-y-1">
                  <p>Version: v{app.version}</p>
                  <p className="text-xs">Created: {new Date(app.created_at).toLocaleDateString()}</p>
                </div>

                <div className="flex gap-2">
                  <Button className="flex-1" onClick={() => handleOpenApp(app.id, app.app_code)}>
                    Open App
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    disabled={deleteAppMutation.isPending}
                    onClick={() => handleDeleteApp(app.id, app.app_code)}
                    title="Delete app"
                    className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-12">
            <Package className="mx-auto size-12 text-muted-foreground/40 mb-4" />
            <h3 className="text-lg font-medium mb-1">No Apps Yet</h3>
            <p className="text-muted-foreground mb-4">Create your first app to start a workspace</p>
            <Button onClick={() => { setShowCreateAppForm(true); setAppCode(randomAppCode()); }}>Create App</Button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-muted/30">
      <header className="bg-card border-b border-border px-6 py-4">
        <div className="flex items-center justify-between max-w-6xl mx-auto">
          <h1 className="text-xl font-semibold">AI Agent Platform</h1>
          <div className="flex items-center gap-4">
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {theme === 'dark' ? <Sun className="size-5" /> : <Moon className="size-5" />}
            </Button>
            <Button variant="ghost" onClick={handleLogout} className="text-sm">
              Logout
            </Button>
          </div>
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-6 py-8">
        <Tabs defaultValue="app" className="space-y-6">
          <TabsList>
            <TabsTrigger value="app">App</TabsTrigger>
            <TabsTrigger value="trigger">Trigger</TabsTrigger>
            <TabsTrigger value="context">Context</TabsTrigger>
          </TabsList>

          <TabsContent value="app">
            {renderAppContent()}
          </TabsContent>

          <TabsContent value="trigger">
            <TriggerPage />
          </TabsContent>

          <TabsContent value="context">
            <Tabs defaultValue="knowledge">
              <TabsList className="mb-4">
                <TabsTrigger value="knowledge">Knowledge</TabsTrigger>
                <TabsTrigger value="tool">Tool</TabsTrigger>
                <TabsTrigger value="memory">Memory</TabsTrigger>
                <TabsTrigger value="skill">Skill</TabsTrigger>
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
            <DialogTitle>Create New App</DialogTitle>
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
            <Button
              type="submit"
              form="create-app-form"
              disabled={createAppMutation.isPending}
            >
              {createAppMutation.isPending ? 'Creating...' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
