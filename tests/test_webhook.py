import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api import webhook


class TestGithubSignature:
    def test_accepts_valid_github_signature(self, monkeypatch):
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "github-secret")
        body = b'{"zen":"Keep it logically awesome."}'
        signature = "sha256=" + hmac.new(
            b"github-secret",
            body,
            hashlib.sha256,
        ).hexdigest()

        assert webhook.verify_github_signature(body, signature) is True

    def test_rejects_missing_github_secret(self, monkeypatch):
        monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)

        assert webhook.verify_github_signature(b"{}", "sha256=abc") is False

    def test_rejects_invalid_github_signature(self, monkeypatch):
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "github-secret")

        assert webhook.verify_github_signature(b"{}", "sha256=wrong") is False


class TestExtractChangedFiles:
    def test_fetches_all_pr_file_pages(self, monkeypatch):
        monkeypatch.setenv("GITHUB_PAT_TOKEN", "token")
        payload = {
            "action": "opened",
            "number": 12,
            "repository": {
                "owner": {"login": "ariefeko"},
                "name": "tagihin",
            },
        }
        first_page = [
            {"filename": f"src/file_{i}.py", "status": "modified"}
            for i in range(100)
        ]
        second_page = [
            {"filename": "src/final.py", "status": "added"},
            {"filename": "src/deleted.py", "status": "removed"},
        ]
        responses = [
            MagicMock(status_code=200, json=MagicMock(return_value=first_page)),
            MagicMock(status_code=200, json=MagicMock(return_value=second_page)),
        ]

        with patch("src.api.webhook.httpx.get", side_effect=responses) as mock_get:
            result = webhook.extract_changed_files("pull_request", payload)

        assert "src/file_0.py" in result
        assert "src/final.py" in result
        assert "src/deleted.py" not in result
        assert mock_get.call_count == 2
        assert mock_get.call_args_list[0].kwargs["params"] == {"per_page": 100, "page": 1}
        assert mock_get.call_args_list[1].kwargs["params"] == {"per_page": 100, "page": 2}

    def test_skips_non_reviewable_pr_actions(self):
        payload = {
            "action": "closed",
            "number": 12,
            "repository": {
                "owner": {"login": "ariefeko"},
                "name": "tagihin",
            },
        }

        assert webhook.extract_changed_files("pull_request", payload) == []


class TestExtractHeadOwner:
    def test_extracts_pull_request_head_owner(self):
        payload = {
            "pull_request": {
                "head": {
                    "repo": {
                        "owner": {"login": "contributor"},
                    }
                }
            }
        }

        assert webhook.extract_head_owner("pull_request", payload) == "contributor"

    def test_returns_none_for_push(self):
        assert webhook.extract_head_owner("push", {}) is None


class TestSentryDedup:
    @pytest.mark.asyncio
    async def test_deletes_dedup_key_when_enqueue_fails(self, monkeypatch):
        monkeypatch.setenv("CODEGUARD_DEFAULT_OWNER", "ariefeko")
        monkeypatch.setenv("CODEGUARD_DEFAULT_REPO", "tagihin")

        redis_client = MagicMock()
        redis_client.set.return_value = True
        queue = MagicMock()
        queue.enqueue.side_effect = RuntimeError("redis enqueue failed")

        request = MagicMock()
        request.body = AsyncMock(return_value=b"{}")
        request.headers.get.side_effect = lambda key, default=None: {
            "Sentry-Hook-Signature": "valid",
            "Sentry-Hook-Resource": "issue",
        }.get(key, default)

        parsed_error = {
            "type": "RuntimeError",
            "message": "boom",
            "file": "src/app.py",
            "line": 1,
            "related_file_paths": ["src/app.py"],
            "issue_id": "issue-1",
        }

        with patch("src.api.webhook.SentryAgent") as agent_cls, patch(
            "src.api.webhook.get_redis_connection",
            return_value=redis_client,
        ), patch("src.api.webhook.get_queue", return_value=queue):
            agent = agent_cls.return_value
            agent.verify_signature.return_value = True
            agent.parse_error.return_value = parsed_error

            with pytest.raises(RuntimeError):
                await webhook.sentry_webhook(request)

        redis_client.delete.assert_called_once_with("codeguard:sentry:processed:issue-1")
