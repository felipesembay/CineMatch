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

## Traduções pt-BR (título e sinopse)

Coletas novas já trazem `translations` no `append_to_response` dos detalhes. Para itens coletados antes:

```bash
uv run cinematch ingest translations --dry-run        # quantos itens faltam (= requisições)
uv run cinematch ingest translations                  # 1 req/item; retomável; Bronze entity=<tipo>_translations
```

Título só da tradução brasileira (os títulos de Portugal costumam ser outros); a sinopse aceita pt-PT como reserva. Itens sem tradução gravam `source_language='none'` e não são buscados de novo.

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
uv run cinematch recs activate '<model_version>' [--mlflow] # troca/rollback; --mlflow move o alias champion
uv run cinematch recs delete '<model_version>' [...]         # apaga conjuntos inativos (recusa o ativo)
docker compose up -d --build api                             # http://127.0.0.1:8010/docs
uv run cinematch evaluate api-latency --out docs/experiments/<arquivo>.json
```

| Rota | Função |
|---|---|
| `GET /genres` | gêneros com contagem de itens |
| `GET /movies` | busca (`q`), filtros (`type`, `genre`, `year_from`, `year_to`), `sort=popular\|top_rated\|recent\|relevance`, paginação; omite não lançados salvo `include_unreleased=true` |
| `GET /movies/{id}` | detalhe com elenco, direção, keywords e links externos |
| `GET /movies/{id}/similar` | vizinhos do conjunto ativo, `max_per_collection` (diversidade), `reason` (pt-BR) e `reason_key`/`reason_args` (estruturado), `model_version`, `request_id`, `fallback` |
| `GET /featured` | destaque da home: `personal` (logado com perfil) ou `weekly` (rodízio da semana ISO) |
| `POST /auth/register` · `/auth/login` · `/auth/logout` · `GET /auth/me` | conta e sessão (cookie `httpOnly`) |
| `GET/PUT /users/me/preferences` | gêneros, curtidos e não curtidos (onboarding) |
| `GET/PUT/DELETE /users/me/favorites[/{id}]` | favoritos |
| `POST /ratings` · `DELETE /ratings/{id}` · `GET /users/me/ratings` | avaliação 1-5 com comentário |
| `GET /recommendations/me` | "Achamos que você vai gostar" (Rocchio sobre vizinhos ativos) |
| `POST /events` | eventos idempotentes (impressões, cliques, favoritos, buscas) |
| `DELETE /users/me` | exclusão de conta e dados (LGPD) |

Rotas que devolvem títulos aceitam `lang=pt-BR` (padrão) ou `lang=en`: título e sinopse vêm de `item_translations` quando há tradução, senão do original. O detalhe informa `overview_language`.

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

### Model Registry

`recs build-similar --mlflow` loga o modelo no flavor sklearn (vetorizador TF-IDF ou reranker) e cria uma nova versão do modelo registrado **`cinematch-similar`**:

| Alias | Significado |
|---|---|
| `champion` | conjunto servido pela API (build com `--activate`, ou `recs activate --mlflow`) |
| `challenger` | último candidato gerado sem `--activate` (teste A/B) |

Cada versão tem a tag `neighbor_set` = `model_version` do conjunto no Postgres. A API continua lendo os vizinhos pré-computados, não o modelo. O registry é o catálogo auditável do que foi gerado e do que está servido. Para carregar o modelo servido: `mlflow.sklearn.load_model("models:/cinematch-similar@champion")`.

Um build que reaproveita um conjunto já gravado (mesmo corpus e parâmetros) não cria versão nova.

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
# com o banco no Azure, CINEMATCH_DATABASE_URL aponta para lá; os testes usam sempre o local
CINEMATCH_TEST_DATABASE_URL="${CINEMATCH_LOCAL_DATABASE_URL:-$CINEMATCH_DATABASE_URL}"
CINEMATCH_TEST_DATABASE_URL="${CINEMATCH_TEST_DATABASE_URL%/*}/cinematch_test" uv run pytest
```

## Banco no Azure (migração completa)

Copia o banco local inteiro (catálogo, features, vizinhos, usuários, histórico) para o Azure Database for PostgreSQL Flexible Server. Nada é recalculado. Ensaio local em 2026-11-11: 896 MB em ~20 s, contagens idênticas em todas as tabelas, com usuário sem privilégio de superusuário (como o admin do Azure).

**Execução real (2026-11-11):** antes, os 2 conjuntos de vizinhos inativos do catálogo de 8 mil itens foram apagados (`recs delete`, 800 mil linhas). Dump de 99 MB; `run` em 2 min 19 s; todas as contagens iguais, Alembic 0008 nos dois lados, mesmo conjunto ativo. pgvector 0.8.2 no Azure e 0.8.7 no local: o formato do tipo `vector` é o mesmo.

1. **No portal do Azure (servidor):**
   - *Server parameters* → `azure.extensions` → marque **VECTOR** → *Save*.
   - *Networking* → adicione o IP desta máquina às regras de firewall.
   - *Databases* → crie o banco `cinematch` (vazio).
2. **No `.env`:** use nomes próprios para o Azure (ex.: `AZURE_PG_*`), nunca `POSTGRES_*`: esses já configuram o container local e a última definição sobrescreve a primeira. Porta do Azure: **5432**. Defina `CINEMATCH_MIGRATION_TARGET_URL=postgresql://USUARIO:SENHA@SERVIDOR.postgres.database.azure.com:5432/cinematch?sslmode=require`. Caracteres especiais da senha precisam de URL-encoding (`@` → `%40`).
3. **Migrar:**
   ```bash
   docker compose stop api           # sem escritas durante a cópia
   scripts/migrate_db.sh check       # conexão, versão, pgvector liberado, destino vazio
   scripts/migrate_db.sh run         # pg_dump -> pg_restore -> ANALYZE -> contagens exatas
   ```
4. **Apontar a aplicação para o Azure** (no `.env`):
   ```bash
   CINEMATCH_DATABASE_URL=postgresql+psycopg://USUARIO:SENHA@SERVIDOR.postgres.database.azure.com:5432/cinematch?sslmode=require
   CINEMATCH_API_DATABASE_URL=${CINEMATCH_DATABASE_URL}   # usado pelo container da API
   CINEMATCH_DB_CONNECT_TIMEOUT_S=10
   CINEMATCH_LOCAL_DATABASE_URL=postgresql+psycopg://...@localhost:5434/cinematch   # rollback e testes
   ```
   Depois: `docker compose up -d api` e `curl -s localhost:8010/health/ready`.
5. **Rollback:** remova `CINEMATCH_API_DATABASE_URL` e volte `CINEMATCH_DATABASE_URL` para o valor de `CINEMATCH_LOCAL_DATABASE_URL`. O banco local continua intacto. Escritas feitas no Azure depois da troca não existem no banco local.

**Pool para banco remoto:** cada ida e volta custa ~40 ms. Por isso:
- leituras do catálogo usam `AUTOCOMMIT`, sem `BEGIN`/`ROLLBACK`;
- não há `pool_pre_ping` (`CINEMATCH_DB_POOL_PRE_PING=false`);
- conexões são recicladas a cada 240 s (`CINEMATCH_DB_POOL_RECYCLE_S`), abaixo do idle de 4 min do load balancer do Azure;
- TCP keepalive ligado;
- `tcp_user_timeout=10s`: na medição, a rede até o Azure travou de 2 s a 60 s algumas vezes, inclusive em `SELECT 1` direto do host, então um travamento vira erro e reconexão em vez de prender o worker.

`scripts/migrate_db.sh verify` compara de novo as contagens a qualquer momento. O dump fica em `data/migration/` (fora do Git). Testes de integração continuam usando o banco local `cinematch_test`; MLflow continua local.

## Login com Google

1. Google Cloud Console → *APIs e serviços* → *Tela de consentimento OAuth* (tipo Externo, escopos `openid`, `email`, `profile`).
2. *Credenciais* → *Criar ID do cliente OAuth* → **Aplicativo da Web**. URIs de redirecionamento autorizados:
   - `http://localhost:5173/api/auth/google/callback` (Vite em dev)
   - `http://localhost:8088/api/auth/google/callback` (nginx no Compose)

   Atenção: são **URIs de redirecionamento autorizados**, com o caminho `/api/auth/google/callback`. *Origens JavaScript autorizadas* (só `http://localhost:5173`) não bastam: o Google recusa com `redirect_uri_mismatch`. Se quiser usar `127.0.0.1`, cadastre também `http://127.0.0.1:5173/api/auth/google/callback` e `http://127.0.0.1:8088/api/auth/google/callback`.
3. No `.env`: `CINEMATCH_GOOGLE_CLIENT_ID` e `CINEMATCH_GOOGLE_CLIENT_SECRET`. O JSON baixado do console (`client_secret_*.json`) é ignorado pelo Git; depois de copiar os valores, apague-o. Reinicie a API. O botão "Continuar com Google" aparece no login quando `GET /auth/providers` devolve `google: true`.
4. Acesse o app por `localhost` (não `127.0.0.1`): o `redirect_uri` é montado a partir da origem do navegador e precisa estar cadastrado.
