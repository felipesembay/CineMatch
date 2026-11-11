"""Embeddings de itens em pgvector, versionados e incrementais.

`model_version = "<repo>@<commit[:12]>|<text_version>"`: o commit do Hugging Face é
resolvido no download e fixa os pesos exatos. Só itens novos ou com texto alterado
(`text_hash` diferente) são re-encodados. Vetores são normalizados (L2): cosseno = produto
interno.
"""

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from cinematch.data.schema import item_embeddings
from cinematch.features.text import (
    EMBED_TEXT_VERSION,
    build_embedding_text,
    load_item_documents,
    text_hash,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


ST_FILES = [
    "*.json",
    "model.safetensors",
    "sentencepiece.bpe.model",
    "*.txt",
    "1_Pooling/*",
]


class Encoder(Protocol):
    model_id: str  # "<repo>@<commit>"
    dimensions: int

    def encode(self, texts: Sequence[str], batch_size: int) -> np.ndarray: ...


class SentenceTransformerEncoder:
    """Carrega o modelo de um snapshot fixo do Hugging Face (CPU)."""

    def __init__(self, repo_id: str = DEFAULT_MODEL, revision: str | None = None) -> None:
        from huggingface_hub import snapshot_download
        from sentence_transformers import SentenceTransformer

        # Só o necessário para sentence-transformers em PyTorch (evita ONNX/OpenVINO/TF).
        path = snapshot_download(repo_id, revision=revision, allow_patterns=ST_FILES)
        commit = path.rstrip("/").rsplit("/", 1)[-1]
        self.model = SentenceTransformer(path, device="cpu")
        self.model_id = f"{repo_id}@{commit[:12]}"
        self.dimensions = int(self.model.get_embedding_dimension() or 0)

    def encode(self, texts: Sequence[str], batch_size: int) -> np.ndarray:
        return np.asarray(
            self.model.encode(
                list(texts),
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            ),
            dtype=np.float32,
        )


def model_version(encoder: Encoder, text_version: str = EMBED_TEXT_VERSION) -> str:
    return f"{encoder.model_id}|{text_version}"


@dataclass
class EmbeddingStats:
    model_version: str
    items: int
    encoded: int
    unchanged: int
    duration_s: float

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def compute_embeddings(
    engine: Engine,
    encoder: Encoder,
    batch_size: int = 64,
    limit: int | None = None,
    text_version: str = EMBED_TEXT_VERSION,
) -> EmbeddingStats:
    started = time.perf_counter()
    version = model_version(encoder, text_version)
    with engine.connect() as conn:
        docs = load_item_documents(conn)
        existing = dict(
            conn.execute(
                select(item_embeddings.c.item_id, item_embeddings.c.text_hash).where(
                    item_embeddings.c.model_version == version
                )
            ).all()
        )
    if limit is not None:
        docs = docs[:limit]

    pending = []
    for doc in docs:
        txt = build_embedding_text(doc, text_version)
        h = text_hash(txt)
        if existing.get(doc.item_id) != h:
            pending.append((doc.item_id, txt, h))

    for start in range(0, len(pending), batch_size * 8):
        chunk = pending[start : start + batch_size * 8]
        vectors = encoder.encode([t for _, t, _ in chunk], batch_size)
        if vectors.shape != (len(chunk), encoder.dimensions):
            raise ValueError(f"formato inesperado de embeddings: {vectors.shape}")
        rows = [
            {
                "item_id": item_id,
                "model_version": version,
                "text_version": text_version,
                "text_hash": h,
                "dimensions": encoder.dimensions,
                "embedding": vec,
            }
            for (item_id, _, h), vec in zip(chunk, vectors, strict=True)
        ]
        stmt = pg_insert(item_embeddings).values(rows)
        with engine.begin() as conn:
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=[item_embeddings.c.item_id, item_embeddings.c.model_version],
                    set_={
                        "text_hash": stmt.excluded.text_hash,
                        "embedding": stmt.excluded.embedding,
                        "dimensions": stmt.excluded.dimensions,
                        "created_at": stmt.excluded.created_at,
                    },
                )
            )
        logger.info("embeddings_batch", extra={"done": start + len(chunk), "total": len(pending)})

    stats = EmbeddingStats(
        model_version=version,
        items=len(docs),
        encoded=len(pending),
        unchanged=len(docs) - len(pending),
        duration_s=round(time.perf_counter() - started, 2),
    )
    logger.info("embeddings_done", extra=stats.as_dict())
    return stats
