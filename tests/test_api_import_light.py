"""A imagem da API não instala o extra `ml`: importar a app não pode exigir essas libs."""

import subprocess
import sys

BLOCK = [
    "numpy",
    "scipy",
    "sklearn",
    "torch",
    "sentence_transformers",
    "pyarrow",
    "lightgbm",
    "mlflow",
    "joblib",
]


def test_api_imports_without_ml_extra() -> None:
    code = f"import sys\nfor m in {BLOCK!r}: sys.modules[m] = None\nimport cinematch.api.main\n"
    proc = subprocess.run(  # noqa: S603 - comando fixo
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
