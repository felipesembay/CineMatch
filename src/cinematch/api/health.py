"""Endpoints de saúde: liveness e readiness."""

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from cinematch import __version__
from cinematch.db import check_database, get_engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, Literal["ok", "error"]]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness: processo responde. Não depende de serviços externos."""
    return HealthResponse(status="ok", version=__version__)


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
def ready(response: Response, engine: Annotated[Engine, Depends(get_engine)]) -> ReadinessResponse:
    """Readiness: dependências (PostgreSQL) acessíveis."""
    try:
        check_database(engine)
    except SQLAlchemyError as exc:
        # Só o tipo do erro: a mensagem pode conter host/usuário.
        logger.warning("database_unavailable", extra={"error_type": type(exc).__name__})
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="unavailable", checks={"database": "error"})
    return ReadinessResponse(status="ok", checks={"database": "ok"})
