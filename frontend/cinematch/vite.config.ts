import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

// Dev: /api -> FastAPI. Mesma origem no navegador, então o cookie de sessão é first-party
// (SameSite=Lax) e não há CORS. Em produção, o nginx faz o mesmo proxy (ver Dockerfile).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.API_PROXY_TARGET || 'http://127.0.0.1:8010',
          rewrite: path => path.replace(/^\/api/, ''),
        },
      },
    },
  };
});
