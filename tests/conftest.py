from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from cinematch.api.main import create_app
from cinematch.config import Settings
from cinematch.db import get_engine


@pytest.fixture
def app() -> FastAPI:
    return create_app(Settings(env="test", log_json=True))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # Banco em memória: testes unitários não dependem de PostgreSQL.
    engine = create_engine("sqlite://")
    app.dependency_overrides[get_engine] = lambda: engine
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # Tudo em tests/integration/ recebe o marcador `integration` automaticamente.
    for item in items:
        if "integration" in item.path.parts:
            item.add_marker(pytest.mark.integration)
