Crie o frontend web do **CineMatch**, um app em português (pt-BR) para descobrir filmes e séries com recomendações personalizadas. O backend (FastAPI) é separado e está sendo construído em paralelo. Este projeto é **somente o frontend**.

## Identidade visual

Experiência de streaming escura e cinematográfica: banner grande no topo, fileiras horizontais de pôsteres e cartões que crescem no hover. A marca é própria; **não** copie nome, logotipo, tipografia, cores ou textos de nenhum serviço de streaming existente.

- **Nome e logotipo:** "CineMatch". Wordmark simples com um ícone de dois quadros de filme sobrepostos.
- **Paleta (tokens CSS):**
  - fundo `#0E0F13`;
  - superfície `#171922`, superfície elevada `#20232F`;
  - texto `#F2F3F5`, texto secundário `#A7ABB8`;
  - **acento âmbar `#F5A524`** (não use vermelho como cor de marca);
  - foco `#7CC4FF`.
- **Tipografia:** "Inter" para texto e "Sora" para títulos (Google Fonts), com fallback `system-ui`.
- **Animações:** discretas (150–250 ms). Respeite `prefers-reduced-motion`.
- **Responsivo:** mobile primeiro. Em telas pequenas, fileiras com scroll por toque e sem hover-expand.

## Stack e restrições técnicas

- React + TypeScript + Vite + Tailwind + shadcn/ui, React Router, TanStack Query e Zod para validar respostas da API.
- **Não use Supabase, Lovable Cloud nem nenhum backend próprio.** Não adicione analytics ou SDKs de terceiros.
- **Um único cliente de API em `src/lib/api.ts`:**
  - URL base em `VITE_API_BASE_URL` (padrão `http://127.0.0.1:8010`).
  - Modo mock controlado por `VITE_USE_MOCKS`, padrão `true`.
  - Com mocks ligados, todas as funções respondem a partir de `src/mocks/`, com latência simulada de 200–600 ms e erro ocasional controlável, para exercitar os estados de carregamento e de erro.
- **Mocks:**
  - ~40 títulos **fictícios** (nomes inventados), filmes e séries, gêneros variados.
  - Sem imagens reais: use pôsteres e backdrops gerados (gradientes com o título).
  - Quando `poster_url` vier `null` da API, mostre esse mesmo placeholder.
- **Requisições:** envie o header `X-Request-ID` (UUID) em cada uma e leia o `request_id` da resposta quando existir.
- **Autenticação:** por enquanto, mock em memória com login por e-mail e senha, sem validação real.
  - Isole tudo em `src/lib/auth.ts` para trocar depois por cookie httpOnly do backend.
  - **Não guarde tokens em localStorage.**

## Contrato da API (tipar com Zod exatamente assim)

```ts
type ItemType = "movie" | "tv";

type ItemSummary = {
  id: number; type: ItemType; title: string; year: number | null;
  poster_url: string | null; backdrop_url: string | null;
  genres: string[]; vote_average: number | null; vote_count: number;
};

type ItemDetail = ItemSummary & {
  overview: string | null; runtime_minutes: number | null; release_date: string | null;
  status: string | null; original_language: string | null;
  directors: string[]; cast: string[]; keywords: string[];
  external: { tmdb_url: string | null; imdb_url: string | null; tvmaze_url: string | null };
};

type Page<T> = { items: T[]; page: number; page_size: number; total: number };

type Recommendation = ItemSummary & { score: number; reason: string | null };

type RecommendationList = {
  request_id: string; model_version: string; fallback: boolean; items: Recommendation[];
};
```

| Método | Rota | Uso |
|---|---|---|
| GET | `/movies?q=&type=&genre=&year_from=&year_to=&sort=popular\|top_rated\|recent&page=&page_size=` | Busca, filtros e fileiras da home |
| GET | `/genres` | Lista de gêneros (`{ id: number; name: string }[]`) |
| GET | `/movies/{id}` | Detalhe |
| GET | `/movies/{id}/similar?k=12` | "Mais títulos como este" (`RecommendationList`) |
| GET | `/recommendations/me?k=20` | Feed personalizado (`RecommendationList`), exige login |
| GET/PUT | `/users/me/preferences` | `{ liked: number[]; disliked: number[]; genres: string[] }` |
| GET/PUT/DELETE | `/users/me/favorites[/{id}]` | "Minha lista" |
| POST | `/ratings` | `{ item_id, value: 1-5 }` |
| POST | `/events` | Lote de eventos (abaixo) |

**Eventos (`POST /events`):**
- Corpo: `{ events: Event[] }`.
- Cada evento tem:
  - `event_id` (UUID gerado no cliente, para idempotência) e `type`: `"impression" | "click" | "favorite_add" | "favorite_remove" | "rating" | "search"`;
  - `item_id?` e `query?`;
  - `surface`: `"home_row" | "similar" | "feed" | "search" | "detail"`;
  - `position?` (índice na lista);
  - `request_id?` e `model_version?` (copiados da `RecommendationList` que exibiu o item);
  - `occurred_at` (ISO).
- **Impressões:** registre quando o cartão fica ≥ 50% visível por ≥ 1 s (IntersectionObserver), uma vez por item por lista.
- **Envio:** fila em memória enviada em lote a cada 5 s ou ao sair da página (`sendBeacon`).

## Telas

1. **Home (`/`):**
   - Banner com o backdrop de um título popular, gradiente para o fundo, título, ano, gêneros, trecho da sinopse e botões "Detalhes" e "+ Minha lista".
   - Fileiras horizontais com setas no desktop e scroll com snap: "Em alta", "Mais bem avaliados", "Recomendados para você" (só logado) e uma fileira por gênero (3–5 gêneros).
   - Cartão no hover (desktop): cresce levemente e mostra ano, nota e gêneros, com botões de favoritar e "ver mais".
2. **Detalhe (`/title/:id`):** abre também como modal sobre a home, com URL própria.
   - Conteúdo: backdrop, pôster, título, ano, duração, nota (com número de votos), gêneros, sinopse, direção/criação, elenco (até 8 nomes), keywords como chips e links externos.
   - Fileira "Mais títulos como este" vinda de `/similar`. Cada item mostra o `reason` como legenda curta quando existir (ex.: "Mesmo diretor", "Tema parecido").
   - Avaliação de 1 a 5 estrelas e botão de favorito.
3. **Busca (`/search`):**
   - Campo com debounce de 300 ms.
   - Filtros: tipo (filme/série), gênero, faixa de anos e ordenação.
   - Grade de pôsteres com paginação infinita.
   - Estados: vazio inicial com sugestões, "nenhum resultado" e erro com "tentar novamente".
4. **Onboarding (`/onboarding`), após o primeiro login:**
   - **Passo 1:** escolher gêneros favoritos.
   - **Passo 2:** grade de pôsteres para marcar 👍 / 👎, com botão "Não conheço".
     - Meta: até 10 positivos e 5 negativos, **sem exigir** esse total.
     - Barra de progresso; "Pular" sempre disponível.
   - Salva em `PUT /users/me/preferences` e leva à home personalizada.
5. **Minha lista (`/my-list`):** grade de favoritos, com estado vazio que convida a explorar.
6. **Login/Cadastro (`/login`):** formulário simples (mock).

## Estados e robustez

- Skeletons em todas as fileiras, grades e no detalhe. Erros por seção, sem derrubar a página inteira.
- **Fallback:** se `fallback: true` vier em uma `RecommendationList`, mostre a fileira com o subtítulo discreto "Populares no momento".
- Sem login, esconda "Recomendados para você" e mostre um card convidando a criar conta.

## Acessibilidade

- Navegação completa por teclado nas fileiras (setas e Tab) e focos visíveis.
- `alt` com o título nos pôsteres; contraste AA.
- Modal com foco preso e fechamento por Esc.

## Atribuição de fontes (obrigatória)

Rodapé em todas as páginas com:
- O logo do TMDB (versão oficial, em `public/`) e o texto: "This product uses the TMDB API but is not endorsed or certified by TMDB."
- "Dados de séries parcialmente de TVmaze (CC BY-SA 4.0)", com link para https://www.tvmaze.com.
- Uma página `/sobre` explicando as fontes de dados e que as recomendações são geradas por modelos do CineMatch.

## Entregáveis

- Código organizado em `src/pages`, `src/components`, `src/lib` (api, auth, events) e `src/mocks`.
- Um `README` explicando:
  - as variáveis `VITE_API_BASE_URL` e `VITE_USE_MOCKS`;
  - como rodar localmente (`npm install && npm run dev`);
  - como apontar para a API real.
