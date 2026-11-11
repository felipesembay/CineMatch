# CineMatch — Registro de decisões (ADRs)

Formato curto: contexto → decisão → consequências. Status: **Aceita**, **Proposta** (aguarda confirmação) ou **Substituída**.

---

## ADR-001 — Monólito modular em Python
**Status:** Aceita (Fase 0)
**Contexto:** Projeto novo, uma pessoa/agente, custo mínimo. Spec veta microserviços sem necessidade.
**Decisão:** Um pacote `cinematch` em `src/` com subpacotes por domínio (`api`, `ingestion`, `data`, `features`, `models`, `evaluation`, `serving`, `monitoring`). Subpacotes são criados quando recebem código.
**Consequências:** Deploy simples; limites internos dependem de disciplina de imports. Extração em serviços futura continua possível.

## ADR-002 — API FastAPI dentro do pacote (`src/cinematch/api`)
**Status:** Aceita (Fase 0)
**Contexto:** A spec sugere `apps/api/`. Separar a API do pacote obrigaria um segundo projeto Python ou manipulação de `PYTHONPATH`.
**Decisão:** Código da API em `src/cinematch/api/`. `apps/` fica reservado para o frontend Next.js (`apps/web`, Fase 4).
**Consequências:** Um único `pyproject.toml`, lockfile e imagem. Desvio consciente da estrutura sugerida.

## ADR-003 — uv para Python 3.12 e dependências
**Status:** Aceita (Fase 0)
**Contexto:** Reprodutibilidade; o Python padrão da máquina (conda) é 3.9.
**Decisão:** uv com `uv.lock` versionado; `requires-python >=3.12`; dependências de dev em `[dependency-groups]`.
**Consequências:** Builds determinísticos local/CI/Docker. Exige uv instalado.

## ADR-004 — PostgreSQL com imagem pgvector desde a Fase 0
**Status:** Aceita (Fase 0)
**Contexto:** Embeddings em pgvector chegam na Fase 2.
**Decisão:** Usar `pgvector/pgvector:pg16` já no Compose; a extensão só será criada por migração na Fase 2.
**Consequências:** Sem troca de imagem/volume depois. Custo zero adicional.

## ADR-005 — Logging JSON com stdlib e ID de correlação
**Status:** Aceita (Fase 0)
**Decisão:** Formatter JSON próprio sobre `logging`; middleware gera/propaga `X-Request-ID`, registra método, rota, status e duração. Nada de corpo de requisição, tokens ou PII nos logs.
**Consequências:** Zero dependência extra; migrar para structlog/OpenTelemetry depois é trivial.

## ADR-006 — Health checks separados
**Status:** Aceita (Fase 0)
**Decisão:** `GET /health` = liveness (processo vivo, não toca DB). `GET /health/ready` = readiness (`SELECT 1` com timeout curto; 503 se falhar).
**Consequências:** Orquestrador não reinicia a API por queda do banco; balanceador deixa de rotear quando DB indisponível.

## ADR-007 — Serviços adicionados só quando usados
**Status:** Aceita (Fase 0)
**Decisão:** Compose inicial = `api` + `postgres`. MinIO, Airflow, MLflow, Prometheus/Grafana entram na fase em que forem consumidos.

---

## Decisões da Fase 1

### D-101 — Autenticação TMDB por Bearer token
**Status:** Aceita (2026-11-10)
**Decisão:** API Read Access Token em `Authorization: Bearer`, lido de `TMDB_API_TOKEN`. Assim o token não aparece em URLs, logs nem no Bronze (`request_params` não inclui credenciais).
**Alternativa descartada:** `api_key` em query string.

### D-102 — Bronze em filesystem local atrás de `RawStore`
**Status:** Aceita (2026-11-10)
**Decisão:** `LocalRawStore` grava `source=/entity=/dt=/run_id=/part-*.jsonl.gz` com escrita atômica (tmp + rename), em modo append-only. O backend MinIO/S3 entra na Fase 2 com o mesmo layout de chaves.
**Consequências:** um serviço a menos agora. `raw_uri` é `file://...` (percent-encoded) e passará a `s3://...`.

### D-103 — Descoberta via `/discover` ordenado por popularidade
**Status:** Aceita (2026-11-10)
**Decisão:** paginação `popularity.desc` limitada a 500 páginas, com `include_adult=false`. A ordem pode mudar entre páginas; a deduplicação por `(run_id, tipo, id)` absorve repetições, e eventuais lacunas são toleradas no MVP.
**Futuro:** exports diários de IDs do TMDB na expansão para ~30 mil títulos.

## ADR-008 — Manter TMDB apesar da restrição de ML nos termos (risco aceito)
**Status:** Aceita pelo responsável do projeto (2026-11-10)
**Contexto:** os Termos de Uso do TMDB (rev. 2023-10-20, §1.A/§1.C) proíbem usar conteúdo TMDB em aplicações de ML/IA, inclusive para treino, e limitam o cache a 6 meses. Foram apresentadas as alternativas: fontes abertas (Wikidata CC0 + Wikipedia CC BY-SA + TVmaze CC BY-SA), apenas TVmaze, ou pedir permissão ao TMDB.
**Decisão:** o responsável optou por manter o TMDB como fonte principal.
**Consequências e mitigações:** detalhadas em `docs/DATA_SOURCES.md`. Em resumo: uso não comercial; nenhum dataset com conteúdo TMDB é redistribuído; Bronze com retenção ≤ 180 dias; atribuição obrigatória; adaptador isolado para permitir trocar de fonte. O risco de suspensão da chave ou exigência de remoção dos dados é **do projeto**. Reavaliar antes de qualquer publicação ou monetização.

## ADR-009 — Identidade externa inclui o tipo
**Status:** Aceita (Fase 1)
**Contexto:** no TMDB, filme 42 e série 42 são recursos distintos com o mesmo número.
**Decisão:** `UNIQUE(source, external_type, external_id)` em `item_external_ids`, em vez do `UNIQUE(fonte, ID externo)` sugerido na spec.

## ADR-010 — Idempotência e checkpoint da ingestão
**Status:** Aceita (Fase 1)
**Decisão:**
- Upsert por identidade externa, com `content_hash` dos campos normalizados. `updated_at` só muda quando o conteúdo muda.
- Checkpoint por item em `ingestion_run_items` (`pending/done/failed/not_found`) e por página de descoberta em `ingestion_runs.state`.
- O upsert e a marcação do item acontecem na mesma transação.
- Advisory lock por fonte impede execuções concorrentes.
- `--resume` reprocessa apenas itens `pending` e `failed` (até 3 tentativas).
**Consequências:** reexecutar não duplica (verificado em teste de integração). O Bronze pode ter duplicatas após uma queda entre a escrita do arquivo e o commit; a deduplicação fica para a Silver (Fase 2).

## ADR-011 — TVmaze: varredura do índice e ligação só por IDs verificados
**Status:** Aceita (2026-11-10)
**Contexto:** a spec pede resolução de entidades cuidadosa e proíbe assumir equivalência de IDs entre fontes.
**Decisão:**
- Varrer `/shows?page=N` (~300 requisições para o índice inteiro) em vez de fazer um lookup por série (1 requisição por item).
- Gravar em `tvmaze_shows`, uma silver própria da fonte.
- Ligar ao catálogo via `item_external_ids` (`source='tvmaze'`) só quando IMDb ou TheTVDB coincidirem com o crosswalk declarado pelo TMDB. Casos ambíguos são descartados e contados. Vínculos que deixaram de valer são removidos a cada recálculo.
- Shows que só existem no TVmaze não entram no catálogo.
- A fusão de campos entre fontes (ex.: preencher a duração pelo TVmaze) fica para a camada Gold, com regra de precedência explícita.
**Alternativas:** lookup `/lookup/shows?imdb=` por série (mais requisições, sem visão do índice); casamento por título/ano (descartado: em 215 vínculos reais, 8 títulos e 6 anos divergem legitimamente).
**Consequências:** a re-varredura completa custa ~300 requisições, e o upsert por hash a torna idempotente. Incremental via `/updates/shows` fica como otimização futura.

## ADR-012 — Crosswalk de IDs declarados pelo TMDB
**Status:** Aceita (2026-11-10)
**Decisão:**
- Gravar `external_ids.imdb_id` (filmes e séries) e `tvdb_id` (séries) em `item_external_ids` com `match_method='tmdb_external_ids'`.
- Nunca reatribuir um ID já ligado a outro item: o conflito é registrado em log e ignorado.
- O crosswalk fica fora do `content_hash`.
- `cinematch crosswalk backfill-tmdb` reprocessa o Bronze sem chamar a API.

## ADR-013 — Keywords e créditos com minimização; reprocessamento a partir do Bronze
**Status:** Aceita (2026-11-10)
**Decisão:**
- Normalizar `keywords` (por item) e créditos limitados a elenco com `order < 15`, equipe-chave (Director, Screenplay, Writer, Story, Novel, Original Music Composer, Director of Photography) e `created_by` nas séries.
- Em `people`, guardar só nome e `known_for_department`: sem gênero, foto ou biografia (minimização de dados pessoais).
- Keywords e créditos entram no `content_hash`.
- `cinematch reprocess tmdb` re-normaliza o registro Bronze mais recente de cada item e faz o upsert completo, sem chamar a API, usando a mesma trava da ingestão. Substitui `crosswalk backfill-tmdb`.
**Limitação:** o `credits` de séries cobre só a temporada mais recente; `aggregate_credits` exigiria buscar os detalhes de novo.
**Consequência:** na primeira execução após a mudança, os itens contam como `updated`, porque o hash passou a incluir keywords e créditos.

## ADR-014 — Checks de qualidade em código, com histórico no Postgres
**Status:** Aceita (2026-11-10)
**Decisão:** checks em SQL e Python (`cinematch.quality.checks`), sem Great Expectations nem Soda por enquanto, gravando resultados em `data_quality_runs`. Status `pass`, `warn`, `fail` ou `info`; o comando sai com código 1 em caso de falha, o que permite usá-lo como gate em CI ou Airflow.
**Alternativas:** Great Expectations ou Soda (mais dependências e configuração, sem necessidade demonstrada agora).

## ADR-015 — Embeddings multilíngues em pgvector; TF-IDF em disco
**Status:** Aceita (2026-11-10)
**Contexto:** as sinopses do catálogo (TMDB `en-US` e TVmaze) estão em inglês, verificado em 2026-11-10: 99,8% com marcadores de inglês. Os usuários do CineMatch escrevem em português. Regra do responsável: descrições em inglês exigem foco multilíngue.
**Decisão:**
- **Modelo:** `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384 dimensões, Apache-2.0), rodando em CPU.
  - Os pesos são fixados pelo commit do Hugging Face resolvido no download.
  - Formato da versão: `model_version = <repo>@<commit[:12]>|emb_text_v1`.
- **PyTorch:** só CPU, pelo índice `download.pytorch.org/whl/cpu`, num extra opcional `ml` que fica fora da imagem da API.
- **Embeddings:**
  - Vetores normalizados em `item_embeddings` (pgvector, coluna sem dimensão fixa; a dimensão de cada versão fica registrada em `dimensions`).
  - Cálculo incremental: só re-encoda itens cujo `text_hash` mudou.
  - Busca exata por cosseno (8 mil itens); índice HNSW quando houver necessidade medida.
- **TF-IDF:**
  - Artefato joblib em disco com `manifest.json`, registrado em `feature_artifacts`.
  - Versão = `text_version` + versão do scikit-learn + hash dos parâmetros; `corpus_hash` identifica os textos exatos usados.
  - Tokens prefixados para gênero, keywords e pessoas.
- **Texto de entrada:** séries sem sinopse no TMDB usam o `summary` do TVmaze quando o vínculo foi verificado por ID.
**Alternativas:** `all-MiniLM-L6-v2` (só inglês, ~2× mais rápido); `multilingual-e5-base` (768 dimensões, ~3× mais lento).
**Risco (ADR-008):** gerar embeddings das sinopses do TMDB é o uso de ML que os §1.A/§1.C restringem. O conteúdo vindo do TVmaze (CC BY-SA) não tem essa restrição.

## ADR-016 — Baseline de similaridade de conteúdo: TF-IDF; candidato: fusão com emb v2
**Status:** Aceita (2026-11-10)
**Contexto:** experimento de sanidade em [docs/experiments/content_similarity_2026-11-10.md](experiments/content_similarity_2026-11-10.md), com 8.000 itens e sem dados de usuários.
- **TF-IDF:** recall de coleção@10 = 0,970, sobreposição de gênero = 0,806.
- **Emb v2** (sem título): recall de coleção = 0,605, gênero = 0,939.
- **Emb v1:** valores intermediários, com o maior vazamento de título (0,164).
**Decisão:**
- Fase 3: TF-IDF como baseline de `/movies/{id}/similar`.
- Fusão TF-IDF + emb v2 como challenger, avaliada com os mesmos proxies.
- Emb v1 mantido apenas para comparação.
- A escolha final depende de métricas online (CTR e favoritos com exposição registrada).
**Consequência:** embeddings continuam úteis para consultas em português (multilíngue) e para a fusão.

## ADR-017 — Modelos de similaridade com rótulo fraco e MLflow próprio
**Status:** Aceita (2026-11-10)
**Contexto:** o responsável pediu para testar Sentence Transformers + cosseno, Rocchio, LogisticRegression/SGD e LightGBM/XGBoost. Modelos supervisionados precisam de rótulo, e não há interações; a spec proíbe inventá-las.
**Decisão:**
- **Rótulo fraco:** "mesma coleção TMDB" (`belongs_to_collection`), derivado do catálogo. Não é interação nem relevância.
  - Validação GroupKFold por coleção, para que a mesma franquia não esteja no treino e no teste.
  - Runs no MLflow levam a tag `label_kind=weak:tmdb_collection`.
- **Rerankers:** candidatos TF-IDF ∪ embedding, features sem título.
- **Rocchio:** em duas formas, PRF item → item e perfil a partir de vários itens.
- **XGBoost:** não incluído (LightGBM cobre gradient boosting, e o wheel traz CUDA).
- **MLflow:**
  - Serviço próprio no Compose (`ghcr.io/mlflow/mlflow:v3.17.2`, `127.0.0.1:5010`), separado do MLflow de outros projetos da máquina.
  - Metadados em SQLite e artefatos no volume `mlflow`, servidos por proxy.
  - Cliente `mlflow-skinny` 3.17.x.
- **Código e notebook:** a lógica fica em `src/cinematch/similarity/` (testada); `models/similarity_models.ipynb` só orquestra.
**Resultado:** ver [similarity_models_2026-11-10.md](experiments/similarity_models_2026-11-10.md). Os rerankers lideram os proxies (recall 0,985, gênero 0,967), em parte por metadados e com viés "mesmo tipo"; LightGBM ≈ LogisticRegression; Rocchio de perfil > MaxSim > seed única.
**Consequência:**
- TF-IDF segue como baseline do `/similar`; reranker LogisticRegression é o challenger principal.
- Rocchio de perfil vai para o onboarding (Fase 4).
- A escolha final depende de métricas online.
**Alternativas:** reusar o MLflow existente (rejeitado: mistura projetos); SQLite contra Postgres como backend (SQLite basta para um usuário e não exige driver na imagem).

## ADR-018 — Serving do `/similar` por vizinhos pré-computados; popularidade bayesiana
**Status:** Aceita (2026-11-10)
**Contexto:** Fase 3 pede `/movies/{id}/similar` determinístico para versão fixa, com fallback e p95 < 200 ms. A imagem da API não tem scikit-learn/torch (ADR-015).
**Decisão:**
- **Vizinhos em batch:** `cinematch recs build-similar` grava top-50 por item em `item_neighbors` (COPY), sob uma `model_version` determinística (método + versões dos artefatos + parâmetros) registrada em `neighbor_sets` e no MLflow (`serving-similar`).
  - Uma versão ativa por slot (índice único parcial); `recs activate` troca ou faz rollback em uma transação.
  - Ativo: TF-IDF (ADR-016). Gravado e não ativo: reranker LogisticRegression (ADR-017), pronto para teste A/B na Fase 4.
- **Diversidade:** no máximo `max_per_collection` (padrão 3) itens da mesma coleção TMDB por lista.
- **Explicação (`reason`):** por sobreposição de metadados, na ordem coleção > direção/criação > elenco > gêneros. Não descreve o interior do modelo.
- **Fallback:** sem conjunto ativo ou sem vizinhos para o item → mais bem avaliados (bayes) do mesmo tipo que compartilham gênero; `fallback=true` e `model_version=fallback:bayes_v1...`.
- **Popularidade:** média bayesiana por tipo, `(v·R + m·C)/(v + m)`, com C = média do tipo e m = percentil 75 de votos do tipo. A mediana (séries: 75 votos) deixava títulos com ~400 votos no topo.
- **Lançamento:** listagens omitem itens com `release_date` futura ou ausente, salvo `include_unreleased=true`.
- **Imagens:** só caminhos relativos do TMDB são aceitos na normalização; a API monta a URL do CDN do TMDB. Atribuição obrigatória no frontend.
- **CORS:** lista explícita de origens (`CINEMATCH_CORS_ORIGINS`), só localhost por padrão.
**Alternativas:** carregar o TF-IDF na API (exigiria scikit-learn na imagem, latência e memória maiores); busca vetorial em pgvector a cada requisição (útil para embeddings, desnecessária com 8 mil itens e vizinhos fixos).
**Consequência:** vizinhos ficam desatualizados até o próximo build; itens novos caem no fallback até lá. Agendar o build após cada ingestão (Fase 6, Airflow).

## ADR-019 — Frontend em React + Vite (em vez de Next.js)
**Status:** Aceita (2026-11-11)
**Contexto:** a spec sugere Next.js. O frontend foi gerado em React + Vite + Tailwind (protótipo externo) e integrado à API.
**Decisão:** manter React + Vite.
- O app é um SPA autenticado, sem necessidade de SSR/SEO por página.
- Em dev, o Vite faz proxy de `/api` para o FastAPI; em produção, o nginx faz o mesmo proxy. Navegador e API ficam na mesma origem, o cookie de sessão é first-party (`SameSite=Lax`) e não há CORS.
- Imagem do frontend: build estático + nginx com CSP, `X-Content-Type-Options` e `Referrer-Policy`.
**Alternativas:** migrar para Next.js (custo de reescrita sem ganho funcional hoje).
**Consequência:** se SEO de páginas de título virar requisito, reavaliar SSR.

## ADR-020 — Contas, sessão, interações e recomendação personalizada (Fase 4)
**Status:** Aceita (2026-11-11)
**Decisão:**
- **Contas:** e-mail + senha com hash **Argon2id**; nome de exibição opcional (PII mínima). Mensagem de erro de login genérica e verificação contra hash fictício quando o e-mail não existe (sem enumeração por tempo). Limite de 10 tentativas por IP + e-mail a cada 15 min (em memória, 1 réplica).
- **Sessão:** token opaco aleatório (256 bits) em cookie `httpOnly`, `SameSite=Lax`, `Secure` em produção; o banco guarda só o SHA-256 do token; expira em 30 dias; logout apaga a sessão.
- **CSRF:** além do `SameSite`, métodos que alteram estado com `Origin` fora da lista permitida recebem 403.
- **LGPD:** `DELETE /users/me` apaga conta, sessões, preferências, favoritos e avaliações em cascata; eventos permanecem anonimizados (`user_id = NULL`).
- **Interações:** curtidas/não curtidas do onboarding, favoritos e avaliações 1-5 com comentário (≤ 1.000 caracteres).
- **Eventos (`POST /events`):** lote de até 100, idempotentes por `event_id` (UUID do cliente), tipos e superfícies validados, `occurred_at` não pode estar no futuro; `user_id` vem da sessão, nunca do corpo. Impressões levam `request_id`, posição e `model_version` (atribuição de exposição para A/B e avaliação offline futura).
- **`/recommendations/me`:** Rocchio no espaço de escores sobre os vizinhos pré-computados ativos: `score(c) = Σ w_s · sim(s, c)`, com pesos +1 (curtido, favorito), (nota − 3)/2 (avaliação) e −0,5 (não curtido). Exclui itens já vistos; no máximo 2 por coleção; motivo "Porque você curtiu X". Sem sinais: fallback bayesiano nos gêneros preferidos. Determinístico para a mesma versão.
- **Retenção:** itens TMDB expirados que têm dados de usuário viram *tombstone* (conteúdo TMDB apagado, ID mantido), em vez de DELETE em cascata.
**Alternativas:** JWT em `localStorage` (exposto a XSS; rejeitado); Rocchio sobre vetores em pgvector a cada requisição (mais caro; o equivalente no espaço de escores reaproveita a versão servida no `/similar`).
**Limitações:** limitador de login em memória (mover para Redis/Postgres com várias réplicas); sem verificação de e-mail nem recuperação de senha; sem dados reais ainda para avaliar a personalização online.

## ADR-021 — Banco no Azure por cópia completa (`pg_dump`/`pg_restore`)
**Status:** Aceita (2026-11-11)
**Contexto:** o responsável quer o banco em nuvem (Azure Database for PostgreSQL Flexible Server), mantendo todo o resto igual e sem recalcular features e vizinhos.
**Decisão:**
- Cópia completa por `pg_dump -Fc --no-owner --no-privileges` + `pg_restore --jobs 4 --exit-on-error`, com cliente PostgreSQL 16 (imagem `pgvector/pgvector:pg16`). `COMMENT ON EXTENSION` é filtrado (o admin do Azure não é superusuário). `ANALYZE` ao final.
- Checagens antes (conexão TLS, versão, `vector` disponível/liberado em `azure.extensions`, destino vazio) e verificação depois (contagem exata por tabela, revisão Alembic, conjunto de vizinhos ativo).
- Credenciais só no `.env`; o script passa para o cliente por variáveis `PG*` (nunca na linha de comando nem na saída).
- API e CLI apontam para o Azure por variável (`CINEMATCH_API_DATABASE_URL` / `CINEMATCH_DATABASE_URL`, `sslmode=require`); o banco local fica como rollback.
**Alternativas:** Azure Database Migration Service (mais pesado para 0,9 GB); recalcular no destino (desperdiça o que já está versionado e validado).
**Riscos:** latência maior por requisição (rede até o Azure) — remedir `evaluate api-latency` após a troca; custo de armazenamento (os 2 conjuntos de vizinhos antigos de 8 mil itens ocupam ~250 MB e podem ser removidos).

## ADR-022 — Login com Google (OAuth 2.0 + OpenID Connect)
**Status:** Aceita (2026-11-11)
**Decisão:**
- Fluxo *Authorization Code* no backend, com `state` (anti-CSRF; guardado em hash no banco e em cookie `httpOnly`), PKCE S256 e `nonce`. Estado de uso único, válido por 10 min.
- ID token validado localmente: assinatura RS256 com as chaves públicas do Google (JWKS em cache de 1 h), `iss`, `aud`, `exp`, `nonce` e `email_verified`.
- Identidade por `sub` em `user_identities`; a sessão é a mesma do login por senha (cookie `httpOnly`).
- **Conta com senha e mesmo e-mail:** o Google comprova a posse do e-mail, mas o cadastro por senha não tem verificação. O vínculo anula a senha anterior e encerra todas as sessões da conta (impede sequestro de conta pré-criada por terceiros).
- `redirect_uri` derivado da origem do navegador (Host preservado pelo proxy) e aceito só se a origem estiver na lista permitida; `next` só aceita caminhos relativos (sem open redirect).
**Alternativas:** Google Identity Services só no frontend (token no navegador; exigiria validar no backend do mesmo jeito); bibliotecas como Authlib (mais dependências para um único provedor).
**Consequência:** novos provedores reaproveitam `user_identities` e `oauth_states`.
