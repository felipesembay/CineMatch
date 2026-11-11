import { useSyncExternalStore } from 'react';
import { api, ApiError } from './api';
import type { User } from './types';

// Sessão real: cookie httpOnly emitido pelo FastAPI (o token nunca fica acessível ao JS).
// Aqui guardamos só o perfil público do usuário logado.
type State = { user: User | null; ready: boolean };

let state: State = { user: null, ready: false };
const listeners = new Set<() => void>();
const set = (next: Partial<State>) => {
  state = { ...state, ...next };
  listeners.forEach(fn => fn());
};

export const auth = {
  getState: () => state,
  getUser: () => state.user,
  subscribe: (fn: () => void) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
  /** Restaura a sessão a partir do cookie (chamado na inicialização). */
  async refresh() {
    try {
      set({ user: await api.me(), ready: true });
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 401)) console.warn('auth.refresh', e);
      set({ user: null, ready: true });
    }
    return state.user;
  },
  async login(email: string, password: string) {
    const user = await api.login(email, password);
    set({ user });
    return user;
  },
  async register(email: string, password: string, name?: string) {
    const user = await api.register(email, password, name);
    set({ user });
    return user;
  },
  async logout() {
    try {
      await api.logout();
    } finally {
      set({ user: null });
    }
  },
  async deleteAccount() {
    await api.deleteAccount();
    set({ user: null });
  },
};

export const useAuth = () => useSyncExternalStore(auth.subscribe, auth.getUser, auth.getUser);
export const useAuthReady = () =>
  useSyncExternalStore(auth.subscribe, () => state.ready, () => state.ready);
