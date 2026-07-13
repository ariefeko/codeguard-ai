import os
import json
import hashlib
import hmac
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from src.worker.worker import (
    get_queue,
    get_redis_connection,
    process_github_review,
    process_sentry_job,
)
from src.agents.sentry_agent import SentryAgent
from src.config import HTTP_REQUEST_TIMEOUT_SECONDS
from src.github.http_client import build_github_headers, get_github_http_client
from src.github.repo_policy import (
    RepositoryAllowlistNotConfiguredError,
    is_repo_allowed,
)

load_dotenv()

router = APIRouter()

SENTRY_DEDUP_TTL_SECONDS = 86400
GITHUB_PR_FILES_PER_PAGE = 100
GITHUB_PR_FILES_MAX_PAGES = 10
# Retry window if enqueue fails after the pending dedup key is acquired.
SENTRY_DEDUP_PENDING_TTL_SECONDS = int(
    os.getenv("SENTRY_DEDUP_PENDING_TTL_SECONDS", "60")
)


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
    for f in changed_files:
        print(f"  - {f}")

    if not changed_files:
        return {"status": "skipped", "reason": "no changed files"}

    ref = payload.get("after") or payload.get("pull_request", {}).get("head", {}).get("sha")
    branch = extract_branch(event_type, payload)
    pr_number = payload.get("number") if event_type == "pull_request" else None
    head_owner = extract_head_owner(event_type, payload)

    # Push the job to Redis Queue for an immediate response.
    queue = get_queue()
    job = queue.enqueue(
        process_github_review,  # Pass the function directly, not its name.
        owner, repo, ref, branch, changed_files, pr_number, head_owner,
        job_timeout=120,
    )

    print(f"[webhook] Job enqueued: {job.id}")

    return JSONResponse(
        status_code = 202,
        content = {
            "status": "accepted",
            "job_id": job.id,
            "message": "Review queued — PR comment will appear shortly"
        }
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
    """
    Verify a GitHub webhook signature.
    GitHub sends the HMAC SHA-256 value as "sha256=<hex digest>".
    """
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

        # Process only newly opened or synchronized pull requests.
        if action not in ("opened", "synchronize"):
            return []

        # Fetch changed files via GitHub API
        owner = payload["repository"]["owner"]["login"]
        repo = payload["repository"]["name"]
        token = os.getenv("GITHUB_PAT_TOKEN")

        headers = build_github_headers(token)
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
            for f in page_files:
                if f.get("status") != "removed":
                    files.add(f["filename"])

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


@router.post("/webhook/sentry")
async def sentry_webhook(request: Request):
    # IMPORTANT: read raw bytes before parsing JSON. Signature verification
    # requires the exact body sent by Sentry. Parsing and serializing it again
    # can change key order or whitespace and cause an otherwise valid signature
    # to fail verification.
    raw_body = await request.body()
    signature = request.headers.get("Sentry-Hook-Signature")

    agent = SentryAgent()
    if not agent.verify_signature(raw_body, signature):
        print("[webhook] Sentry signature verification failed — request rejected")
        return JSONResponse(
            status_code=401,
            content={"status": "rejected"},
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        print("[webhook] Sentry payload contains invalid JSON")
        return JSONResponse(status_code=400, content={"status": "rejected"})

    resource = request.headers.get("Sentry-Hook-Resource", "unknown")
    action = payload.get("action", "unknown")

    print(f"\n=== SENTRY WEBHOOK: resource={resource} action={action} ===")

    # Resources other than error, issue, or event_alert (such as installation
    # and comment) are not relevant to bug analysis; acknowledge and skip them.
    if resource not in ("error", "issue", "event_alert"):
        print(f"[webhook] Resource '{resource}' is not relevant — skipping")
        return {"status": "skipped", "reason": f"resource '{resource}' not handled"}

    error = agent.parse_error(payload)
    if error is None:
        print("[webhook] No error context could be extracted — skipping")
        return {"status": "skipped", "reason": "no parseable error data"}

    # Sentry does not provide the GitHub owner and repository because this is
    # not a GitHub event. Use environment-based project mapping until
    # multi-project mapping is available.
    owner = os.getenv("CODEGUARD_DEFAULT_OWNER")
    repo = os.getenv("CODEGUARD_DEFAULT_REPO")

    if not owner or not repo:
        print("[webhook] CODEGUARD_DEFAULT_OWNER/REPO is not configured — target unknown")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "reason": "no repo mapping configured"},
        )

    print(f"Error: {error['type']} — {error['message']}")
    print(f"File: {error['file']}:{error['line']}")

    # Use a short Redis pending lock before enqueueing, then promote it to a
    # 24-hour queued lock only after enqueue succeeds. Do not delete the key on
    # enqueue failure because that can race with a request that already observed
    # the lock and ignored the event. The pending TTL permits a later retry.
    issue_id = error.get("issue_id", "")
    dedup_key = None
    dedup_redis = None
    if issue_id:
        dedup_redis = get_redis_connection()
        dedup_key = f"codeguard:sentry:processed:{issue_id}"
        lock_acquired = dedup_redis.set(
            dedup_key,
            "pending",
            ex=SENTRY_DEDUP_PENDING_TTL_SECONDS,
            nx=True,
        )
        if not lock_acquired:
            print(f"[webhook] Issue {issue_id} was already processed — ignoring")
            return JSONResponse(
                status_code=200,
                content={"status": "ignored", "reason": "already processed"},
            )
    else:
        print("[webhook] No issue_id was provided — skipping deduplication")

    queue = get_queue()
    job = queue.enqueue(
        process_sentry_job,
        owner, repo,
        error["type"], error["message"], error["file"], error["line"],
        error["related_file_paths"],
        job_timeout=120,
    )

    if dedup_key and dedup_redis:
        dedup_redis.set(dedup_key, f"queued:{job.id}", ex=SENTRY_DEDUP_TTL_SECONDS)

    print(f"[webhook] Sentry job enqueued: {job.id}")

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "job_id": job.id,
            "message": "Bug analysis queued — GitHub Issue will appear shortly",
        },
    )
