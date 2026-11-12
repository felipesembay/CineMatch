import { useState } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import {
  ArrowRight,
  Heart,
  MessageSquareText,
  Sparkles,
  Star,
  ThumbsUp,
  Trash2,
} from 'lucide-react';
import { api } from '../lib/api';
import { auth, useAuth, useAuthReady } from '../lib/auth';
import { useT } from '../lib/i18n';
import type { ItemSummary, Rating } from '../lib/types';
import { TitleCard } from '../components/TitleCard';
import { TitleRow } from '../components/TitleRow';
import { ErrorState, Loading, Poster, SkeletonCards } from '../components/UI';

type Tab = 'favorites' | 'ratings' | 'liked';

export function MyList() {
  const user = useAuth();
  const ready = useAuthReady();
  const { t, reason } = useT();
  const [tab, setTab] = useState<Tab>('favorites');
  const favorites = useQuery({ queryKey: ['favorites'], queryFn: api.favorites, enabled: !!user });
  const ratings = useQuery({ queryKey: ['ratings'], queryFn: api.ratings, enabled: !!user });
  const prefs = useQuery({ queryKey: ['preferences'], queryFn: api.preferences, enabled: !!user });
  const recs = useQuery({
    queryKey: ['recommendations'],
    queryFn: () => api.recommendations(20),
    enabled: !!user,
  });
  const liked = useQueries({
    queries: (prefs.data?.liked ?? []).map(id => ({
      queryKey: ['detail', id],
      queryFn: () => api.detail(id),
    })),
  });

  if (!ready) return <Loading />;
  if (!user) return <Navigate to="/login" replace />;

  const likedItems = liked.flatMap(q => (q.data ? [q.data as ItemSummary] : []));
  const tabs: { id: Tab; label: string; count?: number; icon: React.ReactNode }[] = [
    {
      id: 'favorites',
      label: t('list.favorites'),
      count: favorites.data?.length,
      icon: <Heart size={15} />,
    },
    {
      id: 'ratings',
      label: t('list.ratings'),
      count: ratings.data?.length,
      icon: <MessageSquareText size={15} />,
    },
    {
      id: 'liked',
      label: t('list.liked'),
      count: prefs.data?.liked.length,
      icon: <ThumbsUp size={15} />,
    },
  ];

  return (
    <div className="mx-auto min-h-[65vh] max-w-[1500px] px-5 py-12 md:px-10">
      <span className="text-xs font-bold uppercase tracking-widest text-amber">
        {t('list.kicker')}
      </span>
      <h1 className="mt-2 font-display text-3xl font-bold md:text-4xl">{t('list.title')}</h1>
      <p className="mb-10 mt-3 text-sm text-muted">{t('list.subtitle')}</p>

      <TitleRow
        title={t('list.recs')}
        subtitle={recs.data?.fallback ? t('list.recsFallback') : t('list.recsSub')}
        items={recs.data?.items}
        loading={recs.isPending}
        error={recs.isError}
        retry={() => recs.refetch()}
        surface="my_list"
        requestId={recs.data?.request_id}
        modelVersion={recs.data?.model_version}
        reasons={Object.fromEntries((recs.data?.items ?? []).map(i => [i.id, reason(i)]))}
      />

      <div role="tablist" aria-label={t('list.tabs')} className="mb-8 mt-4 flex flex-wrap gap-2">
        {tabs.map(t => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm transition-colors ${
              tab === t.id
                ? 'border-amber bg-amber/15 text-amber'
                : 'border-fg/15 text-muted hover:bg-raised hover:text-ink'
            }`}
          >
            {t.icon}
            {t.label}
            {t.count !== undefined && <span className="text-xs opacity-70">{t.count}</span>}
          </button>
        ))}
      </div>

      <section role="tabpanel">
        {tab === 'favorites' && (
          <ItemGrid
            loading={favorites.isPending}
            error={favorites.isError}
            retry={() => favorites.refetch()}
            items={favorites.data}
            empty={t('list.emptyFav')}
          />
        )}
        {tab === 'ratings' && (
          <RatingsList
            loading={ratings.isPending}
            error={ratings.isError}
            retry={() => ratings.refetch()}
            ratings={ratings.data}
          />
        )}
        {tab === 'liked' && (
          <ItemGrid
            loading={prefs.isPending || liked.some(q => q.isPending)}
            error={prefs.isError}
            retry={() => prefs.refetch()}
            items={likedItems}
            empty={t('list.emptyLiked')}
            action={
              <Link to="/onboarding" className="btn btn-secondary mt-6">
                {t('list.redo')}
              </Link>
            }
          />
        )}
      </section>

      <DangerZone />
    </div>
  );
}

function ItemGrid({
  loading,
  error,
  retry,
  items,
  empty,
  action,
}: {
  loading: boolean;
  error: boolean;
  retry: () => void;
  items?: ItemSummary[];
  empty: string;
  action?: React.ReactNode;
}) {
  if (loading) return <SkeletonCards />;
  if (error) return <ErrorState onRetry={retry} />;
  if (!items?.length) return <Empty text={empty} action={action} />;
  return (
    <div className="grid grid-cols-2 justify-items-start gap-5 sm:grid-cols-3 md:grid-cols-5 lg:grid-cols-6">
      {items.map((item, i) => (
        <TitleCard key={item.id} item={item} surface="my_list" position={i} />
      ))}
    </div>
  );
}

function RatingsList({
  loading,
  error,
  retry,
  ratings,
}: {
  loading: boolean;
  error: boolean;
  retry: () => void;
  ratings?: Rating[];
}) {
  const qc = useQueryClient();
  const nav = useNavigate();
  const location = useLocation();
  const { t, date } = useT();
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteRating(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ratings'] });
      qc.invalidateQueries({ queryKey: ['recommendations'] });
    },
  });
  if (loading) return <SkeletonCards />;
  if (error) return <ErrorState onRetry={retry} />;
  if (!ratings?.length) return <Empty text={t('list.emptyRatings')} />;
  return (
    <ul className="grid gap-4 md:grid-cols-2">
      {ratings.map(r => (
        <li key={r.item.id} className="flex gap-4 rounded-xl border border-fg/10 bg-surface p-4">
          <button
            className="w-20 shrink-0 overflow-hidden rounded-lg"
            onClick={() => nav(`/title/${r.item.id}`, { state: { backgroundLocation: location } })}
            aria-label={t('list.open', { title: r.item.title })}
          >
            <Poster
              id={r.item.id}
              title={r.item.title}
              url={r.item.poster_url}
              className="aspect-[2/3]"
            />
          </button>
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <button
                className="text-left font-semibold hover:text-amber"
                onClick={() =>
                  nav(`/title/${r.item.id}`, { state: { backgroundLocation: location } })
                }
              >
                {r.item.title} <span className="text-xs font-normal text-muted">{r.item.year}</span>
              </button>
              <button
                aria-label={t('list.removeRating', { title: r.item.title })}
                className="rounded p-1 text-muted hover:text-red-600 dark:hover:text-red-300"
                onClick={() => remove.mutate(r.item.id)}
              >
                <Trash2 size={16} />
              </button>
            </div>
            <div className="mt-1 flex text-amber" aria-label={t('list.score', { n: r.value })}>
              {[1, 2, 3, 4, 5].map(v => (
                <Star key={v} size={14} fill={v <= r.value ? 'currentColor' : 'none'} />
              ))}
            </div>
            {r.comment ? (
              <p className="mt-2 line-clamp-4 whitespace-pre-line text-sm text-ink/90">
                “{r.comment}”
              </p>
            ) : (
              <p className="mt-2 text-xs text-muted">{t('list.noComment')}</p>
            )}
            <p className="mt-2 text-[11px] text-muted">{date(r.updated_at)}</p>
          </div>
        </li>
      ))}
    </ul>
  );
}

function Empty({ text, action }: { text: string; action?: React.ReactNode }) {
  const { t } = useT();
  return (
    <div className="rounded-2xl border border-dashed border-fg/15 py-16 text-center">
      <Sparkles className="mx-auto text-amber" size={36} />
      <h2 className="mt-5 font-display text-lg font-bold">{text}</h2>
      <p className="mt-2 text-sm text-muted">{t('list.emptyText')}</p>
      <Link to="/search" className="btn btn-primary mt-6">
        {t('list.explore')} <ArrowRight size={17} />
      </Link>
      {action && <div>{action}</div>}
    </div>
  );
}

/** Exclusão de conta (LGPD) com confirmação em dois passos, sem `confirm()`. */
function DangerZone() {
  const [armed, setArmed] = useState(false);
  const { t } = useT();
  const nav = useNavigate();
  const qc = useQueryClient();
  const del = useMutation({
    mutationFn: () => auth.deleteAccount(),
    onSuccess: () => {
      qc.clear();
      nav('/');
    },
  });
  return (
    <div className="mt-20 border-t border-fg/10 pt-6 text-sm text-muted">
      {!armed ? (
        <button
          className="hover:text-red-600 dark:hover:text-red-300"
          onClick={() => setArmed(true)}
        >
          {t('list.delete')}
        </button>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-red-600 dark:text-red-300">{t('list.deleteWarn')}</span>
          <button
            className="btn px-3 py-1.5 text-xs bg-red-500/80 text-white hover:bg-red-500"
            disabled={del.isPending}
            onClick={() => del.mutate()}
          >
            {t('list.deleteConfirm')}
          </button>
          <button className="text-xs hover:text-ink" onClick={() => setArmed(false)}>
            {t('list.cancel')}
          </button>
        </div>
      )}
    </div>
  );
}
