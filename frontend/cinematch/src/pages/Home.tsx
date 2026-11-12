import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { ArrowRight, Heart, Play, Sparkles, Star } from 'lucide-react';
import { api } from '../lib/api';
import { useAuth } from '../lib/auth';
import { Poster, ErrorState } from '../components/UI';
import { TitleRow } from '../components/TitleRow';
import { track } from '../lib/events';
import { useT } from '../lib/i18n';

const HOME_GENRES = ['Science Fiction', 'Drama', 'Thriller', 'Animation', 'Comedy'];

function GenreRow({ genre }: { genre: string }) {
  const { genre: label } = useT();
  const q = useQuery({
    queryKey: ['movies', 'genre', genre],
    queryFn: () => api.movies({ genre, page_size: 20 }),
  });
  return (
    <TitleRow
      title={label(genre)}
      items={q.data?.items}
      loading={q.isPending}
      error={q.isError}
      retry={() => q.refetch()}
    />
  );
}

const clip = (text: string, max = 260) =>
  text.length > max ? `${text.slice(0, max - 3).trimEnd()}…` : text;

/** Destaque: recomendado mais bem pontuado (logado com perfil) ou rodízio semanal (visitante). */
function Hero() {
  const user = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const qc = useQueryClient();
  const { t, genre, reason } = useT();
  const featured = useQuery({ queryKey: ['featured', user?.id ?? null], queryFn: api.featured });
  const fav = useQuery({ queryKey: ['favorites'], queryFn: api.favorites, enabled: !!user });
  const hero = featured.data?.item;
  const isFav = !!hero && !!fav.data?.some(x => x.id === hero.id);
  const meta = {
    item_id: hero?.id,
    position: 0,
    request_id: featured.data?.request_id,
    model_version: featured.data?.model_version,
  };
  // Favoritar o destaque não invalida `featured`: o título não troca na frente do usuário.
  const add = useMutation({
    mutationFn: () => (isFav ? api.removeFavorite(hero!.id) : api.addFavorite(hero!.id)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['favorites'] });
      qc.invalidateQueries({ queryKey: ['recommendations'] });
      track(isFav ? 'favorite_remove' : 'favorite_add', 'home_row', meta);
    },
  });

  if (featured.isPending)
    return <div className="skeleton min-h-[460px] md:min-h-[590px]" aria-hidden="true" />;
  if (featured.isError || !hero)
    return (
      <div className="mx-auto max-w-6xl px-5 pt-24">
        <ErrorState onRetry={() => featured.refetch()} />
      </div>
    );
  const personal = featured.data.kind === 'personal';
  const why = personal ? reason(hero) : null;
  return (
    <div className="relative min-h-[460px] overflow-hidden md:min-h-[590px]">
      <Poster
        title={hero.title}
        id={hero.id}
        url={hero.backdrop_url}
        wide
        showTitle={false}
        className="absolute inset-0 h-full w-full opacity-70"
      />
      <div className="absolute inset-0 bg-gradient-to-r from-background via-background/75 to-transparent" />
      <div className="absolute inset-0 bg-gradient-to-t from-background via-transparent to-transparent" />
      <div className="relative mx-auto flex min-h-[460px] max-w-[1500px] flex-col justify-center px-5 pb-10 pt-16 md:min-h-[590px] md:px-10">
        <span className="mb-5 inline-flex w-fit items-center gap-1.5 rounded border border-amber/40 bg-amber/10 px-3 py-1 text-[11px] font-bold uppercase tracking-[.2em] text-amber">
          {personal && <Sparkles size={12} />}
          {personal ? t('home.personal') : t('home.weekly')}
        </span>
        <h1 className="max-w-2xl font-display text-4xl font-extrabold leading-tight md:text-6xl">
          {hero.title}
        </h1>
        <div className="mt-5 flex flex-wrap items-center gap-3 text-sm text-ink/80">
          <span className="flex items-center gap-1 text-amber">
            <Star size={16} fill="currentColor" />
            {hero.vote_average?.toFixed(1)}
          </span>
          <span>•</span>
          <span>{hero.year}</span>
          <span>•</span>
          <span>{hero.genres.map(genre).join(' · ')}</span>
        </div>
        {why && <p className="mt-3 text-sm font-medium text-amber">{why}</p>}
        <p className="mt-5 max-w-xl text-sm leading-7 text-ink/75 md:text-base">
          {featured.data.overview ? clip(featured.data.overview) : ''}
        </p>
        <div className="mt-7 flex flex-wrap gap-3">
          <button
            className="btn btn-primary"
            onClick={() => {
              track('click', 'home_row', meta);
              nav(`/title/${hero.id}`, { state: { backgroundLocation: loc } });
            }}
          >
            <Play size={18} fill="currentColor" />
            {t('home.viewDetails')}
          </button>
          {user ? (
            <button className="btn btn-secondary" onClick={() => add.mutate()}>
              <Heart size={17} fill={isFav ? 'currentColor' : 'none'} />
              {isFav ? t('home.inList') : t('home.addList')}
            </button>
          ) : (
            <Link to="/login" className="btn btn-secondary">
              <Heart size={17} />
              {t('home.addList')}
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}

export function Home() {
  const user = useAuth();
  const { t, reason } = useT();
  const popular = useQuery({
    queryKey: ['movies', 'popular'],
    queryFn: () => api.movies({ sort: 'popular', page_size: 20 }),
  });
  const top = useQuery({
    queryKey: ['movies', 'top'],
    queryFn: () => api.movies({ sort: 'top_rated', page_size: 20 }),
  });
  const rec = useQuery({
    queryKey: ['recommendations'],
    queryFn: () => api.recommendations(),
    enabled: !!user,
  });
  return (
    <>
      <Hero />
      <div className="mx-auto max-w-[1500px] px-5 pt-5 md:px-10">
        <TitleRow
          title={t('home.trending')}
          items={popular.data?.items}
          loading={popular.isPending}
          error={popular.isError}
          retry={() => popular.refetch()}
        />
        <TitleRow
          title={t('home.topRated')}
          items={top.data?.items}
          loading={top.isPending}
          error={top.isError}
          retry={() => top.refetch()}
        />
        {user ? (
          <TitleRow
            title={t('home.forYou')}
            subtitle={rec.data?.fallback ? t('home.forYouFallback') : t('home.forYouSub')}
            items={rec.data?.items}
            loading={rec.isPending}
            error={rec.isError}
            retry={() => rec.refetch()}
            surface="feed"
            requestId={rec.data?.request_id}
            modelVersion={rec.data?.model_version}
            reasons={Object.fromEntries((rec.data?.items || []).map(i => [i.id, reason(i)]))}
          />
        ) : (
          <div className="mb-10 flex flex-col justify-between gap-4 rounded-2xl border border-amber/20 bg-gradient-to-r from-amber/10 to-surface p-6 md:flex-row md:items-center md:p-8">
            <div>
              <span className="text-xs font-bold uppercase tracking-widest text-amber">
                {t('home.ctaKicker')}
              </span>
              <h2 className="mt-2 font-display text-xl font-bold">{t('home.ctaTitle')}</h2>
              <p className="mt-2 text-sm text-muted">{t('home.ctaText')}</p>
            </div>
            <Link to="/login" className="btn btn-primary shrink-0">
              {t('home.ctaButton')} <ArrowRight size={17} />
            </Link>
          </div>
        )}
        {HOME_GENRES.map(g => (
          <GenreRow key={g} genre={g} />
        ))}
      </div>
    </>
  );
}
