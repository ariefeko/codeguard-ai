import os
import re
import redis
from rq import Worker, Queue
from dotenv import load_dotenv
from src.config import CODEGUARD_APP_ID
from src.context.context_builder import ContextBuilder
from src.orchestration.orchestrator import Orchestrator
from src.github.github_client import GitHubClient
from src.utils.formatters import format_pr_comment, format_bug_issue, format_bug_fallback_issue

load_dotenv()

REDIS_URL_SCHEMES = ("redis://", "rediss://", "unix://")
REVIEW_ANALYSIS_FALLBACK_MESSAGE = "Error: all LLM providers failed."
BLOCKING_SEVERITY_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:#+\s*)?(?:\d+\.\s*)?(critical|high)\b"
    r"|\b(critical|high)\s+severity\b",
    re.IGNORECASE,
)


def get_redis_url() -> str:
    for name in ("REDIS_URL", "REDIS_PRIVATE_URL", "REDIS_PUBLIC_URL"):
        value = os.getenv(name)
        if not value:
            continue

        value = value.strip()
        if value.startswith(REDIS_URL_SCHEMES):
            return value

        raise ValueError(
            f"{name} is not a valid Redis connection URL. "
            "See the deployment documentation for supported configuration formats."
        )

    host = os.getenv("REDISHOST") or os.getenv("REDIS_HOST")
    port = os.getenv("REDISPORT") or os.getenv("REDIS_PORT") or "6379"
    password = os.getenv("REDISPASSWORD") or os.getenv("REDIS_PASSWORD")

    if host:
        # The constructed URL may contain the Redis password; never log it.
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{host}:{port}"

    raise RuntimeError(
        "Redis connection is not configured. See the deployment documentation "
        "for supported Railway Redis settings."
    )


def get_redis_connection():
    return redis.from_url(
        get_redis_url(),
        socket_connect_timeout=float(
            os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS", "5")
        ),
        socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "5")),
    )


def get_queue(name: str = "codeguard") -> Queue:
    conn = get_redis_connection()
    return Queue(name, connection=conn)


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
    Job function yang dijalankan oleh worker.
    Dipanggil dari queue — bukan dari webhook langsung.
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

        print("\n[Worker] === LLM REVIEW RESULT ===")
        print(result)

        state, description = review_status_for_result(result)
        github.set_commit_status(
            ref,
            state,
            description,
            context=CODEGUARD_APP_ID,
            target_url=status_target_url,
        )

        # Output → post ke GitHub
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
    Job function yang dijalankan oleh worker untuk Sentry error.
    Dipanggil dari queue — bukan dari webhook langsung.

    related_file_paths: file path dari stack trace Sentry, dipakai
    ContextBuilder untuk fetch isi file (peran serupa changed_files
    di process_github_review, tapi sumbernya stack trace bukan diff PR).
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

    # Context Builder — fetch file dari stack trace, bukan dari PR diff
    cb = ContextBuilder(owner, repo, ref=default_branch)
    context = cb.build(related_file_paths)

    if not context["changed_files"]:
        print("[Worker] No analyzable files from stack trace — skipping")
        return

    # Orchestration → LLM (structured, dengan schema validation)
    orchestrator = Orchestrator()
    analysis = orchestrator.fix_bug(context, error)

    if analysis is None:
        # Semua provider gagal (token habis, validasi schema gagal, atau
        # koneksi error). Jangan crash atau diam -- fallback ke
        # Level 1 minimal: Issue manual dengan raw error data.
        print("[Worker] Sentry analysis gagal untuk semua provider — fallback ke manual issue")
        title = f"🐛 [Bug] {error_type}"
        body = format_bug_fallback_issue(error)
        github.create_issue(title, body, labels=["bug", "needs-manual-review"])
        return

    print("\n[Worker] === BUG ANALYSIS RESULT ===")
    print(f"Status: {analysis.status}")
    print(f"Root cause: {analysis.root_cause}")

    title = f"🐛 [Bug] {error_type} in {analysis.affected_file}"
    body = format_bug_issue(analysis, error)
    github.create_issue(title, body, labels=["bug", "ai-analyzed"])


if __name__ == "__main__":
    conn = get_redis_connection()
    queue = Queue("codeguard", connection=conn)
    worker = Worker([queue], connection=conn)
    print("[Worker] Starting RQ worker...")
    worker.work()
