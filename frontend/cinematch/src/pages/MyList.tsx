import { useState } from 'react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { ArrowRight, Heart, MessageSquareText, Sparkles, Star, ThumbsUp, Trash2 } from 'lucide-react';
import { api } from '../lib/api';
import { auth, useAuth, useAuthReady } from '../lib/auth';
import { reasonLabel } from '../lib/labels';
import type { ItemSummary, Rating } from '../lib/types';
import { TitleCard } from '../components/TitleCard';
import { TitleRow } from '../components/TitleRow';
import { ErrorState, Loading, Poster, SkeletonCards } from '../components/UI';

type Tab = 'favorites' | 'ratings' | 'liked';

export function MyList() {
  const user = useAuth();
  const ready = useAuthReady();
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
    { id: 'favorites', label: 'Favoritos', count: favorites.data?.length, icon: <Heart size={15} /> },
    {
      id: 'ratings',
      label: 'Avaliados e comentados',
      count: ratings.data?.length,
      icon: <MessageSquareText size={15} />,
    },
    { id: 'liked', label: 'Curtidos', count: prefs.data?.liked.length, icon: <ThumbsUp size={15} /> },
  ];

  return (
    <div className="mx-auto min-h-[65vh] max-w-[1500px] px-5 py-12 md:px-10">
      <span className="text-xs font-bold uppercase tracking-widest text-amber">Sua coleção</span>
      <h1 className="mt-2 font-display text-3xl font-bold md:text-4xl">Minha lista</h1>
      <p className="mb-10 mt-3 text-sm text-muted">
        Tudo o que você curtiu, favoritou e avaliou, e o que achamos que você vai gostar a seguir.
      </p>

      <TitleRow
        title="Achamos que você vai gostar"
        subtitle={
          recs.data?.fallback
            ? 'Populares nos seus gêneros. Favorite ou avalie títulos para personalizar.'
            : 'Com base no que você curtiu, favoritou e avaliou.'
        }
        items={recs.data?.items}
        loading={recs.isPending}
        error={recs.isError}
        retry={() => recs.refetch()}
        surface="my_list"
        requestId={recs.data?.request_id}
        modelVersion={recs.data?.model_version}
        reasons={Object.fromEntries((recs.data?.items ?? []).map(i => [i.id, reasonLabel(i.reason) ?? null]))}
      />

      <div role="tablist" aria-label="Seções da lista" className="mb-8 mt-4 flex flex-wrap gap-2">
        {tabs.map(t => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-sm transition-colors ${
              tab === t.id
                ? 'border-amber bg-amber/15 text-amber'
                : 'border-white/15 text-muted hover:bg-raised hover:text-ink'
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
            empty="Você ainda não favoritou nenhum título."
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
            empty="Você ainda não marcou 👍 em nenhum título."
            action={
              <Link to="/onboarding" className="btn btn-secondary mt-6">
                Refazer preferências
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
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteRating(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ratings'] });
      qc.invalidateQueries({ queryKey: ['recommendations'] });
    },
  });
  if (loading) return <SkeletonCards />;
  if (error) return <ErrorState onRetry={retry} />;
  if (!ratings?.length) return <Empty text="Você ainda não avaliou nenhum título." />;
  return (
    <ul className="grid gap-4 md:grid-cols-2">
      {ratings.map(r => (
        <li key={r.item.id} className="flex gap-4 rounded-xl border border-white/10 bg-surface p-4">
          <button
            className="w-20 shrink-0 overflow-hidden rounded-lg"
            onClick={() => nav(`/title/${r.item.id}`, { state: { backgroundLocation: location } })}
            aria-label={`Abrir ${r.item.title}`}
          >
            <Poster id={r.item.id} title={r.item.title} url={r.item.poster_url} className="aspect-[2/3]" />
          </button>
          <div className="min-w-0 flex-1">
            <div className="flex items-start justify-between gap-2">
              <button
                className="text-left font-semibold hover:text-amber"
                onClick={() => nav(`/title/${r.item.id}`, { state: { backgroundLocation: location } })}
              >
                {r.item.title} <span className="text-xs font-normal text-muted">{r.item.year}</span>
              </button>
              <button
                aria-label={`Remover avaliação de ${r.item.title}`}
                className="rounded p-1 text-muted hover:text-red-300"
                onClick={() => remove.mutate(r.item.id)}
              >
                <Trash2 size={16} />
              </button>
            </div>
            <div className="mt-1 flex text-amber" aria-label={`Nota ${r.value} de 5`}>
              {[1, 2, 3, 4, 5].map(v => (
                <Star key={v} size={14} fill={v <= r.value ? 'currentColor' : 'none'} />
              ))}
            </div>
            {r.comment ? (
              <p className="mt-2 line-clamp-4 whitespace-pre-line text-sm text-ink/90">“{r.comment}”</p>
            ) : (
              <p className="mt-2 text-xs text-muted">Sem comentário.</p>
            )}
            <p className="mt-2 text-[11px] text-muted">
              {new Date(r.updated_at).toLocaleDateString('pt-BR')}
            </p>
          </div>
        </li>
      ))}
    </ul>
  );
}

function Empty({ text, action }: { text: string; action?: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-dashed border-white/15 py-16 text-center">
      <Sparkles className="mx-auto text-amber" size={36} />
      <h2 className="mt-5 font-display text-lg font-bold">{text}</h2>
      <p className="mt-2 text-sm text-muted">Explore o catálogo e monte sua coleção.</p>
      <Link to="/search" className="btn btn-primary mt-6">
        Explorar títulos <ArrowRight size={17} />
      </Link>
      {action && <div>{action}</div>}
    </div>
  );
}

/** Exclusão de conta (LGPD) com confirmação em dois passos, sem `confirm()`. */
function DangerZone() {
  const [armed, setArmed] = useState(false);
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
    <div className="mt-20 border-t border-white/10 pt-6 text-sm text-muted">
      {!armed ? (
        <button className="hover:text-red-300" onClick={() => setArmed(true)}>
          Excluir minha conta e meus dados
        </button>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-red-300">
            Isso apaga conta, favoritos, avaliações e preferências. Não dá para desfazer.
          </span>
          <button
            className="btn px-3 py-1.5 text-xs bg-red-500/80 text-white hover:bg-red-500"
            disabled={del.isPending}
            onClick={() => del.mutate()}
          >
            Confirmar exclusão
          </button>
          <button className="text-xs hover:text-ink" onClick={() => setArmed(false)}>
            Cancelar
          </button>
        </div>
      )}
    </div>
  );
}
