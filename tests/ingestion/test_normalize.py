from datetime import date

import pytest

from cinematch.ingestion.tmdb.normalize import NormalizationError, normalize
from tests.ingestion.factories import movie_payload, tv_payload


def test_movie_normalized() -> None:
    rec = normalize("movie", movie_payload(550))
    assert rec.source == "tmdb"
    assert rec.item_type == "movie"
    assert rec.external_id == "550"
    assert rec.title == "Movie 550"
    assert rec.release_date == date(2020, 5, 17)
    assert rec.release_year == 2020
    assert rec.runtime_minutes == 112
    assert [g.name for g in rec.genres] == ["Drama", "Comedy"]


def test_tv_normalized() -> None:
    rec = normalize("tv", tv_payload(1399))
    assert rec.title == "Show 1399"
    assert rec.original_title == "Original Show 1399"
    assert rec.release_year == 2018
    assert rec.runtime_minutes == 45
    assert rec.status == "Returning Series"


def test_missing_and_invalid_values_become_none() -> None:
    rec = normalize(
        "movie",
        movie_payload(1, overview="   ", release_date="", runtime=0, popularity="x", genres=None),
    )
    assert rec.overview is None
    assert rec.release_date is None and rec.release_year is None
    assert rec.runtime_minutes is None
    assert rec.popularity is None
    assert rec.genres == ()


def test_tv_without_run_time() -> None:
    assert normalize("tv", tv_payload(2, episode_run_time=[])).runtime_minutes is None


def test_tv_runtime_falls_back_to_last_episode() -> None:
    payload = tv_payload(3, episode_run_time=[], last_episode_to_air={"runtime": 52})
    assert normalize("tv", payload).runtime_minutes == 52
    # episode_run_time tem prioridade quando presente
    payload = tv_payload(4, episode_run_time=[30], last_episode_to_air={"runtime": 52})
    assert normalize("tv", payload).runtime_minutes == 30


def test_title_falls_back_to_original() -> None:
    assert normalize("movie", movie_payload(3, title="")).title == "Original 3"


def test_without_any_title_fails() -> None:
    with pytest.raises(NormalizationError):
        normalize("movie", movie_payload(4, title=None, original_title=None))


def test_without_id_fails() -> None:
    payload = movie_payload(5)
    del payload["id"]
    with pytest.raises(NormalizationError):
        normalize("movie", payload)


def test_content_hash_stable_and_sensitive() -> None:
    a = normalize("movie", movie_payload(6)).content_hash()
    assert a == normalize("movie", movie_payload(6)).content_hash()
    assert a != normalize("movie", movie_payload(6, overview="changed")).content_hash()
    # Campos não normalizados (ex.: credits) não alteram o hash.
    assert a == normalize("movie", movie_payload(6, credits={"cast": [1]})).content_hash()
