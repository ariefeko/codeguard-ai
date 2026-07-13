import atexit
import threading

import httpx

from src.config import HTTP_REQUEST_TIMEOUT_SECONDS


_client: httpx.Client | None = None
_client_lock = threading.Lock()


def build_github_headers(token: str | None) -> dict[str, str]:
    """Build the common headers required by GitHub REST API requests."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def get_github_http_client() -> httpx.Client:
    """Return the process-wide pooled HTTP client used for GitHub requests."""
    global _client
    with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.Client(timeout=HTTP_REQUEST_TIMEOUT_SECONDS)
        return _client


def close_github_http_client() -> None:
    """Close the pooled client during process shutdown or test cleanup."""
    global _client
    with _client_lock:
        if _client is not None and not _client.is_closed:
            _client.close()
        _client = None


atexit.register(close_github_http_client)
