"""Benchmark de latência da API em execução (spec §8: p95 < 200 ms em condições declaradas).

Sequencial (1 cliente, sem concorrência), após aquecimento. Itens sorteados com semente fixa.
Mede o tempo de ida e volta no cliente, incluindo HTTP local, não só o handler.
"""

import platform
import random
import statistics
import time
from typing import Any

import httpx

DEFAULT_ENDPOINTS = ("similar", "detail", "list_popular", "list_top_rated", "search")


def _paths(kind: str, item_id: int, rng: random.Random) -> str:
    return {
        "similar": f"/movies/{item_id}/similar?k=12",
        "detail": f"/movies/{item_id}",
        "list_popular": f"/movies?sort=popular&page={rng.randint(1, 20)}",
        "list_top_rated": f"/movies?sort=top_rated&type=movie&genre=Drama&page={rng.randint(1, 5)}",
        "search": f"/movies?q={rng.choice(['star', 'love', 'man', 'dark', 'the'])}",
    }[kind]


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def run_latency(
    base_url: str,
    item_ids: list[int],
    requests: int = 300,
    warmup: int = 20,
    seed: int = 0,
    endpoints: tuple[str, ...] = DEFAULT_ENDPOINTS,
) -> dict[str, Any]:
    rng = random.Random(seed)  # noqa: S311 - amostragem, não criptografia
    out: dict[str, Any] = {
        "base_url": base_url,
        "requests_per_endpoint": requests,
        "concurrency": 1,
        "host": {"python": platform.python_version(), "machine": platform.machine()},
        "endpoints": {},
    }
    with httpx.Client(base_url=base_url, timeout=10) as client:
        for kind in endpoints:
            for _ in range(warmup):
                client.get(_paths(kind, rng.choice(item_ids), rng))
            times, errors = [], 0
            for _ in range(requests):
                path = _paths(kind, rng.choice(item_ids), rng)
                t0 = time.perf_counter()
                resp = client.get(path)
                times.append((time.perf_counter() - t0) * 1000)
                errors += resp.status_code >= 400
            out["endpoints"][kind] = {
                "p50_ms": round(_pct(times, 0.50), 2),
                "p95_ms": round(_pct(times, 0.95), 2),
                "p99_ms": round(_pct(times, 0.99), 2),
                "mean_ms": round(statistics.fmean(times), 2),
                "errors": errors,
            }
    return out
