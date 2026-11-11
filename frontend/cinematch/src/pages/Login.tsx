import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { auth } from '../lib/auth';
import { Brand } from '../components/UI';

export function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [register, setRegister] = useState(false);
  const [error, setError] = useState<string | null>(null);
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
