import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.config import HTTP_REQUEST_TIMEOUT_SECONDS
from src.github.http_client import build_github_headers, get_github_http_client
from src.github.repo_policy import (
    RepositoryAllowlistNotConfiguredError,
    is_repo_allowed,
)
from src.worker.redis_connection import get_queue
from src.worker.worker import process_github_review


router = APIRouter()

GITHUB_PR_FILES_PER_PAGE = 100
GITHUB_PR_FILES_MAX_PAGES = 10


@router.post("/webhook/github")
async def github_webhook(request: Request):
    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_github_signature(raw_body, signature):
        print("[webhook] GitHub signature verification failed — request rejected")
        return JSONResponse(
            status_code=401,
            content={"status": "rejected"},
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        print("[webhook] GitHub payload contains invalid JSON")
        return JSONResponse(status_code=400, content={"status": "rejected"})

    event_type = request.headers.get("X-GitHub-Event", "unknown")
    print(f"\n=== GITHUB WEBHOOK: {event_type} ===")

    repo_info = extract_repo_info(payload)
    if repo_info is None:
        print("[webhook] GitHub payload is missing the repository owner or name")
        return JSONResponse(status_code=400, content={"status": "rejected"})

    owner, repo = repo_info
    try:
        repo_allowed = is_repo_allowed(owner, repo)
    except RepositoryAllowlistNotConfiguredError:
        print("[webhook] GitHub repository allowlist is not configured")
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "reason": "repository policy not configured",
            },
        )

    if not repo_allowed:
        print(f"[webhook] GitHub repository is not allowed: {owner}/{repo}")
        return JSONResponse(
            status_code=403,
            content={"status": "rejected"},
        )

    changed_files = extract_changed_files(event_type, payload)
    print(f"Changed files ({len(changed_files)}):")
    for file_path in changed_files:
        print(f"  - {file_path}")

    if not changed_files:
        return {"status": "skipped", "reason": "no changed files"}

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
        job_timeout=120,
    )

    print(f"[webhook] Job enqueued: {job.id}")
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "job_id": job.id,
            "message": "Review queued — PR comment will appear shortly",
        },
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
        print("[webhook] GITHUB_WEBHOOK_SECRET is not configured — rejecting request")
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        print("[webhook] X-Hub-Signature-256 header is invalid")
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
        print(f"PR #{pr_number} action: {action}")

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
                print(f"[webhook] Failed to fetch PR files: HTTP {response.status_code}")
                break

            page_files = response.json()
            for file_data in page_files:
                if file_data.get("status") != "removed":
                    files.add(file_data["filename"])

            if len(page_files) < GITHUB_PR_FILES_PER_PAGE:
                break
        else:
            print(
                "[webhook] PR file pagination limit reached: "
                f"{GITHUB_PR_FILES_MAX_PAGES} pages"
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
