import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from cinematch import __version__
from cinematch.db import check_database, get_engine


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "version": __version__}


def test_ready_ok_when_database_reachable(client: TestClient) -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_ready_503_when_database_unreachable(app: FastAPI) -> None:
    broken = create_engine("sqlite:////nonexistent-dir/cinematch.db")
    app.dependency_overrides[get_engine] = lambda: broken
    with TestClient(app) as c:
        resp = c.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable", "checks": {"database": "error"}}


def test_request_id_generated(client: TestClient) -> None:
    resp = client.get("/health")
    rid = resp.headers.get("X-Request-ID")
    assert rid and len(rid) == 32


def test_request_id_propagated_when_valid(client: TestClient) -> None:
    resp = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert resp.headers["X-Request-ID"] == "abc-123"


def test_request_id_replaced_when_invalid(client: TestClient) -> None:
    for bad in ("x" * 65, "has space", "<script>"):
        resp = client.get("/health", headers={"X-Request-ID": bad})
        assert resp.headers["X-Request-ID"] != bad
        assert len(resp.headers["X-Request-ID"]) == 32


def test_access_log_is_json_with_request_id(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    client.get("/health", headers={"X-Request-ID": "trace-1"})
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    records = [json.loads(ln) for ln in lines]
    access = [r for r in records if r["logger"] == "cinematch.access"]
    assert access, "nenhum log de acesso emitido"
    rec = access[-1]
    assert rec["request_id"] == "trace-1"
    assert rec["path"] == "/health"
    assert rec["status"] == 200
    assert rec["method"] == "GET"
    assert isinstance(rec["duration_ms"], float)


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("CINEMATCH_TEST_DATABASE_URL"),
    reason="defina CINEMATCH_TEST_DATABASE_URL para testar contra PostgreSQL real",
)
def test_check_database_postgres() -> None:
    engine = create_engine(os.environ["CINEMATCH_TEST_DATABASE_URL"])
    try:
        check_database(engine)
    finally:
        engine.dispose()
