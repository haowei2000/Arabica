import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun, ArrowLeft, Bot, Home } from 'lucide-react';
import { authService } from '@/services/authService';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useUIStore } from '@/stores/useUIStore';
import WorkspaceConsole from '@/components/WorkspaceConsole';
import { Button } from '@/components/ui/button';

export default function ChatPage() {
  const navigate = useNavigate();
  const { currentWorkspaceId, currentWorkspaceName, clearCurrentWorkspace } = useWorkspaceStore();
  const { toggleTheme, theme } = useUIStore();
  const { reset } = useChatStore();

  useEffect(() => {
    if (!authService.isAuthenticated()) { navigate('/login'); return; }
    if (!currentWorkspaceId) navigate('/app');
  }, [navigate, currentWorkspaceId]);

  const handleLogout = () => { authService.logout(); reset(); clearCurrentWorkspace(); navigate('/login'); };
  const handleBackToWorkspaces = () => { reset(); clearCurrentWorkspace(); navigate('/app'); };
  const handleGoHome = () => navigate('/home');

  if (!currentWorkspaceId) return null;

  return (
    <div className="flex flex-col h-screen bg-background">
      <header className="bg-card/80 backdrop-blur-sm border-b border-border px-3 py-2 shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Button variant="ghost" size="icon" className="size-6" onClick={handleBackToWorkspaces} title="Back to workspaces">
              <ArrowLeft className="size-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="size-6" onClick={handleGoHome} title="Home">
              <Home className="size-3.5" />
            </Button>
            <div className="h-4 w-px bg-border mx-1" />
            <div className="w-6 h-6 rounded-md bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-sm shadow-primary-500/20">
              <Bot className="size-3 text-white" />
            </div>
            <div>
              <h1 className="text-sm font-semibold leading-tight">{currentWorkspaceName || 'Workspace'}</h1>
              {currentWorkspaceName && (
                <p className="text-[10px] text-muted-foreground font-mono">{currentWorkspaceId?.slice(0, 8)}</p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1">
            <Button type="button" variant="ghost" size="icon" className="size-6" onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
              {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
            <Button variant="ghost" size="sm" onClick={handleLogout}>Logout</Button>
          </div>
        </div>
      </header>

      <WorkspaceConsole />
    </div>
  );
}
