import os
import redis
from rq import Worker, Queue
from dotenv import load_dotenv
from src.context.context_builder import ContextBuilder
from src.orchestration.orchestrator import Orchestrator
from src.github.github_client import GitHubClient
from src.utils.formatters import format_pr_comment

load_dotenv()


def get_redis_connection():
    return redis.from_url(os.getenv("REDIS_URL"))


def get_queue(name: str = "codeguard") -> Queue:
    conn = get_redis_connection()
    return Queue(name, connection=conn)


def process_github_review(owner: str, repo: str, ref: str, branch: str, changed_files: list[str]):
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
    pr_number = github.get_open_pr_for_branch(branch) if branch else None

    if pr_number:
        body = format_pr_comment(result)
        github.post_pr_comment(pr_number, body)
    else:
        title = f"🤖 CodeGuard AI Review — {branch or ref[:7]}"
        body = format_pr_comment(result)
        github.create_issue(title, body)


if __name__ == "__main__":
    conn = get_redis_connection()
    queue = Queue("codeguard", connection=conn)
    worker = Worker([queue], connection=conn)
    print("[Worker] Starting RQ worker...")
    worker.work()
