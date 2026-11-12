import { useEffect, useState } from 'react';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Search as SearchIcon, SlidersHorizontal } from 'lucide-react';
import { api } from '../lib/api';
import { track } from '../lib/events';
import { TitleCard } from '../components/TitleCard';
import { ErrorState, SkeletonCards } from '../components/UI';
import { useT } from '../lib/i18n';
export function Search() {
  const { t, genre: genreLabel, number } = useT();
  const [q, setQ] = useState('');
  const [debounced, setDebounced] = useState('');
  const [type, setType] = useState('');
  const [genre, setGenre] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [sort, setSort] = useState('popular');
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    if (debounced) track('search', 'search', { query: debounced });
  }, [debounced]);
  const genres = useQuery({ queryKey: ['genres'], queryFn: api.genres });
  const active = !!(debounced || type || genre || from || to);
  const results = useInfiniteQuery({
    queryKey: ['search', debounced, type, genre, from, to, sort],
    queryFn: ({ pageParam }) =>
      api.movies({
        q: debounced,
        type,
        genre,
        year_from: from ? Number(from) : undefined,
        year_to: to ? Number(to) : undefined,
        sort,
        page: pageParam,
        page_size: 16,
      }),
    initialPageParam: 1,
    getNextPageParam: last => (last.page * last.page_size < last.total ? last.page + 1 : undefined),
    enabled: active,
  });
  const items = results.data?.pages.flatMap(p => p.items) || [];
  const fetchMore = results.fetchNextPage;
  return (
    <div className="mx-auto min-h-[65vh] max-w-[1500px] px-5 py-12 md:px-10">
      <div className="mb-8">
        <span className="text-xs font-bold uppercase tracking-widest text-amber">
          {t('search.kicker')}
        </span>
        <h1 className="mt-2 font-display text-3xl font-bold md:text-4xl">{t('search.title')}</h1>
        <p className="mt-3 text-sm text-muted">{t('search.subtitle')}</p>
      </div>
      <div className="mb-7 flex items-center gap-3 rounded-xl border border-fg/15 bg-surface px-4 focus-within:border-focus">
        <SearchIcon className="text-muted" />
        <input
          aria-label={t('search.input')}
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder={t('search.placeholder')}
          className="w-full bg-transparent py-4 text-base outline-none placeholder:text-muted"
        />
      </div>
      <div className="mb-9 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <label className="text-xs text-muted">
          {t('search.type')}
          <select className="field mt-1" value={type} onChange={e => setType(e.target.value)}>
            <option value="">{t('search.all')}</option>
            <option value="movie">{t('search.movies')}</option>
            <option value="tv">{t('search.series')}</option>
          </select>
        </label>
        <label className="text-xs text-muted">
          {t('search.genre')}
          <select className="field mt-1" value={genre} onChange={e => setGenre(e.target.value)}>
            <option value="">{t('search.allGenres')}</option>
            {genres.data?.map(g => (
              <option key={g.id} value={g.name}>
                {genreLabel(g.name)}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-muted">
          {t('search.yearFrom')}
          <input
            className="field mt-1"
            type="number"
            min="1900"
            max="2100"
            placeholder={t('search.example', { year: 2015 })}
            value={from}
            onChange={e => setFrom(e.target.value)}
          />
        </label>
        <label className="text-xs text-muted">
          {t('search.yearTo')}
          <input
            className="field mt-1"
            type="number"
            min="1900"
            max="2100"
            placeholder={t('search.example', { year: 2026 })}
            value={to}
            onChange={e => setTo(e.target.value)}
          />
        </label>
        <label className="text-xs text-muted">
          {t('search.sort')}
          <select className="field mt-1" value={sort} onChange={e => setSort(e.target.value)}>
            <option value="popular">{t('search.sortPopular')}</option>
            <option value="top_rated">{t('search.sortTop')}</option>
            <option value="recent">{t('search.sortRecent')}</option>
          </select>
        </label>
      </div>
      {!active ? (
        <div className="rounded-2xl border border-dashed border-fg/15 py-20 text-center">
          <SlidersHorizontal size={34} className="mx-auto text-amber" />
          <h2 className="mt-4 font-display text-xl font-semibold">{t('search.startTitle')}</h2>
          <p className="mt-2 text-sm text-muted">{t('search.startText')}</p>
        </div>
      ) : results.isPending ? (
        <SkeletonCards count={8} />
      ) : results.isError ? (
        <ErrorState onRetry={() => results.refetch()} />
      ) : items.length === 0 ? (
        <div className="py-20 text-center">
          <h2 className="font-display text-xl font-semibold">{t('search.noneTitle')}</h2>
          <p className="mt-2 text-sm text-muted">{t('search.noneText')}</p>
        </div>
      ) : (
        <>
          <p className="mb-5 text-sm text-muted">
            {t('search.found', { count: number(results.data?.pages[0].total ?? 0) })}
          </p>
          <div className="grid grid-cols-2 justify-items-start gap-x-4 gap-y-8 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-7">
            {items.map((item, i) => (
              <TitleCard key={item.id} item={item} surface="search" position={i} />
            ))}
          </div>
          {results.hasNextPage && (
            <div className="mt-10 text-center">
              <button
                className="btn btn-secondary"
                disabled={results.isFetchingNextPage}
                onClick={() => void fetchMore()}
              >
                {results.isFetchingNextPage ? t('search.loading') : t('search.more')}
              </button>
            </div>
          )}
          {results.isFetchNextPageError && <ErrorState onRetry={() => void fetchMore()} />}
        </>
      )}
    </div>
  );
}
