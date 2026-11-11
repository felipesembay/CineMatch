import pytest

from cinematch.ingestion.tmdb.normalize import normalize


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/abc_D-1.jpg", "/abc_D-1.jpg"),
        ("https://evil.example/x.jpg", None),
        ("/../etc/passwd", None),
        ("", None),
        (None, None),
    ],
)
def test_image_path_only_accepts_tmdb_relative_paths(value: object, expected: object) -> None:
    rec = normalize("movie", {"id": 1, "title": "X", "poster_path": value})
    assert rec.poster_path == expected


def test_collection_extracted_and_hashed() -> None:
    base = {"id": 1, "title": "X"}
    rec = normalize("movie", {**base, "belongs_to_collection": {"id": 10, "name": " Saga "}})
    assert (rec.collection_id, rec.collection_name) == (10, "Saga")
    assert normalize("movie", {**base, "belongs_to_collection": "bad"}).collection_id is None
    assert rec.content_hash() != normalize("movie", base).content_hash()
