from unittest.mock import MagicMock, patch

from src.context.context_builder import ContextBuilder
from src.github.github_client import GitHubClient
from src.github.http_client import (
    build_github_headers,
    close_github_http_client,
    get_github_http_client,
)


def test_builds_shared_github_headers():
    assert build_github_headers("token") == {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer token",
    }


def test_reuses_process_wide_http_client():
    close_github_http_client()
    pooled_client = MagicMock()
    pooled_client.is_closed = False

    try:
        with patch("src.github.http_client.httpx.Client", return_value=pooled_client) as factory:
            assert get_github_http_client() is pooled_client
            assert get_github_http_client() is pooled_client

        factory.assert_called_once()
    finally:
        close_github_http_client()


def test_github_components_accept_the_same_injected_client(monkeypatch):
    monkeypatch.setenv("CODEGUARD_ALLOWED_REPOS", "ariefeko/tagihin")
    shared_client = MagicMock()

    github = GitHubClient("ariefeko", "tagihin", http_client=shared_client)
    context = ContextBuilder(
        "ariefeko",
        "tagihin",
        "abc123",
        http_client=shared_client,
    )

    assert github.http_client is shared_client
    assert context.http_client is shared_client
