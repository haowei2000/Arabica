import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun, ArrowLeft } from 'lucide-react';
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
      <header className="bg-card border-b border-border px-6 py-3 shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="icon" onClick={handleBackToWorkspaces} title="Back to workspaces">
              <ArrowLeft className="size-5" />
            </Button>
            <div>
              <h1 className="text-base font-semibold">Workspace</h1>
              <p className="text-sm text-muted-foreground">{currentWorkspaceName || currentWorkspaceId}</p>
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
