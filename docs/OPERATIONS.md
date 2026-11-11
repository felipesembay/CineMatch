# Operação do CineMatch

Referência completa dos comandos (`cinematch` CLI, Docker Compose e testes). Visão geral do projeto no [README](../README.md).

## Ambiente

```bash
cp .env.example .env                       # ajuste POSTGRES_PASSWORD e CINEMATCH_DATABASE_URL
uv sync --extra ml                          # Python 3.12 + deps (o extra `ml` traz scikit-learn, torch CPU, LightGBM, MLflow)
docker compose up -d --build                # postgres, api (:8010), web (:8088), mlflow (:5010)
```

| Ação | Comando |
|---|---|
| Lint | `uv run ruff check . && uv run ruff format --check .` |
| Testes (unitários) | `uv run pytest` |
| Logs | `docker compose logs -f api` |
| Derrubar | `docker compose down` (`-v` apaga os volumes) |
| API sem Docker | `uv run uvicorn cinematch.api.main:app --reload --port 8010` |
| Frontend em dev | `cd frontend/cinematch && npm install && npm run dev` (proxy `/api` → `:8010`) |

## Migrações

```bash
docker compose run --rm api cinematch db upgrade      # no container
uv run cinematch db upgrade                           # no host (usa CINEMATCH_DATABASE_URL)
```

## Ingestão TMDB

1. Gere um **API Read Access Token** em https://www.themoviedb.org/settings/api e coloque-o em `TMDB_API_TOKEN` no `.env`. Nunca em arquivo versionado.
2. Leia [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md): há restrições de ML e um cache máximo de 6 meses.

```bash
# coleta pequena (padrão: 50 por tipo; acima de 500 itens no total exige --yes)
uv run cinematch ingest tmdb --types movie tv --max-items 50
# expandir: buscar só itens novos (pula os sincronizados nos últimos 30 dias)
uv run cinematch ingest tmdb --types movie --max-items 2500 --skip-synced-within-days 30 --yes
# retomar run interrompido/parcial
uv run cinematch ingest tmdb --resume <run_id>
# acompanhar
uv run cinematch ingest status [run_id]
# retenção Bronze (máx. 180 dias, termos TMDB)
uv run cinematch ingest purge-bronze --older-than-days 180
```

No container: `docker compose run --rm api cinematch ingest tmdb --max-items 50`. O Bronze fica no volume `bronze`; no host, em `./data/bronze`.

Saída: JSON com `run_id`, `status` (`succeeded`/`partial`/`failed`) e métricas (`discovered`, `inserted`, `updated`, `unchanged`, `not_found`, `failed`, `http_requests`, `http_retries`, `http_rate_limited`, `duration_s`). Exit 0 só em `succeeded`.

## Ingestão TVmaze e resolução de entidades

API pública, sem chave, licença CC BY-SA 4.0 (ver [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)).

```bash
uv run cinematch reprocess tmdb                   # re-normaliza o Bronze TMDB (crosswalk, keywords, créditos), sem API
uv run cinematch ingest tvmaze                    # 2 páginas (~500 shows), padrão
uv run cinematch ingest tvmaze --all --yes        # índice completo (~300+ páginas, ~6 min a 1 req/s)
uv run cinematch ingest tvmaze --resume <run_id>
uv run cinematch link tvmaze                      # recalcula vínculos sem chamar a API
```

Os shows são ligados às séries do catálogo **somente** por IMDb/TheTVDB coincidentes; casos ambíguos não são ligados.

## Features de conteúdo (extra `ml`)

```bash
uv sync --extra ml                                         # scikit-learn, sentence-transformers, torch CPU
uv run --extra ml cinematch features tfidf                 # artefato TF-IDF versionado em data/artifacts
uv run --extra ml cinematch features embeddings            # incremental; 1ª vez baixa o modelo (~470 MB)
uv run --extra ml cinematch features neighbors <item_id> --method embedding|tfidf -k 10
```

Modelo padrão: `paraphrase-multilingual-MiniLM-L12-v2`. Configurável por `CINEMATCH_EMBEDDING_MODEL` e `CINEMATCH_EMBEDDING_REVISION`.

## Snapshots Gold, retenção e avaliação

```bash
uv run --extra ml cinematch gold snapshot          # Parquet imutável + manifest; exige quality check sem falha
uv run cinematch gold list
uv run cinematch ingest tmdb-refresh --older-than-days 150 --max-items 2000   # renova itens antigos
uv run cinematch catalog expire                    # simulação; --yes remove itens TMDB > 180 dias
uv run --extra ml cinematch evaluate content-sanity --out docs/experiments/<arquivo>.json
```

## API e recomendações

```bash
uv run --extra ml cinematch recs build-similar --method tfidf --activate --mlflow   # baseline servido
uv run --extra ml cinematch recs build-similar --method rerank-logreg --mlflow      # challenger (inativo)
uv run cinematch recs list                                   # conjuntos gravados e qual está ativo
uv run cinematch recs activate '<model_version>'             # troca/rollback do conjunto servido
docker compose up -d --build api                             # http://127.0.0.1:8010/docs
uv run cinematch evaluate api-latency --out docs/experiments/<arquivo>.json
```

| Rota | Função |
|---|---|
| `GET /genres` | gêneros com contagem de itens |
| `GET /movies` | busca (`q`), filtros (`type`, `genre`, `year_from`, `year_to`), `sort=popular\|top_rated\|recent\|relevance`, paginação; omite não lançados salvo `include_unreleased=true` |
| `GET /movies/{id}` | detalhe com elenco, direção, keywords e links externos |
| `GET /movies/{id}/similar` | vizinhos do conjunto ativo, `max_per_collection` (diversidade), `reason`, `model_version`, `request_id`, `fallback` |
| `POST /auth/register` · `/auth/login` · `/auth/logout` · `GET /auth/me` | conta e sessão (cookie `httpOnly`) |
| `GET/PUT /users/me/preferences` | gêneros, curtidos e não curtidos (onboarding) |
| `GET/PUT/DELETE /users/me/favorites[/{id}]` | favoritos |
| `POST /ratings` · `DELETE /ratings/{id}` · `GET /users/me/ratings` | avaliação 1-5 com comentário |
| `GET /recommendations/me` | "Achamos que você vai gostar" (Rocchio sobre vizinhos ativos) |
| `POST /events` | eventos idempotentes (impressões, cliques, favoritos, buscas) |
| `DELETE /users/me` | exclusão de conta e dados (LGPD) |

Imagens vêm do CDN do TMDB; o frontend exibe a atribuição do TMDB. Em produção, frontend e API na mesma origem (nginx `/api`); `CINEMATCH_COOKIE_SECURE=true` com HTTPS. CORS/origens permitidas: `CINEMATCH_CORS_ORIGINS` (lista JSON).

## Experimentos de modelos e MLflow

MLflow exclusivo do CineMatch (não reusa MLflow de outros projetos):

```bash
docker compose up -d mlflow                     # UI em http://127.0.0.1:5010
uv run --extra ml cinematch evaluate similarity-models --mlflow \
    --out docs/experiments/<arquivo>.json       # cosseno, RRF, Rocchio, LR/SGD/LightGBM
```

Notebook interativo (mesmo código, com gráficos, demo de perfil e revisão qualitativa):

```bash
uv sync --extra ml --group notebook
uv run jupyter lab models/                      # abra similarity_models.ipynb
```

Os modelos supervisionados usam rótulo fraco (mesma coleção TMDB), não interações de usuários. Ver ADR-017.

## Qualidade de dados

```bash
uv run cinematch quality check          # mostra só avisos/falhas; grava em data_quality_runs
uv run cinematch quality check --all    # lista todos os checks
uv run cinematch quality check --json   # relatório completo
```

Exit 1 quando algum check falha.

## Testes de integração

Requerem um banco cujo nome termine em `_test` (o schema é recriado a cada sessão).

```bash
docker compose exec postgres sh -c 'createdb -U "$POSTGRES_USER" cinematch_test'   # uma vez
set -a; . ./.env; set +a
CINEMATCH_TEST_DATABASE_URL="${CINEMATCH_DATABASE_URL%/*}/cinematch_test" uv run pytest
```
