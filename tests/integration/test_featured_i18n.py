"""Destaque da home (rodízio semanal / personalizado) e idioma de exibição (pt-BR / en)."""

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from cinematch.api.main import create_app
from cinematch.config import Settings
from cinematch.db import get_engine
from cinematch.serving import catalog as cat
from tests.integration.test_api_catalog import _store
from tests.integration.test_api_catalog import ids as ids


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    app = create_app(Settings(log_json=False))
    app.dependency_overrides[get_engine] = lambda: engine
    with TestClient(app) as c:
        yield c


def _translate(engine: Engine, item_id: int, title: str | None, overview: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO item_translations (item_id, language, title, overview,"
                " source_language) VALUES (:i, 'pt-BR', :t, :o, 'pt-BR')"
            ),
            {"i": item_id, "t": title, "o": overview},
        )


def test_weekly_featured_is_stable_within_week_and_rotates(
    engine: Engine, ids: dict[str, int]
) -> None:
    monday = date(2026, 11, 9)
    with engine.begin() as conn:  # notas altas p/ haver vários candidatos acima da média
        conn.execute(
            text("UPDATE catalog_items SET vote_average = 9.0 WHERE title LIKE 'Alpha Heist _'")
        )
    with engine.connect() as conn:
        week = [cat.weekly_featured(conn, monday + timedelta(days=d))["id"] for d in range(7)]
        assert len(set(week)) == 1  # mesma semana ISO: mesmo destaque
        picks = {cat.weekly_featured(conn, monday + timedelta(weeks=w))["id"] for w in range(30)}
    # candidatos: lançados, com >= 100 votos e nota >= média do tipo
    assert ids["Future Film"] not in picks and ids["Beta Drama"] not in picks
    assert len(picks) > 1  # roda entre semanas


def test_featured_visitor_weekly_and_logged_personal(
    client: TestClient, engine: Engine, ids: dict[str, int]
) -> None:
    visitor = client.get("/featured").json()
    assert visitor["kind"] == "weekly" and visitor["model_version"] == cat.FEATURED_VERSION
    assert visitor["item"]["reason_key"] == "weekly_pick" and visitor["item"]["backdrop_url"]
    assert visitor["period"] == cat.featured_week(date.today())

    client.post("/auth/register", json={"email": "f@example.com", "password": "senha-123456"})
    cold = client.get("/featured").json()
    assert cold["kind"] == "weekly"  # logado sem perfil: rodízio

    _store(engine, ids, "v-feat", {"Alpha Heist": ["Gamma Action", "Alpha Heist 2"]})
    client.put(
        "/users/me/preferences",
        json={"liked": [ids["Alpha Heist"]], "disliked": [], "genres": []},
    )
    personal = client.get("/featured").json()
    assert personal["kind"] == "personal"
    assert personal["model_version"] == "profile_rocchio_v1|v-feat"
    assert personal["item"]["title"] == "Gamma Action"
    assert personal["item"]["reason_key"] == "because_liked"
    assert personal["item"]["reason_args"] == ["Alpha Heist"]
    assert personal["overview"] == "Sinopse de Gamma Action."


def test_lang_translates_title_and_overview_with_fallback(
    client: TestClient, engine: Engine, ids: dict[str, int]
) -> None:
    _translate(engine, ids["Alpha Heist"], "Assalto Alfa", "Sinopse em português.")
    _translate(engine, ids["Gamma Action"], "Ação Gama", None)  # só título traduzido

    def titles(lang: str) -> list[str]:
        r = client.get("/movies", params={"lang": lang, "sort": "popular"})
        return [i["title"] for i in r.json()["items"]]

    assert "Assalto Alfa" in titles("pt-BR") and "Alpha Heist" not in titles("pt-BR")
    assert "Alpha Heist" in titles("en") and "Assalto Alfa" not in titles("en")
    assert "Alpha Heist 2" in titles("pt-BR")  # sem tradução: original

    pt = client.get(f"/movies/{ids['Alpha Heist']}", params={"lang": "pt-BR"}).json()
    assert (pt["title"], pt["overview"], pt["overview_language"]) == (
        "Assalto Alfa",
        "Sinopse em português.",
        "pt-BR",
    )
    en = client.get(f"/movies/{ids['Alpha Heist']}", params={"lang": "en"}).json()
    assert (en["title"], en["overview_language"]) == ("Alpha Heist", "en")
    gamma = client.get(f"/movies/{ids['Gamma Action']}", params={"lang": "pt-BR"}).json()
    assert (gamma["title"], gamma["overview_language"]) == ("Ação Gama", "en")

    # busca casa com o título em qualquer idioma; exibe no idioma pedido
    found = client.get("/movies", params={"q": "assalto", "lang": "en"}).json()["items"]
    assert [i["title"] for i in found] == ["Alpha Heist"]
    exact = client.get("/movies", params={"q": "Assalto Alfa", "lang": "pt-BR"}).json()
    assert exact["items"][0]["title"] == "Assalto Alfa"

    assert client.get("/movies", params={"lang": "fr"}).status_code == 422


def test_structured_reasons_on_similar(
    client: TestClient, engine: Engine, ids: dict[str, int]
) -> None:
    _store(engine, ids, "v-r", {"Alpha Heist": ["Alpha Heist 2", "Gamma Action"]})
    _translate(engine, ids["Alpha Heist 2"], "Assalto Alfa 2", None)
    items = client.get(f"/movies/{ids['Alpha Heist']}/similar").json()["items"]
    first = items[0]
    assert first["title"] == "Assalto Alfa 2"  # padrão: pt-BR
    assert (first["reason_key"], first["reason_args"]) == ("collection", ["Alpha Collection"])
    assert first["reason"] == "Da coleção Alpha Collection"
