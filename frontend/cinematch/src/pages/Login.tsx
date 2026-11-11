import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { auth } from '../lib/auth';
import { Brand } from '../components/UI';

export function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [register, setRegister] = useState(false);
  const [params] = useSearchParams();
  const [error, setError] = useState<string | null>(
    params.get('error') === 'google' ? 'Não foi possível entrar com o Google. Tente novamente.' : null,
  );
  const providers = useQuery({ queryKey: ['auth-providers'], queryFn: api.providers });
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const qc = useQueryClient();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const user = register
        ? await auth.register(email, password, name)
        : await auth.login(email, password);
      qc.clear();
      nav(user.onboarded ? '/' : '/onboarding');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Não foi possível entrar.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-[65vh] max-w-md flex-col justify-center px-5 py-16">
      <div className="mb-8">
        <Brand />
        <h1 className="mt-8 font-display text-3xl font-bold">
          {register ? 'Crie sua conta' : 'Que bom ter você aqui.'}
        </h1>
        <p className="mt-3 text-sm text-muted">
          {register
            ? 'Seu universo de descobertas começa agora.'
            : 'Entre e continue descobrindo histórias incríveis.'}
        </p>
      </div>
      {providers.data?.google && (
        <>
          <a
            href={api.googleLoginUrl()}
            className="btn w-full border-white/15 bg-white text-[#1f1f1f] hover:bg-white/90"
          >
            <GoogleIcon />
            Continuar com Google
          </a>
          <div className="my-6 flex items-center gap-3 text-xs text-muted">
            <span className="h-px flex-1 bg-white/10" />
            ou com e-mail
            <span className="h-px flex-1 bg-white/10" />
          </div>
        </>
      )}
      <form className="space-y-5 rounded-2xl border border-white/10 bg-surface p-7" onSubmit={submit}>
        {register && (
          <label className="block text-sm">
            Nome (opcional)
            <input
              value={name}
              maxLength={80}
              autoComplete="nickname"
              onChange={e => setName(e.target.value)}
              className="field mt-2"
            />
          </label>
        )}
        <label className="block text-sm">
          E-mail
          <input
            required
            type="email"
            autoComplete="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            placeholder="voce@exemplo.com"
            className="field mt-2"
          />
        </label>
        <label className="block text-sm">
          Senha
          <input
            required
            type="password"
            minLength={8}
            maxLength={128}
            autoComplete={register ? 'new-password' : 'current-password'}
            value={password}
            onChange={e => setPassword(e.target.value)}
            placeholder="mínimo de 8 caracteres"
            className="field mt-2"
          />
        </label>
        {error && (
          <p role="alert" className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-300">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy} className="btn btn-primary w-full">
          {busy ? 'Aguarde...' : register ? 'Criar conta' : 'Entrar'}
        </button>
      </form>
      <button
        onClick={() => {
          setRegister(!register);
          setError(null);
        }}
        className="mt-6 text-sm text-amber hover:underline"
      >
        {register ? 'Já tem uma conta? Entrar' : 'Ainda não tem conta? Cadastre-se'}
      </button>
    </div>
  );
}

function GoogleIcon() {
  return (
    <svg aria-hidden="true" width="18" height="18" viewBox="0 0 48 48">
      <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
      <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
    </svg>
  );
}
