# tests/test_github_client.py
"""
Unit tests untuk src/github/github_client.py

Coverage:
- create_issue() -- success, HTTP error, labels default vs custom
- post_pr_comment() -- success, HTTP error
- get_open_pr_for_branch() -- ada PR, tidak ada PR, HTTP error
"""
from unittest.mock import patch, MagicMock
import pytest
from src.github.github_client import GitHubClient


@pytest.fixture
def client():
    with patch.dict(
        "os.environ",
        {
            "GITHUB_PAT_TOKEN": "fake_token",
            "CODEGUARD_ALLOWED_REPOS": "ariefeko/tagihin",
        },
    ):
        return GitHubClient("ariefeko", "tagihin")


def test_rejects_unallowed_repository(monkeypatch):
    monkeypatch.setenv("GITHUB_PAT_TOKEN", "fake_token")
    monkeypatch.setenv("CODEGUARD_ALLOWED_REPOS", "ariefeko/tagihin")

    with pytest.raises(PermissionError):
        GitHubClient("attacker", "repo")


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
        """Tanpa parameter labels, default harus ['codeguard-ai']."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"html_url": "https://github.com/test"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            client.create_issue("Test", "Body")
            payload = mock_post.call_args.kwargs["json"]
            assert payload["labels"] == ["codeguard-ai"]

    def test_custom_labels(self, client):
        """Labels custom harus ter-pass dengan benar, bukan override ke default."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"html_url": "https://github.com/test"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            client.create_issue("Test", "Body", labels=["bug", "ai-analyzed"])
            payload = mock_post.call_args.kwargs["json"]
            assert payload["labels"] == ["bug", "ai-analyzed"]

    def test_fallback_labels_custom(self, client):
        """Label 'needs-manual-review' dipakai saat LLM gagal semua."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"html_url": "https://github.com/test"}
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            client.create_issue("Test", "Body", labels=["bug", "needs-manual-review"])
            payload = mock_post.call_args.kwargs["json"]
            assert "needs-manual-review" in payload["labels"]

    def test_exception_returns_false(self, client):
        """Network error atau exception lain harus return False, bukan crash."""
        with patch("httpx.post", side_effect=Exception("Connection error")):
            result = client.create_issue("Test", "Body")
        assert result is False


# ============================================================
# post_pr_comment()
# ============================================================

class TestPostPrComment:
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
        """URL harus include PR number yang benar."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            client.post_pr_comment(99, "Test comment")
            url = mock_post.call_args.args[0]
            assert "/issues/99/comments" in url

    def test_exception_returns_false(self, client):
        with patch("httpx.post", side_effect=Exception("Timeout")):
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
        mock_resp.json.return_value = []  # list kosong = tidak ada PR open
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
        """Kalau ada beberapa PR open untuk branch yang sama, ambil yang pertama."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"number": 10}, {"number": 11}]
        with patch("httpx.get", return_value=mock_resp):
            result = client.get_open_pr_for_branch("feature/test")
        assert result == 10

    def test_exception_returns_none(self, client):
        with patch("httpx.get", side_effect=Exception("Network error")):
            result = client.get_open_pr_for_branch("feature/test")
        assert result is None


class TestGetDefaultBranch:
    def test_env_default_branch_overrides_repo_metadata(self, client, monkeypatch):
        monkeypatch.setenv("CODEGUARD_DEFAULT_BRANCH", "develop")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"default_branch": "main"}

        with patch("httpx.get") as mock_get:
            assert client.get_default_branch() == "develop"
            mock_get.assert_not_called()

    def test_returns_default_branch_from_repo_metadata(self, client, monkeypatch):
        monkeypatch.delenv("CODEGUARD_DEFAULT_BRANCH", raising=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"default_branch": "main"}

        with patch("httpx.get", return_value=mock_resp):
            assert client.get_default_branch() == "main"

    def test_falls_back_to_env_default_branch(self, client, monkeypatch):
        monkeypatch.delenv("CODEGUARD_DEFAULT_BRANCH", raising=False)
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch("httpx.get", return_value=mock_resp):
            assert client.get_default_branch() == "main"
