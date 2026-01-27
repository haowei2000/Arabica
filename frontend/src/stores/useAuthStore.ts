import { create } from 'zustand';
import type { User } from '@/types/auth';

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  setUser: (user: User | null) => void;
  clearAuth: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  isAuthenticated: !!localStorage.getItem('access_token'),

  setUser: (user) =>
    set({
      user,
      isAuthenticated: !!user,
    }),

  clearAuth: () =>
    set({
      user: null,
      isAuthenticated: false,
    }),
}));
