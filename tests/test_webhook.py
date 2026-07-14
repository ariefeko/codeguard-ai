import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api import webhook
from src.github.repo_policy import RepositoryAllowlistNotConfiguredError


def test_composed_router_exposes_each_webhook_once():
    webhook_paths = [
        route.path
        for route in webhook.router.routes
        if route.path.startswith("/webhook/")
    ]

    assert webhook_paths.count("/webhook/github") == 1
    assert webhook_paths.count("/webhook/sentry") == 1


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

    @pytest.mark.asyncio
    async def test_github_webhook_rejects_with_generic_response(self, monkeypatch):
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "github-secret")
        request = MagicMock()
        request.body = AsyncMock(return_value=b"{}")
        request.headers.get.return_value = "sha256=wrong"

        response = await webhook.github_webhook(request)

        assert response.status_code == 401
        assert response.body == b'{"status":"rejected"}'

    @pytest.mark.asyncio
    async def test_github_webhook_rejects_malformed_json(self, monkeypatch):
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "github-secret")
        body = b"{not-json"
        signature = "sha256=" + hmac.new(
            b"github-secret",
            body,
            hashlib.sha256,
        ).hexdigest()
        request = MagicMock()
        request.body = AsyncMock(return_value=body)
        request.headers.get.side_effect = lambda key, default=None: {
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "push",
        }.get(key, default)

        response = await webhook.github_webhook(request)

        assert response.status_code == 400
        assert response.body == b'{"status":"rejected"}'

    @pytest.mark.asyncio
    async def test_github_webhook_rejects_missing_repository(self, monkeypatch):
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "github-secret")
        body = b'{"zen":"missing repo"}'
        signature = "sha256=" + hmac.new(
            b"github-secret",
            body,
            hashlib.sha256,
        ).hexdigest()
        request = MagicMock()
        request.body = AsyncMock(return_value=body)
        request.headers.get.side_effect = lambda key, default=None: {
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "push",
        }.get(key, default)

        response = await webhook.github_webhook(request)

        assert response.status_code == 400
        assert response.body == b'{"status":"rejected"}'


class TestRepoPolicy:
    def test_allows_configured_repo(self, monkeypatch):
        monkeypatch.setenv("CODEGUARD_ALLOWED_REPOS", "ariefeko/tagihin")

        assert webhook.is_repo_allowed("ariefeko", "tagihin") is True

    def test_allows_default_repo_mapping(self, monkeypatch):
        monkeypatch.delenv("CODEGUARD_ALLOWED_REPOS", raising=False)
        monkeypatch.setenv("CODEGUARD_DEFAULT_OWNER", "ariefeko")
        monkeypatch.setenv("CODEGUARD_DEFAULT_REPO", "tagihin")

        assert webhook.is_repo_allowed("ariefeko", "tagihin") is True

    def test_rejects_unconfigured_repo(self, monkeypatch):
        monkeypatch.setenv("CODEGUARD_ALLOWED_REPOS", "ariefeko/tagihin")
        monkeypatch.delenv("CODEGUARD_DEFAULT_OWNER", raising=False)
        monkeypatch.delenv("CODEGUARD_DEFAULT_REPO", raising=False)

        assert webhook.is_repo_allowed("attacker", "repo") is False

    def test_raises_distinct_error_when_allowlist_is_missing(self, monkeypatch):
        monkeypatch.delenv("CODEGUARD_ALLOWED_REPOS", raising=False)
        monkeypatch.delenv("CODEGUARD_DEFAULT_OWNER", raising=False)
        monkeypatch.delenv("CODEGUARD_DEFAULT_REPO", raising=False)

        with pytest.raises(RepositoryAllowlistNotConfiguredError):
            webhook.is_repo_allowed("ariefeko", "tagihin")

    @pytest.mark.asyncio
    async def test_webhook_reports_missing_allowlist_as_configuration_error(
        self,
        monkeypatch,
    ):
        monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "github-secret")
        monkeypatch.delenv("CODEGUARD_ALLOWED_REPOS", raising=False)
        monkeypatch.delenv("CODEGUARD_DEFAULT_OWNER", raising=False)
        monkeypatch.delenv("CODEGUARD_DEFAULT_REPO", raising=False)
        body = (
            b'{"repository":{"owner":{"login":"ariefeko"},'
            b'"name":"tagihin"},"commits":[]}'
        )
        signature = "sha256=" + hmac.new(
            b"github-secret",
            body,
            hashlib.sha256,
        ).hexdigest()
        request = MagicMock()
        request.body = AsyncMock(return_value=body)
        request.headers.get.side_effect = lambda key, default=None: {
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "push",
        }.get(key, default)

        response = await webhook.github_webhook(request)

        assert response.status_code == 503
        assert response.body == (
            b'{"status":"error","reason":"repository policy not configured"}'
        )


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

        http_client = MagicMock()
        http_client.get.side_effect = responses
        with patch(
            "src.api.github_webhook.get_github_http_client",
            return_value=http_client,
        ):
            result = webhook.extract_changed_files("pull_request", payload)

        assert "src/file_0.py" in result
        assert "src/final.py" in result
        assert "src/deleted.py" not in result
        assert http_client.get.call_count == 2
        assert http_client.get.call_args_list[0].kwargs["params"] == {
            "per_page": 100,
            "page": 1,
        }
        assert http_client.get.call_args_list[1].kwargs["params"] == {
            "per_page": 100,
            "page": 2,
        }

    def test_stops_pr_file_pagination_at_configured_limit(self, monkeypatch):
        monkeypatch.setenv("GITHUB_PAT_TOKEN", "token")
        payload = {
            "action": "opened",
            "number": 12,
            "repository": {
                "owner": {"login": "ariefeko"},
                "name": "tagihin",
            },
        }

        def full_page(*_args, **kwargs):
            page = kwargs["params"]["page"]
            files = [
                {
                    "filename": f"src/page_{page}_file_{index}.py",
                    "status": "modified",
                }
                for index in range(100)
            ]
            return MagicMock(status_code=200, json=MagicMock(return_value=files))

        http_client = MagicMock()
        http_client.get.side_effect = full_page
        with patch(
            "src.api.github_webhook.get_github_http_client",
            return_value=http_client,
        ):
            result = webhook.extract_changed_files("pull_request", payload)

        assert http_client.get.call_count == webhook.GITHUB_PR_FILES_MAX_PAGES
        assert len(result) == 1_000
        assert http_client.get.call_args.kwargs["params"]["page"] == 10

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


class TestExtractRepoInfo:
    def test_extracts_repo_owner_and_name(self):
        payload = {
            "repository": {
                "owner": {"login": "ariefeko"},
                "name": "tagihin",
            }
        }

        assert webhook.extract_repo_info(payload) == ("ariefeko", "tagihin")

    def test_returns_none_for_missing_repo_fields(self):
        assert webhook.extract_repo_info({"repository": {"owner": {}}}) is None


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
    async def test_sentry_webhook_rejects_with_generic_response(self):
        request = MagicMock()
        request.body = AsyncMock(return_value=b"{}")
        request.headers.get.return_value = "invalid"

        with patch("src.api.sentry_webhook.SentryAgent") as agent_cls:
            agent_cls.return_value.verify_signature.return_value = False

            response = await webhook.sentry_webhook(request)

        assert response.status_code == 401
        assert response.body == b'{"status":"rejected"}'

    @pytest.mark.asyncio
    async def test_sentry_webhook_rejects_malformed_json(self):
        request = MagicMock()
        request.body = AsyncMock(return_value=b"{not-json")
        request.headers.get.return_value = "valid"

        with patch("src.api.sentry_webhook.SentryAgent") as agent_cls:
            agent_cls.return_value.verify_signature.return_value = True

            response = await webhook.sentry_webhook(request)

        assert response.status_code == 400
        assert response.body == b'{"status":"rejected"}'

    @pytest.mark.asyncio
    async def test_clears_pending_dedup_key_when_enqueue_fails(self, monkeypatch):
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

        with patch("src.api.sentry_webhook.SentryAgent") as agent_cls, patch(
            "src.api.sentry_webhook.get_redis_connection",
            return_value=redis_client,
        ), patch("src.api.sentry_webhook.get_queue", return_value=queue):
            agent = agent_cls.return_value
            agent.verify_signature.return_value = True
            agent.parse_error.return_value = parsed_error

            with pytest.raises(RuntimeError):
                await webhook.sentry_webhook(request)

        redis_client.set.assert_called_once_with(
            "codeguard:sentry:processed:issue-1",
            "pending",
            ex=webhook.SENTRY_DEDUP_PENDING_TTL_SECONDS,
            nx=True,
        )
        redis_client.delete.assert_called_once_with(
            "codeguard:sentry:processed:issue-1"
        )

    @pytest.mark.asyncio
    async def test_rejects_incomplete_parsed_error_before_enqueue(self, monkeypatch):
        monkeypatch.setenv("CODEGUARD_DEFAULT_OWNER", "ariefeko")
        monkeypatch.setenv("CODEGUARD_DEFAULT_REPO", "tagihin")

        request = MagicMock()
        request.body = AsyncMock(return_value=b"{}")
        request.headers.get.side_effect = lambda key, default=None: {
            "Sentry-Hook-Signature": "valid",
            "Sentry-Hook-Resource": "issue",
        }.get(key, default)

        queue = MagicMock()
        with patch("src.api.sentry_webhook.SentryAgent") as agent_cls, patch(
            "src.api.sentry_webhook.get_redis_connection"
        ) as get_redis, patch(
            "src.api.sentry_webhook.get_queue", return_value=queue
        ):
            agent = agent_cls.return_value
            agent.verify_signature.return_value = True
            agent.parse_error.return_value = {
                "type": "RuntimeError",
                "message": "boom",
            }

            response = await webhook.sentry_webhook(request)

        assert response.status_code == 400
        assert response.body == (
            b'{"status":"rejected","reason":"invalid error data"}'
        )
        get_redis.assert_not_called()
        queue.enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_promotes_dedup_key_after_enqueue_success(self, monkeypatch):
        monkeypatch.setenv("CODEGUARD_DEFAULT_OWNER", "ariefeko")
        monkeypatch.setenv("CODEGUARD_DEFAULT_REPO", "tagihin")

        redis_client = MagicMock()
        redis_client.set.return_value = True
        queue = MagicMock()
        queue.enqueue.return_value = MagicMock(id="job-1")

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

        with patch("src.api.sentry_webhook.SentryAgent") as agent_cls, patch(
            "src.api.sentry_webhook.get_redis_connection",
            return_value=redis_client,
        ), patch("src.api.sentry_webhook.get_queue", return_value=queue):
            agent = agent_cls.return_value
            agent.verify_signature.return_value = True
            agent.parse_error.return_value = parsed_error

            response = await webhook.sentry_webhook(request)

        assert response.status_code == 202
        assert redis_client.set.call_args_list[0].args == (
            "codeguard:sentry:processed:issue-1",
            "pending",
        )
        assert redis_client.set.call_args_list[0].kwargs == {
            "ex": webhook.SENTRY_DEDUP_PENDING_TTL_SECONDS,
            "nx": True,
        }
        assert redis_client.set.call_args_list[1].args == (
            "codeguard:sentry:processed:issue-1",
            "queued:job-1",
        )
        assert redis_client.set.call_args_list[1].kwargs == {
            "ex": webhook.SENTRY_DEDUP_TTL_SECONDS,
        }
