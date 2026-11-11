"""Middleware de ID de correlação e log de acesso."""

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from cinematch.logconfig import request_id_ctx

logger = logging.getLogger("cinematch.access")

REQUEST_ID_HEADER = "X-Request-ID"
# Aceita apenas IDs curtos e seguros vindos do cliente; senão gera um novo.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            route = request.scope.get("route")
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": getattr(route, "path", request.url.path),
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            request_id_ctx.reset(token)


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginCheckMiddleware(BaseHTTPMiddleware):
    """CSRF: rejeita métodos que alteram estado vindos de outra origem de navegador.

    Navegadores sempre enviam `Origin` em requisições cross-site desse tipo; clientes sem
    navegador (curl, testes) não enviam e passam. Complementa o cookie `SameSite=Lax`.
    """

    def __init__(self, app, allowed_origins: list[str]) -> None:
        super().__init__(app)
        self.allowed = frozenset(o.rstrip("/") for o in allowed_origins)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        origin = request.headers.get("origin")
        if request.method in UNSAFE_METHODS and origin and origin.rstrip("/") not in self.allowed:
            return JSONResponse({"detail": "origem não permitida"}, status_code=403)
        return await call_next(request)
