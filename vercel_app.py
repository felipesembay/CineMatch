"""Entrypoint da API no Vercel (service `backend` em vercel.json).

O pacote fica em `src/` (layout src); garante o import mesmo se o build não instalar o projeto.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cinematch.api.main import app

__all__ = ["app"]
