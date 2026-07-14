import hashlib
import hmac
import json
import logging
import os

from fastapi import APIRouter, Request

from src.api.responses import webhook_response
from src.config import HTTP_REQUEST_TIMEOUT_SECONDS, RQ_JOB_TIMEOUT_SECONDS
from src.github.http_client import build_github_headers, get_github_http_client
from src.github.repo_policy import (
    RepositoryAllowlistNotConfiguredError,
    is_repo_allowed,
)
from src.worker.redis_connection import get_queue
from src.worker.worker import process_github_review


router = APIRouter()
logger = logging.getLogger(__name__)

GITHUB_PR_FILES_PER_PAGE = 100
GITHUB_PR_FILES_MAX_PAGES = 10


@router.post("/webhook/github")
async def github_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_github_signature(raw_body, signature):
        logger.warning("GitHub signature verification failed")
        return webhook_response(
            401,
            "rejected",
            "invalid signature",
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("GitHub payload contains invalid JSON")
        return webhook_response(400, "rejected", "invalid JSON payload")

    event_type = request.headers.get("X-GitHub-Event", "unknown")
    logger.info("GitHub webhook received", extra={"github_event": event_type})

    repo_info = extract_repo_info(payload)
    if repo_info is None:
        logger.warning("GitHub payload is missing repository identity")
        return webhook_response(400, "rejected", "invalid repository data")

    owner, repo = repo_info
    try:
        repo_allowed = is_repo_allowed(owner, repo)
    except RepositoryAllowlistNotConfiguredError:
        logger.error("GitHub repository allowlist is not configured")
        return webhook_response(
            503,
            "error",
            "repository policy not configured",
        )

    if not repo_allowed:
        logger.warning("GitHub repository is not allowed")
        return webhook_response(403, "rejected", "repository not allowed")

    changed_files = extract_changed_files(event_type, payload)
    logger.info("GitHub changed files extracted", extra={"file_count": len(changed_files)})

    if not changed_files:
        return webhook_response(200, "skipped", "no changed files")

    ref = payload.get("after") or payload.get("pull_request", {}).get("head", {}).get("sha")
    branch = extract_branch(event_type, payload)
    pr_number = payload.get("number") if event_type == "pull_request" else None
    head_owner = extract_head_owner(event_type, payload)

    queue = get_queue()
    job = queue.enqueue(
        process_github_review,
        owner,
        repo,
        ref,
        branch,
        changed_files,
        pr_number,
        head_owner,
        job_timeout=RQ_JOB_TIMEOUT_SECONDS,
    )

    logger.info("GitHub review job enqueued", extra={"job_id": job.id})
    return webhook_response(
        202,
        "accepted",
        "review queued",
        job_id=job.id,
    )


def extract_repo_info(payload: dict) -> tuple[str, str] | None:
    repository = payload.get("repository")
    if not isinstance(repository, dict):
        return None

    owner_data = repository.get("owner")
    if not isinstance(owner_data, dict):
        return None

    owner = owner_data.get("login")
    repo = repository.get("name")
    if not owner or not repo:
        return None

    return owner, repo


def verify_github_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """Verify a GitHub webhook HMAC SHA-256 signature."""
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if not secret:
        logger.error("GitHub webhook secret is not configured")
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        logger.warning("GitHub signature header is invalid")
        return False

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def extract_changed_files(event_type: str, payload: dict) -> list[str]:
    files = set()

    if event_type == "push":
        commits = payload.get("commits", [])
        for commit in commits:
            files.update(commit.get("added", []))
            files.update(commit.get("modified", []))

    elif event_type == "pull_request":
        action = payload.get("action")
        pr_number = payload.get("number")
        logger.info("GitHub pull request event", extra={"pr_action": action})

        if action not in ("opened", "synchronize"):
            return []

        owner = payload["repository"]["owner"]["login"]
        repo = payload["repository"]["name"]
        headers = build_github_headers(os.getenv("GITHUB_PAT_TOKEN"))
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/files"
        http_client = get_github_http_client()

        for page in range(1, GITHUB_PR_FILES_MAX_PAGES + 1):
            response = http_client.get(
                url,
                headers=headers,
                params={"per_page": GITHUB_PR_FILES_PER_PAGE, "page": page},
                timeout=HTTP_REQUEST_TIMEOUT_SECONDS,
            )

            if response.status_code != 200:
                logger.warning(
                    "Failed to fetch GitHub pull request files",
                    extra={"status_code": response.status_code},
                )
                break

            page_files = response.json()
            for file_data in page_files:
                if file_data.get("status") != "removed":
                    files.add(file_data["filename"])

            if len(page_files) < GITHUB_PR_FILES_PER_PAGE:
                break
        else:
            logger.warning(
                "GitHub pull request file pagination limit reached",
                extra={"page_limit": GITHUB_PR_FILES_MAX_PAGES},
            )

    return list(files)


def extract_branch(event_type: str, payload: dict) -> str | None:
    if event_type == "push":
        ref = payload.get("ref", "")
        return ref.replace("refs/heads/", "") if ref else None
    if event_type == "pull_request":
        return payload.get("pull_request", {}).get("head", {}).get("ref")
    return None


def extract_head_owner(event_type: str, payload: dict) -> str | None:
    if event_type == "pull_request":
        return (
            payload.get("pull_request", {})
            .get("head", {})
            .get("repo", {})
            .get("owner", {})
            .get("login")
        )
    return None
