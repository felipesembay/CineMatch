"""Model Registry do MLflow: registro de versões e alias champion/challenger (sem servidor)."""

import numpy as np
import pytest

mlflow = pytest.importorskip("mlflow")
from sklearn.linear_model import LogisticRegression  # noqa: E402

from cinematch import tracking  # noqa: E402


@pytest.fixture
def local_mlflow(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    tracking.setup("test-registry", tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}")
    yield mlflow.MlflowClient()
    mlflow.set_tracking_uri(None)


def _build(neighbor_set: str, alias: str) -> str:
    model = LogisticRegression().fit(np.array([[0.0], [1.0], [2.0], [3.0]]), [0, 0, 1, 1])
    with tracking.method_run(neighbor_set, data_fingerprint="fp", versions={"tfidf": "v1"}):
        uri = tracking.log_results({"recall_at_10": 0.9}, model=model)
    assert uri is not None
    return tracking.register_model(
        uri, tracking.SIMILAR_MODEL, tags={"neighbor_set": neighbor_set}, alias=alias
    )


def test_build_registers_versions_with_aliases(local_mlflow) -> None:
    v1 = _build("similar:tfidf|a|k50", tracking.CHAMPION)
    v2 = _build("similar:rerank|b|k50", tracking.CHALLENGER)

    model = local_mlflow.get_registered_model(tracking.SIMILAR_MODEL)
    assert model.aliases == {tracking.CHAMPION: v1, tracking.CHALLENGER: v2}
    assert "neighbor_set" in model.description
    loaded = mlflow.sklearn.load_model(f"models:/{tracking.SIMILAR_MODEL}@{tracking.CHAMPION}")
    assert loaded.predict(np.array([[3.0]])).tolist() == [1]


def test_activate_moves_champion_to_the_set_version(local_mlflow) -> None:
    _build("similar:tfidf|a|k50", tracking.CHAMPION)
    v2 = _build("similar:rerank|b|k50", tracking.CHALLENGER)

    assert tracking.promote_neighbor_set("similar:rerank|b|k50") == v2
    aliases = local_mlflow.get_registered_model(tracking.SIMILAR_MODEL).aliases
    assert aliases[tracking.CHAMPION] == v2
    assert tracking.promote_neighbor_set("similar:nunca-registrado") is None
