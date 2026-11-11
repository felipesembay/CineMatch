// TEMPORÁRIO (até a Fase 4 do backend): rotas de usuário em memória, com itens REAIS da API.
// Catálogo e similares já vêm do FastAPI; preferências, favoritos, avaliações e eventos ainda
// não têm endpoint. Nada aqui é persistido nem enviado ao servidor.
import type { ItemSummary, Preferences, RecommendationList } from './types';

type Fetcher = (path: string) => Promise<unknown>;

const store: { favorites: number[]; preferences: Preferences; ratings: Record<number, number> } = {
  favorites: [],
  preferences: { liked: [], disliked: [], genres: [] },
  ratings: {},
};

const toSummary = (d: ItemSummary): ItemSummary => ({
  id: d.id, type: d.type, title: d.title, year: d.year, poster_url: d.poster_url,
  backdrop_url: d.backdrop_url, genres: d.genres, vote_average: d.vote_average,
  vote_count: d.vote_count,
});

async function recommendations(get: Fetcher, k: number): Promise<RecommendationList> {
  const { liked, disliked, genres } = store.preferences;
  const exclude = new Set([...liked, ...disliked, ...store.favorites]);
  const seeds = [...liked, ...store.favorites.filter(id => !liked.includes(id))].slice(0, 6);
  if (seeds.length) {
    // Intercala os similares de cada título curtido (rodízio), sem repetir.
    const lists = (await Promise.all(
      seeds.map(id => get(`/movies/${id}/similar?k=${k}`)),
    )) as RecommendationList[];
    const seen = new Set<number>();
    const items: RecommendationList['items'] = [];
    for (let rank = 0; rank < k && items.length < k; rank++) {
      for (const list of lists) {
        const it = list.items[rank];
        if (it && !seen.has(it.id) && !exclude.has(it.id)) {
          seen.add(it.id);
          items.push(it);
        }
      }
    }
    return {
      request_id: lists[0]?.request_id ?? crypto.randomUUID(),
      model_version: `client-stub:merge(${lists[0]?.model_version ?? 'similar'})`,
      fallback: false,
      items: items.slice(0, k),
    };
  }
  const genre = genres[0] ? `&genre=${encodeURIComponent(genres[0])}` : '';
  const page = (await get(`/movies?sort=top_rated&page_size=${k}${genre}`)) as {
    items: ItemSummary[];
  };
  return {
    request_id: crypto.randomUUID(),
    model_version: 'client-stub:top_rated',
    fallback: true,
    items: page.items
      .filter(i => !exclude.has(i.id))
      .map(i => ({ ...i, score: 0, reason: genres[0] ? `Popular em ${genres[0]}` : null })),
  };
}

export const USER_ROUTE = /^\/(recommendations\/me|users\/me\/|ratings$|events$)/;

export async function userStub(
  method: string, path: string, body: unknown, get: Fetcher,
): Promise<unknown> {
  const url = new URL(path, 'http://stub.local');
  const p = url.pathname;
  if (p === '/recommendations/me') return recommendations(get, Number(url.searchParams.get('k') || 20));
  if (p === '/users/me/preferences') {
    if (method === 'PUT') store.preferences = body as Preferences;
    return { ...store.preferences };
  }
  if (p === '/users/me/favorites') {
    if (method === 'PUT') store.favorites = (body as { ids?: number[] }).ids ?? [];
    const items = await Promise.all(store.favorites.map(id => get(`/movies/${id}`)));
    return (items as ItemSummary[]).map(toSummary);
  }
  const fav = p.match(/^\/users\/me\/favorites\/(\d+)$/);
  if (fav) {
    const id = Number(fav[1]);
    if (method === 'DELETE') store.favorites = store.favorites.filter(x => x !== id);
    else if (!store.favorites.includes(id)) store.favorites.push(id);
    return { ok: true };
  }
  if (p === '/ratings') {
    const { item_id, value } = body as { item_id: number; value: number };
    store.ratings[item_id] = value;
    return { ok: true };
  }
  if (p === '/events') {
    if (import.meta.env.DEV) console.debug('[events stub]', (body as { events: unknown[] }).events);
    return { ok: true };
  }
  throw Error(`Rota de usuário sem stub: ${method} ${p}`);
}
