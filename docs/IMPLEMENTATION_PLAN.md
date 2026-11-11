# CineMatch — Plano de implementação

> Legenda de status: `[x]` implementado e verificado · `[ ]` pendente.
> Convenção: **Meta** (objetivo), **Hipótese** (suposição a validar), **Medido** (resultado observado), **Implementado** (funcionalidade existente no código).

## Estado inicial (2026-11-10)

- Diretório continha apenas `CINEMATCH_PROJECT_SPEC.md`. Sem git, sem código.
- Ambiente local: Python 3.12.3 (`/usr/bin/python3.12`), uv 0.11.7, Docker 29.1.3, `docker-compose` v2.38.2 (binário standalone; o plugin `docker compose` não está instalado).
- Portas 8000 e 5433 já ocupadas por outros projetos na máquina → padrões do CineMatch: API `8010`, Postgres `5434` (configuráveis via `.env`).

## Visão geral das fases

| Fase | Tema | Depende de | Status |
|---|---|---|---|
| 0 | Planejamento e bootstrap | — | concluída |
| 1 | Coleta TMDB e persistência | 0 + chave TMDB | concluída |
| 2 | Data quality, TVmaze, features, embeddings | 1 | em andamento (TVmaze implementado) |
| 3 | Baselines, similaridade, serving | 2 | não iniciada |
| 4 | Aplicação web e coleta de interações | 3 | não iniciada |
| 5 | Colaborativo e ranking | 4 + volume de interações reais | não iniciada |
| 6 | MLOps de produção | 3–5 | não iniciada |

Fases 2–6 serão detalhadas apenas quando a fase anterior for aceita.

---

## Fase 0 — Planejamento e bootstrap

| # | Subtarefa | Verificação |
|---|---|---|
| 0.1 | Inspecionar repositório e ambiente | Seção "Estado inicial" acima |
| 0.2 | `docs/IMPLEMENTATION_PLAN.md`, `docs/DECISIONS.md` | Arquivos existem e cobrem Fases 0–1 |
| 0.3 | `README.md`, `.gitignore`, `.env.example` sem segredos | `.env` ignorado pelo git; `.env.example` só com placeholders |
| 0.4 | `pyproject.toml` (uv, Ruff, Pytest), `uv.lock` | `uv sync` funciona do zero |
| 0.5 | Estrutura `src/cinematch/` + pacotes vazios previstos | `import cinematch` ok |
| 0.6 | Config por ambiente (`cinematch.config`) | Teste unitário de leitura de env |
| 0.7 | Logging JSON estruturado + `X-Request-ID` | Teste verifica header e formato JSON |
| 0.8 | FastAPI: `GET /health` (liveness), `GET /health/ready` (DB) | Testes: 200 / 200 com DB / 503 sem DB |
| 0.9 | `Dockerfile` da API (não-root, healthcheck) | `docker-compose build` ok |
| 0.10 | `docker-compose.yml`: `api` + `postgres` (pgvector) | `docker-compose up -d` → `curl /health/ready` = 200 |
| 0.11 | CI mínima (GitHub Actions: Ruff + Pytest) | Workflow válido; mesmos comandos passam localmente |

**Critérios de aceite da Fase 0**
- [x] `uv run ruff check .` e `uv run ruff format --check .` sem erros.
- [x] `uv run pytest` passa.
- [x] `docker-compose up -d --build` sobe API e Postgres; `/health` e `/health/ready` retornam 200.
- [x] Instruções do README reproduzem o ambiente (executadas nesta máquina; CI ainda não rodou — repositório sem remoto).

**Resultados medidos (2026-11-10, máquina local)**
- Ruff: `All checks passed!`; format: 15 arquivos ok.
- Pytest: 10 passed, 1 skipped (integração sem env); com `CINEMATCH_TEST_DATABASE_URL` → teste de integração 1 passed contra Postgres do Compose.
- Compose: `postgres` healthy; `api` healthy (HEALTHCHECK Docker), processo como `uid=999(app)`.
- `GET /health` → 200; `GET /health/ready` → 200; com Postgres parado → 503 `{"database":"error"}` e log `database_unavailable` com `request_id`; `/health` segue 200; após religar → 200.

**Limitações conhecidas da Fase 0**
- Logs de startup do Uvicorn ainda em texto (só logs da aplicação em JSON).
- Aviso `StarletteDeprecationWarning` do `TestClient` com httpx (Starlette 1.10); sem impacto funcional.
- Workflow de CI escrito mas não executado (sem remoto GitHub).

**Riscos:** conflito de portas com outros projetos locais (mitigado por portas configuráveis); diferença `docker compose` vs `docker-compose` (README cita ambos).

---

## Fase 1 — Coleta TMDB e persistência

**Pré-requisitos:** chave TMDB do usuário em `.env` (nunca versionada); decisões D-101 a D-103 em `docs/DECISIONS.md` confirmadas.

| # | Subtarefa | Verificação |
|---|---|---|
| 1.1 ✅ | Revisar termos atuais TMDB (uso, atribuição, imagens, rate limit) e registrar em `docs/DATA_SOURCES.md` | Documento com links e data de consulta |
| 1.2 ✅ | Alembic configurado; migração `0001` com `catalog_items`, `item_external_ids`, `genres`, `item_genres`, `ingestion_runs`, `ingestion_checkpoints` | `alembic upgrade head` e `downgrade base` em Postgres real (teste de integração) |
| 1.3 ✅ | Cliente HTTP TMDB (HTTPX): timeout, retries limitados com backoff exponencial + jitter, tratamento de 429 (`Retry-After`), limitador de taxa cliente, auth via Bearer token | Testes com `respx`: 200, 429→sucesso, 5xx→retry→falha, timeout |
| 1.4 ✅ | Descoberta paginada de IDs (`/discover/movie`, `/discover/tv` ou endpoints de lista) com limite configurável (`max_items`, padrão baixo p/ dev) | Teste de paginação mockada respeita limite |
| 1.5 ✅ | Extração de detalhes em lotes com checkpoint por `run_id` (retomar sem refazer) | Teste: interromper e retomar → sem chamadas repetidas |
| 1.6 ✅ | Bronze: respostas brutas JSONL.gz com `source`, `external_id`, `fetched_at`, `run_id`, `license_notes` | Teste verifica arquivo e campos de proveniência |
| 1.7 ✅ | Normalização (Pydantic) → upsert idempotente (`INSERT ... ON CONFLICT`) em Postgres | Teste de integração: duas execuções = mesma contagem, `updated_at` atualizado |
| 1.8 ✅ | Métricas de execução (itens descobertos, extraídos, falhas, duração) em `ingestion_runs` + logs | Teste verifica registro do run |
| 1.9 ✅ | CLI `cinematch ingest tmdb --max-items N` + documentação | README com comando; execução pequena real (≤ 50 itens) somente com chave do usuário |

**Critérios de aceite da Fase 1**
- [x] Executar a coleta duas vezes não cria duplicatas: `test_two_runs_do_not_duplicate`, contra Postgres real com HTTP mockado.
- [x] Erros transitórios (429, 5xx, timeout) tratados com retry limitado: `tests/ingestion/test_http.py`, `test_transient_errors_retried`.
- [x] Todo registro tem fonte, ID externo e timestamp: `test_provenance_recorded`.
- [x] Comando de ingestão documentado (README).
- [x] Coleta grande exige confirmação: acima de 500 itens a CLI pede `--yes`.
- [x] Coleta real pequena (50 filmes + 50 séries) executada em 2026-11-10. Resultados abaixo.

**Resultados medidos (2026-11-10, máquina local)**
- Ruff: `All checks passed!`. Pytest: **56 passed** (42 unitários + 14 de integração contra `cinematch_test`). Cobertura de linhas: 95%.
- `alembic upgrade head` aplicado no banco dev pelo container (`cinematch db upgrade`). Roundtrip downgrade/upgrade e `alembic check` passam.
- CLI no container sem token: `erro: TMDB_API_TOKEN não configurado`, exit 2, sem traceback.
- Nenhuma requisição real ao TMDB foi feita.

**Coleta real TMDB (2026-11-10, 50 filmes + 50 séries, 4 req/s)**

| Run | Situação | Resultado |
|---|---|---|
| `25ddf2e6…` | 1ª execução falhou: `CINEMATCH_BRONZE_DIR` relativo quebrou `Path.as_uri()` (bug corrigido, teste de regressão adicionado). Retomado com `--resume` | succeeded: 100 inseridos, 107 requisições, 0 retries, 0 respostas 429, 27,4 s |
| `e15707bd…` | 2ª execução, mesmos parâmetros | 0 inseridos, 99 inalterados, 1 atualizado (mudança na fonte), 27,1 s |
| (3º) | Após a heurística de duração de séries | 0 inseridos, 32 atualizados, 68 inalterados, 27,2 s |

- Banco: 100 itens, 100 IDs externos, **0 duplicatas** em `(source, external_type, external_id)`, 0 itens órfãos, 21 gêneros, 251 vínculos item–gênero. Todos os IDs externos têm `raw_uri`, `last_run_id` e `last_synced_at`.
- Cobertura de campos:
  - Filmes: sinopse, idioma, duração e gênero 100%; ano 98%.
  - Séries: sinopse, ano, idioma e gênero 100%; duração 96%. Antes da heurística, só 34% das séries tinham duração, porque `episode_run_time` vem vazio. Fallback adotado: `last_episode_to_air.runtime`, que é uma aproximação.
- Bronze: 19 arquivos, 1,4 MB. Extrapolação, não medida: ~70 MB para 5 mil títulos.
- Tempo: ~0,27 s por item. Extrapolação, não medida: ~23 min para 5 mil títulos.

**Coleta TMDB completa (run `997d1f77…`, executada pelo responsável em 2026-11-10)**
- `--max-items 2500` por tipo: 5.000 itens buscados, 4.900 inseridos (100 já existiam), `items_by_status = {done: 5000}`, 22,5 min (19:05:49 → 19:28:23 UTC).
- Backfill do crosswalk a partir do Bronze: 7.185 IDs IMDb/TVDB, 0 conflitos, 58 itens sem nenhum ID externo.
- Métricas do run: 5.291 requisições em 1.354 s (≈ 3,9 req/s; o limitador de 4 req/s é o gargalo), 0 retries, 0 respostas 429. Foram 291 páginas de descoberta para 5.000 IDs únicos: ~14% de repetição entre páginas por mudança de ranking durante a paginação, absorvida pela deduplicação.
- Qualidade: 0 duplicatas, 0 órfãos, 27 gêneros, Bronze com 36 MB.
  - Filmes: sinopse 99,8%, duração 99,3%, IMDb 99,8%, ano de 1922 a 2027 (mediana 2014).
  - Séries: sinopse **92,6%**, duração 93,9%, IMDb 95,8%, ano de 1944 a 2026 (mediana 2007).
  - Menos de 10 votos: 115 filmes e **607 séries**.
- Pendências de qualidade para a Fase 2:
  - (a) completar a sinopse das séries pelo TVmaze ou pelo idioma original;
  - (b) baseline de popularidade com média bayesiana, por causa das séries com poucos votos;
  - (c) regra para títulos ainda não lançados (ano 2027).

**Limitações conhecidas da Fase 1**
- O TMDB contraria os termos para uso em ML (ADR-008, risco aceito).
- O refresh e o expurgo de itens do Postgres sem sincronização há mais de 180 dias ainda não estão automatizados (Fase 2).
- Keywords e créditos ficam só no Bronze; a normalização é da Fase 2.
- A ingestão é sequencial (4 req/s padrão). Estimativa, não medida: ~5,5 mil requisições para 5 mil títulos, ≈ 25 min.
- Métricas `http_*` de um run retomado contam só a última execução.

**Riscos:** mudança de termos/limites do TMDB; IDs TMDB de filme e série colidem (mesmo número, tipos diferentes) → chave externa inclui tipo; testes de integração exigem Postgres (usar serviço do Compose / service container na CI).

---

## Fase 2 — Data quality e features (concluída; 2.6 adiada)

| # | Subtarefa | Status | Verificação |
|---|---|---|---|
| 2.1 | Crosswalk IMDb/TVDB declarado pelo TMDB + reprocessamento a partir do Bronze | ✅ | Testes de integração; backfill real (ver Fase 1) |
| 2.2 | Coleta TVmaze: varredura do índice com checkpoint e resume | ✅ | `tests/integration/test_tvmaze_pipeline.py` |
| 2.3 | Resolução TVmaze ↔ catálogo só por IDs verificados, com detecção de ambiguidade | ✅ | Testes: ambiguidade, item duplicado, vínculo obsoleto removido |
| 2.4 | Keywords, pessoas e créditos (elenco ≤ 15, equipe-chave, criadores) + `cinematch reprocess tmdb` | ✅ | `test_credits_keywords.py`, `test_keywords_and_credits_persisted_and_replaced` |
| 2.5a | Checks de qualidade (`cinematch quality check`) com histórico em `data_quality_runs` | ✅ | `tests/quality/`, `tests/integration/test_quality.py` |
| 2.5b | Gold: snapshots Parquet imutáveis, hash de conteúdo, gate de qualidade, `training_datasets` | ✅ | `tests/integration/test_gold.py` |
| 2.6 | Backend MinIO para o Bronze | pendente | |
| 2.7 | TF-IDF e embeddings versionados (pgvector), check de consistência | ✅ | `tests/integration/test_features.py`, `tests/features/` |
| 2.8 | `ingest tmdb-refresh` (itens > N dias, do mais antigo) + `catalog expire` (simulação por padrão) | ✅ | `tests/integration/test_retention.py` |
| 2.9 | Experimento TF-IDF × emb v1 × emb v2 (proxies de sanidade) | ✅ | [relatório](experiments/content_similarity_2026-11-10.md) |

**TVmaze real (2026-11-10, 2 páginas = shows com IDs de 0 a 499)**
- 1º run: 485 shows inseridos, 2 requisições, 0 retries, 2,0 s. **215 vínculos** com séries do catálogo: 214 por IMDb e TVDB ao mesmo tempo, 1 só por IMDb, 0 ambíguos.
- 2º run: 0 inseridos, 485 inalterados, os mesmos 215 vínculos.
- Conferência dos vínculos (não usada como critério de ligação): 207/215 com título idêntico e 209/215 com o mesmo ano. As divergências inspecionadas são variações legítimas, como "Star Wars Rebels" × "Star Wars: Rebels" ou ano do piloto × ano da estreia. Nenhuma ligação errada foi encontrada na amostra.
- A varredura completa do índice ainda não foi executada. Exige `--all --yes`, com ~300+ requisições e ~6 min estimados.

**Execução no banco dev (2026-11-10, após o run TMDB `0e2a3c6c…`)**
- **Run TMDB `0e2a3c6c…`:** 3.000 filmes novos, 2.986 já existentes pulados (`--skip-synced-within-days 30`), 3.334 requisições, 0 retries, 843,8 s. Catálogo: 8.000 itens (5.500 filmes e 2.500 séries).
- **`reprocess tmdb`:** 8.000 itens atualizados em 64 s, sem chamar a API. Primeira execução após keywords e créditos entrarem no hash.
- **TVmaze:** varredura completa executada pelo responsável (383 páginas, 91.004 shows). `link tvmaze`: 2.063 das 2.500 séries ligadas (82,5%); 1.871 por IMDb e TVDB, 40 só por IMDb, 152 só por TVDB; 1 show e 2 séries ambíguos, não ligados.
- **Primeiro baseline de `quality check`:** 22 pass, 1 warn, 3 info, 0 fail (24 pass após o check de embeddings).
  - Cobertura de filmes: sinopse 99,7%, keywords 95,9%, elenco 99,8%.
  - Cobertura de séries: keywords 85,0%, elenco 90,3%, IMDb 95,8%.
  - Único aviso: sinopse de séries, 92,6% (limiar de aviso 95%). Com o fallback do TVmaze, 30 séries a mais ganham texto.
  - Informativos: 46 títulos não lançados, 899 itens com menos de 10 votos.
- **TF-IDF** (`tfidf_text_v1|sklearn-1.9.1|p-3b78591d`): 8.000 itens, vocabulário de 49.297 termos, nnz de 378.613.
- **Embeddings** (`paraphrase-multilingual-MiniLM-L12-v2@e8f8c211226b|emb_text_v1`): 8.000 itens em 130 s no CPU; segundo run, 0 re-encodados em 0,7 s. Download do modelo restrito a 477 MB e ~60 s (antes, 8 min baixando todos os formatos).
- **Sanidade multilíngue:** cosseno entre "heist movie" e "filme de assalto" = 0,69; entre "heist movie" e "romantic comedy" = 0,37.
- **Comparação qualitativa (3 títulos; hipótese, não métrica):**
  - O TF-IDF trouxe vizinhos mais coerentes: Breaking Bad → El Camino, Narcos, Better Call Saul; Spirited Away → filmes do Ghibli e de Shinkai.
  - Os embeddings `emb_text_v1` parecem dominados pelo título: Breaking Bad → "Breakdown", "Point Break".
  - Próximo experimento: `emb_text_v2` sem o título.

**Checks de qualidade implementados (2.5a):**
- **Chaves:** ID primário da fonte presente, título não vazio, proveniência (`raw_uri` e `last_run_id`) nos IDs primários.
- **Cobertura por tipo**, com limiares de aviso e falha: sinopse, ano, duração, gêneros, keywords, elenco, IMDb.
- **Retenção TMDB:** itens sem sincronização há mais de 180 dias dão falha; mais de 150 dias, aviso. Partições Bronze TMDB com mais de 180 dias dão falha.
- **Contagem:** queda de mais de 5% no catálogo em relação à execução anterior dá falha.
- **Schema da fonte:** chaves exigidas ausentes no Bronze dão falha; chaves novas ou removidas em relação à execução anterior dão aviso.
- **Informativos:** títulos ainda não lançados, itens com menos de 10 votos, taxa de vínculo com o TVmaze.

**Execução no banco dev (continuação, 2026-11-10)**
- `catalog expire` (simulação): 0 candidatos, pois tudo foi sincronizado há menos de 180 dias.
- **Snapshot Gold `catalog_items_20261110T203523Z_81efccf5`:** 8.000 itens (5.500 filmes e 2.500 séries) + 8.000 embeddings de 384 dimensões, 14 MB, 3,8 s. Re-execução com o mesmo conteúdo reaproveitou o snapshot. `git_commit = null`, porque o repositório ainda não tem commits.
- **Embeddings `emb_text_v2`:** 8.000 itens em 125,9 s.
- **Experimento de sanidade:** ver [relatório](experiments/content_similarity_2026-11-10.md) e ADR-016.

**Aceite da Fase 2**
- [x] Pipeline reproduzível: reprocessamento a partir do Bronze, TF-IDF determinístico por `corpus_hash`, modelo de embeddings fixado por commit, snapshots com hash de conteúdo.
- [x] Checks automatizados: `quality check`, com gate no snapshot Gold.
- [x] Embeddings consistentes com a versão: check de cobertura, desatualização e dimensão por `model_version`.
- [ ] 2.6, MinIO: adiado; o disco local atende, e a interface `RawStore` está pronta.

## Fase 3 — Modelos de similaridade e primeiro recomendador (concluída)

| # | Subtarefa | Status | Verificação |
|---|---|---|---|
| 3.0 | MLflow próprio no Compose (`127.0.0.1:5010`, SQLite + volume de artefatos) | ✅ | healthcheck; runs registrados com modelo e importâncias |
| 3.1 | Comparação de modelos: ST + cosseno, RRF, Rocchio (PRF e perfil), rerankers LR/SGD/LightGBM com rótulo fraco | ✅ | `tests/similarity/`; [relatório](experiments/similarity_models_2026-11-10.md); notebook `models/similarity_models.ipynb` |
| 3.2 | Baseline de popularidade (média bayesiana, m = p75 de votos por tipo) + regra de não lançados | ✅ | `test_top_rated_uses_bayesian_average`, `test_list_contract_filters_and_unreleased` |
| 3.3 | API: `/genres`, `/movies` (busca, filtros, ordenação, paginação), `/movies/{id}`, `/movies/{id}/similar` com diversidade, `reason` e fallback; CORS | ✅ | `tests/integration/test_api_catalog.py` (contrato, determinismo, fallback, CORS) |
| 3.4 | Vizinhos pré-computados versionados (`recs build-similar`, `recs activate`); TF-IDF ativo, reranker LogisticRegression gravado como challenger | ✅ | `test_neighbor_sets_idempotent_and_activation_swap`; runs em `serving-similar` no MLflow |
| 3.5 | Pôster, backdrop e coleção no catálogo (migração 0006 + reprocessamento do Bronze) | ✅ | `tests/ingestion/test_normalize_images.py` |

**Resultados medidos (2026-11-10, 8.000 itens, k = 10):**
- **Recall de coleção** (rótulo fraco): rerankers 0,985–0,986; TF-IDF 0,970; RRF 0,846; emb v2 0,605.
- **Gênero:** rerankers 0,964–0,969; emb v2 0,939; TF-IDF 0,806.
- **Perfil com 2 seeds:** Rocchio 0,985 (TF-IDF) e 0,637 (emb v2), acima de MaxSim e seed única.
- Benchmark completo pelo CLI: ~68 s em CPU.

**Execução no banco dev (2026-11-10, continuação)**
- Migração 0006 + `reprocess tmdb` (113 s): pôster em 99,9% dos filmes e 97,9% das séries; backdrop em 99,3% e 95,1%; 1.748 filmes com coleção. Fingerprint do conjunto de similaridade inalterado (`338b482788c7906e`), confirmando que as coleções do catálogo = as do Bronze.
- **Conjuntos de vizinhos (k = 50, 400 mil linhas cada):**
  - Ativo: `similar:tfidf|tfidf_text_v1|sklearn-1.9.1|p-3b78591d|c-f554cab2f96a|n-8000|k50`, 11 s.
  - Challenger (inativo): `similar:rerank_logreg|data-338b482788c7906e|p-4a5e1151|k50`, 26 s; métricas out-of-fold idênticas ao benchmark (recall 0,9853, gênero 0,9673).
- **Latência** (container da API, 1 cliente sequencial, 300 requisições por endpoint após 20 de aquecimento, máquina de 16 núcleos, Postgres local): p95 de 7,4 ms (detalhe) a 17,0 ms (busca); `/similar` p50 9,3 ms, p95 12,5 ms, p99 15,0 ms; 0 erros. Ver [api_latency_2026-11-10.json](experiments/api_latency_2026-11-10.json). Não testado sob concorrência.
- Snapshot Gold `catalog_items_20261111T013023Z_da8eb526`, agora com coleção e caminhos de imagem. `quality check`: 24 pass, 3 info, 1 warn, 0 fail.

**Aceite da Fase 3**
- [x] Baselines de popularidade e similaridade de conteúdo.
- [x] Avaliação técnica e qualitativa registrada no MLflow.
- [x] `/movies/{id}/similar` com testes de contrato e latência medida (p95 12,5 ms < 200 ms, condições acima).
- [x] Recomendações determinísticas para versão fixa (`test_similar_diversity_reasons_and_determinism`) e fallback funcional (`test_similar_fallback_when_no_neighbors`).
