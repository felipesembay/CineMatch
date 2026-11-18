"""Registro de experimentos no MLflow próprio do CineMatch (docker-compose `mlflow`).

Cada run carrega a proveniência necessária para reproduzir: fingerprint dos dados, versões
exatas das representações, commit do git (quando houver) e o tipo de rótulo usado. Métricas
de rótulo fraco recebem a tag `label_kind=weak:*` para não serem lidas como relevância.
"""

import json
import math
import os
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cinematch.config import get_settings
from cinematch.data.gold import _git_commit

WEAK_LABEL = "weak:tmdb_collection"

# Model Registry: uma versão por conjunto de vizinhos gerado com `recs build-similar --mlflow`.
# Alias `champion` = conjunto servido pela API; `challenger` = candidato a teste A/B.
SIMILAR_MODEL = "cinematch-similar"
CHAMPION = "champion"
CHALLENGER = "challenger"
REGISTRY_DESCRIPTIONS = {
    SIMILAR_MODEL: (
        "Títulos parecidos (item → item). Cada versão corresponde a um conjunto de vizinhos "
        "top-k gravado no Postgres (tag `neighbor_set`); a API lê os vizinhos, não o modelo. "
        "Alias champion = conjunto servido; challenger = candidato."
    ),
}
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
        if isinstance(value, int | float) and not math.isnan(value):
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
) -> str | None:
    """Loga métricas numéricas, dicts como JSON e (opcional) o modelo no flavor sklearn.

    Devolve a URI do modelo logado (`models:/...`), usada para registrá-lo no Model Registry.
    """
    import mlflow

    mlflow.log_metrics(_numeric(metrics))
    with tempfile.TemporaryDirectory() as tmp:
        for fname, obj in (artifacts or {}).items():
            path = Path(tmp) / fname
            path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
            mlflow.log_artifact(str(path))
    if model is None:
        return None
    import mlflow.sklearn

    # cloudpickle: modelos gerados por este projeto (mesma confiança do joblib usado antes).
    # O padrão `skops` exigiria a dependência e a lista de tipos confiáveis (ex.: LightGBM).
    info = mlflow.sklearn.log_model(
        model, name="model", serialization_format=mlflow.sklearn.SERIALIZATION_FORMAT_CLOUDPICKLE
    )
    return info.model_uri


def register_model(
    model_uri: str, name: str, *, tags: Mapping[str, str], alias: str | None = None
) -> int:
    """Cria uma nova versão de `name` no Model Registry e, se pedido, aponta o alias para ela."""
    import mlflow
    from mlflow import MlflowClient

    version = int(mlflow.register_model(model_uri, name, tags=dict(tags)).version)
    client = MlflowClient()
    client.update_registered_model(name, description=REGISTRY_DESCRIPTIONS.get(name, ""))
    if alias:
        client.set_registered_model_alias(name, alias, version)
    return version


def promote_neighbor_set(neighbor_set: str, *, alias: str = CHAMPION) -> int | None:
    """Move o alias para a versão registrada do conjunto de vizinhos `neighbor_set`.

    Mantém o registry igual ao que a API serve depois de `recs activate`. Devolve a versão
    promovida, ou None se o conjunto nunca foi registrado (build sem `--mlflow`).
    """
    from mlflow import MlflowClient

    client = MlflowClient()
    found = client.search_model_versions(
        f"name = '{SIMILAR_MODEL}' and tags.neighbor_set = '{neighbor_set}'"
    )
    if not found:
        return None
    version = max(int(v.version) for v in found)
    client.set_registered_model_alias(SIMILAR_MODEL, alias, version)
    return version
