"""Registro de experimentos no MLflow próprio do CineMatch (docker-compose `mlflow`).

Cada run carrega a proveniência necessária para reproduzir: fingerprint dos dados, versões
exatas das representações, commit do git (quando houver) e o tipo de rótulo usado. Métricas
de rótulo fraco recebem a tag `label_kind=weak:*` para não serem lidas como relevância.
"""

import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cinematch.config import get_settings
from cinematch.data.gold import _git_commit

WEAK_LABEL = "weak:tmdb_collection"
DISCLAIMER = (
    "Proxies com rótulo fraco (coleção TMDB), sem interações de usuários. "
    "Não medem relevância para pessoas (spec §8)."
)


def setup(experiment: str, tracking_uri: str | None = None) -> str:
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow

    mlflow.set_tracking_uri(tracking_uri or get_settings().mlflow_tracking_uri)
    exp = mlflow.set_experiment(experiment)
    return exp.experiment_id


def _numeric(metrics: Mapping[str, Any]) -> dict[str, float]:
    out = {}
    for key, value in metrics.items():
        name = key.replace("@", "_at_")
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float) and value == value:  # descarta nan
            out[name] = float(value)
        elif isinstance(value, list | tuple) and len(value) == 2:
            out[f"{name}_lo"], out[f"{name}_hi"] = float(value[0]), float(value[1])
    return out


@contextmanager
def method_run(
    name: str,
    *,
    data_fingerprint: str,
    versions: Mapping[str, str],
    params: Mapping[str, Any] | None = None,
    label_kind: str = WEAK_LABEL,
    tags: Mapping[str, str] | None = None,
) -> Iterator[Any]:
    """Abre um run com tags de proveniência. Use `log_results` dentro do bloco."""
    import mlflow

    with mlflow.start_run(run_name=name) as run:
        mlflow.set_tags(
            {
                "method": name,
                "data_fingerprint": data_fingerprint,
                "label_kind": label_kind,
                "git_commit": _git_commit() or "none",
                "disclaimer": DISCLAIMER,
                **{f"version.{k}": v for k, v in versions.items()},
                **(tags or {}),
            }
        )
        if params:
            mlflow.log_params({k: str(v) for k, v in params.items()})
        yield run


def log_results(
    metrics: Mapping[str, Any],
    *,
    artifacts: Mapping[str, Any] | None = None,
    model: Any | None = None,
) -> None:
    """Loga métricas numéricas, dicts como JSON e (opcional) o modelo via joblib."""
    import joblib
    import mlflow

    mlflow.log_metrics(_numeric(metrics))
    with tempfile.TemporaryDirectory() as tmp:
        for fname, obj in (artifacts or {}).items():
            path = Path(tmp) / fname
            path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
            mlflow.log_artifact(str(path))
        if model is not None:
            path = Path(tmp) / "model.joblib"
            joblib.dump(model, path)
            mlflow.log_artifact(str(path), artifact_path="model")
