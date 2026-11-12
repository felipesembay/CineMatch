"""`ingest translations`: grava pt-BR, retomável, Bronze com proveniência, 404 tolerado."""

from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from sqlalchemy import Engine, text

from cinematch.ingestion.http import NotFoundError
from cinematch.ingestion.raw_store import LocalRawStore, read_records
from cinematch.ingestion.tmdb.translations import backfill_translations, missing_items
from tests.integration.test_api_catalog import ids as ids


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def translations(self, item_type: str, tmdb_id: int) -> dict[str, Any]:
        self.calls.append((item_type, tmdb_id))
        if tmdb_id == 8:
            raise NotFoundError("not found", 404)
        data = {"title": f"Título {tmdb_id}", "overview": f"Sinopse {tmdb_id}"}
        if tmdb_id == 5:  # sem tradução brasileira
            return {"id": tmdb_id, "translations": []}
        return {
            "id": tmdb_id,
            "translations": [{"iso_639_1": "pt", "iso_3166_1": "BR", "data": data}],
        }


def test_backfill_writes_translations_and_resumes(
    engine: Engine, ids: dict[str, int], tmp_path: Path
) -> None:
    client = FakeClient()
    first = backfill_translations(engine, client, LocalRawStore(tmp_path), max_items=3)  # type: ignore[arg-type]
    assert first["written"] == 3 and len(client.calls) == 3
    rest = backfill_translations(engine, client, LocalRawStore(tmp_path))  # type: ignore[arg-type]
    assert rest["pending"] == len(ids) - 3  # retoma só os que faltam
    assert rest["not_found"] == 1 and rest["source_none"] == 1
    assert len(client.calls) == len(ids)
    assert missing_items(engine) == [(ids["Lonely 100% _Odd_"], "movie", "8")]  # 404 fica pendente

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT title, overview, source_language, raw_uri FROM item_translations"
                " WHERE item_id = :i"
            ),
            {"i": ids["Alpha Heist"]},
        ).one()
        none = conn.execute(
            text("SELECT title, source_language FROM item_translations WHERE item_id = :i"),
            {"i": ids["Beta Drama"]},
        ).one()
    assert (row.title, row.overview, row.source_language) == ("Título 1", "Sinopse 1", "pt-BR")
    assert tuple(none) == (None, "none")
    raw = list(read_records(Path(url2pathname(urlparse(row.raw_uri).path))))
    assert raw[0]["entity"] == "movie_translations" and raw[0]["endpoint"].endswith("/translations")
