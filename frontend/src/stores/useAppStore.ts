import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

interface AppState {
  currentAppId: string | null;
  currentAppCode: string | null;
  setCurrentApp: (id: string, code: string) => void;
  clearCurrentApp: () => void;
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      currentAppId: null,
      currentAppCode: null,

      setCurrentApp: (id, code) => {
        set({
          currentAppId: id,
          currentAppCode: code,
        });
      },

      clearCurrentApp: () => {
        set({
          currentAppId: null,
          currentAppCode: null,
        });
      },
    }),
    {
      name: 'app-storage',
      storage: createJSONStorage(() => localStorage),
    }
  )
);
