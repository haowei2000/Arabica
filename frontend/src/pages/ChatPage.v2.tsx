import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun, ArrowLeft, LogOut, Zap } from 'lucide-react';
import { authService } from '@/services/authService';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useUIStore } from '@/stores/useUIStore';
import { Button } from '@/components/ui';
import WorkspaceConsole from '@/components/WorkspaceConsole.v2';

export default function ChatPage() {
  const navigate = useNavigate();

  const { currentWorkspaceId, currentWorkspaceName, clearCurrentWorkspace } = useWorkspaceStore();
  const { toggleTheme, theme } = useUIStore();
  const { reset } = useChatStore();

  useEffect(() => {
    if (!authService.isAuthenticated()) {
      navigate('/login');
      return;
    }

    if (!currentWorkspaceId) {
      navigate('/app');
    }
  }, [navigate, currentWorkspaceId]);

  const handleLogout = () => {
    authService.logout();
    reset();
    clearCurrentWorkspace();
    navigate('/login');
  };

  const handleBackToWorkspaces = () => {
    reset();
    clearCurrentWorkspace();
    navigate('/app');
  };

  if (!currentWorkspaceId) {
    return null;
  }

  return (
    <div className="flex flex-col h-screen">
      {/* Responsive Header */}
      <header className="glass border-b border-secondary-200/50 dark:border-navy-700/50 z-10">
        <div className="px-3 sm:px-4 md:px-6 py-3 md:py-4">
          <div className="flex items-center justify-between gap-2 sm:gap-4">
            {/* Left Section */}
            <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
              <Button
                onClick={handleBackToWorkspaces}
                variant="ghost"
                size="sm"
                icon={<ArrowLeft className="w-4 h-4 sm:w-5 sm:h-5" />}
                className="shrink-0"
                title="Back to workspaces"
              />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <div className="hidden sm:flex w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-600 items-center justify-center shadow-lg shadow-primary-500/30 shrink-0">
                    <Zap className="w-4 h-4 text-white" />
                  </div>
                  <div className="min-w-0">
                    <h1 className="text-sm sm:text-base md:text-lg font-bold text-navy-900 dark:text-navy-100 truncate">
                      Workspace
                    </h1>
                    <p className="text-xs sm:text-sm text-secondary-500 dark:text-secondary-400 truncate">
                      {currentWorkspaceName || currentWorkspaceId}
                    </p>
                  </div>
                </div>
              </div>
            </div>

            {/* Right Section */}
            <div className="flex items-center gap-1 sm:gap-2 shrink-0">
              <Button
                onClick={toggleTheme}
                variant="ghost"
                size="sm"
                icon={theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
                title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
                className="hidden sm:inline-flex"
              />
              <Button
                onClick={handleLogout}
                variant="ghost"
                size="sm"
                icon={<LogOut className="w-4 h-4" />}
                className="hidden md:inline-flex"
              >
                <span className="hidden lg:inline">Logout</span>
              </Button>
              {/* Mobile Menu Button */}
              <Button
                onClick={handleLogout}
                variant="ghost"
                size="sm"
                icon={<LogOut className="w-4 h-4" />}
                className="md:hidden"
              />
            </div>
          </div>
        </div>
      </header>

      <WorkspaceConsole />
    </div>
  );
}
