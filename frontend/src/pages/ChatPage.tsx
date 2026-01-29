import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Moon, Sun } from 'lucide-react';
import { authService } from '@/services/authService';
import { useChatStore } from '@/stores/useChatStore';
import { useWorkspaceStore } from '@/stores/useWorkspaceStore';
import { useUIStore } from '@/stores/useUIStore';
import WorkspaceConsole from '@/components/WorkspaceConsole';

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
    <div className="flex flex-col h-screen bg-navy-50 dark:bg-navy-950">
      <header className="bg-white dark:bg-navy-900 border-b border-secondary-200 dark:border-navy-700 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={handleBackToWorkspaces}
              className="text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100"
              title="Back to workspaces"
            >
              <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M10 19l-7-7m0 0l7-7m-7 7h18"
                />
              </svg>
            </button>
            <div>
              <h1 className="text-xl font-semibold text-navy-900 dark:text-navy-100">Workspace</h1>
              <p className="text-sm text-secondary-500 dark:text-secondary-400">
                {currentWorkspaceName || currentWorkspaceId}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <button
              onClick={toggleTheme}
              className="p-2 text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100 rounded-lg hover:bg-navy-100 dark:hover:bg-navy-800"
              title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              {theme === 'dark' ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
            </button>
            <button
              onClick={handleLogout}
              className="text-sm text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100"
            >
              Logout
            </button>
          </div>
        </div>
      </header>

      <WorkspaceConsole />
    </div>
  );
}
