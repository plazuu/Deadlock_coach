"""HTTP client for deadlock-api.com.

Wraps ``httpx`` with:
  * ``X-API-KEY`` auth when a key is configured
  * a client-side minimum interval between requests (politeness / rate-limit headroom)
  * retry with exponential backoff on transport errors and 429/5xx, honouring ``Retry-After``

Only the endpoints Limpet actually needs are exposed. Everything returns parsed
JSON (``dict`` / ``list``); typed wrappers live in ``limpet.api.models``.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

BASE_URL = "https://api.deadlock-api.com"
USER_AGENT = "limpet-coach/0.1 (+https://github.com/; personal coaching tool)"

# demo-query polling
_DEMO_POLL_INTERVAL_S = 3.0
_DEMO_POLL_TIMEOUT_S = 300.0


class DeadlockAPIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class MetadataNotReady(DeadlockAPIError):
    """Match metadata is not available yet (still being ingested from Steam/S3)."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


class _RateLimiter:
    """Crude single-process limiter: enforce a minimum gap between requests."""

    def __init__(self, min_interval_s: float):
        self._min_interval = min_interval_s
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
            self._next_allowed = max(now, self._next_allowed) + self._min_interval


class DeadlockClient:
    def __init__(
        self,
        api_key: str = "",
        *,
        base_url: str = BASE_URL,
        min_interval_s: float = 0.25,
        timeout_s: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if api_key:
            headers["X-API-KEY"] = api_key
        self._http = httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout_s, transport=transport
        )
        self._limiter = _RateLimiter(min_interval_s)

    def __enter__(self) -> DeadlockClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- low level -----------------------------------------------------------

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        self._limiter.wait()
        resp = self._http.request(method, path, **kw)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                time.sleep(min(int(retry_after), 30))
        resp.raise_for_status()
        return resp

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            return self._request("GET", path, params=_clean(params)).json()
        except httpx.HTTPStatusError as e:
            raise DeadlockAPIError(
                f"GET {path} -> {e.response.status_code}: {e.response.text[:200]}",
                status=e.response.status_code,
            ) from e

    def _post(self, path: str, json_body: dict[str, Any]) -> Any:
        try:
            return self._request("POST", path, json=json_body).json()
        except httpx.HTTPStatusError as e:
            raise DeadlockAPIError(
                f"POST {path} -> {e.response.status_code}: {e.response.text[:200]}",
                status=e.response.status_code,
            ) from e

    # -- players -----------------------------------------------------------

    def match_history(
        self, account_id: int, *, force_refetch: bool = False
    ) -> list[dict[str, Any]]:
        return self._get(
            f"/v1/players/{account_id}/match-history",
            {"force_refetch": force_refetch or None},
        )

    def player_rank(self, account_id: int) -> dict[str, Any]:
        return self._get(f"/v1/players/{account_id}/rank")

    def player_hero_stats(self, account_ids: list[int], **filters: Any) -> list[dict[str, Any]]:
        params = {"account_ids": ",".join(map(str, account_ids)), **filters}
        return self._get("/v1/players/hero-stats", params)

    # -- matches ----------------------------------------------------------

    def match_metadata(self, match_id: int) -> dict[str, Any]:
        try:
            return self._get(f"/v1/matches/{match_id}/metadata")
        except DeadlockAPIError as e:
            if e.status in (404, 422):
                raise MetadataNotReady(
                    f"Metadata for match {match_id} is not available yet."
                ) from e
            raise

    def match_salts(self, match_id: int) -> dict[str, Any]:
        return self._get(f"/v1/matches/{match_id}/salts")

    # -- analytics / benchmarks -----------------------------------------

    def player_stats_metrics(self, **filters: Any) -> Any:
        return self._get("/v1/analytics/player-stats/metrics", filters)

    def hero_stats(self, **filters: Any) -> Any:
        return self._get("/v1/analytics/hero-stats", filters)

    def sql(self, query: str) -> Any:
        return self._get("/v1/sql", {"query": query})

    # -- assets ---------------------------------------------------------

    def assets_heroes(self) -> list[dict[str, Any]]:
        return self._get("/v1/assets/heroes")

    def assets_items(self) -> list[dict[str, Any]]:
        return self._get("/v1/assets/items")

    def assets_ranks(self) -> list[dict[str, Any]]:
        return self._get("/v1/assets/ranks")

    # -- demo query (hosted replay analysis) --------------------------

    def demo_query(self, match_id: int, query: str, *, poll: bool = True) -> dict[str, Any]:
        job = self._post("/v1/matches/demo/query", {"match_id": match_id, "query": query})
        job_id = job.get("job_id") or job.get("id")
        if not poll or not job_id:
            return job
        deadline = time.monotonic() + _DEMO_POLL_TIMEOUT_S
        while time.monotonic() < deadline:
            status = self._get(f"/v1/matches/demo/query/{job_id}")
            state = str(status.get("status", "")).lower()
            if state in ("done", "completed", "succeeded", "finished"):
                return status
            if state in ("failed", "error"):
                raise DeadlockAPIError(f"demo query {job_id} failed: {status}")
            time.sleep(_DEMO_POLL_INTERVAL_S)
        raise DeadlockAPIError(f"demo query {job_id} timed out after {_DEMO_POLL_TIMEOUT_S:.0f}s")

    def demo_schema(self, match_id: int | None = None) -> dict[str, Any]:
        return self._get("/v1/matches/demo/schema", {"match_id": match_id})


def _clean(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop ``None`` values so httpx doesn't serialise them as empty query params."""
    if not params:
        return None
    return {k: v for k, v in params.items() if v is not None}
