import { useRef } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { TitleCard } from './TitleCard';
import { ErrorState, SectionTitle, SkeletonCards } from './UI';
import type { ItemSummary, EventSurface } from '../lib/types';
import { useT } from '../lib/i18n';
export function TitleRow({
  title,
  items,
  loading,
  error,
  retry,
  subtitle,
  surface = 'home_row',
  requestId,
  modelVersion,
  reasons,
}: {
  title: string;
  items?: ItemSummary[];
  loading?: boolean;
  error?: boolean;
  retry?: () => void;
  subtitle?: string;
  surface?: EventSurface;
  requestId?: string;
  modelVersion?: string;
  reasons?: Record<number, string | null>;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const { t } = useT();
  return (
    <section className="mb-10">
      <div className="flex items-center justify-between">
        <SectionTitle subtitle={subtitle}>{title}</SectionTitle>
        <div className="hidden gap-2 md:flex">
          <button
            aria-label={t('row.left', { title })}
            className="rounded-full border border-fg/15 p-2 hover:bg-raised"
            onClick={() => scroll.current?.scrollBy({ left: -550, behavior: 'smooth' })}
          >
            <ChevronLeft size={18} />
          </button>
          <button
            aria-label={t('row.right', { title })}
            className="rounded-full border border-fg/15 p-2 hover:bg-raised"
            onClick={() => scroll.current?.scrollBy({ left: 550, behavior: 'smooth' })}
          >
            <ChevronRight size={18} />
          </button>
        </div>
      </div>
      {loading ? (
        <SkeletonCards />
      ) : error ? (
        <ErrorState onRetry={retry || (() => {})} />
      ) : items?.length ? (
        <div
          ref={scroll}
          tabIndex={0}
          onKeyDown={e => {
            if (e.target === e.currentTarget && e.key === 'ArrowRight')
              scroll.current?.scrollBy({ left: 220, behavior: 'smooth' });
            if (e.target === e.currentTarget && e.key === 'ArrowLeft')
              scroll.current?.scrollBy({ left: -220, behavior: 'smooth' });
          }}
          aria-label={t('row.aria', { title })}
          className="scrollbar-hide flex snap-x snap-mandatory gap-4 overflow-x-auto pb-4 pt-2"
        >
          <>
            {items.map((item, i) => (
              <TitleCard
                key={item.id}
                item={item}
                position={i}
                surface={surface}
                requestId={requestId}
                modelVersion={modelVersion}
                reason={reasons?.[item.id]}
              />
            ))}
          </>
        </div>
      ) : (
        <p className="text-sm text-muted">{t('row.empty')}</p>
      )}
    </section>
  );
}
