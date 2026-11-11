"""Logging estruturado em JSON com ID de correlação.

Regra: nunca registrar tokens, senhas, corpos de requisição ou PII.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, TextIO

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Atributos padrão de LogRecord que não devem ir como campos extras.
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class _StdoutHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Resolve `sys.stdout` a cada emissão (respeita redirecionamentos posteriores)."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property  # type: ignore[override]
    def stream(self) -> TextIO:
        return sys.stdout

    @stream.setter
    def stream(self, _value: object) -> None:
        pass


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    handler = _StdoutHandler()
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Evita log de acesso duplicado: o middleware já registra cada requisição.
    logging.getLogger("uvicorn.access").disabled = True
    # httpx loga cada requisição em INFO (URL completa); nossos logs já cobrem com contexto.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # usado pelo huggingface_hub
