from cinematch.features.text import ItemDoc, embedding_text, text_hash, tfidf_text

DOC = ItemDoc(
    item_id=1,
    item_type="movie",
    title="Inception",
    overview="A thief who steals secrets through dreams.",
    overview_source="tmdb",
    genres=("Action", "Science Fiction"),
    keywords=("dream", "time travel"),
    directors=("Christopher Nolan",),
    cast=("Leonardo DiCaprio",),
)


def test_embedding_text_template() -> None:
    assert embedding_text(DOC) == (
        "Inception. Genres: Action, Science Fiction. Keywords: dream, time travel. "
        "A thief who steals secrets through dreams."
    )


def test_embedding_text_without_optional_parts() -> None:
    doc = ItemDoc(2, "tv", "Show", None, None, (), (), (), ())
    assert embedding_text(doc) == "Show."


def test_tfidf_tokens_prefixed() -> None:
    txt = tfidf_text(DOC)
    for token in (
        "genre_science_fiction",
        "kw_time_travel",
        "person_christopher_nolan",
        "person_leonardo_dicaprio",
    ):
        assert token in txt.split()


def test_tfidf_unicode_names() -> None:
    doc = ItemDoc(3, "tv", "T", None, None, (), ("São Paulo",), ("Hayao Miyazaki",), ())
    assert "kw_são_paulo" in tfidf_text(doc).split()


def test_text_hash_stable() -> None:
    assert text_hash("a") == text_hash("a") != text_hash("b")
