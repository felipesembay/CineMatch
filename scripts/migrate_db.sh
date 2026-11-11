#!/usr/bin/env bash
# Copia o banco local (container `postgres` do Compose) para outro PostgreSQL, ex.: Azure
# Database for PostgreSQL Flexible Server. Cópia completa: schema, dados, sequências e a
# extensão pgvector. Nada é recalculado.
#
# Uso:
#   scripts/migrate_db.sh check      # só checagens no destino (conexão, versão, pgvector, vazio)
#   scripts/migrate_db.sh run        # dump + restore + verificação de contagens
#   scripts/migrate_db.sh verify     # só compara contagens origem x destino
#
# Destino: variável CINEMATCH_MIGRATION_TARGET_URL no .env, formato libpq:
#   postgresql://USUARIO:SENHA@HOST:5432/BANCO?sslmode=require
# A URL nunca é impressa nem passada na linha de comando (vai por variáveis PG* do ambiente).
#
# Pré-requisitos no Azure: extensão VECTOR liberada em `azure.extensions`; IP desta máquina
# liberado no firewall do servidor; banco de destino criado e vazio.
set -euo pipefail

cd "$(dirname "$0")/.."
MODE="${1:-check}"
SRC_CONTAINER="${SRC_CONTAINER:-cinematch-postgres-1}"
CLIENT_IMAGE="${CLIENT_IMAGE:-pgvector/pgvector:pg16}"
DUMP_DIR="$PWD/data/migration"
mkdir -p "$DUMP_DIR"  # antes de qualquer `docker run -v`, senão o Docker cria a pasta como root

if [[ -f .env ]]; then set -a; . ./.env; set +a; fi
: "${CINEMATCH_MIGRATION_TARGET_URL:?defina CINEMATCH_MIGRATION_TARGET_URL no .env}"
: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"

# URL -> variáveis PG* (sem imprimir nada).
eval "$(python3 - <<'PY'
import os, shlex
from urllib.parse import urlparse, parse_qs, unquote
u = urlparse(os.environ["CINEMATCH_MIGRATION_TARGET_URL"].replace("postgresql+psycopg://", "postgresql://"))
q = parse_qs(u.query)
env = {
    "PGHOST": u.hostname or "", "PGPORT": str(u.port or 5432),
    "PGUSER": unquote(u.username or ""), "PGPASSWORD": unquote(u.password or ""),
    "PGDATABASE": (u.path or "/").lstrip("/") or "postgres",
    "PGSSLMODE": q.get("sslmode", ["require"])[0],
}
for k, v in env.items():
    print(f"export {k}={shlex.quote(v)}")
PY
)"

target() {  # roda um cliente PG16 com as credenciais via ambiente
  docker run --rm -i --network host \
    -e PGHOST -e PGPORT -e PGUSER -e PGPASSWORD -e PGDATABASE -e PGSSLMODE \
    -e PGCONNECT_TIMEOUT=15 -v "$DUMP_DIR:/dump" "$CLIENT_IMAGE" "$@"
}
source_sql() { docker exec -i "$SRC_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -AtX "$@"; }
target_sql() { target psql -AtX -v ON_ERROR_STOP=1 "$@"; }

check() {
  echo "== destino: ${PGHOST}:${PGPORT}/${PGDATABASE} (usuário ${PGUSER}, sslmode=${PGSSLMODE})"
  echo "-- versão do servidor: $(target_sql -c 'SHOW server_version')"
  local allow
  allow="$(target_sql -c "SELECT current_setting('azure.extensions', true)" || true)"
  [[ -n "$allow" ]] && echo "-- azure.extensions: $allow"
  local avail
  avail="$(target_sql -c "SELECT default_version FROM pg_available_extensions WHERE name='vector'")"
  if [[ -z "$avail" ]]; then
    echo "ERRO: extensão 'vector' indisponível no destino. No Azure: Server parameters ->"
    echo "      azure.extensions -> marque VECTOR -> Save." >&2
    return 1
  fi
  echo "-- pgvector disponível no destino: $avail (origem: $(source_sql -c "SELECT extversion FROM pg_extension WHERE extname='vector'"))"
  local tables
  tables="$(target_sql -c "SELECT count(*) FROM pg_tables WHERE schemaname='public'")"
  if [[ "$tables" != "0" && "${FORCE:-0}" != "1" ]]; then
    echo "ERRO: destino já tem $tables tabelas em public. Use um banco vazio (ou FORCE=1 para" >&2
    echo "      restaurar por cima, apagando objetos existentes com --clean)." >&2
    return 1
  fi
  echo "-- destino vazio: ok"
  echo "-- tamanho da origem: $(source_sql -c "SELECT pg_size_pretty(pg_database_size(current_database()))")"
}

counts_sql="SELECT format('%s|%s', relname, (xpath('/row/c/text()', query_to_xml(format('SELECT count(*) AS c FROM %I.%I', schemaname, relname), false, true, '')))[1]::text) FROM pg_stat_user_tables WHERE schemaname='public' ORDER BY relname"

verify() {
  echo "== verificação de contagens (exatas)"
  diff <(source_sql -c "$counts_sql") <(target_sql -c "$counts_sql") \
    && echo "-- todas as tabelas com o mesmo número de linhas" \
    || { echo "ERRO: contagens divergentes (acima: < origem, > destino)" >&2; return 1; }
  echo "-- alembic: origem $(source_sql -c 'SELECT version_num FROM alembic_version') / destino $(target_sql -c 'SELECT version_num FROM alembic_version')"
  echo "-- conjunto de vizinhos ativo no destino: $(target_sql -c "SELECT model_version FROM neighbor_sets WHERE is_active")"
}

run() {
  check
  local dump="cinematch_$(date -u +%Y%m%dT%H%M%SZ).dump"
  echo "== dump da origem -> data/migration/$dump"
  echo "   (pare escritas na origem durante a migração: docker compose stop api)"
  docker exec "$SRC_CONTAINER" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --format=custom --compress=6 --no-owner --no-privileges > "$DUMP_DIR/$dump"
  echo "-- dump: $(du -h "$DUMP_DIR/$dump" | cut -f1)"
  echo "== restore no destino"
  local clean=()
  [[ "${FORCE:-0}" == "1" ]] && clean=(--clean --if-exists)
  # Sem COMMENT ON EXTENSION: no Azure o admin não é superusuário e o comentário falharia.
  target pg_restore -l "/dump/$dump" | grep -v 'COMMENT - EXTENSION' > "$DUMP_DIR/$dump.list"
  target pg_restore --no-owner --no-privileges --exit-on-error --jobs=4 "${clean[@]}" \
    -L "/dump/$dump.list" -d "$PGDATABASE" "/dump/$dump"
  target_sql -c "SET client_min_messages = error" -c "ANALYZE" > /dev/null
  verify
  echo "== concluído. Aponte a aplicação para o destino (ver docs/OPERATIONS.md, 'Banco no Azure')."
}

case "$MODE" in
  check) check ;;
  run) run ;;
  verify) verify ;;
  *) echo "uso: $0 check|run|verify" >&2; exit 2 ;;
esac
