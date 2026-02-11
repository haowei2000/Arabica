import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun, Plus, Zap, Box, ArrowRight, LogOut } from 'lucide-react';
import { authService } from '@/services/authService';
import { useChatStore } from '@/stores/useChatStore';
import { useUIStore } from '@/stores/useUIStore';
import { useApps, useCreateApp, useTemplates } from '@/hooks/useApps';
import { useAppStore } from '@/stores/useAppStore';
import { Card, CardHeader, CardBody, Button, Input, Badge } from '@/components/ui';

import KnowledgePage from './context/KnowledgePage';
import ToolPage from './context/ToolPage';
import MemoryPage from './context/MemoryPage';
import SkillPage from './context/SkillPage';

type MainTab = 'app' | 'context';
type ContextTab = 'knowledge' | 'tool' | 'memory' | 'skill';

export default function HomePage() {
  const [mainTab, setMainTab] = useState<MainTab>('app');
  const [contextTab, setContextTab] = useState<ContextTab>('knowledge');
  const [showCreateAppForm, setShowCreateAppForm] = useState(false);
  const [appCode, setAppCode] = useState('');
  const [templateCode, setTemplateCode] = useState('');

  const navigate = useNavigate();
  const { data: appsData, isLoading: appsLoading } = useApps({
    page: 1,
    page_size: 50,
  });
  const { data: templatesData } = useTemplates();
  const createAppMutation = useCreateApp();
  const { setCurrentApp } = useAppStore();
  const { reset: resetChat } = useChatStore();
  const { toggleTheme, theme } = useUIStore();

  const handleCreateApp = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createAppMutation.mutateAsync({
        app_code: appCode,
        agent_template_code: templateCode || undefined,
        enabled: true,
      });
      setShowCreateAppForm(false);
      setAppCode('');
      setTemplateCode('');
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
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

  const renderContextContent = () => {
    switch (contextTab) {
      case 'knowledge':
        return <KnowledgePage />;
      case 'tool':
        return <ToolPage />;
      case 'memory':
        return <MemoryPage />;
      case 'skill':
        return <SkillPage />;
      default:
        return <KnowledgePage />;
    }
  };

  const renderAppContent = () => {
    if (appsLoading) {
      return (
        <div className="flex items-center justify-center py-20">
          <div className="relative">
            <div className="w-12 h-12 rounded-full border-4 border-primary-200 dark:border-primary-900/30 border-t-primary-500 animate-spin"></div>
            <div className="absolute inset-0 w-12 h-12 rounded-full border-4 border-transparent border-b-primary-400 dark:border-b-primary-600 animate-spin animation-delay-150"></div>
          </div>
        </div>
      );
    }

    return (
      <div className="space-y-6">
        {appsData?.items && appsData.items.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
            {appsData.items.map((app, index) => (
              <Card
                key={app.id}
                hover
                className="group"
                style={{ animationDelay: `${index * 50}ms` }}
              >
                <CardBody className="space-y-4">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                      <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center text-white shadow-lg shadow-primary-500/30 group-hover:scale-110 transition-transform">
                        <Box className="w-6 h-6" />
                      </div>
                      <div>
                        <h3 className="text-lg font-bold text-navy-900 dark:text-navy-100 group-hover:text-primary-600 dark:group-hover:text-primary-400 transition-colors">
                          {app.app_code}
                        </h3>
                        <p className="text-xs text-secondary-500 dark:text-secondary-400">
                          v{app.version}
                        </p>
                      </div>
                    </div>
                    <Badge variant={app.enabled ? 'success' : 'neutral'} size="sm" dot>
                      {app.enabled ? 'Active' : 'Inactive'}
                    </Badge>
                  </div>

                  <div className="flex items-center gap-2 text-xs text-secondary-500 dark:text-secondary-400">
                    <Zap className="w-3.5 h-3.5" />
                    <span>Created {new Date(app.created_at).toLocaleDateString()}</span>
                  </div>

                  <Button
                    onClick={() => handleOpenApp(app.id, app.app_code)}
                    variant="primary"
                    size="md"
                    className="w-full group-hover:shadow-2xl"
                    icon={<ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />}
                  >
                    Open Workspace
                  </Button>
                </CardBody>
              </Card>
            ))}
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-20 text-center animate-fade-in">
            <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-secondary-100 to-secondary-200 dark:from-secondary-900/30 dark:to-secondary-800/30 flex items-center justify-center mb-6">
              <Box className="w-10 h-10 text-secondary-400 dark:text-secondary-500" />
            </div>
            <h3 className="text-2xl font-bold text-navy-900 dark:text-navy-100 mb-2">
              No Apps Yet
            </h3>
            <p className="text-secondary-500 dark:text-secondary-400 mb-8 max-w-md">
              Create your first app to get started with AI-powered workspaces and collaboration
            </p>
            <Button
              onClick={() => setShowCreateAppForm(true)}
              variant="primary"
              size="lg"
              icon={<Plus className="w-5 h-5" />}
            >
              Create Your First App
            </Button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="min-h-screen">
      {/* Modern Header with Glass Effect */}
      <header className="sticky top-0 z-50 glass border-b border-secondary-200/50 dark:border-navy-700/50">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-lg shadow-primary-500/30">
                <Zap className="w-5 h-5 text-white" />
              </div>
              <div>
                <h1 className="text-xl font-bold bg-gradient-to-r from-primary-600 to-primary-500 dark:from-primary-400 dark:to-primary-300 bg-clip-text text-transparent">
                  AI Agent Platform
                </h1>
                <p className="text-xs text-secondary-500 dark:text-secondary-400">
                  Build, Deploy, Collaborate
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3">
              {appsData?.items && appsData.items.length > 0 && mainTab === 'app' && (
                <Button
                  onClick={() => setShowCreateAppForm(true)}
                  variant="primary"
                  size="sm"
                  icon={<Plus className="w-4 h-4" />}
                >
                  New App
                </Button>
              )}
              <Button
                onClick={toggleTheme}
                variant="ghost"
                size="sm"
                icon={theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
                title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
              >
              </Button>
              <Button
                onClick={handleLogout}
                variant="ghost"
                size="sm"
                icon={<LogOut className="w-4 h-4" />}
              >
                Logout
              </Button>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Modern Tabs */}
        <div className="mb-8">
          <div className="inline-flex items-center gap-2 p-1 rounded-xl bg-secondary-100/50 dark:bg-navy-800/50 backdrop-blur-sm border border-secondary-200/50 dark:border-navy-700/50">
            <button
              onClick={() => setMainTab('app')}
              className={`px-6 py-2.5 rounded-lg font-medium text-sm transition-all duration-200 ${
                mainTab === 'app'
                  ? 'bg-white dark:bg-navy-700 text-primary-600 dark:text-primary-400 shadow-md'
                  : 'text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100'
              }`}
            >
              Apps
            </button>
            <button
              onClick={() => setMainTab('context')}
              className={`px-6 py-2.5 rounded-lg font-medium text-sm transition-all duration-200 ${
                mainTab === 'context'
                  ? 'bg-white dark:bg-navy-700 text-primary-600 dark:text-primary-400 shadow-md'
                  : 'text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100'
              }`}
            >
              Context
            </button>
          </div>
        </div>

        {/* ContextSchema Sub-tabs */}
        {mainTab === 'context' && (
          <div className="mb-6 flex flex-wrap gap-2 animate-slide-in">
            {(['knowledge', 'tool', 'memory', 'skill'] as ContextTab[]).map((tab) => (
              <button
                key={tab}
                onClick={() => setContextTab(tab)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
                  contextTab === tab
                    ? 'bg-primary-500 text-white shadow-lg shadow-primary-500/30'
                    : 'bg-white dark:bg-navy-800 text-secondary-600 dark:text-secondary-400 hover:bg-secondary-50 dark:hover:bg-navy-700 border border-secondary-200 dark:border-navy-700'
                }`}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            ))}
          </div>
        )}

        {/* Content */}
        <div className="animate-fade-in">
          {mainTab === 'app' ? renderAppContent() : renderContextContent()}
        </div>
      </div>

      {/* Modern Modal for Create App */}
      {showCreateAppForm && (
        <div className="fixed inset-0 bg-navy-950/60 dark:bg-navy-950/80 backdrop-blur-sm flex items-center justify-center z-50 p-4 animate-fade-in">
          <Card className="max-w-md w-full animate-scale-in">
            <CardHeader className="space-y-1">
              <h3 className="text-2xl font-bold text-navy-900 dark:text-navy-100">
                Create New App
              </h3>
              <p className="text-sm text-secondary-500 dark:text-secondary-400">
                Set up a new AI-powered application
              </p>
            </CardHeader>

            <form onSubmit={handleCreateApp}>
              <CardBody className="space-y-5">
                <Input
                  label="App Code"
                  type="text"
                  value={appCode}
                  onChange={(e) => setAppCode(e.target.value)}
                  placeholder="e.g., product-agent"
                  required
                  helperText="Unique identifier for your app"
                />

                <div>
                  <label className="block text-sm font-medium text-secondary-700 dark:text-secondary-300 mb-2">
                    Agent Template (optional)
                  </label>
                  <select
                    value={templateCode}
                    onChange={(e) => setTemplateCode(e.target.value)}
                    className="input-modern w-full"
                  >
                    <option value="">No default template</option>
                    {templatesData?.map((template) => (
                      <option key={template.id} value={template.template_code}>
                        {template.template_name}
                      </option>
                    ))}
                  </select>
                </div>
              </CardBody>

              <div className="px-6 py-4 bg-secondary-50/50 dark:bg-navy-900/50 border-t border-secondary-100 dark:border-navy-700/60 flex gap-3 rounded-b-xl">
                <Button
                  type="button"
                  onClick={() => {
                    setShowCreateAppForm(false);
                    setAppCode('');
                    setTemplateCode('');
                  }}
                  variant="secondary"
                  className="flex-1"
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  loading={createAppMutation.isPending}
                  variant="primary"
                  className="flex-1"
                >
                  Create App
                </Button>
              </div>
            </form>
          </Card>
        </div>
      )}
    </div>
  );
}
