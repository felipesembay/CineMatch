"""CineMatch: recomendação de filmes e séries."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cinematch")
except PackageNotFoundError:  # pragma: no cover - pacote não instalado
    __version__ = "0.0.0"
