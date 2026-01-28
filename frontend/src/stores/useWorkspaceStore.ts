import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

interface WorkspaceState {
  currentWorkspaceId: string | null;
  currentWorkspaceName: string | null;
  currentWorkspaceAppId: string | null;
  setCurrentWorkspace: (id: string, name: string, appId?: string | null) => void;
  clearCurrentWorkspace: () => void;
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set) => ({
      currentWorkspaceId: null,
      currentWorkspaceName: null,
      currentWorkspaceAppId: null,

      setCurrentWorkspace: (id, name, appId = null) => {
        set({
          currentWorkspaceId: id,
          currentWorkspaceName: name,
          currentWorkspaceAppId: appId,
        });
      },

      clearCurrentWorkspace: () => {
        set({
          currentWorkspaceId: null,
          currentWorkspaceName: null,
          currentWorkspaceAppId: null,
        });
      },
    }),
    {
      name: 'workspace-storage',
      storage: createJSONStorage(() => localStorage),
    }
  )
);
