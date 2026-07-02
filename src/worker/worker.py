import os
import redis
from rq import Worker, Queue
from dotenv import load_dotenv
from src.context.context_builder import ContextBuilder
from src.orchestration.orchestrator import Orchestrator
from src.github.github_client import GitHubClient
from src.utils.formatters import format_pr_comment, format_bug_issue, format_bug_fallback_issue

load_dotenv()

REDIS_URL_SCHEMES = ("redis://", "rediss://", "unix://")


def get_redis_url() -> str:
    for name in ("REDIS_URL", "REDIS_PRIVATE_URL", "REDIS_PUBLIC_URL"):
        value = os.getenv(name)
        if not value:
            continue

        value = value.strip()
        if value.startswith(REDIS_URL_SCHEMES):
            return value

        raise ValueError(
            f"{name} must start with one of {', '.join(REDIS_URL_SCHEMES)}. "
            "Set it to the full Redis connection URL from Railway, not just host:port."
        )

    host = os.getenv("REDISHOST") or os.getenv("REDIS_HOST")
    port = os.getenv("REDISPORT") or os.getenv("REDIS_PORT") or "6379"
    password = os.getenv("REDISPASSWORD") or os.getenv("REDIS_PASSWORD")

    if host:
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{host}:{port}"

    raise RuntimeError(
        "Redis connection is not configured. Set REDIS_URL to a full URL like "
        "redis://default:<password>@<host>:<port> or connect a Railway Redis service."
    )


def get_redis_connection():
    return redis.from_url(get_redis_url())


def get_queue(name: str = "codeguard") -> Queue:
    conn = get_redis_connection()
    return Queue(name, connection=conn)


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

    # Context Builder
    cb = ContextBuilder(owner, repo, ref)
    context = cb.build(changed_files)

    if not context["changed_files"]:
        print("[Worker] No analyzable files — skipping")
        return

    # Orchestration → LLM
    orchestrator = Orchestrator()
    result = orchestrator.review_code(context)

    print("\n[Worker] === LLM REVIEW RESULT ===")
    print(result)

    # Output → post ke GitHub
    github = GitHubClient(owner, repo)
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


def process_sentry_job(
    owner: str,
    repo: str,
    error_type: str,
    error_message: str,
    error_file: str,
    error_line: int | None,
    related_file_paths: list[str],
):
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
