from unittest.mock import MagicMock, patch

from src.worker import worker


def test_github_review_uses_pr_number_from_webhook():
    context_builder = MagicMock()
    context_builder.build.return_value = {
        "changed_files": {"src/app.py": "print('ok')"},
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


def test_github_review_falls_back_to_branch_lookup_with_head_owner():
    context_builder = MagicMock()
    context_builder.build.return_value = {
        "changed_files": {"src/app.py": "print('ok')"},
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
        "changed_files": {"src/app.py": "raise RuntimeError()"},
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
