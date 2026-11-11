import { z } from 'zod';
import { mockRequest } from '../mocks/handlers';
import {
  eventAckSchema,
  favoritesSchema,
  genreSchema,
  itemDetailSchema,
  itemSummarySchema,
  pageSchema,
  providersSchema,
  preferencesSchema,
  ratingsSchema,
  recommendationListSchema,
  userSchema,
} from './types';
import type { Preferences, TrackingEvent } from './types';

// Em dev, o Vite faz proxy de /api para o FastAPI (mesma origem: cookie de sessão first-party).
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';
export const USE_MOCKS = import.meta.env.VITE_USE_MOCKS === 'true';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const okSchema = z.object({ ok: z.boolean() }).passthrough();

async function request<T>(
  method: string,
  path: string,
  schema: z.ZodType<T>,
  body?: unknown,
): Promise<T> {
  let raw: unknown;
  if (USE_MOCKS) {
    raw = await mockRequest(method, path, body);
  } else {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-Request-ID': crypto.randomUUID() },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      const detail = await response
        .json()
        .then((j: { detail?: unknown }) => (typeof j.detail === 'string' ? j.detail : null))
        .catch(() => null);
      throw new ApiError(detail ?? `Erro HTTP ${response.status}`, response.status);
    }
    raw = response.status === 204 ? { ok: true } : await response.json();
  }
  return schema.parse(raw);
}

const qs = (params: Record<string, string | number | undefined>) => {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== '') u.set(k, String(v));
  });
  return u.toString();
};

export type MovieFilters = {
  q?: string;
  type?: string;
  genre?: string;
  year_from?: number;
  year_to?: number;
  sort?: string;
  page?: number;
  page_size?: number;
};

export const api = {
  // catálogo
  movies: (p: MovieFilters = {}) =>
    request('GET', `/movies?${qs(p)}`, pageSchema(itemSummarySchema)),
  genres: () => request('GET', '/genres', genreSchema),
  detail: (id: number) => request('GET', `/movies/${id}`, itemDetailSchema),
  similar: (id: number, k = 12) =>
    request('GET', `/movies/${id}/similar?k=${k}`, recommendationListSchema),

  // conta
  register: (email: string, password: string, name?: string) =>
    request('POST', '/auth/register', userSchema, { email, password, name: name || undefined }),
  login: (email: string, password: string) =>
    request('POST', '/auth/login', userSchema, { email, password }),
  logout: () => request('POST', '/auth/logout', okSchema),
  me: () => request('GET', '/auth/me', userSchema),
  providers: () => request('GET', '/auth/providers', providersSchema),
  /** Navegação completa (não fetch): o backend redireciona para o Google e volta. */
  googleLoginUrl: (next = '/my-list') =>
    `${API_BASE_URL}/auth/google/login?${new URLSearchParams({ next })}`,
  deleteAccount: () => request('DELETE', '/users/me', okSchema),

  // personalização
  recommendations: (k = 20) =>
    request('GET', `/recommendations/me?k=${k}`, recommendationListSchema),
  preferences: () => request('GET', '/users/me/preferences', preferencesSchema),
  savePreferences: (p: Preferences) =>
    request('PUT', '/users/me/preferences', preferencesSchema, p),
  favorites: () => request('GET', '/users/me/favorites', favoritesSchema),
  addFavorite: (id: number) => request('PUT', `/users/me/favorites/${id}`, okSchema),
  removeFavorite: (id: number) => request('DELETE', `/users/me/favorites/${id}`, okSchema),
  ratings: () => request('GET', '/users/me/ratings', ratingsSchema),
  rate: (item_id: number, value: number, comment?: string | null) =>
    request('POST', '/ratings', okSchema, { item_id, value, comment: comment || null }),
  deleteRating: (item_id: number) => request('DELETE', `/ratings/${item_id}`, okSchema),

  // eventos
  events: (events: TrackingEvent[]) => request('POST', '/events', eventAckSchema, { events }),
};
