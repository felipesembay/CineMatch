import { useT } from '../lib/i18n';

export function About() {
  const { t } = useT();
  return (
    <article className="mx-auto min-h-[65vh] max-w-3xl px-5 py-16">
      <span className="text-xs font-bold uppercase tracking-widest text-amber">
        {t('about.kicker')}
      </span>
      <h1 className="mt-3 font-display text-4xl font-bold">{t('about.title')}</h1>
      <p className="mt-7 leading-8 text-muted">{t('about.p1')}</p>
      <h2 className="mt-10 font-display text-xl font-bold">{t('about.sources')}</h2>
      <p className="mt-4 leading-8 text-muted">{t('about.p2')}</p>
      <p className="mt-4 leading-8 text-muted">
        {t('about.p3a')}{' '}
        <a
          className="text-amber underline"
          href="https://www.tvmaze.com"
          target="_blank"
          rel="noreferrer"
        >
          TVmaze
        </a>
        {t('about.p3b')}
      </p>
      <h2 className="mt-10 font-display text-xl font-bold">{t('about.langTitle')}</h2>
      <p className="mt-4 leading-8 text-muted">{t('about.p4')}</p>
    </article>
  );
}
