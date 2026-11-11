# CineMatch — frontend

Frontend independente em **React + TypeScript + Vite + Tailwind**, com componentes no padrão shadcn/ui, React Router, TanStack Query e validação de contratos com Zod. Interface pt-BR, mobile-first, modo escuro.

## Início rápido

```bash
npm install
cp .env.example .env
npm run dev
```

Abra a URL indicada pelo Vite (geralmente `http://localhost:5173`). Para produção: `npm run build` e `npm run preview`.

## Variáveis de ambiente

- `VITE_API_BASE_URL=http://127.0.0.1:8010`: origem do FastAPI, **sem** barra final.
- `VITE_USE_MOCKS=true`: usa dados locais fictícios, sem backend. Para API real, defina `false`.
- `VITE_MOCK_ERROR_RATE=0`: probabilidade entre 0 e 1 de erro nas respostas mock, para testar recuperação e estados de erro.

Para conectar ao backend real, altere `VITE_USE_MOCKS=false` e a URL em `VITE_API_BASE_URL`. Configure CORS no FastAPI para a origem do frontend, permitindo `Content-Type`, `X-Request-ID` e credenciais (cookies). O contrato de rotas está centralizado em `src/lib/api.ts`, e os schemas em `src/lib/types.ts`.

## Organização

- `src/pages`: Home, detalhe, busca, login, onboarding, favoritos e sobre.
- `src/components`: navegação, fileiras, cards, placeholders e estados visuais.
- `src/lib/api.ts`: **único cliente HTTP**, seleção de mocks, validação Zod, UUID por requisição.
- `src/lib/auth.ts`: autenticação mock em memória (não persiste tokens).
- `src/lib/events.ts`: fila de eventos, impressão com IntersectionObserver, flush a cada 5 s e sendBeacon no pagehide.
- `src/mocks`: catálogo de 40 títulos fictícios, gêneros e handlers com latência de 200–600 ms.

## Notas de integração

- Login/cadastro é simulado em memória; após recarregar a página, a sessão é perdida. Substitua `auth.ts` por sessão httpOnly quando o backend estiver pronto.
- `GET /users/me/favorites` é interpretado como lista de `ItemSummary[]`; `PUT /users/me/favorites/{id}` e `DELETE` são usados para adição/remoção. Confirme o formato de resposta com a API real.
- Eventos usam UUID idempotente e incluem `request_id` e `model_version` quando originados de recomendações. `sendBeacon` não permite cabeçalhos personalizados; configure a rota `/events` para aceitar o corpo JSON do beacon sem `X-Request-ID`, ou substitua por envio `fetch(..., {keepalive:true})` no backend real.
- O modo mock usa gradientes como pôsteres/backdrops; URLs `null` são tratadas como placeholders.
- Os mocks não representam obras reais nem fazem chamadas a TMDB/TVmaze.

## Atribuições

TMDB: “This product uses the TMDB API but is not endorsed or certified by TMDB.” Séries parcialmente de TVmaze (CC BY-SA 4.0). Consulte `/sobre`.

**Importante:** `public/tmdb-logo.svg` é um marcador visual temporário, **não é o logo oficial**. Antes de publicar, substitua-o pelo arquivo SVG oficial fornecido pelo TMDB, preservando o mesmo caminho. O ambiente de geração estava sem acesso externo para baixar o ativo oficial.
