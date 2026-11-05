"""Shared httpx.AsyncClient with keep-alive for Meta Model API adapters."""

import httpx

_client: httpx.AsyncClient | None = None


def get_shared_client(timeout: float = 60.0) -> httpx.AsyncClient:
    """Return a process-wide AsyncClient (connection reuse, no per-call pool)."""
    global _client
    if _client is None or _client.is_closed:
        limits = httpx.Limits(max_connections=20, max_keepalive_connections=20)
        _client = httpx.AsyncClient(timeout=timeout, limits=limits)
    return _client


async def aclose_shared_client() -> None:
    """Close the shared client (tests / lifespan shutdown)."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
