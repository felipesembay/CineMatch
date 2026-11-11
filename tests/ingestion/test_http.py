import httpx
import pytest
import respx

from cinematch.ingestion.http import (
    AuthError,
    HttpError,
    NotFoundError,
    RateLimiter,
    RetryingClient,
    RetryPolicy,
)

BASE = "https://api.example.test"


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def client(clock: FakeClock) -> RetryingClient:
    return RetryingClient(
        client=httpx.Client(base_url=BASE, timeout=1),
        rate_limiter=RateLimiter(1000, clock=clock, sleep=clock.sleep),
        policy=RetryPolicy(max_attempts=3, base_backoff_s=1, max_backoff_s=10, jitter_s=0),
        sleep=clock.sleep,
    )


@respx.mock
def test_success_returns_json(client: RetryingClient) -> None:
    respx.get(f"{BASE}/x").respond(200, json={"ok": True})
    assert client.get_json("/x") == {"ok": True}
    assert client.stats.requests == 1
    assert client.stats.retries == 0


@respx.mock
def test_429_respects_retry_after_then_succeeds(client: RetryingClient, clock: FakeClock) -> None:
    respx.get(f"{BASE}/x").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    assert client.get_json("/x") == {"ok": 1}
    assert 2.0 in clock.sleeps
    assert client.stats.rate_limited == 1
    assert client.stats.retries == 1


@respx.mock
def test_5xx_exponential_backoff_then_gives_up(client: RetryingClient, clock: FakeClock) -> None:
    route = respx.get(f"{BASE}/x").respond(503)
    with pytest.raises(HttpError, match="esgotadas 3 tentativas"):
        client.get_json("/x")
    assert route.call_count == 3
    backoffs = [s for s in clock.sleeps if s >= 1]
    assert backoffs == [1.0, 2.0]


@respx.mock
def test_timeout_is_retried(client: RetryingClient) -> None:
    respx.get(f"{BASE}/x").mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json=[1])]
    )
    assert client.get_json("/x") == [1]
    assert client.stats.retries == 1


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_not_retried(client: RetryingClient, status: int) -> None:
    route = respx.get(f"{BASE}/x").respond(status)
    with pytest.raises(AuthError):
        client.get_json("/x")
    assert route.call_count == 1


@respx.mock
def test_404_not_retried(client: RetryingClient) -> None:
    route = respx.get(f"{BASE}/x").respond(404)
    with pytest.raises(NotFoundError):
        client.get_json("/x")
    assert route.call_count == 1


@respx.mock
def test_other_4xx_not_retried(client: RetryingClient) -> None:
    route = respx.get(f"{BASE}/x").respond(422)
    with pytest.raises(HttpError):
        client.get_json("/x")
    assert route.call_count == 1


def test_retry_after_capped() -> None:
    assert RetryPolicy(max_backoff_s=5).backoff(1, retry_after=999) == 5


def test_rate_limiter_spaces_requests(clock: FakeClock) -> None:
    limiter = RateLimiter(2, clock=clock, sleep=clock.sleep)  # 0.5 s entre chamadas
    for _ in range(3):
        limiter.acquire()
    assert clock.sleeps == [0.5, 0.5]


def test_rate_limiter_rejects_invalid_rate() -> None:
    with pytest.raises(ValueError):
        RateLimiter(0)
