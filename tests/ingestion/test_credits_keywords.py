from cinematch.ingestion.tmdb.normalize import MAX_CAST, normalize
from tests.ingestion.factories import movie_payload, tv_payload


def _cast(i: int, person: int | None = None) -> dict:
    pid = person if person is not None else 1000 + i
    return {
        "id": pid,
        "name": f"Actor {pid}",
        "character": f"Role {i}",
        "order": i,
        "credit_id": f"c{i}",
        "known_for_department": "Acting",
        "gender": 2,
    }


CREDITS = {
    "cast": [_cast(i) for i in range(MAX_CAST + 5)],
    "crew": [
        {"id": 1, "name": "Dir", "department": "Directing", "job": "Director", "credit_id": "d1"},
        {"id": 2, "name": "Grip", "department": "Crew", "job": "Grip", "credit_id": "g1"},
        {
            "id": 3,
            "name": "Writer",
            "department": "Writing",
            "job": "Screenplay",
            "credit_id": "w1",
        },
        {"id": 4, "name": "", "department": "Writing", "job": "Writer", "credit_id": "x1"},
    ],
}


def test_movie_keywords_and_credits_filtered() -> None:
    rec = normalize("movie", movie_payload(1, credits=CREDITS))
    assert [k.name for k in rec.keywords] == ["friendship"]
    cast = [c for c in rec.credits if c.credit_type == "cast"]
    crew = [c for c in rec.credits if c.credit_type == "crew"]
    assert len(cast) == MAX_CAST
    assert max(c.ordering or 0 for c in cast) == MAX_CAST - 1
    assert {c.job for c in crew} == {"Director", "Screenplay"}  # Grip e nome vazio descartados


def test_credit_has_no_sensitive_fields() -> None:
    rec = normalize("movie", movie_payload(1, credits=CREDITS))
    assert "gender" not in rec.credits[0].model_dump()


def test_tv_keywords_use_results_and_creators() -> None:
    rec = normalize(
        "tv",
        tv_payload(
            1,
            keywords={"results": [{"id": 9, "name": "zombie"}, {"id": 9, "name": "zombie"}]},
            created_by=[{"id": 7, "name": "Creator", "credit_id": "cr1"}],
            credits={"cast": [], "crew": []},
        ),
    )
    assert [k.external_id for k in rec.keywords] == ["9"]  # deduplicado
    assert [(c.credit_type, c.person_name) for c in rec.credits] == [("creator", "Creator")]


def test_same_person_multiple_roles_kept() -> None:
    credits = {
        "cast": [_cast(0, person=1)],
        "crew": [{"id": 1, "name": "Actor 1", "job": "Director", "credit_id": "d1"}],
    }
    rec = normalize("movie", movie_payload(1, credits=credits))
    assert sorted(c.credit_type for c in rec.credits) == ["cast", "crew"]


def test_credits_change_hash() -> None:
    a = normalize("movie", movie_payload(1)).content_hash()
    assert a != normalize("movie", movie_payload(1, credits=CREDITS)).content_hash()
