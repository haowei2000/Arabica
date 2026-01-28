import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

interface WorkspaceState {
  currentWorkspaceId: string | null;
  currentWorkspaceName: string | null;
  currentWorkspaceAgentTemplateId: string | null;
  setCurrentWorkspace: (id: string, name: string, agentTemplateId?: string | null) => void;
  clearCurrentWorkspace: () => void;
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set) => ({
      currentWorkspaceId: null,
      currentWorkspaceName: null,
      currentWorkspaceAgentTemplateId: null,

      setCurrentWorkspace: (id, name, agentTemplateId = null) => {
        set({
          currentWorkspaceId: id,
          currentWorkspaceName: name,
          currentWorkspaceAgentTemplateId: agentTemplateId,
        });
      },

      clearCurrentWorkspace: () => {
        set({
          currentWorkspaceId: null,
          currentWorkspaceName: null,
          currentWorkspaceAgentTemplateId: null,
        });
      },
    }),
    {
      name: 'workspace-storage',
      storage: createJSONStorage(() => localStorage),
    }
  )
);
