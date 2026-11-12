import { Link, NavLink, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Search, UserRound, LogOut, Menu, X, Sun, Moon } from 'lucide-react';
import { useState } from 'react';
import { Brand } from './UI';
import { auth, useAuth } from '../lib/auth';
import { useT } from '../lib/i18n';
import { prefs, useTheme } from '../lib/prefs';

/** Alterna PT/EN. Mostra o idioma ativo; o rótulo acessível diz para qual vai trocar. */
function LangToggle() {
  const { lang, t } = useT();
  return (
    <button
      onClick={() => prefs.setLang(lang === 'pt-BR' ? 'en' : 'pt-BR')}
      aria-label={t('prefs.switchLang')}
      title={t('prefs.switchLang')}
      className="rounded-lg border border-fg/15 px-2 py-1.5 text-[11px] font-bold tracking-wider text-muted hover:bg-raised hover:text-ink"
    >
      <span className={lang === 'pt-BR' ? 'text-amber' : ''}>PT</span>
      <span className="mx-1 opacity-40">/</span>
      <span className={lang === 'en' ? 'text-amber' : ''}>EN</span>
    </button>
  );
}

function ThemeToggle() {
  const theme = useTheme();
  const { t } = useT();
  const label = theme === 'dark' ? t('prefs.toLight') : t('prefs.toDark');
  return (
    <button
      onClick={() => prefs.setTheme(theme === 'dark' ? 'light' : 'dark')}
      aria-label={label}
      title={label}
      className="rounded-lg p-2 text-muted hover:bg-raised hover:text-ink"
    >
      {theme === 'dark' ? <Sun size={19} /> : <Moon size={19} />}
    </button>
  );
}

export function Layout({ children }: { children: React.ReactNode }) {
  const user = useAuth();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { t } = useT();
  const [mobile, setMobile] = useState(false);
  const links = [
    ['/', t('nav.home')],
    ['/search', t('nav.explore')],
    ...(user ? [['/my-list', t('nav.myList')]] : []),
  ];
  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-40 border-b border-fg/[.06] bg-background/95 backdrop-blur-xl">
        <div className="mx-auto flex h-[72px] max-w-[1500px] items-center gap-8 px-5 md:px-10">
          <Link to="/" aria-label={t('nav.brandHome')}>
            <Brand />
          </Link>
          <nav aria-label={t('nav.main')} className="hidden items-center gap-7 md:flex">
            {links.map(([to, label]) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  `text-sm font-medium transition-colors hover:text-ink ${isActive ? 'text-amber' : 'text-muted'}`
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <Link
              to="/search"
              aria-label={t('nav.search')}
              className="rounded-lg p-2 text-muted hover:bg-raised hover:text-ink"
            >
              <Search size={21} />
            </Link>
            <LangToggle />
            <ThemeToggle />
            {user ? (
              <>
                <span className="hidden items-center gap-2 text-xs text-muted lg:flex">
                  <UserRound size={16} />
                  {user.name}
                </span>
                <button
                  onClick={async () => {
                    await auth.logout();
                    qc.clear();
                    nav('/');
                  }}
                  title={t('nav.signOut')}
                  aria-label={t('nav.signOut')}
                  className="rounded-lg p-2 text-muted hover:text-ink"
                >
                  <LogOut size={19} />
                </button>
              </>
            ) : (
              <Link to="/login" className="btn btn-primary px-4 py-2 text-xs">
                {t('nav.signIn')}
              </Link>
            )}
            <button
              className="p-2 md:hidden"
              aria-label={mobile ? t('nav.closeMenu') : t('nav.openMenu')}
              aria-expanded={mobile}
              onClick={() => setMobile(!mobile)}
            >
              {mobile ? <X /> : <Menu />}
            </button>
          </div>
        </div>
        {mobile && (
          <nav className="flex flex-col gap-1 border-t border-fg/10 px-5 py-3 md:hidden">
            {links.map(([to, label]) => (
              <Link
                key={to}
                onClick={() => setMobile(false)}
                to={to}
                className="rounded-lg p-3 text-sm hover:bg-raised"
              >
                {label}
              </Link>
            ))}
          </nav>
        )}
      </header>
      <main>{children}</main>
      <footer className="mt-20 border-t border-fg/10 bg-footer">
        <div className="mx-auto grid max-w-[1500px] gap-7 px-5 py-10 text-xs text-muted md:grid-cols-3 md:px-10">
          <div>
            <Brand />
            <p className="mt-3 max-w-xs leading-relaxed">{t('footer.tagline')}</p>
          </div>
          <div>
            <img
              src="/tmdb-logo.svg"
              alt="TMDB"
              className="mb-3 h-5 w-auto max-w-[120px] object-contain object-left"
            />
            <p className="max-w-sm leading-relaxed">
              This product uses the TMDB API but is not endorsed or certified by TMDB.
            </p>
          </div>
          <div>
            <p>
              {t('footer.tvmaze')}{' '}
              <a
                href="https://www.tvmaze.com"
                target="_blank"
                rel="noreferrer"
                className="text-ink underline"
              >
                TVmaze
              </a>{' '}
              (CC BY-SA 4.0)
            </p>
            <Link className="mt-3 inline-block text-amber hover:underline" to="/sobre">
              {t('footer.about')}
            </Link>
            <p className="mt-4">© 2026 CineMatch</p>
          </div>
        </div>
      </footer>
    </div>
  );
}
