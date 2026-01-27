import { create } from 'zustand';
import {persist} from 'zustand/middleware';

type Theme = 'light' | 'dark' | 'system';

interface UIState {
  sidebarOpen: boolean;
    theme: Theme;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
    setTheme: (theme: Theme) => void;
    toggleTheme: () => void;
}

// Apply theme to document
const applyTheme = (theme: Theme) => {
    const root = document.documentElement;
    const isDark = theme === 'dark' ||
        (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

    if (isDark) {
        root.classList.add('dark');
    } else {
        root.classList.remove('dark');
    }
};

export const useUIStore = create<UIState>()(
    persist(
        (set, get) => ({
            sidebarOpen: true,
            theme: 'system',

            toggleSidebar: () =>
                set((state) => ({sidebarOpen: !state.sidebarOpen})),

            setSidebarOpen: (open) =>
                set({sidebarOpen: open}),

            setTheme: (theme) => {
                applyTheme(theme);
                set({theme});
            },

            toggleTheme: () => {
                const currentTheme = get().theme;
                const newTheme: Theme = currentTheme === 'light' ? 'dark' : 'light';
                applyTheme(newTheme);
                set({theme: newTheme});
            },
        }),
        {
            name: 'ui-storage',
            onRehydrateStorage: () => (state) => {
                if (state) {
                    applyTheme(state.theme);
                }
            },
        }
    )
);

// Listen for system theme changes
if (typeof window !== 'undefined') {
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        const state = useUIStore.getState();
        if (state.theme === 'system') {
            applyTheme('system');
        }
    });
}
