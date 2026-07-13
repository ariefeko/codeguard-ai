import logging
import os
import re
from rq import Worker, Queue
from dotenv import load_dotenv
from src.config import CODEGUARD_APP_ID
from src.context.context_builder import ContextBuilder
from src.orchestration.orchestrator import Orchestrator
from src.github.github_client import GitHubClient
from src.utils.formatters import format_pr_comment, format_bug_issue, format_bug_fallback_issue
from src.worker.redis_connection import get_redis_connection

load_dotenv()

logger = logging.getLogger(__name__)
REVIEW_ANALYSIS_FALLBACK_MESSAGE = "Error: all LLM providers failed."
BLOCKING_SEVERITY_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:#+\s*)?(?:\d+\.\s*)?(critical|high)\b"
    r"|\b(critical|high)\s+severity\b",
    re.IGNORECASE,
)


def has_blocking_findings(review_result: str) -> bool:
    """Return True when review text contains a blocking severity finding."""
    for line in review_result.splitlines():
        normalized = line.strip().lower()
        if not normalized or normalized.startswith(("no high", "no critical")):
            continue
        if BLOCKING_SEVERITY_RE.search(line):
            return True
    return False


def review_status_for_result(review_result: str) -> tuple[str, str]:
    if review_result.startswith("Error:"):
        return "error", "CodeGuard analysis failed"
    if has_blocking_findings(review_result):
        return "failure", "CodeGuard found blocking issues"
    return "success", "CodeGuard found no blocking issues"


def log_llm_completion(
    analysis_type: str,
    result_length: int,
    status: str | None = None,
) -> None:
    """Log completion metadata without exposing model-generated content."""
    metadata = {"analysis_type": analysis_type}
    if status is not None:
        metadata["analysis_status"] = status
    if os.getenv("DEBUG_LLM_OUTPUT") == "1":
        metadata["result_length"] = result_length

    logger.info("LLM analysis completed", extra=metadata)


def process_github_review(
    owner: str,
    repo: str,
    ref: str,
    branch: str,
    changed_files: list[str],
    pr_number: int | None = None,
    head_owner: str | None = None,
):
    """
    Job function executed by the worker.
    Called from the queue rather than directly from the webhook.
    """

    print(f"[Worker] Processing review for {owner}/{repo} ref={ref}")
    github = GitHubClient(owner, repo)
    status_target_url = os.getenv("CODEGUARD_STATUS_TARGET_URL")
    github.set_commit_status(
        ref,
        "pending",
        "CodeGuard review is running",
        context=CODEGUARD_APP_ID,
        target_url=status_target_url,
    )

    try:
        # Context Builder
        cb = ContextBuilder(owner, repo, ref)
        context = cb.build(changed_files)

        if not context["changed_files"]:
            print("[Worker] No analyzable files — skipping")
            github.set_commit_status(
                ref,
                "success",
                "CodeGuard found no analyzable files",
                context=CODEGUARD_APP_ID,
                target_url=status_target_url,
            )
            return

        # Orchestration → LLM
        orchestrator = Orchestrator()
        result = orchestrator.review_code(context)
        if result is None:
            result = REVIEW_ANALYSIS_FALLBACK_MESSAGE

        log_llm_completion("github_review", len(result))

        state, description = review_status_for_result(result)
        github.set_commit_status(
            ref,
            state,
            description,
            context=CODEGUARD_APP_ID,
            target_url=status_target_url,
        )

        # Output → post to GitHub
        pr_number = pr_number or (
            github.get_open_pr_for_branch(branch, head_owner=head_owner) if branch else None
        )

        if pr_number:
            body = format_pr_comment(result)
            github.post_pr_comment(pr_number, body)
        else:
            title = f"🤖 CodeGuard AI Review — {branch or ref[:7]}"
            body = format_pr_comment(result)
            github.create_issue(title, body)
    except Exception:
        github.set_commit_status(
            ref,
            "error",
            "CodeGuard worker failed",
            context=CODEGUARD_APP_ID,
            target_url=status_target_url,
        )
        raise


def process_sentry_job(
    owner: str,
    repo: str,
    error_type: str,
    error_message: str,
    error_file: str,
    error_line: int | None,
    related_file_paths: list[str],
) -> None:
    """
    Job function executed by the worker for a Sentry error.
    Called from the queue rather than directly from the webhook.

    related_file_paths contains paths from the Sentry stack trace. ContextBuilder
    uses them to fetch file contents, similarly to changed_files in
    process_github_review, except the source is a stack trace rather than a PR diff.
    """

    print(f"[Worker] Processing Sentry error for {owner}/{repo}: {error_type}")

    error = {
        "type": error_type,
        "message": error_message,
        "file": error_file,
        "line": error_line,
    }

    github = GitHubClient(owner, repo)
    default_branch = github.get_default_branch()

    # Context Builder — fetch files from the stack trace, not a PR diff
    cb = ContextBuilder(owner, repo, ref=default_branch)
    context = cb.build(related_file_paths)

    if not context["changed_files"]:
        print("[Worker] No analyzable files from stack trace — skipping")
        return

    # Orchestration → LLM (structured, with schema validation)
    orchestrator = Orchestrator()
    analysis = orchestrator.fix_bug(context, error)

    if analysis is None:
        # All providers failed due to exhausted tokens, schema validation, or a
        # connection error. Fall back to a minimal manual issue with raw error data.
        print("[Worker] Sentry analysis failed for all providers — creating a manual issue")
        title = f"🐛 [Bug] {error_type}"
        body = format_bug_fallback_issue(error)
        github.create_issue(title, body, labels=["bug", "needs-manual-review"])
        return

    log_llm_completion(
        "sentry_bug",
        len(analysis.root_cause),
        status=analysis.status,
    )

    title = f"🐛 [Bug] {error_type} in {analysis.affected_file}"
    body = format_bug_issue(analysis, error)
    github.create_issue(title, body, labels=["bug", "ai-analyzed"])


if __name__ == "__main__":
    conn = get_redis_connection()
    queue = Queue("codeguard", connection=conn)
    worker = Worker([queue], connection=conn)
    print("[Worker] Starting RQ worker...")
    worker.work()
