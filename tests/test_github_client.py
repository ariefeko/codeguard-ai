# tests/test_github_client.py
"""
Unit tests for src/github/github_client.py

Coverage:
- create_issue() -- success, HTTP error, labels default vs custom
- post_pr_comment() -- success, HTTP error
- get_open_pr_for_branch() -- pull request found, none found, HTTP error
"""
import os
from typing import Any, get_type_hints
from unittest.mock import patch, MagicMock
import logging

import httpx
import pytest
from src.github.github_client import GitHubClient
from src.github.repo_policy import RepositoryAllowlistNotConfiguredError
from src.config import DEFAULT_REPOSITORY_BRANCH, GITHUB_STATUS_DESCRIPTION_MAX_LENGTH


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GITHUB_PAT_TOKEN", "fake_token")
    monkeypatch.setenv("CODEGUARD_ALLOWED_REPOS", "ariefeko/tagihin")
    http_client = MagicMock()
    http_client.post.side_effect = lambda *args, **kwargs: httpx.post(*args, **kwargs)
    http_client.get.side_effect = lambda *args, **kwargs: httpx.get(*args, **kwargs)
    return GitHubClient(
        "ariefeko",
        "tagihin",
        http_client=http_client,
        sleep=MagicMock(),
    )


def test_client_fixture_keeps_environment_active(client):
    assert client.token == "fake_token"
    assert os.getenv("GITHUB_PAT_TOKEN") == "fake_token"
    assert os.getenv("CODEGUARD_ALLOWED_REPOS") == "ariefeko/tagihin"


def test_parse_json_response_expected_type_annotation_is_explicit():
    hints = get_type_hints(GitHubClient._parse_json_response)

    assert hints["expected_type"] == type[Any]


def test_rejects_unallowed_repository(monkeypatch):
    monkeypatch.setenv("GITHUB_PAT_TOKEN", "fake_token")
    monkeypatch.setenv("CODEGUARD_ALLOWED_REPOS", "ariefeko/tagihin")

    with pytest.raises(PermissionError):
        GitHubClient("attacker", "repo")


def test_rejects_invalid_repository_name_format(monkeypatch):
    monkeypatch.setenv("GITHUB_PAT_TOKEN", "fake_token")
    monkeypatch.setenv("CODEGUARD_ALLOWED_REPOS", "ariefeko/tagihin")

    with pytest.raises(ValueError):
        GitHubClient("ariefeko", "bad repo")


def test_reports_missing_repository_allowlist(monkeypatch):
    monkeypatch.setenv("GITHUB_PAT_TOKEN", "fake_token")
    monkeypatch.delenv("CODEGUARD_ALLOWED_REPOS", raising=False)
    monkeypatch.delenv("CODEGUARD_DEFAULT_OWNER", raising=False)
    monkeypatch.delenv("CODEGUARD_DEFAULT_REPO", raising=False)

    with pytest.raises(RepositoryAllowlistNotConfiguredError):
        GitHubClient("ariefeko", "tagihin")


class TestSetCommitStatus:
    def test_success_posts_commit_status(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 201

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = client.set_commit_status(
                "abc123",
                "failure",
                "CodeGuard found blocking issues",
                target_url="https://example.com/build",
            )

        assert result is True
        assert mock_post.call_args.args[0].endswith("/statuses/abc123")
        payload = mock_post.call_args.kwargs["json"]
        assert payload == {
            "state": "failure",
            "description": "CodeGuard found blocking issues",
            "context": "codeguard-ai",
            "target_url": "https://example.com/build",
        }

    def test_truncates_long_description(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 201

        with patch("httpx.post", return_value=mock_resp) as mock_post:
            client.set_commit_status("abc123", "success", "x" * 200)

        payload = mock_post.call_args.kwargs["json"]
        assert len(payload["description"]) == GITHUB_STATUS_DESCRIPTION_MAX_LENGTH

    def test_rejects_invalid_state(self, client):
        with pytest.raises(ValueError):
            client.set_commit_status("abc123", "done", "invalid")

    def test_http_error_returns_false(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 422

        with patch("httpx.post", return_value=mock_resp):
            assert client.set_commit_status("abc123", "success", "ok") is False

    def test_timeout_is_classified_in_log(self, client, caplog):
        with caplog.at_level(logging.WARNING), patch(
            "httpx.post",
            side_effect=httpx.ReadTimeout("request timed out"),
        ):
            assert client.set_commit_status("abc123", "success", "ok") is False

        record = next(
            record
            for record in caplog.records
            if record.name == "src.github.github_client"
            and hasattr(record, "github_error_category")
        )
        assert record.github_error_category == "timeout"
        assert record.exception_class == "ReadTimeout"
        assert record.github_operation == "setting commit status"

    def test_authentication_failure_is_classified_in_log(self, client, caplog):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.headers = {}

        with caplog.at_level(logging.WARNING), patch("httpx.post", return_value=mock_resp):
            assert client.set_commit_status("abc123", "success", "ok") is False

        record = next(
            record
            for record in caplog.records
            if record.name == "src.github.github_client"
            and hasattr(record, "github_error_category")
        )
        assert record.github_error_category == "authentication"
        assert record.status_code == 401

    def test_retries_transient_server_error_then_succeeds(self, client):
        server_error = MagicMock(status_code=503, headers={})
        success = MagicMock(status_code=201, headers={})
        client.http_client.post.side_effect = [server_error, success]

        assert client.set_commit_status("abc123", "success", "ok") is True
        assert client.http_client.post.call_count == 2
        client._sleep.assert_called_once_with(0.5)

    def test_retries_network_error_then_succeeds(self, client):
        success = MagicMock(status_code=201, headers={})
        client.http_client.post.side_effect = [
            httpx.ConnectError("connection reset"),
            success,
        ]

        assert client.set_commit_status("abc123", "success", "ok") is True
        assert client.http_client.post.call_count == 2
        client._sleep.assert_called_once_with(0.5)

    def test_does_not_retry_permanent_client_error(self, client):
        response = MagicMock(status_code=422, headers={})
        client.http_client.post.side_effect = None
        client.http_client.post.return_value = response

        assert client.set_commit_status("abc123", "success", "ok") is False
        client.http_client.post.assert_called_once()
        client._sleep.assert_not_called()


# ============================================================
# create_issue()
# ============================================================

class TestCreateIssue:
    def test_success_returns_true(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "html_url": "https://github.com/ariefeko/tagihin/issues/1"
        }
        with patch("httpx.post", return_value=mock_resp):
            result = client.create_issue("Test Issue", "Test body")
        assert result is True

    def test_http_error_returns_false(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 422
        mock_resp.text = "Validation failed"
        with patch("httpx.post", return_value=mock_resp):
            result = client.create_issue("Test Issue", "Test body")
        assert result is False

    def test_default_labels(self, client):
        """Labels default to ['codeguard-ai'] when omitted."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"html_url": "https://github.com/test"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            client.create_issue("Test", "Body")
            payload = mock_post.call_args.kwargs["json"]
            assert payload["labels"] == ["codeguard-ai"]

    def test_custom_labels(self, client):
        """Custom labels are passed through instead of being replaced by defaults."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"html_url": "https://github.com/test"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            client.create_issue("Test", "Body", labels=["bug", "ai-analyzed"])
            payload = mock_post.call_args.kwargs["json"]
            assert payload["labels"] == ["bug", "ai-analyzed"]

    def test_fallback_labels_custom(self, client):
        """The fallback uses 'needs-manual-review' after all LLMs fail."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"html_url": "https://github.com/test"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            client.create_issue("Test", "Body", labels=["bug", "needs-manual-review"])
            payload = mock_post.call_args.kwargs["json"]
            assert "needs-manual-review" in payload["labels"]

    def test_exception_returns_false(self, client):
        """A network error returns False instead of crashing."""
        with patch("httpx.post", side_effect=httpx.RequestError("Connection error")):
            result = client.create_issue("Test", "Body")
        assert result is False

    def test_unexpected_exception_is_not_swallowed(self, client):
        with patch("httpx.post", side_effect=RuntimeError("programming error")), pytest.raises(
            RuntimeError,
            match="programming error",
        ):
            client.create_issue("Test", "Body")

    def test_malformed_json_returns_false(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.side_effect = ValueError("invalid JSON")

        with patch("httpx.post", return_value=mock_resp):
            assert client.create_issue("Test", "Body") is False

    def test_unexpected_json_shape_returns_false(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = ["not", "an", "object"]

        with patch("httpx.post", return_value=mock_resp):
            assert client.create_issue("Test", "Body") is False


# ============================================================
# post_pr_comment()
# ============================================================

class TestPostPrComment:
    @pytest.mark.parametrize(
        "pr_number",
        [0, -1, True, "42", 2_147_483_648],
    )
    def test_rejects_invalid_pr_number(self, client, pr_number):
        with patch("httpx.post") as mock_post, pytest.raises(ValueError):
            client.post_pr_comment(pr_number, "Test comment")

        mock_post.assert_not_called()

    def test_success_returns_true(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        with patch("httpx.post", return_value=mock_resp):
            result = client.post_pr_comment(42, "Test comment")
        assert result is True

    def test_http_error_returns_false(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        with patch("httpx.post", return_value=mock_resp):
            result = client.post_pr_comment(42, "Test comment")
        assert result is False

    def test_posts_to_correct_pr_number(self, client):
        """The URL includes the correct pull request number."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            client.post_pr_comment(99, "Test comment")
            url = mock_post.call_args.args[0]
            assert "/issues/99/comments" in url

    def test_exception_returns_false(self, client):
        with patch("httpx.post", side_effect=httpx.RequestError("Timeout")):
            result = client.post_pr_comment(1, "body")
        assert result is False


# ============================================================
# get_open_pr_for_branch()
# ============================================================

class TestGetOpenPrForBranch:
    def test_returns_pr_number_when_found(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"number": 42}]
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            result = client.get_open_pr_for_branch("feature/test")
        assert result == 42
        assert mock_get.call_args.kwargs["params"] == {
            "state": "open",
            "head": "ariefeko:feature/test",
        }

    def test_uses_head_owner_for_fork_branch_lookup(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"number": 7}]
        with patch("httpx.get", return_value=mock_resp) as mock_get:
            result = client.get_open_pr_for_branch("feature/test", head_owner="contributor")

        assert result == 7
        assert mock_get.call_args.kwargs["params"]["head"] == "contributor:feature/test"

    def test_returns_none_when_no_pr(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = []  # An empty list means no open pull request.
        with patch("httpx.get", return_value=mock_resp):
            result = client.get_open_pr_for_branch("feature/test")
        assert result is None

    def test_returns_none_on_http_error(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        with patch("httpx.get", return_value=mock_resp):
            result = client.get_open_pr_for_branch("feature/test")
        assert result is None

    def test_returns_first_pr_when_multiple(self, client):
        """Return the first of multiple open pull requests for the same branch."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"number": 10}, {"number": 11}]
        with patch("httpx.get", return_value=mock_resp):
            result = client.get_open_pr_for_branch("feature/test")
        assert result == 10

    def test_exception_returns_none(self, client):
        with patch("httpx.get", side_effect=httpx.RequestError("Network error")):
            result = client.get_open_pr_for_branch("feature/test")
        assert result is None

    def test_rate_limit_failure_is_classified_in_log(self, client, caplog):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.headers = {"X-RateLimit-Remaining": "0"}

        with caplog.at_level(logging.WARNING), patch("httpx.get", return_value=mock_resp):
            assert client.get_open_pr_for_branch("feature/test") is None

        record = next(
            record
            for record in caplog.records
            if record.name == "src.github.github_client"
            and hasattr(record, "github_error_category")
        )
        assert record.github_error_category == "rate_limit"
        assert record.status_code == 403

    def test_malformed_json_returns_none(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("invalid JSON")

        with patch("httpx.get", return_value=mock_resp):
            assert client.get_open_pr_for_branch("feature/test") is None

    def test_malformed_pr_shape_returns_none(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"title": "missing number"}]

        with patch("httpx.get", return_value=mock_resp):
            assert client.get_open_pr_for_branch("feature/test") is None


class TestGetDefaultBranch:
    def test_env_default_branch_overrides_repo_metadata(self, client, monkeypatch):
        monkeypatch.setenv("CODEGUARD_DEFAULT_BRANCH", "develop")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"default_branch": DEFAULT_REPOSITORY_BRANCH}

        with patch("httpx.get") as mock_get:
            assert client.get_default_branch() == "develop"
            mock_get.assert_not_called()

    def test_returns_default_branch_from_repo_metadata(self, client, monkeypatch):
        monkeypatch.delenv("CODEGUARD_DEFAULT_BRANCH", raising=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"default_branch": DEFAULT_REPOSITORY_BRANCH}

        with patch("httpx.get", return_value=mock_resp):
            assert client.get_default_branch() == DEFAULT_REPOSITORY_BRANCH

    def test_falls_back_to_env_default_branch(self, client, monkeypatch):
        monkeypatch.delenv("CODEGUARD_DEFAULT_BRANCH", raising=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("httpx.get", return_value=mock_resp):
            assert client.get_default_branch() == DEFAULT_REPOSITORY_BRANCH

    def test_malformed_json_falls_back_to_main(self, client, monkeypatch):
        monkeypatch.delenv("CODEGUARD_DEFAULT_BRANCH", raising=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("invalid JSON")

        with patch("httpx.get", return_value=mock_resp):
            assert client.get_default_branch() == DEFAULT_REPOSITORY_BRANCH

    def test_unexpected_json_shape_falls_back_to_main(self, client, monkeypatch):
        monkeypatch.delenv("CODEGUARD_DEFAULT_BRANCH", raising=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["not", "an", "object"]

        with patch("httpx.get", return_value=mock_resp):
            assert client.get_default_branch() == DEFAULT_REPOSITORY_BRANCH
