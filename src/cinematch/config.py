"""Configuração por ambiente (variáveis de ambiente / arquivo .env)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações da aplicação. Segredos nunca têm valor padrão real."""

    model_config = SettingsConfigDict(
        env_prefix="CINEMATCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    database_url: SecretStr = Field(
        default=SecretStr("postgresql+psycopg://cinematch:cinematch@localhost:5434/cinematch"),
        description="URL SQLAlchemy do PostgreSQL.",
    )
    db_connect_timeout_s: int = Field(default=3, ge=1, le=30)

    bronze_dir: Path = Field(
        default=Path("data/bronze"), description="Raiz da camada Bronze (backend local)."
    )
    gold_dir: Path = Field(default=Path("data/gold"), description="Snapshots Gold (Parquet).")
    artifacts_dir: Path = Field(
        default=Path("data/artifacts"), description="Artefatos de features/modelos (local)."
    )
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    # Commit do Hugging Face fixado (reprodutibilidade). None = mais recente.
    embedding_revision: str | None = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
    # MLflow próprio do CineMatch (docker-compose, serviço `mlflow`).
    mlflow_tracking_uri: str = "http://127.0.0.1:5010"

    # API pública: origens do frontend (Vite/Lovable em dev). JSON no .env.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:8088",  # frontend no docker-compose (nginx)
        "http://127.0.0.1:8088",
    ]
    # Sessão: cookie httpOnly com token opaco. `cookie_secure=true` em produção (HTTPS).
    session_cookie_name: str = "cm_session"
    session_ttl_days: int = Field(default=30, ge=1, le=365)
    cookie_secure: bool = False
    # Login com Google (OAuth 2.0 / OIDC). Habilitado só com os dois valores definidos.
    # Redirect autorizado no Google Cloud: <origem do frontend><api_prefix>/auth/google/callback
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Prefixo pelo qual o navegador alcança a API (proxy do Vite/nginx).
    public_api_prefix: str = "/api"
    # Imagens servidas direto pelo CDN do TMDB (atribuição obrigatória no frontend).
    tmdb_image_base_url: str = "https://image.tmdb.org/t/p"
    poster_size: str = "w342"
    backdrop_size: str = "w1280"


class TmdbSettings(BaseSettings):
    """Configuração do TMDB. O token só vem do ambiente/.env, nunca do código."""

    model_config = SettingsConfigDict(
        env_prefix="TMDB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_token: SecretStr | None = None
    base_url: str = "https://api.themoviedb.org/3"
    language: str = "en-US"
    # Limite documentado do TMDB fica na faixa de ~40 req/s; usamos margem ampla.
    requests_per_second: float = Field(default=4.0, gt=0, le=20)
    timeout_s: float = Field(default=10.0, gt=0, le=60)
    max_attempts: int = Field(default=5, ge=1, le=10)


class TvmazeSettings(BaseSettings):
    """TVmaze: API pública sem chave. Limite documentado: >= 20 req / 10 s por IP."""

    model_config = SettingsConfigDict(
        env_prefix="TVMAZE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str = "https://api.tvmaze.com"
    requests_per_second: float = Field(default=1.0, gt=0, le=2)
    timeout_s: float = Field(default=15.0, gt=0, le=60)
    max_attempts: int = Field(default=5, ge=1, le=10)


@lru_cache
def get_settings() -> Settings:
    return Settings()
