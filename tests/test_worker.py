from unittest.mock import MagicMock, patch

import pytest

from src.worker import worker


def clear_redis_env(monkeypatch):
    for name in (
        "REDIS_URL",
        "REDIS_PRIVATE_URL",
        "REDIS_PUBLIC_URL",
        "REDISHOST",
        "REDIS_HOST",
        "REDISPORT",
        "REDIS_PORT",
        "REDISPASSWORD",
        "REDIS_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_get_redis_url_accepts_full_redis_url(monkeypatch):
    clear_redis_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://default:secret@redis.railway.internal:6379")

    assert worker.get_redis_url() == "redis://default:secret@redis.railway.internal:6379"


def test_get_redis_url_builds_from_host_port_password(monkeypatch):
    clear_redis_env(monkeypatch)
    monkeypatch.setenv("REDISHOST", "redis.railway.internal")
    monkeypatch.setenv("REDISPORT", "6379")
    monkeypatch.setenv("REDISPASSWORD", "secret")

    assert worker.get_redis_url() == "redis://:secret@redis.railway.internal:6379"


def test_get_redis_url_rejects_url_without_scheme(monkeypatch):
    clear_redis_env(monkeypatch)
    invalid_url = "not-redis://default:super-secret@redis.internal:6379"
    monkeypatch.setenv("REDIS_URL", invalid_url)

    try:
        worker.get_redis_url()
    except ValueError as exc:
        message = str(exc)
        assert "not a valid Redis connection URL" in message
        assert "super-secret" not in message
        assert invalid_url not in message
    else:
        raise AssertionError("Expected ValueError")


def test_missing_redis_configuration_message_has_no_credential_template(monkeypatch):
    clear_redis_env(monkeypatch)

    with pytest.raises(RuntimeError) as exc_info:
        worker.get_redis_url()

    message = str(exc_info.value)
    assert "password" not in message.lower()
    assert "redis://" not in message


def test_get_redis_connection_sets_socket_timeouts(monkeypatch):
    clear_redis_env(monkeypatch)
    monkeypatch.delenv("REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("REDIS_SOCKET_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://default:secret@redis.railway.internal:6379")

    with patch("src.worker.worker.redis.from_url") as from_url:
        worker.get_redis_connection()

    from_url.assert_called_once_with(
        "redis://default:secret@redis.railway.internal:6379",
        socket_connect_timeout=5.0,
        socket_timeout=5.0,
    )


def test_has_blocking_findings_detects_high_and_critical():
    assert worker.has_blocking_findings("HIGH - SQL injection risk") is True
    assert worker.has_blocking_findings("Critical severity issue found") is True


def test_has_blocking_findings_ignores_non_blocking_summary():
    assert worker.has_blocking_findings("No high severity issues found") is False
    assert worker.has_blocking_findings("LOW - typo") is False


def test_github_review_uses_pr_number_from_webhook():
    context_builder = MagicMock()
    context_builder.build.return_value = {
        "changed_files": {"src/app.py": "changed content"},
        "related_files": {},
    }
    orchestrator = MagicMock()
    orchestrator.review_code.return_value = "review result"
    github = MagicMock()

    with patch("src.worker.worker.ContextBuilder", return_value=context_builder), patch(
        "src.worker.worker.Orchestrator",
        return_value=orchestrator,
    ), patch("src.worker.worker.GitHubClient", return_value=github):
        worker.process_github_review(
            "ariefeko",
            "tagihin",
            "abc123",
            "feature/test",
            ["src/app.py"],
            pr_number=42,
            head_owner="contributor",
        )

    github.get_open_pr_for_branch.assert_not_called()
    github.post_pr_comment.assert_called_once()
    assert github.post_pr_comment.call_args.args[0] == 42
    assert github.set_commit_status.call_args_list[0].args[:2] == ("abc123", "pending")
    assert github.set_commit_status.call_args_list[1].args[:2] == ("abc123", "success")


def test_github_review_sets_failure_status_for_blocking_findings():
    context_builder = MagicMock()
    context_builder.build.return_value = {
        "changed_files": {"src/app.py": "changed content"},
        "related_files": {},
    }
    orchestrator = MagicMock()
    orchestrator.review_code.return_value = "HIGH - SQL injection risk"
    github = MagicMock()

    with patch("src.worker.worker.ContextBuilder", return_value=context_builder), patch(
        "src.worker.worker.Orchestrator",
        return_value=orchestrator,
    ), patch("src.worker.worker.GitHubClient", return_value=github):
        worker.process_github_review(
            "ariefeko",
            "tagihin",
            "abc123",
            "feature/test",
            ["src/app.py"],
            pr_number=42,
        )

    assert github.set_commit_status.call_args_list[0].args[:2] == ("abc123", "pending")
    assert github.set_commit_status.call_args_list[1].args[:2] == ("abc123", "failure")


def test_github_review_sets_success_status_when_no_analyzable_files():
    context_builder = MagicMock()
    context_builder.build.return_value = {
        "changed_files": {},
        "related_files": {},
    }
    github = MagicMock()

    with patch("src.worker.worker.ContextBuilder", return_value=context_builder), patch(
        "src.worker.worker.GitHubClient",
        return_value=github,
    ):
        worker.process_github_review(
            "ariefeko",
            "tagihin",
            "abc123",
            "feature/test",
            ["README.md"],
            pr_number=42,
        )

    assert github.set_commit_status.call_args_list[0].args[:2] == ("abc123", "pending")
    assert github.set_commit_status.call_args_list[1].args[:2] == ("abc123", "success")
    github.post_pr_comment.assert_not_called()


def test_github_review_sets_error_status_when_worker_fails():
    context_builder = MagicMock()
    context_builder.build.side_effect = RuntimeError("boom")
    github = MagicMock()

    with patch("src.worker.worker.ContextBuilder", return_value=context_builder), patch(
        "src.worker.worker.GitHubClient",
        return_value=github,
    ), pytest.raises(RuntimeError):
        worker.process_github_review(
            "ariefeko",
            "tagihin",
            "abc123",
            "feature/test",
            ["src/app.py"],
            pr_number=42,
        )

    assert github.set_commit_status.call_args_list[0].args[:2] == ("abc123", "pending")
    assert github.set_commit_status.call_args_list[1].args[:2] == ("abc123", "error")


def test_github_review_falls_back_to_branch_lookup_with_head_owner():
    context_builder = MagicMock()
    context_builder.build.return_value = {
        "changed_files": {"src/app.py": "changed content"},
        "related_files": {},
    }
    orchestrator = MagicMock()
    orchestrator.review_code.return_value = "review result"
    github = MagicMock()
    github.get_open_pr_for_branch.return_value = 7

    with patch("src.worker.worker.ContextBuilder", return_value=context_builder), patch(
        "src.worker.worker.Orchestrator",
        return_value=orchestrator,
    ), patch("src.worker.worker.GitHubClient", return_value=github):
        worker.process_github_review(
            "ariefeko",
            "tagihin",
            "abc123",
            "feature/test",
            ["src/app.py"],
            head_owner="contributor",
        )

    github.get_open_pr_for_branch.assert_called_once_with(
        "feature/test",
        head_owner="contributor",
    )
    github.post_pr_comment.assert_called_once()


def test_sentry_job_fetches_context_from_default_branch(bug_analysis_factory):
    context_builder = MagicMock()
    context_builder.build.return_value = {
        "changed_files": {"src/app.py": "changed content"},
        "related_files": {},
    }
    orchestrator = MagicMock()
    orchestrator.fix_bug.return_value = bug_analysis_factory(affected_file="src/app.py")
    github = MagicMock()
    github.get_default_branch.return_value = "develop"

    with patch("src.worker.worker.GitHubClient", return_value=github), patch(
        "src.worker.worker.ContextBuilder",
        return_value=context_builder,
    ) as context_builder_cls, patch(
        "src.worker.worker.Orchestrator",
        return_value=orchestrator,
    ):
        worker.process_sentry_job(
            "ariefeko",
            "tagihin",
            "RuntimeError",
            "boom",
            "src/app.py",
            1,
            ["src/app.py"],
        )

    context_builder_cls.assert_called_once_with("ariefeko", "tagihin", ref="develop")
    github.create_issue.assert_called_once()
