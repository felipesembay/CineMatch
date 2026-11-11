"""Fábrica da aplicação FastAPI."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from cinematch import __version__
from cinematch.api.catalog import router as catalog_router
from cinematch.api.health import router as health_router
from cinematch.api.middleware import OriginCheckMiddleware, RequestContextMiddleware
from cinematch.api.users import router as users_router
from cinematch.config import Settings, get_settings
from cinematch.logconfig import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(title="CineMatch API", version=__version__)
    app.add_middleware(OriginCheckMiddleware, allowed_origins=settings.cors_origins)
    app.add_middleware(RequestContextMiddleware)
    # CORS por fora: respostas de preflight também recebem X-Request-ID.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
        allow_credentials=True,
    )
    app.include_router(health_router)
    app.include_router(catalog_router)
    app.include_router(users_router)
    return app


app = create_app()
