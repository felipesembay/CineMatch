"""Schema do catálogo (SQLAlchemy Core). Fonte da verdade das migrações Alembic.

Mudanças aqui exigem nova migração; o teste de integração roda `alembic check`.
"""

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

NAMING = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
metadata = MetaData(naming_convention=NAMING)


def _ts(name: str) -> Column:
    return Column(name, DateTime(timezone=True), nullable=False, server_default=func.now())


catalog_items = Table(
    "catalog_items",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("item_type", String(8), nullable=False),
    Column("title", Text, nullable=False),
    Column("original_title", Text),
    Column("overview", Text),
    Column("release_date", Date),
    Column("release_year", Integer),
    Column("original_language", String(16)),
    Column("runtime_minutes", Integer),
    Column("status", String(64)),
    # Caminhos relativos de imagem do TMDB (URL montada na API com o tamanho desejado).
    Column("poster_path", Text),
    Column("backdrop_path", Text),
    # Coleção TMDB (`belongs_to_collection`, só filmes): diversidade e rótulo fraco.
    Column("collection_id", Integer),
    Column("collection_name", Text),
    # Agregados externos (fonte em item_external_ids); NÃO são avaliações CineMatch.
    Column("popularity", Float),
    Column("vote_average", Float),
    Column("vote_count", Integer),
    Column("content_hash", String(64), nullable=False),
    _ts("created_at"),
    _ts("updated_at"),
    CheckConstraint("item_type IN ('movie', 'tv')", name="item_type"),
)
Index(
    "ix_catalog_items_item_type_release_year",
    catalog_items.c.item_type,
    catalog_items.c.release_year,
)

item_external_ids = Table(
    "item_external_ids",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "item_id",
        BigInteger,
        ForeignKey("catalog_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("source", String(32), nullable=False),
    # IDs TMDB de filme e série colidem (mesmo número, recursos distintos): tipo entra na chave.
    Column("external_type", String(16), nullable=False),
    Column("external_id", String(64), nullable=False),
    Column("first_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_synced_at", DateTime(timezone=True), nullable=False),
    Column("last_run_id", UUID(as_uuid=False)),
    Column("raw_uri", Text),
    # NULL = fonte primária do item. Senão, como o vínculo foi estabelecido
    # (ex.: 'tmdb_external_ids', 'imdb_id', 'thetvdb_id'). Nunca correspondência aproximada.
    Column("match_method", String(32)),
    UniqueConstraint("source", "external_type", "external_id"),
)

genres = Table(
    "genres",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(32), nullable=False),
    Column("external_id", String(64), nullable=False),
    Column("name", Text, nullable=False),
    UniqueConstraint("source", "external_id"),
)

item_genres = Table(
    "item_genres",
    metadata,
    Column(
        "item_id", BigInteger, ForeignKey("catalog_items.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("genre_id", Integer, ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)

ingestion_runs = Table(
    "ingestion_runs",
    metadata,
    Column("run_id", UUID(as_uuid=False), primary_key=True),
    Column("source", String(32), nullable=False),
    Column("status", String(16), nullable=False),
    Column("params", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    # Checkpoint de descoberta: {"movie": {"last_page": 3, "done": false}, ...}
    Column("state", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("metrics", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("error", Text),
    _ts("started_at"),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint("status IN ('running', 'succeeded', 'partial', 'failed')", name="status"),
)

ingestion_run_items = Table(
    "ingestion_run_items",
    metadata,
    Column(
        "run_id",
        UUID(as_uuid=False),
        ForeignKey("ingestion_runs.run_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("external_type", String(16), primary_key=True),
    Column("external_id", String(64), primary_key=True),
    Column("status", String(16), nullable=False, server_default="pending"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("last_error", Text),
    _ts("updated_at"),
    CheckConstraint("status IN ('pending', 'done', 'failed', 'not_found')", name="status"),
)
Index(
    "ix_ingestion_run_items_run_id_status",
    ingestion_run_items.c.run_id,
    ingestion_run_items.c.status,
)

# Silver específica do TVmaze (CC BY-SA 4.0). Ligação ao catálogo via item_external_ids.
tvmaze_shows = Table(
    "tvmaze_shows",
    metadata,
    Column("tvmaze_id", Integer, primary_key=True, autoincrement=False),
    Column("name", Text, nullable=False),
    Column("show_type", String(32)),
    Column("language", String(32)),
    Column("genres", JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    Column("status", String(32)),
    Column("runtime_minutes", Integer),
    Column("average_runtime_minutes", Integer),
    Column("premiered", Date),
    Column("ended", Date),
    Column("network", Text),
    Column("web_channel", Text),
    Column("country_code", String(8)),
    Column("rating_average", Float),
    Column("weight", Integer),
    Column("summary", Text),
    Column("imdb_id", String(16), index=True),
    Column("thetvdb_id", String(16), index=True),
    Column("tvrage_id", String(16)),
    Column("url", Text),
    Column("source_updated_at", DateTime(timezone=True)),
    Column("content_hash", String(64), nullable=False),
    Column("first_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("last_synced_at", DateTime(timezone=True), nullable=False),
    Column("last_run_id", UUID(as_uuid=False)),
    Column("raw_uri", Text),
)

keywords = Table(
    "keywords",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(32), nullable=False),
    Column("external_id", String(64), nullable=False),
    Column("name", Text, nullable=False),
    UniqueConstraint("source", "external_id"),
)

item_keywords = Table(
    "item_keywords",
    metadata,
    Column(
        "item_id", BigInteger, ForeignKey("catalog_items.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("keyword_id", Integer, ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True),
)

# Minimização (LGPD/spec §5): só nome e departamento; sem gênero, foto ou biografia.
people = Table(
    "people",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("source", String(32), nullable=False),
    Column("external_id", String(64), nullable=False),
    Column("name", Text, nullable=False),
    Column("known_for_department", String(64)),
    UniqueConstraint("source", "external_id"),
)

item_credits = Table(
    "item_credits",
    metadata,
    Column(
        "item_id", BigInteger, ForeignKey("catalog_items.id", ondelete="CASCADE"), primary_key=True
    ),
    # credit_id do TMDB: único por participação (mesma pessoa pode ter vários papéis).
    Column("credit_id", String(64), primary_key=True),
    Column(
        "person_id",
        BigInteger,
        ForeignKey("people.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("credit_type", String(8), nullable=False),
    Column("character", Text),
    Column("department", String(64)),
    Column("job", String(64)),
    Column("ordering", Integer),
    CheckConstraint("credit_type IN ('cast', 'crew', 'creator')", name="credit_type"),
)

data_quality_runs = Table(
    "data_quality_runs",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    _ts("ran_at"),
    Column("status", String(8), nullable=False),
    Column("summary", JSONB, nullable=False),
    Column("results", JSONB, nullable=False),
    CheckConstraint("status IN ('pass', 'warn', 'fail')", name="status"),
)

# Embeddings versionados. `embedding` sem dimensão fixa: cada `model_version` define a sua
# (registrada em `dimensions`); índices ANN por modelo podem ser criados com cast + WHERE.
item_embeddings = Table(
    "item_embeddings",
    metadata,
    Column(
        "item_id", BigInteger, ForeignKey("catalog_items.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("model_version", String(200), primary_key=True),
    Column("text_version", String(32), nullable=False),
    Column("text_hash", String(64), nullable=False),
    Column("dimensions", Integer, nullable=False),
    Column("embedding", Vector(), nullable=False),
    _ts("created_at"),
)

# Artefatos de features fora do banco (ex.: TF-IDF em disco), com manifesto rastreável.
feature_artifacts = Table(
    "feature_artifacts",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("kind", String(32), nullable=False),
    Column("version", String(200), nullable=False),
    Column("uri", Text, nullable=False),
    Column("params", JSONB, nullable=False),
    Column("n_items", Integer, nullable=False),
    Column("corpus_hash", String(64), nullable=False),
    _ts("created_at"),
    UniqueConstraint("kind", "version", "corpus_hash"),
)

# Snapshots Gold imutáveis (spec §5/§6): rastreabilidade de datasets de treino/avaliação.
training_datasets = Table(
    "training_datasets",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("name", String(64), nullable=False),
    Column("snapshot_id", String(64), nullable=False, unique=True),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("uri", Text, nullable=False),
    Column("manifest", JSONB, nullable=False),
    _ts("created_at"),
    UniqueConstraint("name", "content_hash"),
)

# Listas de vizinhos pré-computadas em batch (serving de `/movies/{id}/similar`).
# A API só lê daqui: sem scikit-learn/torch na imagem, latência previsível e resposta
# determinística para uma `model_version` fixa. No máximo um conjunto ativo por `slot`.
neighbor_sets = Table(
    "neighbor_sets",
    metadata,
    Column("model_version", String(200), primary_key=True),
    Column("slot", String(32), nullable=False),  # ex.: "similar"
    Column("method", String(32), nullable=False),
    Column("k", Integer, nullable=False),
    Column("n_items", Integer, nullable=False),
    Column("params", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("metrics", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("is_active", Boolean, nullable=False, server_default=text("false")),
    _ts("created_at"),
)
Index(
    "uq_neighbor_sets_active_slot",
    neighbor_sets.c.slot,
    unique=True,
    postgresql_where=neighbor_sets.c.is_active,
)

item_neighbors = Table(
    "item_neighbors",
    metadata,
    Column(
        "model_version",
        String(200),
        ForeignKey("neighbor_sets.model_version", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "item_id",
        BigInteger,
        ForeignKey("catalog_items.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("rank", SmallInteger, primary_key=True),
    Column(
        "neighbor_id",
        BigInteger,
        ForeignKey("catalog_items.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("score", Float, nullable=False),
)
