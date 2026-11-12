"""Motivos de recomendação estruturados (`key` + `args`), traduzidos no frontend.

A API também devolve `reason` em pt-BR (compatibilidade). Nomes de gênero ficam no original do
TMDB nos `args`: o frontend traduz pelo mesmo dicionário dos filtros.
"""

Reason = tuple[str, list[str]]

_PT = {
    "collection": "Da coleção {0}",
    "same_collection": "Mesma coleção",
    "director": "Direção de {0}",
    "creator": "Criação de {0}",
    "cast": "Com {0}",
    "genres": "Também {0} e {1}",
    "genre": "Também {0}",
    "theme": "Tema parecido",
    "popular_genre": "Popular em {0}",
    "top_rated": "Mais bem avaliados",
    "because_liked": "Porque você curtiu {0}",
    "weekly_pick": "Destaque da semana",
}


def text_pt(reason: Reason | None) -> str | None:
    if reason is None:
        return None
    key, args = reason
    return _PT[key].format(*args)
