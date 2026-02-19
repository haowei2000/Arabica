import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun, ArrowLeft, Bot } from 'lucide-react';
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

  if (!currentWorkspaceId) return null;

  return (
    <div className="flex flex-col h-screen bg-background">
      <header className="bg-card/80 backdrop-blur-sm border-b border-border px-4 py-2.5 shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon" className="size-7" onClick={handleBackToWorkspaces} title="Back to workspaces">
              <ArrowLeft className="size-3.5" />
            </Button>
            <div className="h-4 w-px bg-border" />
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-sm shadow-primary-500/20">
              <Bot className="size-3.5 text-white" />
            </div>
            <div>
              <h1 className="text-sm font-semibold leading-tight">{currentWorkspaceName || 'Workspace'}</h1>
              {currentWorkspaceName && (
                <p className="text-[10px] text-muted-foreground font-mono">{currentWorkspaceId?.slice(0, 8)}</p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button type="button" variant="ghost" size="icon" onClick={toggleTheme}
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
              {theme === 'dark' ? <Sun className="size-5" /> : <Moon className="size-5" />}
            </Button>
            <Button variant="ghost" size="sm" onClick={handleLogout}>Logout</Button>
          </div>
        </div>
      </header>

      <WorkspaceConsole />
    </div>
  );
}
