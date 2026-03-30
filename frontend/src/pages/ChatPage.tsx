import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun, ArrowLeft, Bot, Home, LogOut } from 'lucide-react';
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
    <div className="flex flex-col h-screen bg-background overflow-hidden">
      <header className="bg-card/80 backdrop-blur-sm border-b border-border px-4 py-3 shrink-0 sticky top-0 z-50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              <Button variant="ghost" size="icon" className="size-8 rounded-xl" onClick={handleBackToWorkspaces} title="Back to workspaces">
                <ArrowLeft className="size-4.5" />
              </Button>
              <Button variant="ghost" size="icon" className="size-8 rounded-xl" onClick={handleGoHome} title="Home">
                <Home className="size-4.5" />
              </Button>
            </div>
            <div className="h-6 w-px bg-border mx-1" />
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-md shadow-primary-500/20">
              <Bot className="size-4 text-white" />
            </div>
            <div>
              <h1 className="text-base font-bold text-foreground leading-tight tracking-tight">{currentWorkspaceName || 'Workspace'}</h1>
              {currentWorkspaceName && (
                <p className="text-[10px] text-muted-foreground font-mono">{currentWorkspaceId?.slice(0, 8)}</p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button type="button" variant="ghost" size="icon" className="size-8 rounded-xl" onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
              {theme === 'dark' ? <Sun className="size-4.5 text-muted-foreground" /> : <Moon className="size-4.5 text-muted-foreground" />}
            </Button>
            <Button variant="ghost" size="icon" className="size-8 rounded-xl" onClick={handleLogout} title="Logout">
              <LogOut className="size-4.5 text-muted-foreground" />
            </Button>
          </div>
        </div>
      </header>

      <div className="flex-1 min-h-0 flex flex-col">
        <WorkspaceConsole />
      </div>
    </div>
  );
}
