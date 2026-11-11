# CineMatch

Plataforma de recomendação de filmes e séries com catálogo de fontes externas autorizadas (TMDB, TVmaze), interações first-party e pipeline de MLOps.

> **Estado atual:** Fase 1 concluída; Fase 2 em andamento (TVmaze implementado). Já existem: API FastAPI com `/health` e `/health/ready`; schema do catálogo com migrações Alembic; ingestão TMDB incremental e idempotente, com checkpoint/resume, Bronze JSONL.gz e proveniência. Ainda **não** há modelos nem recomendações. Catálogo real: 5.000 títulos TMDB; TVmaze parcial.

- Especificação: [CINEMATCH_PROJECT_SPEC.md](CINEMATCH_PROJECT_SPEC.md)
- Plano: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)
- Decisões (ADRs): [docs/DECISIONS.md](docs/DECISIONS.md)
- Fontes, termos e atribuição: [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)

## Requisitos

- [uv](https://docs.astral.sh/uv/) ≥ 0.4 (instala Python 3.12 se necessário)
- Docker + Docker Compose v2 (`docker compose` ou `docker-compose`)

## Setup local

```bash
cp .env.example .env          # ajuste POSTGRES_PASSWORD e CINEMATCH_DATABASE_URL
uv sync                       # cria .venv com Python 3.12 e dependências (incl. dev)
```

## Comandos

| Ação | Comando |
|---|---|
| Lint | `uv run ruff check . && uv run ruff format --check .` |
| Formatar | `uv run ruff format .` |
| Testes unitários | `uv run pytest` |
| Testes de integração (Postgres) | ver "Testes de integração" abaixo |
| Subir stack | `docker compose up -d --build` |
| Ver logs | `docker compose logs -f api` |
| Derrubar stack | `docker compose down` (adicione `-v` para apagar o volume do banco) |
| API local sem Docker | `uv run uvicorn cinematch.api.main:app --reload --port 8010` (requer Postgres acessível para `/health/ready`) |

Verificação após subir:

```bash
curl -s localhost:8010/health        # {"status":"ok","version":"0.1.0"}
curl -s localhost:8010/health/ready  # {"status":"ok","checks":{"database":"ok"}}
```

Documentação interativa: http://localhost:8010/docs

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

## API de catálogo e recomendações

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

Imagens vêm do CDN do TMDB; o frontend deve exibir a atribuição do TMDB. CORS: `CINEMATCH_CORS_ORIGINS` (lista JSON).

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

Requerem um banco cujo nome termine em `_test`. O schema é recriado a cada sessão.

```bash
docker compose exec postgres sh -c 'createdb -U "$POSTGRES_USER" cinematch_test'   # uma vez
set -a; . ./.env; set +a
CINEMATCH_TEST_DATABASE_URL="${CINEMATCH_DATABASE_URL%/*}/cinematch_test" uv run pytest
```

## Estrutura

```text
src/cinematch/        pacote Python
  api/                FastAPI (health)
  data/schema.py      schema SQLAlchemy (fonte das migrações)
  ingestion/          cliente HTTP com retry, Bronze, repositório, pipeline
    tmdb/             adaptador TMDB (cliente + normalização)
  similarity/         modelos de similaridade item-item e benchmark (rótulo fraco)
  serving/            consultas de catálogo e vizinhos pré-computados (lidos pela API)
  tracking.py         registro no MLflow
  cli.py              comando `cinematch`
models/               notebooks de experimentos
migrations/           Alembic
tests/                Pytest (unitários; integração com marcador `integration`)
docs/                 plano de implementação e ADRs
Dockerfile            imagem da API (multi-stage com uv, usuário não-root)
docker-compose.yml    api + postgres (pgvector) + mlflow
```

## Fontes de dados e atribuição

This product uses the TMDB API but is not endorsed or certified by TMDB. Dados de séries complementares de [TVmaze](https://www.tvmaze.com) (CC BY-SA 4.0). Uso não comercial; ver restrições e risco aceito em [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) (ADR-008).

## Limitações conhecidas

- Sem autenticação, API de catálogo ou recomendações (fases seguintes).
- Os termos do TMDB restringem uso em ML (ADR-008, risco aceito pelo responsável).
- CI escrita, mas não executada (repositório sem remoto).
