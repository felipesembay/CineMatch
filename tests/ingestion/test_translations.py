"""Escolha da tradução pt-BR (título só BR; sinopse com reserva pt-PT)."""

from cinematch.ingestion.tmdb.normalize import normalize, pick_translation


def _t(country: str, lang: str = "pt", **data: str) -> dict:
    return {"iso_639_1": lang, "iso_3166_1": country, "data": data}


def test_prefers_brazilian_title_and_overview() -> None:
    payload = {
        "translations": [
            _t("PT", title="O Padrinho", overview="Sinopse PT"),
            _t("BR", title="O Poderoso Chefão", overview="Sinopse BR"),
            _t("US", lang="en", title="The Godfather", overview="EN"),
        ]
    }
    tr = pick_translation("movie", payload)
    assert (tr.title, tr.overview, tr.source_language) == (
        "O Poderoso Chefão",
        "Sinopse BR",
        "pt-BR",
    )


def test_overview_falls_back_to_portugal_but_title_does_not() -> None:
    payload = {"translations": [_t("PT", title="O Padrinho", overview="Sinopse PT")]}
    tr = pick_translation("movie", payload)
    assert (tr.title, tr.overview, tr.source_language) == (None, "Sinopse PT", "pt-PT")


def test_tv_uses_name_and_empty_strings_are_missing() -> None:
    payload = {"translations": [_t("BR", name="A Série", overview="  ")]}
    tr = pick_translation("tv", payload)
    assert (tr.title, tr.overview, tr.source_language) == ("A Série", None, "pt-BR")
    none = pick_translation("tv", {"translations": [_t("US", lang="en", name="X")]})
    assert (none.title, none.overview, none.source_language) == (None, None, "none")


def test_normalize_reads_appended_translations_without_changing_hash() -> None:
    base = {"id": 1, "title": "The Godfather", "release_date": "1972-03-14"}
    plain = normalize("movie", base)
    with_tr = normalize(
        "movie", {**base, "translations": {"translations": [_t("BR", title="O Poderoso Chefão")]}}
    )
    assert plain.translation is None  # coletas antigas: não mexe na tradução gravada
    assert with_tr.translation is not None and with_tr.translation.title == "O Poderoso Chefão"
    assert plain.content_hash() == with_tr.content_hash()
