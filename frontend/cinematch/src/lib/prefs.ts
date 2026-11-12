import { useSyncExternalStore } from 'react';

// Preferências de exibição do visitante (idioma e tema). Ficam só neste navegador:
// localStorage pode falhar (aba anônima, bloqueio), então toda leitura/escrita é protegida.
export type Lang = 'pt-BR' | 'en';
export type Theme = 'dark' | 'light';

const LANG_KEY = 'cm.lang';
const THEME_KEY = 'cm.theme';

const read = (key: string): string | null => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};
const write = (key: string, value: string) => {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* sem armazenamento: vale só nesta visita */
  }
};

const initialLang = (): Lang => {
  const saved = read(LANG_KEY);
  if (saved === 'pt-BR' || saved === 'en') return saved;
  return typeof navigator !== 'undefined' && !navigator.language.toLowerCase().startsWith('pt')
    ? 'en'
    : 'pt-BR';
};
const initialTheme = (): Theme => {
  const saved = read(THEME_KEY);
  if (saved === 'dark' || saved === 'light') return saved;
  return typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-color-scheme: light)').matches
    ? 'light'
    : 'dark';
};

type State = { lang: Lang; theme: Theme };
let state: State = { lang: initialLang(), theme: initialTheme() };
const listeners = new Set<() => void>();

function apply() {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.lang = state.lang;
  root.dataset.theme = state.theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute('content', state.theme === 'dark' ? '#0E0F13' : '#F7F5F1');
}
apply();

const set = (next: Partial<State>) => {
  state = { ...state, ...next };
  apply();
  listeners.forEach(fn => fn());
};

export const prefs = {
  get: () => state,
  subscribe: (fn: () => void) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  },
  setLang(lang: Lang) {
    write(LANG_KEY, lang);
    set({ lang });
  },
  setTheme(theme: Theme) {
    write(THEME_KEY, theme);
    set({ theme });
  },
};

export const useLang = () =>
  useSyncExternalStore(
    prefs.subscribe,
    () => state.lang,
    () => state.lang,
  );
export const useTheme = () =>
  useSyncExternalStore(
    prefs.subscribe,
    () => state.theme,
    () => state.theme,
  );
